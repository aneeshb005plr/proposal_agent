# app/api/sessions.py

import logging

from fastapi import APIRouter, Depends, HTTPException
from pymongo.asynchronous.database import AsyncDatabase

from app.auth.claims_resolver import UserClaims, get_current_user
from app.database import get_database
from app.repository.session_repository import (
    SessionAccessDeniedError,
    SessionNotFoundError,
)
from app.services import session_service

logger = logging.getLogger("app.api.sessions")

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("")
async def create_session_route(
    db: AsyncDatabase = Depends(get_database),
    user: UserClaims = Depends(get_current_user),
):
    session_id = await session_service.create_session(db, user.user_id)
    return {"session_id": session_id}


@router.get("/{session_id}")
async def get_session_route(
    session_id: str,
    db: AsyncDatabase = Depends(get_database),
    user: UserClaims = Depends(get_current_user),
):
    try:
        session = await session_service.get_session_or_raise(
            db, session_id, user.user_id
        )
    except (SessionNotFoundError, SessionAccessDeniedError):
        # Deliberately the SAME error for both cases — returning a
        # different message for "doesn't exist" vs "exists but isn't
        # yours" would let a user probe for valid session_ids
        # belonging to other people. Both map to a plain 404.
        raise HTTPException(status_code=404, detail="Session not found")

    return {
        "session_id": str(session["_id"]),
        "document_confirmed": session["document_confirmed"],
        "uploaded_file_count": session["uploaded_file_count"],
        "created_at": session["created_at"].isoformat(),
    }

@router.get("")
async def list_sessions_route(
    db: AsyncDatabase = Depends(get_database),
    user: UserClaims = Depends(get_current_user),
):
    sessions = await session_service.list_sessions(db, user.user_id)
    return [
        {
            "session_id": str(s["_id"]),
            "created_at": s["created_at"].isoformat(),
            "document_confirmed": s.get("document_confirmed", False),
            "uploaded_file_count": s.get("uploaded_file_count", 0),
        }
        for s in sessions
    ]