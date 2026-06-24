# app/repository/session_repository.py
#
# Minimal, genuinely real session management — not a throwaway
# stub. Deliberately scoped narrowly for now: create a session,
# fetch it, mark its submission document as confirmed. Does NOT yet
# include RFP Analyzer's full criteria/confirmation state machine
# (that's the LangGraph workflow's concern, layered on top of this
# later) — but the shape here is real and won't need reworking when
# that layer is added, the same way claims_resolver.py's dummy-header
# path produced the same UserClaims shape real auth eventually does.
#
# Generic across any agent — no agent-specific logic here. Behavior
# differences (e.g. what happens on upload-after-confirmation) live
# in config (UPLOAD_AFTER_CONFIRMATION_POLICY), read by the service
# layer, not here.

import logging
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

logger = logging.getLogger("app.repository.session_repository")

SESSIONS_COLLECTION = "sessions"


class SessionRepository:

    def __init__(self, db: AsyncDatabase):
        self._collection = db[SESSIONS_COLLECTION]

    async def create_session(self) -> str:
        """
        Creates a new session record. Returns the session_id as a
        string. document_confirmed starts False — set True once the
        upload service confirms a submission document, used by the
        upload-after-confirmation policy check.
        """
        doc = {
            "_id": ObjectId(),
            "created_at": datetime.now(timezone.utc),
            "document_confirmed": False,
            "uploaded_file_count": 0,
        }
        result = await self._collection.insert_one(doc)
        session_id = str(result.inserted_id)
        logger.info("Created session %s", session_id)
        return session_id

    async def get_session(self, session_id: str) -> Optional[dict]:
        """Returns the session record, or None if it doesn't exist."""
        return await self._collection.find_one({"_id": ObjectId(session_id)})

    async def increment_file_count(self, session_id: str) -> int:
        """
        Atomically increments uploaded_file_count and returns the new
        value. Used by the upload service to enforce
        MAX_UPLOADED_FILES_PER_SESSION before this call — the
        increment itself happens only after that check passes.
        """
        result = await self._collection.find_one_and_update(
            {"_id": ObjectId(session_id)},
            {"$inc": {"uploaded_file_count": 1}},
            return_document=True,
        )
        return result["uploaded_file_count"]

    async def mark_document_confirmed(self, session_id: str) -> None:
        await self._collection.update_one(
            {"_id": ObjectId(session_id)},
            {"$set": {"document_confirmed": True}},
        )

    async def reset_confirmation(self, session_id: str) -> None:
        """
        Called when the upload-after-confirmation policy is
        "invalidate" and a new file arrives post-confirmation —
        flips document_confirmed back to False, requiring
        re-confirmation, per RFP Analyzer's own stated rule.
        """
        await self._collection.update_one(
            {"_id": ObjectId(session_id)},
            {"$set": {"document_confirmed": False}},
        )
        logger.info(
            "Session %s confirmation reset due to post-confirmation "
            "upload (policy=invalidate)", session_id,
        )