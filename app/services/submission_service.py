# app/services/submission_service.py
#
# Coordinates the upload flow: enforce file-count limit, apply
# upload-after-confirmation policy, parse + chunk + embed (reusing
# the SAME pattern as pipeline.py — embed ourselves, never via
# MongoDBAtlasVectorSearch.aadd_documents(), for the same cost
# reason), store via submission_repository.
#
# Generic across agents: behavior driven entirely by
# settings.MAX_UPLOADED_FILES_PER_SESSION and
# settings.UPLOAD_AFTER_CONFIRMATION_POLICY, not hardcoded logic.

import logging

from pymongo.asynchronous.database import AsyncDatabase

from app.config import settings
from app.documents.chunker import chunk_documents
from app.documents.parser import UnsupportedFileTypeError, parse_document
from app.llm import embeddings
from app.repository.session_repository import SessionRepository
from app.repository.submission_repository import SubmissionRepository

logger = logging.getLogger("app.services.submission_service")


class UploadLimitExceededError(Exception):
    pass


async def upload_submission_file(
    db: AsyncDatabase, session_id: str, filename: str, file_bytes: bytes
) -> dict:
    """
    Handles one uploaded file end to end: limit check, policy check,
    parse, chunk, embed, store. Returns a summary dict.

    Raises UnsupportedFileTypeError (from parser.py — e.g. .doc) or
    UploadLimitExceededError (session already at
    MAX_UPLOADED_FILES_PER_SESSION) — callers (the route) translate
    these into appropriate HTTP responses.
    """
    session_repo = SessionRepository(db)
    submission_repo = SubmissionRepository(db)

    session = await session_repo.get_session(session_id)
    if session is None:
        raise ValueError(f"Session {session_id} not found")

    # ── Enforce file limit BEFORE doing any expensive work ──────────
    if session["uploaded_file_count"] >= settings.MAX_UPLOADED_FILES_PER_SESSION:
        raise UploadLimitExceededError(
            f"Session {session_id} has reached the limit of "
            f"{settings.MAX_UPLOADED_FILES_PER_SESSION} uploaded files."
        )

    # ── Apply upload-after-confirmation policy ──────────────────────
    if session["document_confirmed"]:
        if settings.UPLOAD_AFTER_CONFIRMATION_POLICY == "invalidate":
            deleted = await submission_repo.delete_session_chunks(session_id)
            await session_repo.reset_confirmation(session_id)
            logger.info(
                "Session %s: post-confirmation upload triggered "
                "invalidation — deleted %d prior chunk(s)",
                session_id, deleted,
            )
        elif settings.UPLOAD_AFTER_CONFIRMATION_POLICY == "allow":
            pass  # new file simply joins the active set
        else:
            raise RuntimeError(
                f"Unknown UPLOAD_AFTER_CONFIRMATION_POLICY: "
                f"{settings.UPLOAD_AFTER_CONFIRMATION_POLICY!r}"
            )

    # ── Parse (reused from the knowledge pipeline, format-agnostic) ─
    parsed_documents = parse_document(file_bytes, filename)

    for doc in parsed_documents:
        doc.metadata["session_id"] = session_id

    # ── Chunk (reused — large files genuinely need this; see earlier
    # decision that exhaustive processing, not similarity search,
    # consumes these chunks downstream) ──────────────────────────────
    chunks = chunk_documents(parsed_documents)

    if not chunks:
        logger.warning("No chunks produced for %s — nothing stored", filename)
        return {"filename": filename, "chunks_stored": 0}

    # ── Embed ourselves — same reasoning as pipeline.py: never let
    # MongoDBAtlasVectorSearch.aadd_documents() re-embed internally ──
    texts = [chunk.page_content for chunk in chunks]
    vectors = await embeddings.aembed_documents(texts)
    metadatas = [chunk.metadata for chunk in chunks]

    chunks_stored = await submission_repo.insert_chunks(
        session_id, filename, texts, vectors, metadatas
    )

    await session_repo.increment_file_count(session_id)

    return {"filename": filename, "chunks_stored": chunks_stored}


async def delete_submission_file(
    db: AsyncDatabase, session_id: str, filename: str
) -> dict:
    session_repo = SessionRepository(db)
    submission_repo = SubmissionRepository(db)

    session = await session_repo.get_session(session_id)
    if session is None:
        raise ValueError(f"Session {session_id} not found")

    deleted_count = await submission_repo.delete_file_chunks(session_id, filename)
    if deleted_count > 0:
        await session_repo.decrement_file_count(session_id)

    return {"filename": filename, "chunks_deleted": deleted_count}