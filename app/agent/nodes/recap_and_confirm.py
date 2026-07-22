# app/agent/nodes/recap_and_confirm.py
#
# Runs when stage == "awaiting_criteria_confirmation" — criteria
# were already extracted in a prior turn (request_criteria) and the
# user is now responding to "Does this include everything... let me
# know if anything needs adjusting."
#
# CONTEXT WINDOW: unlike classify_intent/request_criteria, this node
# genuinely needs more than just the latest message — a bare "yes"
# or "add cost as well" is meaningless without seeing what was
# proposed. Uses a small recent window (last 4 messages), not the
# full unbounded history — consistent with the confirmed real
# production failure mode of unbounded state growth (180KB
# checkpoints) found earlier in this build.
#
# Two real outcomes:
#   1. User confirms ("yes", "looks good", "that's correct")
#      → criteria_confirmed=True, stage advances to
#        "awaiting_document"
#   2. User wants changes ("add X", "remove Y", "also include Z")
#      → re-extract a MERGED criteria list (existing + requested
#        change), stay in awaiting_criteria_confirmation, recap
#        again with the updated list — per RFP Analyzer's own rule
#        that mid-confirmation adjustments are expected and should
#        be re-confirmed, not silently accepted
#
# Uses the SAME defensive include_raw pattern as request_criteria
# for token logging — see that file's header for the open,
# unconfirmed bug this guards against, and the confirmed-harmless
# Pydantic serializer warning that may appear regardless.

import logging

from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field

from app.agent.context import AgentContext
from app.agent.state import RFPAnalyzerState
from app.llm import llm
from app.repository.token_usage_repository import TokenUsageRepository

logger = logging.getLogger("app.agent.nodes.recap_and_confirm")

_RECENT_MESSAGE_WINDOW = 4

_SYSTEM_PROMPT_TEMPLATE = """You are helping confirm evaluation criteria for a proposal/RFP scoring tool.

The criteria currently proposed are:
{current_criteria}

Look at the user's most recent reply (in the conversation below) and determine:
- confirmed: True if the user is explicitly confirming these criteria are correct/complete as-is (e.g. "yes", "looks good", "that's correct")
- updated_criteria: if the user requested any change (add/remove/adjust something), provide the FULL updated criteria list incorporating their change. If they simply confirmed with no changes, leave this empty.

Respond using the structured format provided."""


class ConfirmationResult(BaseModel):
    confirmed: bool = Field(
        description="True if the user explicitly confirmed the criteria as-is, with no changes requested"
    )
    updated_criteria: str = Field(
        default="",
        description="The full updated criteria list if the user requested a change. Empty if confirmed as-is.",
    )


async def recap_and_confirm(
    state: RFPAnalyzerState, runtime: Runtime[AgentContext]
) -> dict:
    recent_messages = state["messages"][-_RECENT_MESSAGE_WINDOW:]

    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
        current_criteria=state["criteria"] or "(none recorded)"
    )

    structured_llm = llm.with_structured_output(
        ConfirmationResult, include_raw=True
    )

    try:
        result = await structured_llm.ainvoke(
            [SystemMessage(content=system_prompt)] + recent_messages
        )
        parsed: ConfirmationResult = result["parsed"]
        raw_message = result.get("raw")

        if raw_message is not None and getattr(raw_message, "usage_metadata", None):
            token_repo = TokenUsageRepository(runtime.context.db)
            await token_repo.record_llm_call(
                session_id=state["session_id"],
                user_id=state["user_id"],
                node="recap_and_confirm",
                response=raw_message,
            )
        else:
            logger.warning(
                "recap_and_confirm: no usage_metadata available — "
                "token usage not logged for this call."
            )

    except Exception as e:
        logger.warning(
            "recap_and_confirm: include_raw path failed (%s) — "
            "falling back to plain structured output without token "
            "logging for this call.", e,
        )
        plain_structured_llm = llm.with_structured_output(ConfirmationResult)
        parsed = await plain_structured_llm.ainvoke(
            [SystemMessage(content=system_prompt)] + recent_messages
        )

    if parsed.confirmed:
        logger.info("Criteria confirmed for session %s", state["session_id"])
        
        # FIXED: previously always asked for the document, even when
        # one was ALREADY uploaded (a real, common flow: user
        # uploads first, then provides criteria afterward). Mirrors
        # request_document's own same-turn handoff pattern (ADR-R004)
        # — if the document already exists, act on it immediately
        # rather than making the user repeat information they've
        # already given.
        if state.get("uploaded_filenames"):
            logger.info(
                "recap_and_confirm: document(s) already present for "
                "session %s — proceeding straight to evaluation in "
                "this turn.", state["session_id"],
            )
            return {
                "criteria_confirmed": True,
                "stage": "ready_to_evaluate",
                "response_to_user": (
                    "Great — criteria confirmed. Since your document is "
                    "already uploaded, I'll begin the evaluation now."
                ),
            }
        
        return {
            "criteria_confirmed": True,
            "stage": "awaiting_document",
            "response_to_user": (
                "Great — criteria confirmed. Please upload the proposal "
                "or RFP response document you'd like evaluated."
            ),
        }

    if parsed.updated_criteria:
        logger.info(
            "Criteria updated mid-confirmation for session %s", state["session_id"]
        )
        return {
            "criteria": parsed.updated_criteria,
            "criteria_confirmed": False,
            "response_to_user": (
                f"Updated. Here's the revised criteria:\n\n"
                f"{parsed.updated_criteria}\n\n"
                f"Does this include everything now?"
            ),
        }

    # Neither clearly confirmed nor a clear update — ask again rather
    # than guess.
    return {
        "response_to_user": (
            "I want to make sure I have this right — does the criteria "
            "I shared look complete, or is there something to add or "
            "change?"
        ),
    }