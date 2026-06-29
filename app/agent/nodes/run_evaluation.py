# app/agent/nodes/run_evaluation.py
#
# Runs when stage == "ready_to_evaluate", reached directly from
# request_document in the SAME turn (see ADR-R004) — never waits
# for a separate user message to trigger.
#
# ADAPTIVE STRATEGY, decided after weighing accuracy tradeoffs
# explicitly (see design discussion):
#   Small/medium document (fits in one context call per criterion)
#     → simple path: one call per criterion, full document in context
#   Large document (doesn't fit)
#     → map-reduce: batch chunks by token count, one call per batch
#       covering ALL criteria (map), then one synthesis call per
#       criterion combining all batches' findings (reduce)
#
# Per criterion: score 0-5 + rationale citing specific pages/slides/
# sections, PLUS an explicit confidence signal (thin vs strong
# evidence) — does not eliminate evidence-fragmentation risk, but
# makes it visible to the human reviewer rather than hidden behind
# uniform-looking scores. Every output here is draft-only, human
# review required, per the agent's own system instructions.
#
# Uses structured output (with_structured_output + include_raw, same
# defensive pattern as request_criteria/recap_and_confirm) — not
# free text parsing.

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field

from app.agent.context import AgentContext
from app.agent.state import RFPAnalyzerState
from app.llm import llm
from app.repository.submission_repository import SubmissionRepository
from app.repository.token_usage_repository import TokenUsageRepository

logger = logging.getLogger("app.agent.nodes.run_evaluation")

# Rough chars-per-token heuristic, consistent with how this project
# has approximated token counts elsewhere when an exact tokenizer
# count isn't readily available (see chunker.py's natural-unit
# size check).
_CHARS_PER_TOKEN_ESTIMATE = 4

# Conservative ceiling for "does this fit comfortably in one call,
# alongside the system prompt, criteria, and room for the response."
# Deliberately conservative rather than pushing right up against a
# model's actual max context — leaves headroom and avoids the
# documented "lost in the middle" quality degradation that can occur
# even when content technically fits.
_SIMPLE_PATH_TOKEN_CEILING = 12_000

# Batch size for the map-reduce path — same conservative reasoning.
_BATCH_TOKEN_CEILING = 8_000


class CriterionScore(BaseModel):
    criterion: str = Field(description="The exact criterion being scored")
    score: int = Field(description="Score from 0 (not addressed) to 5 (fully addressed)")
    rationale: str = Field(
        description="Evidence-based rationale, citing specific sections/pages/slides where possible. Must not infer content not explicitly present in the document."
    )
    confidence: str = Field(
        description="One of: 'strong' (clear, well-supported evidence), 'moderate' (some evidence found), 'thin' (little to no direct evidence found)"
    )


class EvaluationResult(BaseModel):
    scores: list[CriterionScore]


def _parse_criteria_list(criteria_text: str) -> list[str]:
    """
    Splits the stored criteria text into individual criterion names.
    criteria is stored as a cleanly-formatted list (per
    request_criteria's extraction prompt) — typically markdown-style
    bullets or a comma-separated line. This is a best-effort plain-
    text split, not a structured field, since request_criteria
    deliberately stores criteria as a readable string for display
    purposes (e.g. recap_and_confirm shows it back to the user
    verbatim).
    """
    lines = [
        line.strip("-•* ").strip()
        for line in criteria_text.splitlines()
        if line.strip("-•* ").strip()
    ]
    if len(lines) > 1:
        return lines
    # Fallback: single line, possibly comma-separated
    return [c.strip() for c in criteria_text.split(",") if c.strip()]


def _estimate_tokens(text: str) -> int:
    return len(text) // _CHARS_PER_TOKEN_ESTIMATE


