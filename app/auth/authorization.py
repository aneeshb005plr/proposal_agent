# app/repository/job_repository.py
#
# AGENT-SIDE subset of the worker's own job_repository.py — this
# agent only ever CREATES jobs (when a user hits POST /knowledge/sync)
# and READS their status (GET /knowledge/sync/{job_id}). It never
# claims, processes, or completes jobs — that's exclusively the
# worker's job. Deliberately duplicated rather than shared as a
# library (small enough file that duplication is cheaper than
# maintaining a shared package across repos — same reasoning applied
# to the document-parser reuse question earlier in this build).

import logging
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

logger = logging.getLogger("app.repository.job_repository")

JOBS_COLLECTION = "knowledge_sync_jobs"


class JobRepository:

    def __init__(self, db: AsyncDatabase):
        self._collection = db[JOBS_COLLECTION]

    async def ensure_indexes(self) -> None:
        await self._collection.create_index([("status", 1), ("requested_at", 1)])

    async def create(self, agent_id: str, source_id: str, mode: str) -> ObjectId:
        result = await self._collection.insert_one({
            "agent_id": agent_id,
            "source_id": source_id,
            "mode": mode,
            "status": "pending",
            "requested_at": datetime.now(timezone.utc),
            "started_at": None,
            "completed_at": None,
            "heartbeat_at": None,
            "claimed_by": None,
            "result": None,
            "error": None,
        })
        return result.inserted_id

    async def get_by_id(self, job_id: ObjectId) -> Optional[dict]:
        return await self._collection.find_one({"_id": job_id})