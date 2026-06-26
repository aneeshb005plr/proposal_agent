# app/agent/nodes/route_by_stage.py
#
# Pure routing function — no LLM call, no DB access. Reads
# state["stage"] and returns the name of the next node to run.
# Used as the conditional_edge routing function from
# load_session_state.

from app.agent.state import RFPAnalyzerState

# Maps each stage directly to the node responsible for handling it.
# Centralizing this mapping here means adding a new stage later is
# a one-line change in exactly one place.
_STAGE_TO_NODE = {
    "awaiting_criteria": "request_criteria",
    "awaiting_criteria_confirmation": "recap_and_confirm",
    "awaiting_document": "request_document",
    "ready_to_evaluate": "run_evaluation",
    "evaluated": "handle_post_evaluation_input",
}


def route_by_stage(state: RFPAnalyzerState) -> str:
    stage = state["stage"]
    next_node = _STAGE_TO_NODE.get(stage)

    if next_node is None:
        raise ValueError(f"route_by_stage: unrecognized stage {stage!r}")

    return next_node