async def run_evaluation(
    state: RFPAnalyzerState, runtime: Runtime[AgentContext]
) -> dict:
    submission_repo = SubmissionRepository(runtime.context.db)
    chunks = await submission_repo.get_session_chunks(state["session_id"])

    if not chunks:
        logger.error(
            "run_evaluation: no chunks found for session %s despite "
            "stage being ready_to_evaluate", state["session_id"],
        )
        return {
            "response_to_user": (
                "I wasn't able to find the uploaded document's content. "
                "Could you try uploading it again?"
            ),
        }

    criteria_list = _parse_criteria_list(state["criteria"] or "")
    full_document_text = "\n\n".join(
        f"[{c.get('page', c.get('slide', 'section'))}] {c['text']}"
        for c in chunks
    )
    total_tokens = _estimate_tokens(full_document_text)

    logger.info(
        "run_evaluation: session=%s criteria_count=%d estimated_tokens=%d",
        state["session_id"], len(criteria_list), total_tokens,
    )

    if total_tokens <= _SIMPLE_PATH_TOKEN_CEILING:
        scores = await _evaluate_simple_path(
            state, runtime, criteria_list, full_document_text
        )
    else:
        scores = await _evaluate_map_reduce(
            state, runtime, criteria_list, chunks
        )

    return _finalize_scoring(state, scores)


# Add to app/agent/nodes/run_evaluation.py

_SIMPLE_PATH_SYSTEM_PROMPT = """You are scoring a submission document against ONE evaluation criterion.

Criterion: {criterion}

You will be given the full text of the submission document, with page/slide/section markers in brackets.

Score how well the document addresses this criterion, from 0 to 5:
0 = Not addressed
1 = Minimally addressed
2 = Partially addressed
3 = Adequately addressed
4 = Strongly addressed
5 = Fully addressed

Rules:
- Provide a concise, evidence-based rationale.
- Reference SPECIFIC page/slide/section markers from the document where the evidence appears.
- Do NOT infer or assume anything not explicitly stated in the document.
- Set confidence to "thin" if you found little or no direct evidence, "moderate" if you found some, "strong" if the evidence is clear and well-supported.

Respond using the structured format provided."""


async def _evaluate_simple_path(
    state: RFPAnalyzerState,
    runtime: Runtime[AgentContext],
    criteria_list: list[str],
    full_document_text: str,
) -> list[CriterionScore]:
    """
    One LLM call per criterion, with the full document in context.
    Used when the document is small enough to fit comfortably —
    simpler and more accurate than map-reduce, since the model sees
    everything at once for each criterion rather than fragmented
    batches.
    """
    scores: list[CriterionScore] = []
    token_repo = TokenUsageRepository(runtime.context.db)

    for criterion in criteria_list:
        system_prompt = _SIMPLE_PATH_SYSTEM_PROMPT.format(criterion=criterion)

        structured_llm = llm.with_structured_output(
            CriterionScore, include_raw=True
        )

        try:
            result = await structured_llm.ainvoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=full_document_text),
            ])
            score: CriterionScore = result["parsed"]
            raw_message = result.get("raw")

            if raw_message is not None and getattr(raw_message, "usage_metadata", None):
                await token_repo.record_llm_call(
                    session_id=state["session_id"],
                    user_id=state["user_id"],
                    node="run_evaluation_simple",
                    response=raw_message,
                )
            else:
                logger.warning(
                    "run_evaluation: no usage_metadata for criterion "
                    "'%s' — token usage not logged.", criterion,
                )

        except Exception as e:
            logger.warning(
                "run_evaluation: include_raw path failed for criterion "
                "'%s' (%s) — falling back without token logging.",
                criterion, e,
            )
            plain_structured_llm = llm.with_structured_output(CriterionScore)
            score = await plain_structured_llm.ainvoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=full_document_text),
            ])

        # Defensive: ensure the criterion name in the result matches
        # what we asked about, in case the model paraphrases it.
        score.criterion = criterion
        scores.append(score)

    return scores


class BatchFinding(BaseModel):
    criterion: str
    evidence_found: bool = Field(
        description="True if this batch contains evidence relevant to this criterion"
    )
    evidence_summary: str = Field(
        default="",
        description="What evidence was found, with specific page/slide/section markers. Empty if evidence_found is False."
    )


class BatchEvaluation(BaseModel):
    findings: list[BatchFinding]


