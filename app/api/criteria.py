# app/api/criteria.py
#
# Upload route for a criteria document. Uses CriteriaUploadRepository
# (RFP-Analyzer-specific), NOT the generic SessionRepository — see
# that repository's own header for why this separation matters.

import logging

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pymongo.asynchronous.database import AsyncDatabase

from app.auth.claims_resolver import UserClaims, get_current_user
from app.database import get_database
from app.documents.parser import UnsupportedFileTypeError, parse_document
from app.repository.criteria_upload_repository import CriteriaUploadRepository
from app.repository.session_repository import (
    SessionAccessDeniedError,
    SessionNotFoundError,
    SessionRepository,
)

logger = logging.getLogger("app.api.criteria")

router = APIRouter(prefix="/sessions/{session_id}/criteria-document", tags=["criteria"])


@router.post("")
async def upload_criteria_document_route(
    session_id: str,
    file: UploadFile,
    db: AsyncDatabase = Depends(get_database),
    user: UserClaims = Depends(get_current_user),
):
    session_repo = SessionRepository(db)

    try:
        await session_repo.get_owned_session(session_id, user.user_id)
    except (SessionNotFoundError, SessionAccessDeniedError):
        raise HTTPException(status_code=404, detail="Session not found")

    file_bytes = await file.read()

    try:
        parsed_documents = parse_document(file_bytes, file.filename)
    except UnsupportedFileTypeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    full_text = "\n\n".join(d.page_content for d in parsed_documents)

    if not full_text.strip():
        raise HTTPException(
            status_code=400,
            detail="Could not extract any text from this file.",
        )

    criteria_upload_repo = CriteriaUploadRepository(db)
    await criteria_upload_repo.set_pending_text(session_id, user.user_id, full_text)

    logger.info(
        "Criteria document uploaded for session %s: %s (%d chars extracted)",
        session_id, file.filename, len(full_text),
    )

    return {
        "filename": file.filename,
        "characters_extracted": len(full_text),
    }