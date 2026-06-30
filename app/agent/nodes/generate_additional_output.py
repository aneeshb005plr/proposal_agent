# app/agent/nodes/generate_additional_output.py
#
# Handles Category A: an explicit request for something additional,
# based on the ALREADY-COMPLETED evaluation (scoring_results,
# executive_summary already in state — no re-evaluation needed).
#
# Per the spec's named examples: slide-ready summary, improvement
# suggestions for low scores, risk/gap highlights — plus anything
# else the user explicitly asks for that's answerable from existing
# results. Uses post_eval_output_description (from classification)
# to know what specifically was requested.
#
# Same risk-word prompt-injection pattern as generate_summary —
# this is agent-generated free prose, same compliance consideration
# applies. Does NOT advance stage — stays "evaluated", since this
# doesn't change the evaluation itself, just produces something
# additional from it.

import logging

from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.state import RFPAnalyzerState
from app.knowledge.risk_words import get_risk_words
from app.llm import llm
from app.repository.token_usage_repository import TokenUsageRepository

logger = logging.getLogger("app.agent.nodes.generate_additional_output")

_SYSTEM_PROMPT = """A user has requested additional output based on a proposal/RFP evaluation that has already been completed.

What they specifically asked for: {output_description}

The following words/phrases are NOT permitted anywhere in your output:
{risk_words_list}

Full evaluation results to draw from:
{scoring_summary}

Executive summary already provided to the user:
{executive_summary}

Produce exactly what was requested above, based ONLY on the evaluation results and executive summary given — do not introduce new claims not supported by them. Remain neutral, objective, and professional. Avoid subjective or emotional language.

If what's being requested is a "slide-ready summary," format it as concise bullet points suitable for presentation slides.
If it's "improvement suggestions for low scores," focus specifically on criteria that scored below 4, with concrete, actionable suggestions per criterion.
If it's "risk/gap highlights," focus specifically on criteria with low scores or thin confidence, framed as risks or gaps.
Otherwise, address the request as described, using the same neutral, evidence-based style as the rest of this evaluation."""


def _format_scoring_for_prompt(scoring_results: dict) -> str:
    lines = []
    for s in scoring_results.get("scores", []):
        lines.append(
            f"- {s['criterion']}: {s['score']}/5 (confidence: {s['confidence']}) "
            f"— {s['rationale']}"
        )
    total = scoring_results.get("total_score")
    max_possible = scoring_results.get("max_possible")
    percentage = scoring_results.get("percentage")
    lines.append(f"\nOverall: {total}/{max_possible} ({percentage}%)")
    return "\n".join(lines)


async def generate_additional_output(
    state: RFPAnalyzerState, runtime: Runtime[AgentContext]
) -> dict:
    scoring_results = state.get("scoring_results")
    output_description = state.get("post_eval_output_description") or "additional information"

    if not scoring_results:
        logger.error(
            "generate_additional_output: no scoring_results found for "
            "session %s", state["session_id"],
        )
        return {
            "response_to_user": (
                "I don't have evaluation results to draw from right now."
            ),
        }

    risk_data = get_risk_words()
    risk_words_list = ", ".join(f'"{w}"' for w in risk_data.blocked)

    prompt = _SYSTEM_PROMPT.format(
        output_description=output_description,
        risk_words_list=risk_words_list,
        scoring_summary=_format_scoring_for_prompt(scoring_results),
        executive_summary=state.get("executive_summary") or "(none)",
    )

    response = await llm.ainvoke([SystemMessage(content=prompt)])

    token_repo = TokenUsageRepository(runtime.context.db)
    await token_repo.record_llm_call(
        session_id=state["session_id"],
        user_id=state["user_id"],
        node="generate_additional_output",
        response=response,
    )

    logger.info(
        "Additional output generated for session %s: %s",
        state["session_id"], output_description,
    )

    return {"response_to_user": response.content}