# app/repository/teams_conversation_repository.py
#
# Maps a Teams conversation to whichever RFP-Analyzer session_id is
# currently "active" for it. Teams has no sidebar/session-switcher
# UI (see rfp_analyzer_teams_integration.md) — this is the one small
# piece of new state needed to bridge that gap, NOT a parallel
# session system. Once resolved to a real session_id, everything
# downstream (the graph, chat_service, submission_service) is
# completely unchanged and unaware Teams is even involved.

import logging
from datetime import datetime, timezone
from typing import Optional

from pymongo.asynchronous.database import AsyncDatabase

logger = logging.getLogger("app.repository.teams_conversation_repository")

TEAMS_CONVERSATIONS_COLLECTION = "teams_conversations"


class TeamsConversationRepository:

    def __init__(self, db: AsyncDatabase):
        self._collection = db[TEAMS_CONVERSATIONS_COLLECTION]

    async def ensure_indexes(self) -> None:
        """Call once at startup, alongside other repositories' index
        setup. _id is already the natural unique key (conversation_id),
        so no additional index is strictly required — kept as an
        explicit method anyway for consistency with every other
        repository in this project, and as a natural place to add a
        real index later if query patterns beyond get-by-id emerge."""
        pass

    async def get_active_session(self, conversation_id: str) -> Optional[str]:
        doc = await self._collection.find_one({"_id": conversation_id})
        return doc.get("active_session_id") if doc else None

    async def get_conversation_state(self, conversation_id: str) -> Optional[dict]:
        """
        Returns the FULL mapping doc (active_session_id + updated_at),
        not just the session_id — needed so callers can check
        staleness against TEAMS_SESSION_STALE_DAYS, not just whether
        a mapping exists at all. See teams_service.py's
        resolve_session_for_conversation for how this is used.
        """
        return await self._collection.find_one({"_id": conversation_id})

    async def set_active_session(
        self, conversation_id: str, session_id: str, user_id: str
    ) -> None:
        """
        Upsert — called both when a conversation is seen for the
        FIRST time ever (creates the mapping) and when the user
        explicitly starts a new evaluation (repoints an existing
        mapping to a freshly created session_id).
        """
        await self._collection.update_one(
            {"_id": conversation_id},
            {
                "$set": {
                    "active_session_id": session_id,
                    "user_id": user_id,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )
        logger.info(
            "Teams conversation %s now points at session %s",
            conversation_id, session_id,
        )