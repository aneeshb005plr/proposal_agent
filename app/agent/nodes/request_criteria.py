# app/agent/nodes/request_criteria.py
#
# Runs when stage == "awaiting_criteria". REWRITTEN to use the
# shared extract_criteria() helper (app/agent/criteria_extraction.py),
# which combines BOTH the chat message text AND any pending uploaded
# criteria document text into one extraction call — not "file OR
# text," but both considered together, since a user might upload a
# file with substantive chat text alongside it (see design
# discussion).
#
# Checks for pending_criteria_text via CriteriaUploadRepository
# (RFP-Analyzer-specific, NOT shared infra — see that repository's
# header) and clears it immediately after use, regardless of whether
# extraction succeeded — a consumed upload should never be silently
# reconsidered on a later, unrelated turn.

import logging

from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.criteria_extraction import extract_criteria, weights_to_dict
from app.agent.state import RFPAnalyzerState
from app.repository.criteria_upload_repository import CriteriaUploadRepository

logger = logging.getLogger("app.agent.nodes.request_criteria")


async def request_criteria(
    state: RFPAnalyzerState, runtime: Runtime[AgentContext]
) -> dict:
    last_message = state["messages"][-1].content if state["messages"] else ""

    criteria_upload_repo = CriteriaUploadRepository(runtime.context.db)
    pending_file_text = await criteria_upload_repo.get_pending_text(
        state["session_id"]
    )

    parsed = await extract_criteria(
        chat_text=last_message,
        uploaded_file_text=pending_file_text,
        session_id=state["session_id"],
        user_id=state["user_id"],
        node_name="request_criteria",
        runtime=runtime,
    )

    # Clear the pending upload regardless of outcome — it's been
    # considered either way, and must not silently resurface later.
    if pending_file_text:
        await criteria_upload_repo.clear_pending_text(state["session_id"])

    if parsed.criteria_found:
        logger.info(
            "Criteria extracted for session %s (weighted=%s, "
            "from_file=%s)",
            state["session_id"], parsed.has_weighting,
            bool(pending_file_text),
        )
        return {
            "criteria": parsed.extracted_criteria,
            "criteria_confirmed": False,
            "stage": "awaiting_criteria_confirmation",
            "criteria_weights": weights_to_dict(parsed),
            "response_to_user": (
                f"Got it. Here's what I have for your evaluation criteria:\n\n"
                f"{parsed.extracted_criteria}\n\n"
                f"Does this include everything you want in the evaluation? "
                f"Let me know if anything needs to be added or adjusted."
            ),
        }

    return {
        "response_to_user": (
            "Please share your evaluation criteria — you can paste them "
            "directly, upload a document with your rubric, or describe "
            "what you'd like the proposal scored on (for example: "
            "technical approach, cost, timeline). You can optionally "
            "assign weights if some criteria matter more than others."
        ),
    }