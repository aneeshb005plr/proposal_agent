# app/knowledge/pipeline.py
#
# Orchestrates the knowledge-base sync: lists files in the agent's
# SharePoint knowledge folder, downloads new/changed ones, parses
# them, chunks them, embeds them OURSELVES, and inserts directly via
# async pymongo — NOT via MongoDBAtlasVectorSearch.aadd_documents(),
# which re-embeds internally and would double our embedding API cost
# (confirmed from a real reference implementation that hit this
# exact problem). LangChain's vector store wrapper is reserved
# entirely for the SEARCH path (see app/knowledge/search.py, not yet
# built), where re-embedding a single query string is cheap and
# the convenience is worth it.
#
# Vector index creation is a SEPARATE, later, one-time step —
# confirmed from official docs: every real example creates the
# index AFTER data already exists in the collection, not before.
# This file does NOT create or depend on the index existing.
#
# EXCLUSIONS — files filtered out BEFORE parsing/chunking ever runs:
#   - risk_words.txt: loaded separately, whole, by
#     app/knowledge/risk_words.py — never embedded/chunked.
#   - Per-agent EXCLUDED_FROM_INDEXING list (app/config.py) — for
#     RFP Analyzer, this includes the boilerplate Q&A file out of
#     scope per ADR-R001.

import logging
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from langchain_core.documents import Document
from pymongo.asynchronous.database import AsyncDatabase

from app.config import settings
from app.documents.chunker import chunk_documents
from app.documents.parser import UnsupportedFileTypeError, parse_document
from app.knowledge.graph_client import graph_client
from app.knowledge.risk_words import RISK_WORDS_FILENAME
from app.llm import embeddings

logger = logging.getLogger("app.knowledge.pipeline")

TEXT_KEY = "text"
EMBEDDING_KEY = "embedding"
KNOWLEDGE_CHUNKS_COLLECTION = "knowledge_chunks"


class KnowledgeSyncResult:
    """Summary of one sync run, returned to the caller for logging/inspection."""

    def __init__(self):
        self.files_seen = 0
        self.files_indexed = 0
        self.files_excluded = 0
        self.files_deleted = 0
        self.files_failed = 0
        self.chunks_inserted = 0
        self.errors: list[str] = []

    def __repr__(self) -> str:
        return (
            f"KnowledgeSyncResult(seen={self.files_seen}, "
            f"indexed={self.files_indexed}, chunks={self.chunks_inserted}, "
            f"excluded={self.files_excluded}, deleted={self.files_deleted}, "
            f"failed={self.files_failed})"
        )


def _is_excluded(filename: str) -> bool:
    """
    True if this file should never be parsed/chunked/embedded.
    See module docstring for the two exclusion categories.
    """
    if filename.lower() == RISK_WORDS_FILENAME.lower():
        return True

    excluded_names = [name.lower() for name in settings.EXCLUDED_FROM_INDEXING]
    return filename.lower() in excluded_names


async def _get_stored_delta_link(db: AsyncDatabase) -> Optional[str]:
    record = await db["knowledge_sources"].find_one({"agent_id": settings.AGENT_ID})
    return record.get("delta_link") if record else None


