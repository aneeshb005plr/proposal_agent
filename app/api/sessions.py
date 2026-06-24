# app/api/sessions.py
#
# Thin HTTP layer. No business logic — extracts dependencies, calls
# the service layer, returns the response.

import logging

from fastapi import APIRouter, Depends, HTTPException
from pymongo.asynchronous.database import AsyncDatabase

from app.database import get_database
from app.services import session_service

logger = logging.getLogger("app.api.sessions")

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("")
async def create_session_route(db: AsyncDatabase = Depends(get_database)):
    """Creates a new session. Returns its session_id."""
    session_id = await session_service.create_session(db)
    return {"session_id": session_id}


@router.get("/{session_id}")
async def get_session_route(
    session_id: str, db: AsyncDatabase = Depends(get_database)
):
    try:
        session = await session_service.get_session_or_raise(db, session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Session not found")

    return {
        "session_id": str(session["_id"]),
        "document_confirmed": session["document_confirmed"],
        "uploaded_file_count": session["uploaded_file_count"],
        "created_at": session["created_at"].isoformat(),
    }