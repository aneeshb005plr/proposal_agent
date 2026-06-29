# app/api/chat.py
#
# Thin route — no business logic. Extracts dependencies, calls
# chat_service, returns the response. Uses send_message
# (non-streaming) for now — see chat_service.py / graph structure
# doc Section 7 for why streaming is deferred until the non-streaming
# path is proven working end to end.

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.synchronous.database import Database as SyncDatabase

from app.auth.claims_resolver import UserClaims, get_current_user
from app.checkpointer import get_checkpointer
from app.database import get_database,get_sync_database
from app.repository.session_repository import (
    SessionAccessDeniedError,
    SessionNotFoundError,
)
from app.services import chat_service
from app.services.session_service import get_session_or_raise

logger = logging.getLogger("app.api.chat")

router = APIRouter(prefix="/sessions/{session_id}/chat", tags=["chat"])


class ChatMessageRequest(BaseModel):
    message: str


@router.post("")
async def send_chat_message(
    session_id: str,
    body: ChatMessageRequest,
    db: AsyncDatabase = Depends(get_database),
    sync_db: SyncDatabase = Depends(get_sync_database),
    user: UserClaims = Depends(get_current_user),
):
    # Ownership check BEFORE invoking the graph at all — same
    # pattern used everywhere else in this project.
    try:
        await get_session_or_raise(db, session_id, user.user_id)
    except (SessionNotFoundError, SessionAccessDeniedError):
        raise HTTPException(status_code=404, detail="Session not found")

    checkpointer = get_checkpointer(db.client.get_io_loop if False else None)  # placeholder, see note below

    reply = await chat_service.send_message(
        db=db,
        sync_db=sync_db,
        checkpointer=checkpointer,
        session_id=session_id,
        user_id=user.user_id,
        message_text=body.message,
    )

    return {"response": reply}