# app/api/documents.py

import logging

from fastapi import APIRouter, Depends, HTTPException, UploadFile,Request

from app.checkpointer import get_checkpointer
from pymongo.asynchronous.database import AsyncDatabase

from app.auth.claims_resolver import UserClaims, get_current_user
from app.database import get_database
from app.documents.parser import UnsupportedFileTypeError
from app.repository.session_repository import (
    SessionAccessDeniedError,
    SessionNotFoundError,
)
from app.services import submission_service
from app.services.submission_service import UploadLimitExceededError
from typing import Optional, Callable, Awaitable

logger = logging.getLogger("app.api.documents")

router = APIRouter(prefix="/sessions/{session_id}/documents", tags=["documents"])


post_upload_hook: Optional[
    Callable[[AsyncDatabase, object, str, str], Awaitable[Optional[str]]]
] = None


@router.post("")
async def upload_document_route(
    session_id: str,
    file: UploadFile,
    request: Request,
    db: AsyncDatabase = Depends(get_database),
    user: UserClaims = Depends(get_current_user),
):
    file_bytes = await file.read()

    try:
        result = await submission_service.upload_submission_file(
            db, session_id, user.user_id, file.filename, file_bytes
        )

        confirmation_message = None
        if post_upload_hook is not None:
            checkpointer = get_checkpointer(request.app)
            confirmation_message = await post_upload_hook(db, checkpointer, session_id, user.user_id)

        result["confirmation_message"] = confirmation_message
        return result
    
    except (SessionNotFoundError, SessionAccessDeniedError):
        raise HTTPException(status_code=404, detail="Session not found")
    except UploadLimitExceededError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except UnsupportedFileTypeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{filename}")
async def delete_document_route(
    session_id: str,
    filename: str,
    db: AsyncDatabase = Depends(get_database),
    user: UserClaims = Depends(get_current_user),
):
    try:
        result = await submission_service.delete_submission_file(
            db, session_id, user.user_id, filename
        )
    except (SessionNotFoundError, SessionAccessDeniedError):
        raise HTTPException(status_code=404, detail="Session not found")

    if result["chunks_deleted"] == 0:
        raise HTTPException(status_code=404, detail="File not found in session")

    return result