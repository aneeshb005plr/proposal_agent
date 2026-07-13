# app/services/knowledge_service.py
#
# Coordinates knowledge operations. sync_knowledge no longer runs
# the sync itself (that's the worker's job entirely now) — it
# creates job(s) for the worker to pick up. process_document holds
# the logic behind the new /internal/documents/process callback.
# Retrieval (retrieve_relevant_knowledge) is UNCHANGED — nothing
# about the async job-queue redesign affects the search path at all.

import logging

from langchain_core.documents import Document
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.synchronous.database import Database

from app.documents.chunker import chunk_documents
from app.documents.parser import parse_document
from app.repository.job_repository import JobRepository
from app.repository.knowledge_repository import get_knowledge_repository
from app.repository.source_repository import SourceRepository
from app.schema.knowledge_source import (
    CreateSourceRequest,
    SourceSummaryResponse,
    UpdateSourceRequest,
)
from app.security.encryption import encrypt_secret

logger = logging.getLogger("app.services.knowledge_service")

EMBEDDING_DIMENSIONS = 3072  # unchanged from before


class SourceAlreadyExistsError(Exception):
    pass


class SourceNotFoundError(Exception):
    pass


def _encrypt_config_secrets(config: dict, secret_fields: list[str]) -> dict:
    """Returns a NEW dict — every field named in secret_fields is
    encrypted and stored under "{field}_encrypted", with the plain
    key removed. Fields NOT in secret_fields pass through untouched.
    Generic across source_type — this function has no idea what
    "graph_client_secret" or any other specific field name means."""
    result = {k: v for k, v in config.items() if k not in secret_fields}
    for field in secret_fields:
        if field in config:
            result[f"{field}_encrypted"] = encrypt_secret(config[field])
    return result


async def create_source(
    db: AsyncDatabase, agent_id: str, request: CreateSourceRequest
) -> None:
    source_repo = SourceRepository(db)
    existing = await source_repo.get_raw(agent_id, request.source_id)
    if existing is not None:
        raise SourceAlreadyExistsError(
            f"Source {request.source_id!r} already exists for this agent "
            f"— use PATCH to update it."
        )

    encrypted_config = _encrypt_config_secrets(request.config, request.secret_fields)
    await source_repo.create(
        agent_id, request.source_id, request.source_type,
        encrypted_config, request.enabled,
    )
    logger.info(
        "Created knowledge source %r (type=%s) for agent_id=%s",
        request.source_id, request.source_type, agent_id,
    )


async def update_source(
    db: AsyncDatabase, agent_id: str, source_id: str, request: UpdateSourceRequest
) -> None:
    source_repo = SourceRepository(db)
    updates: dict = {}

    if request.config is not None:
        updates["config"] = _encrypt_config_secrets(request.config, request.secret_fields)
    if request.enabled is not None:
        updates["enabled"] = request.enabled

    if not updates:
        logger.info("update_source called for %s with no fields to change", source_id)
        return

    updated = await source_repo.update(agent_id, source_id, updates)
    if not updated:
        raise SourceNotFoundError(f"Source {source_id!r} not found for this agent")

    logger.info("Updated knowledge source %r for agent_id=%s", source_id, agent_id)


async def list_sources(db: AsyncDatabase, agent_id: str) -> list[SourceSummaryResponse]:
    source_repo = SourceRepository(db)
    docs = await source_repo.list_all(agent_id)
    return [
        SourceSummaryResponse(
            source_id=doc["source_id"],
            source_type=doc["source_type"],
            config=doc["config"],  # already stripped of *_encrypted by the repository
            enabled=doc.get("enabled", True),
        )
        for doc in docs
    ]


async def create_sync_jobs(
    db: AsyncDatabase, agent_id: str, source_id: str | None, mode: str
) -> list[str]:
    """
    Creates one job per source. If source_id is omitted, creates ONE
    JOB PER enabled source — not one combined job — so sources
    succeed/fail/retry independently and status is clear per source.
    Returns the created job IDs as strings.
    """
    job_repo = JobRepository(db)

    if source_id is not None:
        source_ids = [source_id]
    else:
        source_repo = SourceRepository(db)
        source_ids = await source_repo.get_enabled_source_ids(agent_id)
        if not source_ids:
            logger.warning(
                "No enabled sources found for agent_id=%s — no jobs created",
                agent_id,
            )

    job_ids = []
    for sid in source_ids:
        job_id = await job_repo.create(agent_id, sid, mode)
        job_ids.append(str(job_id))

    logger.info(
        "Created %d sync job(s) for agent_id=%s (mode=%s)",
        len(job_ids), agent_id, mode,
    )
    return job_ids


async def get_sync_job(db: AsyncDatabase, job_id: str) -> dict | None:
    from bson import ObjectId
    job_repo = JobRepository(db)
    job = await job_repo.get_by_id(ObjectId(job_id))
    if job is not None:
        job["_id"] = str(job["_id"])
    return job


async def process_document_for_indexing(file_bytes: bytes, filename: str) -> list[dict]:
    """
    Backs POST /internal/documents/process — reuses the EXISTING
    parser/chunker unchanged, the same code path already used for
    user document uploads. Called by the knowledge worker for
    file-based sources (SharePoint today).
    """
    parsed_documents = parse_document(file_bytes, filename)
    chunks = chunk_documents(parsed_documents)
    return [
        {"text": c.page_content, "metadata": c.metadata}
        for c in chunks
    ]


async def create_vector_index(sync_db: Database) -> None:
    """Unchanged from before."""
    repo = get_knowledge_repository(sync_db)
    await repo.create_vector_index(dimensions=EMBEDDING_DIMENSIONS)


async def retrieve_relevant_knowledge(
    sync_db: Database, query: str, k: int = 15
) -> list[Document]:
    """Unchanged from before — the search path is completely
    unaffected by the sync-side redesign."""
    repo = get_knowledge_repository(sync_db)
    results = await repo.similarity_search(query=query, k=k)
    logger.info("Retrieved %d knowledge chunk(s) for query", len(results))
    return results