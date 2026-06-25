# app/services/session_service.py

import logging

from pymongo.asynchronous.database import AsyncDatabase

from app.repository.session_repository import SessionRepository

logger = logging.getLogger("app.services.session_service")


async def create_session(db: AsyncDatabase, user_id: str) -> str:
    repo = SessionRepository(db)
    return await repo.create_session(user_id)


async def get_session_or_raise(
    db: AsyncDatabase, session_id: str, user_id: str
) -> dict:
    """
    Renamed conceptually from before — now ALWAYS checks ownership.
    Raises SessionNotFoundError or SessionAccessDeniedError (from
    the repository) — the route layer maps both to 404, deliberately
    not distinguishing them in the HTTP response (see route comment).
    """
    repo = SessionRepository(db)
    return await repo.get_owned_session(session_id, user_id)