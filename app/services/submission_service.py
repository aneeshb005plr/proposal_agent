# app/services/submission_service.py
#
# Updated: every function now requires and enforces user_id,
# matching the ownership-checked pattern just established in
# session_service.py. A user can no longer upload to, or delete
# from, a session that isn't theirs by guessing/reusing a session_id.

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
    db: AsyncDatabase,
    session_id: str,
    user_id: str,
    filename: str,
    file_bytes: bytes,
) -> dict:
    """
    Handles one uploaded file end to end. Now requires user_id and
    verifies ownership via get_owned_session before doing anything
    else — raises SessionNotFoundError or SessionAccessDeniedError
    (from session_repository) if the session doesn't exist or isn't
    owned by this user.
    """
    session_repo = SessionRepository(db)
    submission_repo = SubmissionRepository(db)

    # Ownership check happens here — raises if not found/not owned,
    # same as session_service.get_session_or_raise
    session = await session_repo.get_owned_session(session_id, user_id)

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
            pass
        else:
            raise RuntimeError(
                f"Unknown UPLOAD_AFTER_CONFIRMATION_POLICY: "
                f"{settings.UPLOAD_AFTER_CONFIRMATION_POLICY!r}"
            )

    # ── Parse, chunk, embed (unchanged from before) ─────────────────
    parsed_documents = parse_document(file_bytes, filename)

    for doc in parsed_documents:
        doc.metadata["session_id"] = session_id

    chunks = chunk_documents(parsed_documents)

    if not chunks:
        logger.warning("No chunks produced for %s — nothing stored", filename)
        return {"filename": filename, "chunks_stored": 0}

    texts = [chunk.page_content for chunk in chunks]
    vectors = await embeddings.aembed_documents(texts)
    metadatas = [chunk.metadata for chunk in chunks]

    chunks_stored = await submission_repo.insert_chunks(
        session_id, filename, texts, vectors, metadatas
    )

    await session_repo.increment_file_count(session_id)

    return {"filename": filename, "chunks_stored": chunks_stored}


async def delete_submission_file(
    db: AsyncDatabase, session_id: str, user_id: str, filename: str
) -> dict:
    """
    Now requires and enforces user_id, same as upload above. Also
    applies the SAME upload-after-confirmation policy on deletion as
    on upload — removing evidence from an already-confirmed,
    already-scored evaluation is exactly as disruptive as adding new
    evidence, per the reasoning we settled on earlier.
    """
    session_repo = SessionRepository(db)
    submission_repo = SubmissionRepository(db)

    session = await session_repo.get_owned_session(session_id, user_id)

    deleted_count = await submission_repo.delete_file_chunks(session_id, filename)

    if deleted_count > 0:
        await session_repo.decrement_file_count(session_id)

        if session["document_confirmed"]:
            if settings.UPLOAD_AFTER_CONFIRMATION_POLICY == "invalidate":
                await session_repo.reset_confirmation(session_id)
                logger.info(
                    "Session %s: post-confirmation deletion triggered "
                    "invalidation (policy=invalidate)", session_id,
                )
            # "allow" policy: deletion simply shrinks the active set,
            # no invalidation — consistent with upload's "allow" branch

    return {"filename": filename, "chunks_deleted": deleted_count}