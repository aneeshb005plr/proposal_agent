# app/agent/nodes/classify_mid_flow_intent.py
#
# Runs BEFORE recap_and_confirm/request_document when stage is
# "awaiting_criteria_confirmation" or "awaiting_document". Catches
# the off-script "different document" signal those stages were
# missing, AND — to stay consistent with
# classify_post_evaluation_intent's behavior — detects if the user
# already proactively answered "same criteria or new?" in the same
# message, so reset_for_new_document doesn't re-ask a question
# that's already been answered, regardless of which stage the
# request came from.

import logging

from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field

from app.agent.context import AgentContext
from app.agent.state import RFPAnalyzerState
from app.llm import llm
from app.repository.token_usage_repository import TokenUsageRepository

logger = logging.getLogger("app.agent.nodes.classify_mid_flow_intent")

_RECENT_MESSAGE_WINDOW = 6

_SYSTEM_PROMPT_TEMPLATE = """The user is mid-workflow in a proposal evaluation tool. Current stage: {stage}.

At "awaiting_criteria_confirmation", the user is expected to either confirm the proposed evaluation criteria, or ask to adjust them — both count as on_script.

At "awaiting_document", the user is expected to acknowledge they're about to upload, or have just uploaded, a document — this counts as on_script.

Classify the user's most recent message:
- on_script: normal input for the current stage.
- new_document: the user clearly wants to evaluate a DIFFERENT document.
- criteria_edit: the user wants to ADD/REMOVE/CHANGE evaluation criteria (only meaningful at "awaiting_document" — at "awaiting_criteria_confirmation" this is already on_script, since recap_and_confirm handles it natively).
- unclear: doesn't clearly fit any of the above.

Default to on_script unless the message clearly signals one of the other categories. Respond using the structured format provided."""



class MidFlowClassification(BaseModel):
    category: str = Field(description="One of: on_script, new_document, criteria_edit, unclear")

    keep_criteria: bool = Field(
        description="Relevant only if category is new_document. True if user said to reuse same criteria. Ignore if keep_criteria_specified is False."
    )
    keep_criteria_specified: bool = Field(
        description="True if the user explicitly addressed whether to keep criteria (relevant only when category is new_document). False if not mentioned at all."
    )


async def classify_mid_flow_intent(
    state: RFPAnalyzerState, runtime: Runtime[AgentContext]
) -> dict:
    recent_messages = state["messages"][-_RECENT_MESSAGE_WINDOW:]
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(stage=state["stage"])

    structured_llm = llm.with_structured_output(
        MidFlowClassification, include_raw=True
    )

    try:
        result = await structured_llm.ainvoke(
            [SystemMessage(content=system_prompt)] + recent_messages
        )
        parsed: MidFlowClassification = result["parsed"]
        raw_message = result.get("raw")

        if raw_message is not None and getattr(raw_message, "usage_metadata", None):
            token_repo = TokenUsageRepository(runtime.context.db)
            await token_repo.record_llm_call(
                session_id=state["session_id"],
                user_id=state["user_id"],
                node="classify_mid_flow_intent",
                response=raw_message,
            )
        else:
            logger.warning(
                "classify_mid_flow_intent: no usage_metadata — "
                "token usage not logged."
            )
    except Exception as e:
        logger.warning(
            "classify_mid_flow_intent: include_raw path failed (%s) — "
            "falling back without token logging.", e,
        )
        plain_structured_llm = llm.with_structured_output(MidFlowClassification)
        parsed = await plain_structured_llm.ainvoke(
            [SystemMessage(content=system_prompt)] + recent_messages
        )

    logger.info(
        "Mid-flow intent classified as '%s' for session %s (stage=%s)",
        parsed.category, state["session_id"], state["stage"],
    )

    return {
        "mid_flow_category": parsed.category,
        "keep_criteria": parsed.keep_criteria,
        "keep_criteria_specified": parsed.keep_criteria_specified,
    }