# app/api/documents.py
#
# Thin HTTP layer for uploading submission documents to a session.

import logging

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pymongo.asynchronous.database import AsyncDatabase

from app.database import get_database
from app.documents.parser import UnsupportedFileTypeError
from app.services import submission_service
from app.services.submission_service import UploadLimitExceededError

logger = logging.getLogger("app.api.documents")

router = APIRouter(prefix="/sessions/{session_id}/documents", tags=["documents"])


@router.post("")
async def upload_document_route(
    session_id: str,
    file: UploadFile,
    db: AsyncDatabase = Depends(get_database),
):
    file_bytes = await file.read()

    try:
        result = await submission_service.upload_submission_file(
            db, session_id, file.filename, file_bytes
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Session not found")
    except UploadLimitExceededError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except UnsupportedFileTypeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return result

@router.delete("/{filename}")
async def delete_document_route(
    session_id: str,
    filename: str,
    db: AsyncDatabase = Depends(get_database),
):
    try:
        result = await submission_service.delete_submission_file(
            db, session_id, filename
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Session not found")

    if result["chunks_deleted"] == 0:
        raise HTTPException(status_code=404, detail="File not found in session")

    return result