# app/agent/criteria_extraction.py
#
# Shared criteria extraction logic, used by THREE call sites:
#   1. request_criteria (first-time collection)
#   2. recap_and_confirm's adjustment path (criteria change during
#      confirmation)
#   3. handle_post_evaluation_input's Category B (criteria change
#      after evaluation already completed)
#
# Built ONCE, here, rather than duplicated across those three nodes
# — they all need the exact same capability: combine whatever chat
# text and/or uploaded file text is available, and extract criteria
# (with optional weighting) from the combined input.
#
# CRITICAL: dict[str, X] fields are NOT supported in structured
# output schemas (confirmed Azure/OpenAI strict-mode limitation,
# see CriterionWeight in run_evaluation.py for the established
# pattern) — weights uses list[CriterionWeight], converted to a
# plain dict in application code AFTER the call returns.

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field


from app.agent.context import AgentContext
from app.agent.state import RFPAnalyzerState
from app.llm import llm
from app.repository.token_usage_repository import TokenUsageRepository
from app.repository.criteria_upload_repository import CriteriaUploadRepository

logger = logging.getLogger("app.agent.criteria_extraction")

_SYSTEM_PROMPT = """You help collect or update evaluation criteria for a proposal/RFP scoring tool.

You may be given an uploaded criteria document's content, the user's chat message, or both. Consider EVERYTHING given together — if both a file and a message are present, combine their content; the chat message may add to, clarify, or override what's in the file.

Determine:
- Does the combined input contain actual evaluation criteria (things to score a document against, e.g. "technical approach", "pricing", "timeline")?
- If yes: extract them as a clean, readable list.
- Did the input explicitly assign weights/percentages to any criteria (e.g. "technical approach (40%)")? Weighting is OPTIONAL — only set has_weighting=True if weights are explicitly and unambiguously stated. Never infer or invent weights.
- If no criteria present at all: there are no criteria present.

Respond using the structured format provided."""


class CriterionWeight(BaseModel):
    criterion: str = Field(description="The criterion name")
    weight: float = Field(description="Weight as a fraction, e.g. 0.4 for 40%")


class CriteriaExtraction(BaseModel):
    criteria_found: bool = Field(
        description="True if the combined input contains actual evaluation criteria"
    )
    extracted_criteria: str = Field(
        description="The criteria, cleanly formatted as a list. Empty string if criteria_found is False."
    )
    has_weighting: bool = Field(
        description="True ONLY if weights/percentages were explicitly assigned. False if not mentioned."
    )
    weights: list[CriterionWeight] = Field(
        description="List of criterion/weight pairs, ONLY if has_weighting is True. Empty list otherwise."
    )


def _build_combined_input(chat_text: str, uploaded_file_text: str | None) -> str:
    parts = []
    if uploaded_file_text:
        parts.append(f"[Uploaded criteria document content]\n{uploaded_file_text}")
    if chat_text:
        parts.append(f"[User's chat message]\n{chat_text}")
    return "\n\n".join(parts) if parts else ""


async def extract_criteria(
    chat_text: str,
    uploaded_file_text: str | None,
    session_id: str,
    user_id: str,
    node_name: str,
    runtime: Runtime[AgentContext],
) -> CriteriaExtraction:
    """
    Runs the combined-input extraction call. node_name is passed
    through purely for token-usage logging, so usage can be
    attributed to whichever node actually invoked this (request_criteria,
    recap_and_confirm, or handle_post_evaluation_input), even though
    the underlying call is shared.
    """
    combined_input = _build_combined_input(chat_text, uploaded_file_text)

    structured_llm = llm.with_structured_output(
        CriteriaExtraction, include_raw=True
    )

    try:
        result = await structured_llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=combined_input),
        ])
        parsed: CriteriaExtraction = result["parsed"]
        raw_message = result.get("raw")

        if raw_message is not None and getattr(raw_message, "usage_metadata", None):
            token_repo = TokenUsageRepository(runtime.context.db)
            await token_repo.record_llm_call(
                session_id=session_id,
                user_id=user_id,
                node=node_name,
                response=raw_message,
            )
        else:
            logger.warning(
                "extract_criteria: no usage_metadata available (called "
                "from %s) — token usage not logged.", node_name,
            )

    except Exception as e:
        logger.warning(
            "extract_criteria: include_raw path failed (%s, called from "
            "%s) — falling back without token logging.", e, node_name,
        )
        plain_structured_llm = llm.with_structured_output(CriteriaExtraction)
        parsed = await plain_structured_llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=combined_input),
        ])

    return parsed


def weights_to_dict(extraction: CriteriaExtraction) -> dict[str, float]:
    """Converts the list[CriterionWeight] shape back to a plain dict,
    matching state["criteria_weights"]'s existing type."""
    if not extraction.has_weighting:
        return {}
    return {w.criterion: w.weight for w in extraction.weights}


async def reset_for_criteria_change(
    state: RFPAnalyzerState, runtime: Runtime[AgentContext]
) -> dict:
    last_message = state["messages"][-1].content if state["messages"] else ""

    criteria_upload_repo = CriteriaUploadRepository(runtime.context.db)
    pending_file_text = await criteria_upload_repo.get_pending_text(
        state["session_id"]
    )

    # FIXED: previously only passed last_message, with no knowledge
    # of the EXISTING confirmed criteria — a request like "also
    # evaluate against X" would silently overwrite state["criteria"]
    # down to just the new item, discarding everything previously
    # confirmed. Explicitly include existing criteria in the input
    # so the extraction call can genuinely merge, not just replace.
    combined_chat_text = (
        f"Existing confirmed criteria:\n{state['criteria']}\n\n"
        f"Requested change:\n{last_message}"
    )

    parsed = await extract_criteria(
        chat_text=combined_chat_text,
        uploaded_file_text=pending_file_text,
        session_id=state["session_id"],
        user_id=state["user_id"],
        node_name="reset_for_criteria_change",
        runtime=runtime,
    )

    if pending_file_text:
        await criteria_upload_repo.clear_pending_text(state["session_id"])

    if not parsed.criteria_found:
        return {
            "response_to_user": (
                "I wasn't able to identify the updated criteria — "
                "could you share what you'd like to change?"
            ),
        }

    logger.info(
        "Criteria changed post-evaluation for session %s — prior "
        "results invalidated", state["session_id"],
    )

    return {
        "criteria": parsed.extracted_criteria,
        "criteria_confirmed": False,
        "criteria_weights": weights_to_dict(parsed),
        "scoring_results": None,
        "executive_summary": None,
        "stage": "awaiting_criteria_confirmation",
        "response_to_user": (
            f"Got it — here's the updated criteria, which will be used "
            f"to re-evaluate the same document:\n\n"
            f"{parsed.extracted_criteria}\n\n"
            f"Does this include everything you want in the evaluation? "
            f"Let me know if anything needs to be added or adjusted."
        ),
    }