async def _save_delta_link(db: AsyncDatabase, delta_link: Optional[str]) -> None:
    if delta_link is None:
        logger.warning(
            "No delta_link returned from this sync run — leaving "
            "previously stored delta_link unchanged."
        )
        return

    await db["knowledge_sources"].update_one(
        {"agent_id": settings.AGENT_ID},
        {"$set": {"delta_link": delta_link, "last_synced_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


async def sync_knowledge_base(db: AsyncDatabase) -> KnowledgeSyncResult:
    """
    Runs one full sync pass: full scan on first run (no stored
    delta_link), delta-only scan on subsequent runs. For each
    relevant file: download, parse, chunk, embed (ourselves), insert
    directly. Deleted files (per Graph's delta "deleted" facet) are
    removed via async delete_many on sharepoint_item_id.

    Does NOT touch or depend on any Atlas Vector Search index —
    that is created separately, once, after this has run at least
    once and real data exists (see scripts/create_vector_index.py,
    a separate file, not yet written).
    """
    result = KnowledgeSyncResult()
    collection = db[KNOWLEDGE_CHUNKS_COLLECTION]

    stored_delta_link = await _get_stored_delta_link(db)
    is_full_sync = stored_delta_link is None

    logger.info(
        "Starting knowledge sync for %s (%s)",
        settings.AGENT_ID, "full" if is_full_sync else "delta",
    )

    async for item in graph_client.iter_changes(delta_link=stored_delta_link):

        if item.get("folder") is not None:
            continue
        if not graph_client.is_in_knowledge_folder(item):
            continue

        result.files_seen += 1
        filename = item.get("name", "")

        # ── Deleted item ──────────────────────────────────────────
        if item.get("deleted") is not None:
            delete_result = await collection.delete_many(
                {"sharepoint_item_id": item["id"]}
            )
            if delete_result.deleted_count > 0:
                result.files_deleted += 1
                logger.info(
                    "Removed %d chunk(s) for deleted file: %s",
                    delete_result.deleted_count, filename,
                )
            continue

        # ── Hidden/temp Office files ──────────────────────────────
        if graph_client.is_hidden_or_temp(filename):
            result.files_excluded += 1
            continue

        # ── Deliberate exclusions ─────────────────────────────────
        if _is_excluded(filename):
            result.files_excluded += 1
            logger.debug("Excluded from indexing: %s", filename)
            continue

        # ── Process the file ──────────────────────────────────────
        try:
            chunks_inserted = await _index_one_file(item, collection)
            result.files_indexed += 1
            result.chunks_inserted += chunks_inserted
        except UnsupportedFileTypeError as e:
            result.files_excluded += 1
            logger.info("Skipped unsupported file type: %s (%s)", filename, e)
        except Exception as e:
            result.files_failed += 1
            result.errors.append(f"{filename}: {e}")
            logger.exception("Failed to index file: %s", filename)

    await _save_delta_link(db, graph_client.last_delta_link)

    logger.info("Knowledge sync complete: %s", result)
    return result


async def _index_one_file(item: dict, collection) -> int:
    """
    Downloads, parses, chunks, embeds (ourselves, via app/llm.py's
    embeddings client), and inserts one file's chunks directly via
    async insert_many. Existing chunks for this item are removed
    first, so re-indexing a changed file never leaves stale chunks
    alongside fresh ones.

    Returns the number of chunks inserted.
    """
    item_id = item["id"]
    filename = item.get("name", "")

    file_bytes = await graph_client.download_file(item_id)
    if file_bytes is None:
        raise RuntimeError(f"Failed to download content for {filename}")

    parsed_documents: list[Document] = parse_document(file_bytes, filename)

    for doc in parsed_documents:
        doc.metadata["sharepoint_item_id"] = item_id
        doc.metadata["agent_id"] = settings.AGENT_ID

    chunks = chunk_documents(parsed_documents)

    if not chunks:
        logger.warning("No chunks produced for %s — skipping", filename)
        return 0

    # Embed ourselves, in one batched call — NOT via
    # MongoDBAtlasVectorSearch.aadd_documents(), which would call the
    # embeddings client a second time internally.
    texts = [chunk.page_content for chunk in chunks]
    vectors = await embeddings.aembed_documents(texts)

    docs_to_insert = [
        {
            "_id": ObjectId(),
            TEXT_KEY: chunk.page_content,
            EMBEDDING_KEY: vector,
            **chunk.metadata,
        }
        for chunk, vector in zip(chunks, vectors)
    ]

    # Remove existing chunks for this item first (re-index-on-change case).
    await collection.delete_many({"sharepoint_item_id": item_id})

    await collection.insert_many(docs_to_insert)
    logger.info("Indexed %s: %d chunk(s)", filename, len(docs_to_insert))
    return len(docs_to_insert)