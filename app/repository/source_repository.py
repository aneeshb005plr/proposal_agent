# app/repository/source_repository.py
#
# AGENT-SIDE subset — this agent only ever needs to LIST its own
# enabled sources (to create one job per source when
# POST /knowledge/sync omits source_id). It never reads/writes
# cursors or per-source credentials — that's the worker's exclusive
# concern. Deliberately does NOT decrypt anything here (unlike the
# worker's version) — this agent has no need to ever see decrypted
# source credentials, only source_id values, which is a real,
# meaningful security boundary, not just less code.

from pymongo.asynchronous.database import AsyncDatabase

SOURCES_COLLECTION = "knowledge_sources"


class SourceRepository:

    def __init__(self, db: AsyncDatabase):
        self._collection = db[SOURCES_COLLECTION]

    async def get_enabled_source_ids(self, agent_id: str) -> list[str]:
        cursor = self._collection.find(
            {"agent_id": agent_id, "enabled": True}, {"source_id": 1}
        )
        docs = await cursor.to_list(length=None)
        return [doc["source_id"] for doc in docs]