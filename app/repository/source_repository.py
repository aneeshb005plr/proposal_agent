# app/repository/source_repository.py
#
# AGENT-SIDE. Previously read-only (get_enabled_source_ids, used
# only when submitting sync jobs). NOW EXTENDED with real write
# methods — this agent's own API can create/update its knowledge
# sources directly, rather than requiring a manual DB insert (the
# gap this extension closes). Encryption of secret fields happens in
# the SERVICE layer (app/services/knowledge_service.py), never here
# — this repository only ever stores/retrieves whatever dict it's
# given, same separation-of-concerns already used everywhere else in
# this codebase.

from typing import Optional

from pymongo.asynchronous.database import AsyncDatabase

SOURCES_COLLECTION = "knowledge_sources"


def _strip_encrypted_fields(config: dict) -> dict:
    """Used only for LIST responses — never returns any *_encrypted
    key, so a summary response can never leak ciphertext either,
    even though it's already encrypted. Same caution level as
    AgentSummaryResponse on the worker side."""
    return {k: v for k, v in config.items() if not k.endswith("_encrypted")}


class SourceRepository:

    def __init__(self, db: AsyncDatabase):
        self._collection = db[SOURCES_COLLECTION]

    async def ensure_indexes(self) -> None:
        await self._collection.create_index(
            [("agent_id", 1), ("source_id", 1)], unique=True
        )

    async def get_enabled_source_ids(self, agent_id: str) -> list[str]:
        cursor = self._collection.find(
            {"agent_id": agent_id, "enabled": True}, {"source_id": 1}
        )
        docs = await cursor.to_list(length=None)
        return [doc["source_id"] for doc in docs]

    async def get_raw(self, agent_id: str, source_id: str) -> Optional[dict]:
        return await self._collection.find_one(
            {"agent_id": agent_id, "source_id": source_id}
        )

    async def list_all(self, agent_id: str) -> list[dict]:
        """Returns raw documents with config's *_encrypted fields
        ALREADY STRIPPED — callers (the service layer building
        SourceSummaryResponse) never handle raw encrypted values."""
        cursor = self._collection.find({"agent_id": agent_id})
        docs = await cursor.to_list(length=None)
        for doc in docs:
            doc["config"] = _strip_encrypted_fields(doc.get("config", {}))
        return docs

    async def create(
        self, agent_id: str, source_id: str, source_type: str,
        config: dict, enabled: bool,
    ) -> None:
        """config must already have secrets ENCRYPTED (with
        *_encrypted keys) by the caller — this method never
        encrypts/decrypts anything itself."""
        await self._collection.insert_one({
            "agent_id": agent_id,
            "source_id": source_id,
            "source_type": source_type,
            "config": config,
            "cursor": None,
            "enabled": enabled,
            "last_synced_at": None,
        })

    async def update(self, agent_id: str, source_id: str, updates: dict) -> bool:
        """Returns True if a matching source existed and was
        updated, False otherwise."""
        result = await self._collection.update_one(
            {"agent_id": agent_id, "source_id": source_id}, {"$set": updates}
        )
        return result.matched_count > 0