# app/services/chat_service.py
#
# Two ways to invoke the graph for one chat turn:
#   send_message       — non-streaming, returns the complete reply
#   stream_message      — streaming, yields token chunks as they
#                         arrive (for Streamlit/any real-time UI)
#
# Both persist chat history the same way: user message BEFORE
# invoking the graph, assistant reply AFTER it completes — kept
# consistent between the two paths so history is correct regardless
# of which one a caller uses.
#
# Streaming uses stream_mode="messages" — confirmed current pattern
# (version="v2", the proven, still-current default as of multiple
# March/April 2026 sources; v3 exists but adds typed projections we
# have no current need for — adopting it now would be complexity
# ahead of actual need).

import logging
from typing import AsyncIterator

from langchain_core.messages import HumanMessage

from app.agent.context import AgentContext
from app.agent.graph import build_graph
from app.repository.message_repository import MessageRepository

logger = logging.getLogger("app.services.chat_service")


def _initial_state(session_id: str, user_id: str, message_text: str) -> dict:
    """Shared initial state builder — used by both send_message and
    stream_message so they stay consistent with each other."""
    return {
        "messages": [HumanMessage(content=message_text)],
        "session_id": session_id,
        "user_id": user_id,
        "stage": "awaiting_criteria",
        "criteria": None,
        "criteria_confirmed": False,
        "document_confirmed": False,
        "uploaded_filenames": [],
        "scoring_results": None,
        "executive_summary": None,
        "intent": None,
        "response_to_user": None,
    }


async def send_message(
    db, checkpointer, session_id: str, user_id: str, message_text: str
) -> str:
    """Non-streaming — returns the complete reply at once."""
    message_repo = MessageRepository(db)
    await message_repo.add_message(session_id, user_id, "user", message_text)

    graph = build_graph(checkpointer)
    result = await graph.ainvoke(
        _initial_state(session_id, user_id, message_text),
        config={"configurable": {"thread_id": session_id}},
        context=AgentContext(db=db),
    )

    assistant_text = result.get("response_to_user") or ""
    await message_repo.add_message(session_id, user_id, "assistant", assistant_text)
    return assistant_text


async def stream_message(
    db, checkpointer, session_id: str, user_id: str, message_text: str
) -> AsyncIterator[str]:
    """
    Streaming — yields token chunks as they arrive. Caller (the
    route) is responsible for wrapping these into whatever wire
    format it needs (e.g. SSE "data: ..." framing).

    Persists the user's message BEFORE streaming starts, and the
    full assembled assistant reply AFTER the stream completes —
    same persistence guarantee as send_message.
    """
    message_repo = MessageRepository(db)
    await message_repo.add_message(session_id, user_id, "user", message_text)

    graph = build_graph(checkpointer)
    assembled_reply = ""

    async for chunk in graph.astream(
        _initial_state(session_id, user_id, message_text),
        config={"configurable": {"thread_id": session_id}},
        context=AgentContext(db=db),
        stream_mode="messages",
        version="v2",
    ):
        message_chunk, metadata = chunk
        if hasattr(message_chunk, "content") and message_chunk.content:
            assembled_reply += message_chunk.content
            yield message_chunk.content

    await message_repo.add_message(session_id, user_id, "assistant", assembled_reply)