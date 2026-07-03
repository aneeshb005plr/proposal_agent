# app/services/chat_service.py
#
# Coordinates one chat turn: persist the user's message BEFORE
# invoking the graph, invoke the graph, persist the assistant's
# reply AFTER it completes.
#
# CONFIRMED FIX (see rfp_analyzer_graph_structure.md Section 9 for
# the full investigation): overwrite fields (stage, criteria, etc.)
# must NOT be passed on every call — doing so overwrites the
# checkpoint's real stored value, confirmed via isolated empirical
# testing. Default values for these fields are only included when
# graph.aget_state(config) confirms this is a genuinely new thread
# (snapshot.values == {}) — also confirmed via isolated testing,
# both sync and async variants.

import logging

from langchain_core.messages import HumanMessage

from app.agent.context import AgentContext
from app.agent.graph import build_graph
from app.repository.message_repository import MessageRepository
from app.repository.session_repo import SessionRepository
from app.agent.state import DEFAULT_OVERWRITE_FIELDS as _DEFAULT_OVERWRITE_FIELDS


logger = logging.getLogger("app.services.chat_service")




async def send_message(
    db, sync_db, checkpointer, session_id: str, user_id: str, message_text: str
) -> str:
    """Non-streaming — returns the complete reply at once."""
    message_repo = MessageRepository(db)
    await message_repo.add_message(session_id, user_id, "user", message_text)

    # Cheap, idempotent — clears expires_at on the FIRST real message
    # for a session (10.6: abandoned-session TTL). Every subsequent
    # call is a harmless no-op update.
    session_repo = SessionRepository(db)
    await session_repo.mark_session_active(session_id)

    graph = build_graph(checkpointer)
    config = {"configurable": {"thread_id": session_id}}

    # Confirmed via direct testing: snapshot.values == {} (falsy)
    # for a thread that has never been invoked; populated (truthy)
    # after a real invocation. This is the correct, verified way to
    # detect a brand-new thread — NOT inferring it from an unrelated
    # collection's row count (the earlier, rejected approach).
    snapshot = await graph.aget_state(config)
    is_new_thread = not snapshot.values

    base_input = {
        "messages": [HumanMessage(content=message_text)],
        "session_id": session_id,
        "user_id": user_id,
    }
    if is_new_thread:
        base_input.update(_DEFAULT_OVERWRITE_FIELDS)

    result = await graph.ainvoke(
        base_input,
        config=config,
        context=AgentContext(db=db, sync_db=sync_db),
    )

    assistant_text = result.get("response_to_user") or ""
    await message_repo.add_message(session_id, user_id, "assistant", assistant_text)
    return assistant_text


async def stream_message(
    db, sync_db, checkpointer, session_id: str, user_id: str, message_text: str
):
    """
    Streaming — yields token chunks as they arrive. Same
    mark_session_active + is_new_thread logic as send_message above,
    applied consistently.

    KNOWN LIMITATION (confirmed, see graph structure doc Section 7):
    nodes using with_structured_output (request_criteria,
    recap_and_confirm, run_evaluation, classify_mid_flow_intent,
    classify_post_evaluation_intent, handle_criteria_choice) will
    NOT stream token-by-token — the full response arrives at once
    for those turns.
    """
    message_repo = MessageRepository(db)
    await message_repo.add_message(session_id, user_id, "user", message_text)

    session_repo = SessionRepository(db)
    await session_repo.mark_session_active(session_id)

    graph = build_graph(checkpointer)
    config = {"configurable": {"thread_id": session_id}}

    snapshot = await graph.aget_state(config)
    is_new_thread = not snapshot.values

    base_input = {
        "messages": [HumanMessage(content=message_text)],
        "session_id": session_id,
        "user_id": user_id,
    }
    if is_new_thread:
        base_input.update(_DEFAULT_OVERWRITE_FIELDS)

    assembled_reply = ""

    async for chunk in graph.astream(
        base_input,
        config=config,
        context=AgentContext(db=db, sync_db=sync_db),
        stream_mode="messages",
        version="v2",
    ):
        message_chunk, metadata = chunk
        if hasattr(message_chunk, "content") and message_chunk.content:
            assembled_reply += message_chunk.content
            yield message_chunk.content

    await message_repo.add_message(session_id, user_id, "assistant", assembled_reply)