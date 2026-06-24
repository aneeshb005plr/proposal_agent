# app/services/knowledge_service.py
#
# Coordinates knowledge operations: triggering a sync, creating the
# vector index (one-time), and retrieving relevant knowledge chunks
# for a given query. Routes call this layer — never the repository
# or pipeline.py directly.

import logging

from langchain_core.documents import Document
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.synchronous.database import Database

from app.knowledge.pipeline import KnowledgeSyncResult, sync_knowledge_base
from app.repository.knowledge_repository import get_knowledge_repository

logger = logging.getLogger("app.services.knowledge_service")

# Must match the real dimension count from your embeddings model —
# confirmed by inspecting a real chunk's embedding length during
# pipeline.py testing (see earlier test script's "embedding length"
# print). NOT assumed — set this to whatever you actually observed.
EMBEDDING_DIMENSIONS = 3072  # CONFIRM against your real test output
                              # before running create_vector_index


async def sync_knowledge(db: AsyncDatabase) -> KnowledgeSyncResult:
    """Triggers a full or delta sync against this agent's SharePoint
    knowledge folder. Thin pass-through to pipeline.py, kept here so
    routes never import knowledge sync internals directly."""
    return await sync_knowledge_base(db)


async def create_vector_index(sync_db: Database) -> None:
    """
    One-time setup. Will raise clearly if knowledge_chunks is empty
    — see KnowledgeRepository.create_vector_index for that guard.
    """
    repo = get_knowledge_repository(sync_db)
    await repo.create_vector_index(dimensions=EMBEDDING_DIMENSIONS)


async def retrieve_relevant_knowledge(
    sync_db: Database, query: str, k: int = 5
) -> list[Document]:
    """
    Returns the k most relevant knowledge chunks for a query.
    Used internally by the RFP Analyzer LangGraph workflow (not yet
    built) — e.g. to pull relevant brand/tone guidance when
    generating the executive summary. Not exposed as its own user-
    facing API route; tested standalone for now, the same pattern
    used for every other piece of infrastructure in this project
    before it gets wired into the actual agent workflow.
    """
    repo = get_knowledge_repository(sync_db)
    results = await repo.similarity_search(query=query, k=k)
    logger.info("Retrieved %d knowledge chunk(s) for query", len(results))
    return results