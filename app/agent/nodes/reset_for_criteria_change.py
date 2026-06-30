# app/agent/nodes/reset_for_criteria_change.py
#
# Handles Category B: user wants to adjust criteria and RE-EVALUATE
# THE SAME document already uploaded — not a new document.
#
# Reuses the SAME submission_chunks already in submission_repository
# (no need to re-upload or re-parse) — only criteria and the prior
# evaluation's results are reset. Reuses extract_criteria() (shared
# with request_criteria and recap_and_confirm), since this is the
# same underlying capability: combine chat text and/or any newly
# uploaded criteria file into an extraction call.
#
# Per ADR established earlier in this build: changing criteria after
# a completed evaluation invalidates prior results — scoring_results
# and executive_summary are cleared, stage routes back through
# confirmation before evaluation can run again.

import logging

from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.criteria_extraction import extract_criteria, weights_to_dict
from app.agent.state import RFPAnalyzerState
from app.repository.criteria_upload_repository import CriteriaUploadRepository

logger = logging.getLogger("app.agent.nodes.reset_for_criteria_change")


async def reset_for_criteria_change(
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
        node_name="reset_for_criteria_change",
        runtime=runtime,
    )

    if pending_file_text:
        await criteria_upload_repo.clear_pending_text(state["session_id"])

    if not parsed.criteria_found:
        # Classification already determined this was a criteria-
        # change request, but extraction couldn't find usable
        # criteria in the actual message/file — ask plainly rather
        # than silently failing or guessing.
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