_MAP_SYSTEM_PROMPT = """You are reviewing ONE SECTION of a larger submission document, looking for evidence relevant to a set of evaluation criteria.

Criteria to look for evidence about:
{criteria_list}

You are given only a PORTION of the document, with page/slide/section markers in brackets. This is not the whole document — only report what THIS portion contains.

For EACH criterion, report:
- Whether this portion contains any relevant evidence
- If so, summarize it, citing the specific page/slide/section markers from THIS portion

Do not infer or assume anything not explicitly stated in this portion. It is normal and expected for most criteria to have no evidence in any given portion.

Respond using the structured format provided."""

_REDUCE_SYSTEM_PROMPT = """You are finalizing a score for ONE evaluation criterion, based on evidence gathered from across an entire document (gathered in separate portions, shown below).

Criterion: {criterion}

Evidence gathered from across the document:
{evidence_summary}

Score from 0 to 5:
0 = Not addressed
1 = Minimally addressed
2 = Partially addressed
3 = Adequately addressed
4 = Strongly addressed
5 = Fully addressed

Rules:
- Base your score ONLY on the evidence shown above — do not assume anything beyond it.
- Reference the specific page/slide/section markers mentioned in the evidence.
- If no evidence was found across any portion, score 0 and say so plainly.
- Set confidence to "thin" if evidence was sparse/found in only one portion, "moderate" if found in a couple of places, "strong" if found clearly and repeatedly across multiple portions.

Respond using the structured format provided."""


def _batch_chunks_by_tokens(chunks: list[dict]) -> list[list[dict]]:
    """
    Groups chunks into batches, each kept under _BATCH_TOKEN_CEILING.
    A single batch may exceed the ceiling only if one chunk alone
    is larger than the ceiling (rare — would only happen for an
    unusually large "natural unit" chunk, e.g. a dense PPTX slide
    that wasn't split further) — accepted as a known edge case
    rather than splitting a chunk awkwardly mid-content.
    """
    batches: list[list[dict]] = []
    current_batch: list[dict] = []
    current_tokens = 0

    for chunk in chunks:
        chunk_tokens = _estimate_tokens(chunk["text"])
        if current_batch and current_tokens + chunk_tokens > _BATCH_TOKEN_CEILING:
            batches.append(current_batch)
            current_batch = []
            current_tokens = 0
        current_batch.append(chunk)
        current_tokens += chunk_tokens

    if current_batch:
        batches.append(current_batch)

    return batches


