# app/repository/session_repository.py
#
# Real session management. Now requires user_id at creation —
# every session belongs to exactly one identified user, the same
# guarantee claims_resolver.py already provides for every
# authenticated request. get_session() additionally verifies
# ownership, so knowing a session_id alone is not enough to read or
# resume another user's session — necessary given this agent
# handles confidential client proposal content.

import logging
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

logger = logging.getLogger("app.repository.session_repository")

SESSIONS_COLLECTION = "sessions"


class SessionNotFoundError(Exception):
    pass


class SessionAccessDeniedError(Exception):
    """Raised when a session exists but does not belong to the
    requesting user — kept distinct from SessionNotFoundError so
    callers can choose how to respond (we recommend treating both
    as 404 at the HTTP layer, to avoid confirming a session_id's
    existence to a user who doesn't own it)."""
    pass


class SessionRepository:

    def __init__(self, db: AsyncDatabase):
        self._collection = db[SESSIONS_COLLECTION]

    async def create_session(self, user_id: str) -> str:
        """Creates a new session owned by user_id. Returns session_id."""
        doc = {
            "_id": ObjectId(),
            "user_id": user_id,
            "created_at": datetime.now(timezone.utc),
            "document_confirmed": False,
            "uploaded_file_count": 0,
        }
        result = await self._collection.insert_one(doc)
        session_id = str(result.inserted_id)
        logger.info("Created session %s for user %s", session_id, user_id)
        return session_id

    async def get_session(self, session_id: str) -> Optional[dict]:
        """
        Returns the raw session record with NO ownership check.
        Internal use only (e.g. the upload-after-confirmation policy
        check, which already has the session_id from a trusted
        internal call). Routes should use get_owned_session instead.
        """
        return await self._collection.find_one({"_id": ObjectId(session_id)})

    async def get_owned_session(self, session_id: str, user_id: str) -> dict:
        """
        Returns the session record ONLY if it belongs to user_id.
        Raises SessionNotFoundError if no session with this ID
        exists at all, or SessionAccessDeniedError if it exists but
        belongs to a different user. Use this for anything reachable
        from an HTTP route.
        """
        session = await self._collection.find_one({"_id": ObjectId(session_id)})
        if session is None:
            raise SessionNotFoundError(f"Session {session_id} not found")
        if session["user_id"] != user_id:
            raise SessionAccessDeniedError(
                f"Session {session_id} does not belong to user {user_id}"
            )
        return session

    async def increment_file_count(self, session_id: str) -> int:
        result = await self._collection.find_one_and_update(
            {"_id": ObjectId(session_id)},
            {"$inc": {"uploaded_file_count": 1}},
            return_document=True,
        )
        return result["uploaded_file_count"]

    async def decrement_file_count(self, session_id: str) -> int:
        result = await self._collection.find_one_and_update(
            {"_id": ObjectId(session_id)},
            {"$inc": {"uploaded_file_count": -1}},
            return_document=True,
        )
        return result["uploaded_file_count"]

    async def mark_document_confirmed(self, session_id: str) -> None:
        await self._collection.update_one(
            {"_id": ObjectId(session_id)},
            {"$set": {"document_confirmed": True}},
        )

    async def reset_confirmation(self, session_id: str) -> None:
        await self._collection.update_one(
            {"_id": ObjectId(session_id)},
            {"$set": {"document_confirmed": False}},
        )
        logger.info(
            "Session %s confirmation reset (policy=invalidate)", session_id
        )