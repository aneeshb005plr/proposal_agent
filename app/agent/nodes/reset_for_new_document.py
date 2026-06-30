# app/agent/nodes/reset_for_new_document.py
#
# Handles Category C: user wants to evaluate a DIFFERENT document.
# Always clears the OLD document's submission_chunks and prior
# results. Whether criteria are also reset depends on whether the
# user already specified an answer (post_eval_keep_criteria_specified)
# — if they proactively said "use my old criteria" or similar, we
# honor that without re-asking (per design discussion: don't force
# a question the user already answered). If unspecified, we ask
# explicitly rather than guess either direction.

import logging

from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.state import RFPAnalyzerState
from app.repository.submission_repository import SubmissionRepository

logger = logging.getLogger("app.agent.nodes.reset_for_new_document")


async def reset_for_new_document(
    state: RFPAnalyzerState, runtime: Runtime[AgentContext]
) -> dict:
    submission_repo = SubmissionRepository(runtime.context.db)
    deleted_count = await submission_repo.delete_session_chunks(state["session_id"])

    logger.info(
        "New document requested for session %s — cleared %d old "
        "chunk(s)", state["session_id"], deleted_count,
    )

    base_update = {
        "uploaded_filenames": [],
        "scoring_results": None,
        "executive_summary": None,
        "document_confirmed": False,
    }

    keep_specified = state.get("post_eval_keep_criteria_specified", False)
    keep_criteria = state.get("post_eval_keep_criteria", False)

    if keep_specified and keep_criteria:
        # User already said to reuse the same criteria — honor it,
        # skip straight to waiting for the new document, criteria
        # untouched.
        logger.info(
            "Session %s: reusing existing criteria for new document",
            state["session_id"],
        )
        return {
            **base_update,
            "stage": "awaiting_document",
            "response_to_user": (
                "Got it — I'll keep the same evaluation criteria and "
                "use them for the new document. Please upload it when "
                "you're ready."
            ),
        }

    if keep_specified and not keep_criteria:
        # User explicitly said they want different criteria too.
        return {
            **base_update,
            "criteria": None,
            "criteria_confirmed": False,
            "criteria_weights": {},
            "stage": "awaiting_criteria",
            "response_to_user": (
                "Got it — let's start fresh. Please share the evaluation "
                "criteria for the new document."
            ),
        }

    # Not specified either way — ask explicitly, don't guess.
    return {
        **base_update,
        "stage": "awaiting_new_document_criteria_choice",
        "response_to_user": (
            "Sure — before you upload the new document, would you like "
            "to use the same evaluation criteria as before, or provide "
            "new ones?"
        ),
    }