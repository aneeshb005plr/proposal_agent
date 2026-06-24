# app/repository/knowledge_repository.py
#
# Owns direct access to the knowledge_chunks collection for the
# operations services need: vector index creation (one-time) and
# similarity search (retrieval). Insert/delete for the SYNC pipeline
# itself stays in app/knowledge/pipeline.py, since that's pure async
# pymongo work already tested and working — no reason to move it.
#
# This repository exists specifically for the operations that
# REQUIRE the sync MongoDB client, because MongoDBAtlasVectorSearch
# has no async-native variant — confirmed across every official
# example. Those sync calls are wrapped in asyncio.to_thread() here,
# confined to this one file, so nothing else in the codebase needs
# to think about the sync/async mismatch.

import asyncio
import logging

from langchain_core.documents import Document
from langchain_mongodb import MongoDBAtlasVectorSearch
from pymongo.synchronous.database import Database

from app.config import settings
from app.llm import embeddings

logger = logging.getLogger("app.repository.knowledge_repository")

KNOWLEDGE_CHUNKS_COLLECTION = "knowledge_chunks"
VECTOR_INDEX_NAME = "knowledge_chunks_vector_index"
TEXT_KEY = "text"
EMBEDDING_KEY = "embedding"


class KnowledgeRepository:
    """
    One instance per request, constructed with the sync database
    handle (see app/database.py's get_sync_database dependency).
    """

    def __init__(self, sync_db: Database):
        self._collection = sync_db[KNOWLEDGE_CHUNKS_COLLECTION]
        self._vector_store = MongoDBAtlasVectorSearch(
            collection=self._collection,
            embedding=embeddings,
            index_name=VECTOR_INDEX_NAME,
            text_key=TEXT_KEY,
            embedding_key=EMBEDDING_KEY,
            relevance_score_fn="cosine",
        )

    async def create_vector_index(self, dimensions: int) -> None:
        """
        One-time setup. Deliberately NOT called automatically by
        anything — must be triggered explicitly, and only after real
        data already exists in knowledge_chunks (confirmed from
        official docs: every real example creates the index after
        data is loaded, not before — see pipeline.py module docstring
        for the full reasoning).

        create_vector_search_index() is itself a synchronous call on
        the underlying library — wrapped in to_thread() here, same
        as similarity_search() below.
        """
        existing_count = await asyncio.to_thread(self._collection.count_documents, {})
        if existing_count == 0:
            raise RuntimeError(
                "knowledge_chunks is empty. Run the knowledge sync "
                "(POST /knowledge/sync) first — creating the vector "
                "index before any data exists is not the confirmed, "
                "documented order and is not supported here."
            )

        logger.info(
            "Creating vector search index '%s' with %d dimensions "
            "(%d existing document(s))",
            VECTOR_INDEX_NAME, dimensions, existing_count,
        )

        await asyncio.to_thread(
            self._vector_store.create_vector_search_index,
            dimensions=dimensions,
            wait_until_complete=60,
        )

        logger.info("Vector search index created and ready.")

    async def similarity_search(
        self, query: str, k: int = 5, pre_filter: dict | None = None
    ) -> list[Document]:
        """
        Embeds the query text internally (via MongoDBAtlasVectorSearch's
        own embedding call — fine here, since this is ONE query string,
        not a batch of chunks, so there's no double-embedding cost
        concern the way there was for pipeline.py's inserts) and runs
        $vectorSearch against knowledge_chunks.

        pre_filter follows MongoDBAtlasVectorSearch's own pre_filter
        kwarg shape — e.g. {"agent_id": "rfp_analyzer"} if/when that
        becomes relevant, though for THIS agent's own isolated
        database, filtering by agent_id is not actually necessary
        (there is only ever one agent's data in this collection,
        per the per-agent-microservice model) — left available here
        for completeness, not because we currently need it.

        Wrapped in to_thread() since similarity_search() itself is a
        synchronous method.
        """
        return await asyncio.to_thread(
            self._vector_store.similarity_search,
            query=query,
            k=k,
            pre_filter=pre_filter,
        )


knowledge_repository_instance_cache: dict[int, KnowledgeRepository] = {}


def get_knowledge_repository(sync_db: Database) -> KnowledgeRepository:
    """
    Simple per-db-instance cache so we don't reconstruct
    MongoDBAtlasVectorSearch (and re-validate its config) on every
    single request — constructed once per sync_db identity, reused
    after that.
    """
    key = id(sync_db)
    if key not in knowledge_repository_instance_cache:
        knowledge_repository_instance_cache[key] = KnowledgeRepository(sync_db)
    return knowledge_repository_instance_cache[key]