# app/agent/nodes/classify_post_evaluation_intent.py
#
# Runs when stage == "evaluated" and intent == "task_relevant"
# (classify_intent already filtered out social/off-topic before
# this is ever reached). Determines what the user wants to do now
# that an evaluation has already completed — per the spec's named
# examples (slide summary, improvement suggestions, risk/gap
# highlights) and the broader cases discussed (criteria change,
# new document).
#
# Per the spec's Optional Outputs Policy ("Do NOT offer or suggest
# any optional features. Only produce additional outputs if the
# user explicitly requests them") — this node NEVER proactively
# suggests options. It only classifies what was explicitly asked,
# and falls back to "unclear" (asking the user directly) rather
# than guessing or volunteering choices.

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field

from app.agent.context import AgentContext
from app.agent.state import RFPAnalyzerState
from app.llm import llm
from app.repository.token_usage_repository import TokenUsageRepository

logger = logging.getLogger("app.agent.nodes.classify_post_evaluation_intent")

_RECENT_MESSAGE_WINDOW = 3

_SYSTEM_PROMPT = """A user has already received a completed proposal/RFP evaluation (scoring table, overall score, executive summary). They have now sent a new message. Classify what they want, using the recent conversation for context.

Categories:
- additional_output: an explicit request for something ADDITIONAL based on the EXISTING evaluation already completed (e.g. "create a slide-ready summary", "give improvement suggestions for scores below 4", "highlight major risks or gaps"). Only set this if the request can be answered using the evaluation already done — no new document or criteria needed.
- criteria_change: the user wants to adjust/add/remove evaluation criteria and RE-EVALUATE THE SAME document already uploaded.
- new_document: the user wants to evaluate a DIFFERENT/NEW document.
- unclear: anything that doesn't clearly fit the above, or any general follow-up question.

If category is "new_document": did the user ALREADY specify whether to reuse the same criteria as before, or provide new ones? Set keep_criteria to true (explicitly said to reuse/same criteria), false (explicitly said they want different/new criteria), or null (not specified either way — must be asked).

If category is "additional_output": describe specifically what they're asking for, in a few words (e.g. "slide-ready summary", "improvement suggestions for low scores", "risk and gap highlights").

Do not guess generously — if genuinely unclear, use "unclear" rather than assuming.

Respond using the structured format provided."""


class PostEvaluationClassification(BaseModel):
    category: str = Field(
        description="One of: additional_output, criteria_change, new_document, unclear"
    )
    additional_output_description: str = Field(
        description="What specific additional output is being requested. Empty string if category is not additional_output."
    )
    keep_criteria: bool = Field(
        description="True if user said to reuse same criteria, relevant ONLY if category is new_document. Defaults to False when not applicable or not specified — see keep_criteria_specified for whether this was actually stated."
    )
    keep_criteria_specified: bool = Field(
        description="True if the user explicitly addressed whether to keep criteria (relevant only when category is new_document). False if not mentioned at all, in which case keep_criteria's value should be ignored and the user must be asked."
    )


async def classify_post_evaluation_intent(
    state: RFPAnalyzerState, runtime: Runtime[AgentContext]
) -> dict:
    recent_messages = state["messages"][-_RECENT_MESSAGE_WINDOW:]

    structured_llm = llm.with_structured_output(
        PostEvaluationClassification, include_raw=True
    )

    try:
        result = await structured_llm.ainvoke(
            [SystemMessage(content=_SYSTEM_PROMPT)] + recent_messages
        )
        parsed: PostEvaluationClassification = result["parsed"]
        raw_message = result.get("raw")

        if raw_message is not None and getattr(raw_message, "usage_metadata", None):
            token_repo = TokenUsageRepository(runtime.context.db)
            await token_repo.record_llm_call(
                session_id=state["session_id"],
                user_id=state["user_id"],
                node="classify_post_evaluation_intent",
                response=raw_message,
            )
        else:
            logger.warning(
                "classify_post_evaluation_intent: no usage_metadata — "
                "token usage not logged."
            )

    except Exception as e:
        logger.warning(
            "classify_post_evaluation_intent: include_raw path failed "
            "(%s) — falling back without token logging.", e,
        )
        plain_structured_llm = llm.with_structured_output(PostEvaluationClassification)
        parsed = await plain_structured_llm.ainvoke(
            [SystemMessage(content=_SYSTEM_PROMPT)] + recent_messages
        )

    logger.info(
        "Post-evaluation intent classified as '%s' for session %s",
        parsed.category, state["session_id"],
    )

    # SAFETY NET (same principle as classify_mid_flow_intent's fix,
    # but NOT the same behavior — see note below). If a document is
    # ALREADY uploaded and the model says new_document, this is NOT
    # necessarily wrong: at stage == "evaluated", "I have a
    # different document" IS the normal, expected trigger for this
    # category. Unlike the mid-flow gate, we do NOT force-downgrade
    # to a different category here — doing so would break the
    # correct, common case. This is deliberately just a visibility
    # log, so a genuine misfire pattern would still be noticeable in
    # logs if it ever starts happening often, without changing
    # behavior for the expected, correct case.
    if parsed.category == "new_document" and state.get("uploaded_filenames"):
        logger.info(
            "classify_post_evaluation_intent: 'new_document' with "
            "document(s) already present for session %s — expected "
            "for this stage, proceeding normally.", state["session_id"],
        )

    return {
        "post_eval_category": parsed.category,
        "post_eval_output_description": parsed.additional_output_description,
        "keep_criteria": parsed.keep_criteria,
        "keep_criteria_specified": parsed.keep_criteria_specified,
    }