async def _evaluate_map_reduce(
    state: RFPAnalyzerState,
    runtime: Runtime[AgentContext],
    criteria_list: list[str],
    chunks: list[dict],
) -> list[CriterionScore]:
    """
    MAP: one call per batch, covering ALL criteria at once, gathering
    evidence found in that batch only.
    REDUCE: one call per criterion, synthesizing all batches'
    findings for that criterion into a final score.
    """
    token_repo = TokenUsageRepository(runtime.context.db)
    batches = _batch_chunks_by_tokens(chunks)

    logger.info(
        "run_evaluation: map-reduce path, %d batch(es) for session %s",
        len(batches), state["session_id"],
    )

    # ── MAP ──────────────────────────────────────────────────────────
    # evidence_by_criterion[criterion] = list of evidence_summary
    # strings gathered across all batches that found something.
    evidence_by_criterion: dict[str, list[str]] = {c: [] for c in criteria_list}

    criteria_list_text = "\n".join(f"- {c}" for c in criteria_list)

    for batch_index, batch in enumerate(batches):
        batch_text = "\n\n".join(
            f"[{c.get('page', c.get('slide', 'section'))}] {c['text']}"
            for c in batch
        )
        system_prompt = _MAP_SYSTEM_PROMPT.format(criteria_list=criteria_list_text)

        structured_llm = llm.with_structured_output(
            BatchEvaluation, include_raw=True
        )

        try:
            result = await structured_llm.ainvoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=batch_text),
            ])
            batch_eval: BatchEvaluation = result["parsed"]
            raw_message = result.get("raw")

            if raw_message is not None and getattr(raw_message, "usage_metadata", None):
                await token_repo.record_llm_call(
                    session_id=state["session_id"],
                    user_id=state["user_id"],
                    node="run_evaluation_map",
                    response=raw_message,
                )
            else:
                logger.warning(
                    "run_evaluation: no usage_metadata for batch %d — "
                    "token usage not logged.", batch_index,
                )

        except Exception as e:
            logger.warning(
                "run_evaluation: include_raw path failed for batch %d "
                "(%s) — falling back without token logging.",
                batch_index, e,
            )
            plain_structured_llm = llm.with_structured_output(BatchEvaluation)
            batch_eval = await plain_structured_llm.ainvoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=batch_text),
            ])

        for finding in batch_eval.findings:
            if finding.evidence_found and finding.criterion in evidence_by_criterion:
                evidence_by_criterion[finding.criterion].append(finding.evidence_summary)

    # ── REDUCE ───────────────────────────────────────────────────────
    scores: list[CriterionScore] = []

    for criterion in criteria_list:
        gathered_evidence = evidence_by_criterion.get(criterion, [])
        evidence_text = (
            "\n".join(f"- {e}" for e in gathered_evidence)
            if gathered_evidence
            else "(no evidence found in any portion of the document)"
        )
        system_prompt = _REDUCE_SYSTEM_PROMPT.format(
            criterion=criterion, evidence_summary=evidence_text
        )

        structured_llm = llm.with_structured_output(
            CriterionScore, include_raw=True
        )

        try:
            result = await structured_llm.ainvoke([
                SystemMessage(content=system_prompt),
            ])
            score: CriterionScore = result["parsed"]
            raw_message = result.get("raw")

            if raw_message is not None and getattr(raw_message, "usage_metadata", None):
                await token_repo.record_llm_call(
                    session_id=state["session_id"],
                    user_id=state["user_id"],
                    node="run_evaluation_reduce",
                    response=raw_message,
                )
            else:
                logger.warning(
                    "run_evaluation: no usage_metadata for reduce step "
                    "on criterion '%s' — token usage not logged.", criterion,
                )

        except Exception as e:
            logger.warning(
                "run_evaluation: include_raw path failed for reduce "
                "step on criterion '%s' (%s) — falling back without "
                "token logging.", criterion, e,
            )
            plain_structured_llm = llm.with_structured_output(CriterionScore)
            score = await plain_structured_llm.ainvoke([
                SystemMessage(content=system_prompt),
            ])

        score.criterion = criterion
        scores.append(score)

    return scores


def _finalize_scoring(state: RFPAnalyzerState, scores: list[CriterionScore]) -> dict:
    """
    Aggregates per-criterion scores into a total. Weighted ONLY if
    state["criteria_weights"] was actually populated (user-provided,
    optional — see ADR on weighting being purely user-driven, never
    invented by the agent). Otherwise a simple equal-weighted total,
    matching the spec's plain example ("48/60").
    """
    weights = state.get("criteria_weights") or {}

    if weights:
        # Weighted total: sum(score * weight) — assumes weights are
        # fractions summing to ~1.0, as captured by request_criteria.
        weighted_sum = sum(
            s.score * weights.get(s.criterion, 0.0) for s in scores
        )
        max_possible = 5 * sum(weights.get(s.criterion, 0.0) for s in scores)
        is_weighted = True
    else:
        weighted_sum = sum(s.score for s in scores)
        max_possible = 5 * len(scores)
        is_weighted = False

    percentage = (weighted_sum / max_possible * 100) if max_possible > 0 else 0

    scoring_results = {
        "scores": [s.model_dump() for s in scores],
        "total_score": round(weighted_sum, 2),
        "max_possible": max_possible,
        "percentage": round(percentage, 1),
        "is_weighted": is_weighted,
    }

    logger.info(
        "run_evaluation complete for session %s: %s/%s (%.1f%%)",
        state["session_id"], scoring_results["total_score"],
        max_possible, percentage,
    )

    return {
        "stage": "evaluated",
        "scoring_results": scoring_results,
    }