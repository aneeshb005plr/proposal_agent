# app/repository/message_repository.py
#
# Plain conversation history — separate from LangGraph's checkpoint.
# The checkpoint is LangGraph's internal resume mechanism; this
# collection is ours, for anything that needs to read "what was
# said" in a simple, stable way (Streamlit UI now; Teams or any
# future UI later; audit).

import logging
from datetime import datetime, timezone

from bson import ObjectId
from pymongo import ASCENDING
from pymongo.asynchronous.database import AsyncDatabase

logger = logging.getLogger("app.repository.message_repository")

MESSAGES_COLLECTION = "messages"


class MessageRepository:

    def __init__(self, db: AsyncDatabase):
        self._collection = db[MESSAGES_COLLECTION]

    async def setup_indexes(self) -> None:
        await self._collection.create_index(
            [("session_id", ASCENDING), ("timestamp", ASCENDING)],
            name="idx_session_messages",
        )

    async def add_message(
        self, session_id: str, user_id: str, role: str, content: str
    ) -> None:
        """role is 'user' or 'assistant'."""
        await self._collection.insert_one({
            "_id": ObjectId(),
            "session_id": session_id,
            "user_id": user_id,
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc),
        })

    async def get_history(self, session_id: str) -> list[dict]:
        """Returns all messages for a session, oldest first."""
        cursor = self._collection.find(
            {"session_id": session_id}
        ).sort("timestamp", 1)
        return await cursor.to_list(length=None)