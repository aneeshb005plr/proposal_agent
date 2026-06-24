# app/api/knowledge.py
#
# Thin HTTP layer. No business logic here — every route just
# extracts dependencies and calls the service layer.

import logging

from fastapi import APIRouter, Depends
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.synchronous.database import Database

from app.database import get_database, get_sync_database
from app.services import knowledge_service

logger = logging.getLogger("app.api.knowledge")

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.post("/sync")
async def sync_knowledge_route(db: AsyncDatabase = Depends(get_database)):
    """
    Triggers a knowledge sync against this agent's own SharePoint
    folder. Full sync on first call, delta sync thereafter (handled
    internally by pipeline.py via the stored delta_link).
    """
    result = await knowledge_service.sync_knowledge(db)
    return {
        "files_seen": result.files_seen,
        "files_indexed": result.files_indexed,
        "files_excluded": result.files_excluded,
        "files_deleted": result.files_deleted,
        "files_failed": result.files_failed,
        "chunks_inserted": result.chunks_inserted,
        "errors": result.errors,
    }


@router.post("/create-index")
async def create_index_route(sync_db: Database = Depends(get_sync_database)):
    """
    One-time setup. Must be called AFTER /knowledge/sync has run at
    least once and real data exists — will raise a clear error
    otherwise. Not automatic; deliberately manual.
    """
    await knowledge_service.create_vector_index(sync_db)
    return {"status": "index created"}