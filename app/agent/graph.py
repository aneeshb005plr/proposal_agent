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
from app.agent.nodes.request_document import request_document
from app.agent.nodes.run_evaluation import run_evaluation
from app.agent.nodes.generate_summary import generate_summary
from app.agent.nodes.validate_output import validate_output
from app.agent.nodes.render_output import render_output

# New imports
from app.agent.nodes.classify_post_evaluation_intent import classify_post_evaluation_intent
from app.agent.nodes.generate_additional_output import generate_additional_output
from app.agent.nodes.reset_for_criteria_change import reset_for_criteria_change
from app.agent.nodes.reset_for_new_document import reset_for_new_document
from app.agent.nodes.ask_for_clarification import ask_for_clarification
from app.agent.nodes.handle_criteria_choice import handle_criteria_choice







def route_after_document_check(state: RFPAnalyzerState) -> str:
    if state["stage"] == "ready_to_evaluate":
        return "run_evaluation"
    return "wait"  # placeholder name for the END branch



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
        "awaiting_document": "request_document",  
        "awaiting_new_document_criteria_choice": "handle_criteria_choice",
        "evaluated": "classify_post_evaluation_intent",
    }
    next_node = stage_map.get(state["stage"])
    if next_node is None:
        raise NotImplementedError(
            f"No node wired yet for stage={state['stage']!r}. "
            f"See rfp_analyzer_graph_structure.md for build status."
        )
    return next_node

def route_after_post_eval_classification(state: RFPAnalyzerState) -> str:
    category_map = {
        "additional_output": "generate_additional_output",
        "criteria_change": "reset_for_criteria_change",
        "new_document": "reset_for_new_document",
        "unclear": "ask_for_clarification",
    }
    return category_map.get(state["post_eval_category"], "ask_for_clarification")



def build_graph(checkpointer):
    builder = StateGraph(state_schema=RFPAnalyzerState, context_schema=AgentContext)

    builder.add_node("load_session_state", load_session_state)
    builder.add_node("classify_intent", classify_intent)
    builder.add_node("handle_social", handle_social)
    builder.add_node("handle_off_topic", handle_off_topic)
    builder.add_node("request_criteria", request_criteria)
    builder.add_node("recap_and_confirm", recap_and_confirm)
    builder.add_node("request_document", request_document)
    builder.add_node("run_evaluation", run_evaluation)
    builder.add_node("generate_summary", generate_summary)
    builder.add_node("validate_output", validate_output)
    builder.add_node("render_output", render_output)

    builder.add_node("classify_post_evaluation_intent", classify_post_evaluation_intent)
    builder.add_node("generate_additional_output", generate_additional_output)
    builder.add_node("reset_for_criteria_change", reset_for_criteria_change)
    builder.add_node("reset_for_new_document", reset_for_new_document)
    builder.add_node("ask_for_clarification", ask_for_clarification)
    builder.add_node("handle_criteria_choice", handle_criteria_choice)







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
            "request_document": "request_document",
            "handle_criteria_choice": "handle_criteria_choice",
            "classify_post_evaluation_intent": "classify_post_evaluation_intent",
        },
    )

    builder.add_conditional_edges(
        "request_document",
        route_after_document_check,
        {
            "run_evaluation": "run_evaluation",                                                   
            "wait": END,
        },
    )



    
    builder.add_edge("run_evaluation", "generate_summary")
    builder.add_edge("generate_summary", "validate_output")
    builder.add_edge("validate_output", "render_output")


    builder.add_conditional_edges(
        "classify_post_evaluation_intent",
        route_after_post_eval_classification,
        {
            "generate_additional_output": "generate_additional_output",
            "reset_for_criteria_change": "reset_for_criteria_change",
            "reset_for_new_document": "reset_for_new_document",
            "ask_for_clarification": "ask_for_clarification",
        },
    )



    builder.add_edge("handle_social", END)
    builder.add_edge("handle_off_topic", END)
    builder.add_edge("request_criteria", END)
    builder.add_edge("recap_and_confirm", END)
    builder.add_edge("render_output", END)
    builder.add_edge("generate_additional_output", END)
    builder.add_edge("reset_for_criteria_change", END)
    builder.add_edge("reset_for_new_document", END)
    builder.add_edge("ask_for_clarification", END)
    builder.add_edge("handle_criteria_choice", END)





    return builder.compile(checkpointer=checkpointer)