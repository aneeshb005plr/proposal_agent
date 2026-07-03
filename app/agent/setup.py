# app/agent/setup.py
#
# RFP-Analyzer-specific wiring into shared/generic infra modules.
# Lives here, next to graph.py/state.py, deliberately NOT in
# main.py.

from langchain_core.messages import AIMessage

from app.agent.graph import build_graph
from app.agent.state import DEFAULT_OVERWRITE_FIELDS
from app.api import documents as documents_api
from app.repository.message_repository import MessageRepository


async def _rfp_analyzer_post_upload_hook(db, checkpointer, session_id, user_id) -> str:
    graph = build_graph(checkpointer)
    config = {"configurable": {"thread_id": session_id}}
    snapshot = await graph.aget_state(config)

    # FIXED: previously a single hardcoded message regardless of
    # whether criteria existed yet — misleading when a document is
    # uploaded BEFORE criteria are given (a real, common flow: user
    # uploads first, then types criteria). Check current state
    # before choosing what to say.
    criteria_confirmed = (
        snapshot.values.get("criteria_confirmed", False) if snapshot.values else False
    )

    if criteria_confirmed:
        text = (
            "Received your document — let me know when you'd like "
            "me to begin the evaluation."
        )
    else:
        text = (
            "Received your document. Once you share the evaluation "
            "criteria you'd like it scored against, I'll begin the "
            "evaluation."
        )

    message_repo = MessageRepository(db)
    await message_repo.add_message(session_id, user_id, "assistant", text)

    # Same is_new_thread guard as chat_service — critical if this
    # upload is the FIRST thing to ever touch this session's
    # checkpoint (i.e. document uploaded before any chat message).
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
    """Called once from main.py's lifespan."""
    documents_api.post_upload_hook = _rfp_analyzer_post_upload_hook