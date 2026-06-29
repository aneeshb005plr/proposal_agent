# app/agent/nodes/request_document.py
#
# Runs when stage == "awaiting_document". File upload happens via a
# SEPARATE route (/sessions/{id}/documents), not as chat text — but
# the user's chat message at this stage may still carry real content
# (e.g. "here's the proposal, go ahead") sent alongside or near the
# upload. This node does not extract anything from that text (no
# LLM call — the decision is driven entirely by whether a file has
# actually landed in submission_chunks, synced into
# state["uploaded_filenames"] by load_session_state) but
# acknowledges it rather than ignoring it.
#
# If a file IS found, this node does NOT end the turn — it hands off
# directly to run_evaluation in the SAME turn, since the response
# implies immediate action and should reflect the real outcome, not
# a promise about something happening later (see graph.py's
# route_after_document_check for the conditional edge that makes
# this possible).

import logging

from app.agent.state import RFPAnalyzerState

logger = logging.getLogger("app.agent.nodes.request_document")


async def request_document(state: RFPAnalyzerState) -> dict:
    filenames = state.get("uploaded_filenames") or []

    if filenames:
        logger.info(
            "Document(s) found for session %s: %s — proceeding to "
            "evaluation in this turn", state["session_id"], filenames,
        )
        # Stage advances here; response_to_user is intentionally
        # left for render_output to set at the end of the evaluation
        # chain — this node's own message would otherwise be
        # immediately overwritten anyway, since the turn continues.
        return {"stage": "ready_to_evaluate"}

    return {
        "response_to_user": (
            "Please upload the proposal or RFP response document "
            "you'd like evaluated — once it's uploaded, let me know "
            "and I'll begin the evaluation."
        ),
    }