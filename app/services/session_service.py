# app/services/session_service.py
#
# Thin coordination layer over session_repository. Routes call this,
# never the repository directly.

import logging

from pymongo.asynchronous.database import AsyncDatabase

from app.repository.session_repository import SessionRepository

logger = logging.getLogger("app.services.session_service")


async def create_session(db: AsyncDatabase) -> str:
    repo = SessionRepository(db)
    return await repo.create_session()


async def get_session_or_raise(db: AsyncDatabase, session_id: str) -> dict:
    repo = SessionRepository(db)
    session = await repo.get_session(session_id)
    if session is None:
        raise ValueError(f"Session {session_id} not found")
    return session