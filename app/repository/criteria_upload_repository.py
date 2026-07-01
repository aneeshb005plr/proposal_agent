# app/repository/criteria_upload_repository.py

import logging
from datetime import datetime, timezone
from typing import Optional

from pymongo.asynchronous.database import AsyncDatabase

logger = logging.getLogger("app.repository.criteria_upload_repository")

CRITERIA_UPLOADS_COLLECTION = "rfp_pending_criteria"

# Buffer is meant to be consumed on the VERY NEXT chat turn after
# upload. 10 minutes is generous slack for a user to upload then
# type their message, while still guaranteeing a buffer from an
# earlier, abandoned test/session never resurfaces on some later,
# unrelated turn (the exact bug that contaminated turn 12's
# extraction with stale "Technical approach" content).
PENDING_CRITERIA_TTL_SECONDS = 600


class CriteriaUploadRepository:

    def __init__(self, db: AsyncDatabase):
        self._collection = db[CRITERIA_UPLOADS_COLLECTION]

    async def ensure_indexes(self) -> None:
        """Call once at startup (main.py lifespan), alongside other
        index setup — creates the TTL index if it doesn't exist yet."""
        await self._collection.create_index(
            "created_at", expireAfterSeconds=PENDING_CRITERIA_TTL_SECONDS
        )

    async def set_pending_text(self, session_id: str, user_id: str, text: str) -> None:
        await self._collection.update_one(
            {"_id": session_id},
            {"$set": {
                "text": text,
                "user_id": user_id,
                "created_at": datetime.now(timezone.utc),
            }},
            upsert=True,
        )

    async def get_pending_text(self, session_id: str) -> Optional[str]:
        doc = await self._collection.find_one({"_id": session_id})
        return doc.get("text") if doc else None

    async def clear_pending_text(self, session_id: str) -> None:
        await self._collection.delete_one({"_id": session_id})