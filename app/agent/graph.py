# app/agent/graph.py
#
# Assembles the StateGraph. Only wires in nodes that actually exist
# yet — see rfp_analyzer_graph_structure.md for current build status.
# Extended as each remaining node gets written.

from langgraph.graph import StateGraph, START, END

from app.agent.context import AgentContext
from app.agent.state import RFPAnalyzerState
from app.agent.nodes.load_session_state import load_session_state
from app.agent.nodes.classify_intent import classify_intent
from app.agent.nodes.handle_social import handle_social
from app.agent.nodes.handle_off_topic import handle_off_topic
from app.agent.nodes.request_criteria import request_criteria
from app.agent.nodes.recap_and_confirm import recap_and_confirm



def route_after_classification(state: RFPAnalyzerState) -> str:
    if state["intent"] == "social":
        return "handle_social"
    if state["intent"] == "off_topic":
        return "handle_off_topic"

    # task_relevant — defer to stage. Only "awaiting_criteria" has a
    # real node right now; anything else raises clearly rather than
    # silently misrouting, until those nodes are written.
    stage_map = {
        "awaiting_criteria": "request_criteria",
        "awaiting_criteria_confirmation": "recap_and_confirm",
    }
    next_node = stage_map.get(state["stage"])
    if next_node is None:
        raise NotImplementedError(
            f"No node wired yet for stage={state['stage']!r}. "
            f"See rfp_analyzer_graph_structure.md for build status."
        )
    return next_node


def build_graph(checkpointer):
    builder = StateGraph(state_schema=RFPAnalyzerState, context_schema=AgentContext)

    builder.add_node("load_session_state", load_session_state)
    builder.add_node("classify_intent", classify_intent)
    builder.add_node("handle_social", handle_social)
    builder.add_node("handle_off_topic", handle_off_topic)
    builder.add_node("request_criteria", request_criteria)
    builder.add_node("recap_and_confirm", recap_and_confirm)


    builder.add_edge(START, "load_session_state")
    builder.add_edge("load_session_state", "classify_intent")

    builder.add_conditional_edges(
        "classify_intent",
        route_after_classification,
        {
            "handle_social": "handle_social",
            "handle_off_topic": "handle_off_topic",
            "request_criteria": "request_criteria",
            "recap_and_confirm": "recap_and_confirm", 
        },
    )

    builder.add_edge("handle_social", END)
    builder.add_edge("handle_off_topic", END)
    builder.add_edge("request_criteria", END)
    builder.add_edge("recap_and_confirm", END)


    return builder.compile(checkpointer=checkpointer)