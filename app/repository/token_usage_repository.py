# app/repository/token_usage_repository.py
#
# One document per LLM call — granular token audit trail. Extraction
# confirmed via response.usage_metadata (input_tokens/output_tokens/
# total_tokens) — real, documented field, confirmed consistent across
# current langchain-openai versions.
#
# KNOWN RISK, not yet resolved: usage_metadata may come back empty
# for STREAMED calls against our custom base_url specifically —
# LangChain defaults stream_usage=False when a non-default base_url
# is set, since many non-OpenAI-hosted endpoints don't support
# streaming token usage. Must be verified once streaming is actually
# implemented (Step 5+) — do not assume this works identically for
# streamed vs non-streamed calls.

import logging
from datetime import datetime, timezone

from bson import ObjectId
from langchain_core.messages import AIMessage
from pymongo import ASCENDING, DESCENDING
from pymongo.asynchronous.database import AsyncDatabase

from app.config import settings

logger = logging.getLogger("app.repository.token_usage_repository")

TOKEN_USAGE_COLLECTION = "token_usage"


class TokenUsageRepository:

    def __init__(self, db: AsyncDatabase):
        self._collection = db[TOKEN_USAGE_COLLECTION]

    async def setup_indexes(self) -> None:
        await self._collection.create_index(
            [("session_id", ASCENDING), ("timestamp", DESCENDING)],
            name="idx_usage_session",
        )
        await self._collection.create_index(
            [("user_id", ASCENDING), ("timestamp", DESCENDING)],
            name="idx_usage_user",
        )
        await self._collection.create_index(
            [("node", ASCENDING), ("timestamp", DESCENDING)],
            name="idx_usage_node",
        )

    async def record_llm_call(
        self, session_id: str, user_id: str, node: str, response: AIMessage
    ) -> None:
        """
        Logs a warning (does not raise) if usage_metadata is
        missing — must never crash the agent's actual response to
        the user just because token accounting failed.
        """
        usage = getattr(response, "usage_metadata", None)

        if usage is None:
            logger.warning(
                "No usage_metadata on response from node '%s' "
                "(session=%s) — token usage not recorded.",
                node, session_id,
            )
            return

        await self._collection.insert_one({
            "_id": ObjectId(),
            "session_id": session_id,
            "user_id": user_id,
            "agent_id": settings.AGENT_ID,
            "node": node,
            "model": settings.GENAI_LLM_MODEL,
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "timestamp": datetime.now(timezone.utc),
        })

    async def get_session_totals(self, session_id: str) -> dict:
        """
        Computed on read via aggregation — deliberately NOT a
        maintained running-total rollup. We have no evidence yet
        that aggregate-on-read is too slow at our actual scale; if
        that changes, that's the trigger to add a rollup, not before.
        """
        pipeline = [
            {"$match": {"session_id": session_id}},
            {"$group": {
                "_id": None,
                "total_input_tokens": {"$sum": "$input_tokens"},
                "total_output_tokens": {"$sum": "$output_tokens"},
                "total_tokens": {"$sum": "$total_tokens"},
                "call_count": {"$sum": 1},
            }},
        ]
        result = await self._collection.aggregate(pipeline).to_list(length=1)
        if not result:
            return {
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_tokens": 0,
                "call_count": 0,
            }
        result[0].pop("_id")
        return result[0]