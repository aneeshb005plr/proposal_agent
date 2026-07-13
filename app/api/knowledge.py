# app/api/knowledge.py
#
# Thin HTTP layer. No business logic here — every route just
# extracts dependencies and calls the service layer.
#
# /sync no longer awaits the sync inline — it submits job(s) for the
# knowledge worker to pick up. NEW: /sync/{job_id} to poll status,
# and /internal/documents/process — the callback the worker calls
# for file-based sources, reusing THIS agent's existing parser.

import logging

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.synchronous.database import Database

from app.auth.authorization import require_admin, require_internal_service
from app.auth.claims_resolver import UserClaims
from app.config import settings
from app.database import get_database, get_sync_database
from app.schema.knowledge_source import (
    CreateSourceRequest,
    SourceSummaryResponse,
    UpdateSourceRequest,
)
from app.services import knowledge_service

logger = logging.getLogger("app.api.knowledge")

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


class SyncRequest(BaseModel):
    source_id: str | None = None
    mode: str = "incremental"  # or "full_reset"


@router.post("/sync")
async def sync_knowledge_route(
    body: SyncRequest,
    db: AsyncDatabase = Depends(get_database),
    user: UserClaims = Depends(require_admin),
):
    """
    Admin-only. Submits job(s) for the knowledge worker — does NOT
    run the sync inline anymore. If source_id is omitted, creates
    one job per enabled source.
    """
    job_ids = await knowledge_service.create_sync_jobs(
        db, agent_id=settings.AGENT_ID, source_id=body.source_id, mode=body.mode,
    )
    return {"job_ids": job_ids, "status": "pending"}


@router.get("/sync/{job_id}")
async def get_sync_job_route(
    job_id: str,
    db: AsyncDatabase = Depends(get_database),
    user: UserClaims = Depends(require_admin),
):
    job = await knowledge_service.get_sync_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/create-index")
async def create_index_route(
    sync_db: Database = Depends(get_sync_database),
    user: UserClaims = Depends(require_admin),
):
    """Unchanged from before — one-time setup, admin-only."""
    await knowledge_service.create_vector_index(sync_db)
    return {"status": "index created"}


@router.post("/sources", status_code=201)
async def create_source_route(
    body: CreateSourceRequest,
    db: AsyncDatabase = Depends(get_database),
    user: UserClaims = Depends(require_admin),
):
    """
    Admin-only. Replaces the manual "insert a document directly into
    knowledge_sources" step from the earlier end-to-end test —
    secrets are encrypted here, not by the caller, so the caller
    only ever sends plain values plus a secret_fields list naming
    which config keys need encryption. Works for ANY source_type,
    not just SharePoint.
    """
    try:
        await knowledge_service.create_source(db, settings.AGENT_ID, body)
    except knowledge_service.SourceAlreadyExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"status": "created", "source_id": body.source_id}


@router.patch("/sources/{source_id}")
async def update_source_route(
    source_id: str,
    body: UpdateSourceRequest,
    db: AsyncDatabase = Depends(get_database),
    user: UserClaims = Depends(require_admin),
):
    try:
        await knowledge_service.update_source(db, settings.AGENT_ID, source_id, body)
    except knowledge_service.SourceNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "updated", "source_id": source_id}


@router.get("/sources", response_model=list[SourceSummaryResponse])
async def list_sources_route(
    db: AsyncDatabase = Depends(get_database),
    user: UserClaims = Depends(require_admin),
):
    """Never includes secrets, encrypted or not — see
    SourceSummaryResponse's own docstring."""
    return await knowledge_service.list_sources(db, settings.AGENT_ID)


@router.post("/internal/documents/process")
async def process_document_route(
    file: UploadFile,
    _: None = Depends(require_internal_service),
):
    """
    NOT exposed via Ocelot's public routes — internal-only, called
    by the knowledge worker. Reuses this agent's existing parser via
    the service layer, never re-implemented here.
    """
    file_bytes = await file.read()
    chunks = await knowledge_service.process_document_for_indexing(
        file_bytes, file.filename
    )
    return {"chunks": chunks}