# app/agent/nodes/update_criteria_mid_flow.py
#
# Handles a criteria edit arriving while stage == "awaiting_document"
# — a real gap found via testing: previously this text was silently
# dropped by request_document, which only checks file presence.
# Merges the edit into state["criteria"] via the SAME shared
# extract_criteria() helper used everywhere else, WITHOUT resetting
# stage or clearing anything document-related — no evaluation has
# run yet, so there's nothing to invalidate.

import logging
from langgraph.runtime import Runtime
from app.agent.context import AgentContext
from app.agent.criteria_extraction import extract_criteria, weights_to_dict
from app.agent.state import RFPAnalyzerState

logger = logging.getLogger("app.agent.nodes.update_criteria_mid_flow")


async def update_criteria_mid_flow(
    state: RFPAnalyzerState, runtime: Runtime[AgentContext]
) -> dict:
    last_message = state["messages"][-1].content if state["messages"] else ""

    parsed = await extract_criteria(
        chat_text=f"Existing criteria:\n{state['criteria']}\n\nRequested change:\n{last_message}",
        uploaded_file_text=None,
        session_id=state["session_id"],
        user_id=state["user_id"],
        node_name="update_criteria_mid_flow",
        runtime=runtime,
    )

    if not parsed.criteria_found:
        return {
            "response_to_user": (
                "I wasn't able to identify the criteria change — could "
                "you clarify what you'd like to add or adjust? Once "
                "that's settled, please upload the document you'd "
                "like evaluated."
            ),
        }

    logger.info("Criteria edited mid-flow (awaiting_document) for session %s", state["session_id"])

    return {
        "criteria": parsed.extracted_criteria,
        "criteria_weights": weights_to_dict(parsed),
        # stage deliberately unchanged — still awaiting_document
        "response_to_user": (
            f"Got it — updated criteria:\n\n{parsed.extracted_criteria}\n\n"
            f"Please upload the proposal or RFP response document "
            f"you'd like evaluated whenever you're ready."
        ),
    }