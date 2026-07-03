# app/agent/setup.py

from langchain_core.messages import AIMessage

from app.agent.graph import build_graph
from app.agent.state import DEFAULT_OVERWRITE_FIELDS
from app.api import documents as documents_api
from app.repository.message_repository import MessageRepository


async def _rfp_analyzer_post_upload_hook(db, checkpointer, session_id, user_id) -> str:
    text = "Received your document — let me know when you'd like me to begin the evaluation."

    # UI-facing history — unchanged from before.
    message_repo = MessageRepository(db)
    await message_repo.add_message(session_id, user_id, "assistant", text)

    # ALSO inject into the LangGraph checkpoint itself, so any node
    # reading state["messages"] for context (classify_intent,
    # classify_mid_flow_intent, recap_and_confirm, etc.) sees this
    # exchange too — not just what MessageRepository shows the UI.
    graph = build_graph(checkpointer)
    config = {"configurable": {"thread_id": session_id}}

    # SAME is_new_thread check used in chat_service — critical to
    # repeat here. If a document is uploaded before any chat message
    # ever happened, this call would be the FIRST thing to touch
    # this thread's checkpoint. Without applying the default
    # overwrite fields here too, the checkpoint would end up with a
    # "messages" entry but no "stage"/"criteria"/etc. — the exact
    # missing-defaults failure mode this build already root-caused
    # and fixed once before, now reachable through a second entry
    # point if not guarded here as well.
    snapshot = await graph.aget_state(config)
    is_new_thread = not snapshot.values

    update = {
        "messages": [AIMessage(content=text)],
        "session_id": session_id,
        "user_id": user_id,
    }
    if is_new_thread:
        update.update(DEFAULT_OVERWRITE_FIELDS)

    await graph.aupdate_state(config, update)

    return text


def register_agent_hooks() -> None:
    documents_api.post_upload_hook = _rfp_analyzer_post_upload_hook