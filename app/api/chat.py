# app/api/chat.py
#
# Thin route — no business logic. Extracts dependencies, calls
# chat_service, returns the response.

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.synchronous.database import Database as SyncDatabase


from app.auth.claims_resolver import UserClaims, get_current_user
from app.checkpointer import get_checkpointer
from app.database import get_database, get_sync_database
from app.repository.message_repository import MessageRepository
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
    request: Request,
    db: AsyncDatabase = Depends(get_database),
    sync_db: SyncDatabase = Depends(get_sync_database),
    user: UserClaims = Depends(get_current_user),
):
    try:
        await get_session_or_raise(db, session_id, user.user_id)
    except (SessionNotFoundError, SessionAccessDeniedError):
        raise HTTPException(status_code=404, detail="Session not found")

    # Uses the same defensive accessor already established in
    # checkpointer.py — raises clearly if startup hasn't run, rather
    # than reconstructing or guessing app.state directly.
    checkpointer = get_checkpointer(request.app)

    reply = await chat_service.send_message(
        db=db, sync_db=sync_db, checkpointer=checkpointer,
        session_id=session_id, user_id=user.user_id,
        message_text=body.message,
    )
    return {"response": reply}


@router.post("/stream")
async def stream_chat_message(
    session_id: str,
    body: ChatMessageRequest,
    request: Request,
    db: AsyncDatabase = Depends(get_database),
    sync_db: SyncDatabase = Depends(get_sync_database),
    user: UserClaims = Depends(get_current_user),
):
    try:
        await get_session_or_raise(db, session_id, user.user_id)
    except (SessionNotFoundError, SessionAccessDeniedError):
        raise HTTPException(status_code=404, detail="Session not found")

    checkpointer = get_checkpointer(request.app)

    async def event_generator():
        async for token in chat_service.stream_message(
            db=db, sync_db=sync_db, checkpointer=checkpointer,
            session_id=session_id, user_id=user.user_id,
            message_text=body.message,
        ):
            yield f"data: {token}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/history")
async def get_chat_history(
    session_id: str,
    db: AsyncDatabase = Depends(get_database),
    user: UserClaims = Depends(get_current_user),
):
    try:
        await get_session_or_raise(db, session_id, user.user_id)
    except (SessionNotFoundError, SessionAccessDeniedError):
        raise HTTPException(status_code=404, detail="Session not found")

    message_repo = MessageRepository(db)
    messages = await message_repo.get_history(session_id)
    return [
        {"role": m["role"], "content": m["content"]}
        for m in messages
    ]