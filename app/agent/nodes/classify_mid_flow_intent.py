# app/agent/nodes/classify_mid_flow_intent.py
#
# Runs BEFORE recap_and_confirm/request_document when stage is
# "awaiting_criteria_confirmation" or "awaiting_document". Catches
# off-script signals those stages otherwise can't see: a genuine
# "different document" request, or a criteria edit at
# "awaiting_document".
#
# FIXED — confirmed real incident: once a document was already
# uploaded, a routine follow-up message ("here is the document")
# was misclassified as "new_document" again, re-triggering
# reset_for_new_document and silently DELETING the just-uploaded
# chunks before they were ever evaluated. Root cause: the model had
# no signal that a document was already present, and anchored on
# earlier "different document" phrasing from a prior turn instead of
# judging the current message on its own.
#
# Fix has TWO layers, deliberately not just one:
#   1. Prompt now explicitly states whether a document is already
#      uploaded, and instructs the model to treat any message that
#      could plausibly be confirming/referencing that document as
#      on_script, not new_document.
#   2. A hard code-level override: if a document is ALREADY uploaded
#      for this session, "new_document" can NEVER be returned from
#      this node, regardless of what the model says. This makes the
#      destructive path structurally unreachable rather than just
#      "less likely" — a false negative (occasionally missing a
#      genuine new-document request) is a far smaller cost than a
#      false positive (silently deleting real uploaded data), so the
#      override is deliberately asymmetric.

import logging

from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field

from app.agent.context import AgentContext
from app.agent.state import RFPAnalyzerState
from app.llm import llm
from app.repository.token_usage_repository import TokenUsageRepository

logger = logging.getLogger("app.agent.nodes.classify_mid_flow_intent")

_RECENT_MESSAGE_WINDOW = 3

_SYSTEM_PROMPT_TEMPLATE = """The user is mid-workflow in a proposal evaluation tool. Current stage: {stage}.

A document has {doc_status} been uploaded for this session at this stage.

At "awaiting_criteria_confirmation", the user is expected to either confirm the proposed evaluation criteria, or ask to adjust them — both count as on_script.

At "awaiting_document", the user is expected to acknowledge they're about to upload, or have just uploaded, a document — this counts as on_script. Generic acknowledgments like "here you go", "here is the document", "done", "sent it", "that's the one", "use the same criteria for this" are ALWAYS on_script at this stage, even if a new-document/keep-criteria question was resolved in the immediately preceding turn — a resolved question does not need to be re-litigated by a routine follow-up.

CRITICAL RULE: if a document has ALREADY been uploaded at this stage (see above), treat ANY message that could plausibly be describing, confirming, referencing, or acknowledging THAT already-uploaded document as on_script — even if the wording loosely echoes an earlier "different document" request. Do NOT classify a message as new_document just because it superficially resembles earlier phrasing, once a document is already present. Only classify new_document when a document is already uploaded if the message is UNAMBIGUOUS about wanting to discard that document and swap to a DIFFERENT, THIRD one.

Classify the user's most recent message. Base your classification ONLY on what the CURRENT message itself says — do not infer new_document intent, or a keep_criteria answer, from earlier turns unless the CURRENT message restates or clearly continues that same request in its own words.

- on_script: normal input for the current stage, as described above.
- new_document: the CURRENT message clearly and explicitly signals wanting a DIFFERENT document than whatever is currently in progress (subject to the CRITICAL RULE above if a document is already uploaded).
- criteria_edit: the CURRENT message wants to ADD/REMOVE/CHANGE evaluation criteria (only meaningful at "awaiting_document").
- unclear: doesn't clearly fit any of the above.

If a new_document request was already resolved in a prior turn (the assistant already asked for/acknowledged the new document), do NOT reclassify a routine follow-up as new_document again — treat it as on_script instead.

Default to on_script unless the CURRENT message unambiguously signals one of the other categories. Respond using the structured format provided."""


class MidFlowClassification(BaseModel):
    category: str = Field(
        description="One of: on_script, new_document, criteria_edit, unclear"
    )
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

    doc_status = "already" if state.get("uploaded_filenames") else "NOT yet"
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
        stage=state["stage"], doc_status=doc_status
    )

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

    # SAFETY NET: if a document is ALREADY present and the model
    # still says new_document, override to on_script rather than
    # trusting it blindly. This is the exact misfire pattern
    # confirmed in real testing — a just-uploaded document silently
    # deleted one turn later.
    if parsed.category == "new_document" and state.get("uploaded_filenames"):
        logger.warning(
            "classify_mid_flow_intent: model returned 'new_document' "
            "while a document is ALREADY uploaded for session %s — "
            "overriding to 'on_script' to prevent silent data loss. "
            "If this fires often, the prompt needs further tuning "
            "rather than relying solely on this override.",
            state["session_id"],
        )
        parsed.category = "on_script"

    logger.info(
        "Mid-flow intent classified as '%s' for session %s (stage=%s, doc_present=%s)",
        parsed.category, state["session_id"], state["stage"],
        bool(state.get("uploaded_filenames")),
    )

    return {
        "mid_flow_category": parsed.category,
        "keep_criteria": parsed.keep_criteria,
        "keep_criteria_specified": parsed.keep_criteria_specified,
    }