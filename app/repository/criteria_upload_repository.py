# app/repository/criteria_upload_repository.py
#
# RFP-ANALYZER-SPECIFIC — deliberately NOT part of the shared
# infrastructure layer (session_repository.py, message_repository.py,
# etc. remain generic and reusable across any agent). "Criteria" is
# a concept specific to this agent's domain; baking it into the
# generic SessionRepository would leak agent-specific meaning into
# code meant to be copy-paste-reusable by future agents that may
# have no concept of "criteria" at all.
#
# Holds a temporary buffer: text extracted from an uploaded criteria
# document, written via a REST route (outside the graph), read and
# cleared by extract_criteria() on the next chat turn.

import logging
from typing import Optional

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

logger = logging.getLogger("app.repository.criteria_upload_repository")

CRITERIA_UPLOADS_COLLECTION = "rfp_pending_criteria"


class CriteriaUploadRepository:

    def __init__(self, db: AsyncDatabase):
        self._collection = db[CRITERIA_UPLOADS_COLLECTION]

    async def set_pending_text(self, session_id: str, user_id: str, text: str) -> None:
        """
        Stores uploaded criteria-document text. user_id is included for
        audit/consistency with every other piece of agent activity in
        this build (messages, token usage, document uploads all carry
        user_id) — NOT for access control, since ownership is already
        verified at the route layer (get_owned_session) before this is
        ever called.
        """
        await self._collection.update_one(
            {"_id": session_id},
            {"$set": {"text": text, "user_id": user_id}},
            upsert=True,
        )

    async def get_pending_text(self, session_id: str) -> Optional[str]:
        doc = await self._collection.find_one({"_id": session_id})
        return doc.get("text") if doc else None

    async def clear_pending_text(self, session_id: str) -> None:
        """
        Called immediately after extract_criteria() has consumed the
        pending text — regardless of whether criteria were
        successfully found in it. Either way, this upload's content
        has been considered and must not be silently reconsidered
        on a future, unrelated turn.
        """
        await self._collection.delete_one({"_id": session_id})