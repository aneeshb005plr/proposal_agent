# app/repository/submission_repository.py
#
# Owns direct access to the submission_chunks collection — the
# session-scoped, ephemeral counterpart to knowledge_chunks. NOT
# inserted into knowledge_chunks under any circumstances: this is
# per-conversation, user-uploaded content, not permanent agent
# knowledge. See earlier design discussion on why these must never
# share a collection.

import logging

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

logger = logging.getLogger("app.repository.submission_repository")

SUBMISSION_CHUNKS_COLLECTION = "submission_chunks"
TEXT_KEY = "text"
EMBEDDING_KEY = "embedding"


class SubmissionRepository:

    def __init__(self, db: AsyncDatabase):
        self._collection = db[SUBMISSION_CHUNKS_COLLECTION]

    async def insert_chunks(
        self,
        session_id: str,
        filename: str,
        texts: list[str],
        vectors: list[list[float]],
        metadatas: list[dict],
    ) -> int:
        """
        Inserts pre-embedded chunks for one uploaded file, tagged
        with session_id and filename so they can be retrieved or
        deleted as a unit later.
        """
        docs = [
            {
                "_id": ObjectId(),
                TEXT_KEY: text,
                EMBEDDING_KEY: vector,
                "session_id": session_id,
                "filename": filename,
                **metadata,
            }
            for text, vector, metadata in zip(texts, vectors, metadatas)
        ]
        if not docs:
            return 0

        await self._collection.insert_many(docs)
        logger.info(
            "Inserted %d chunk(s) for %s in session %s",
            len(docs), filename, session_id,
        )
        return len(docs)

    async def delete_session_chunks(self, session_id: str) -> int:
        """
        Removes ALL chunks for a session — used when the
        upload-after-confirmation policy is "invalidate" and prior
        results must be discarded, or for session cleanup generally.
        """
        result = await self._collection.delete_many({"session_id": session_id})
        return result.deleted_count
    
    async def delete_file_chunks(self, session_id: str, filename: str) -> int:
        """Removes chunks for one specific file within a session,
        leaving other files in the session untouched."""
        result = await self._collection.delete_many(
            {"session_id": session_id, "filename": filename}
        )
        return result.deleted_count

    async def get_session_chunks(self, session_id: str) -> list[dict]:
        """
        Returns ALL chunks for a session, across all uploaded files.
        Used by the (not-yet-built) exhaustive per-criterion scoring
        logic — deliberately returns everything, not a similarity-
        search subset, per the earlier decision that approximate
        retrieval risks missing real evidence for this agent.
        """
        cursor = self._collection.find({"session_id": session_id})
        return await cursor.to_list(length=None)
    
    async def get_distinct_filenames(self, session_id: str) -> list[str]:
        """
        Returns just the distinct filenames uploaded for this session —
        NOT full chunk content. Used by request_document to check
        whether a file has been uploaded and to report which ones, without
        pulling potentially large chunk documents (each carrying a
        3072-dim embedding vector) into memory just to answer that.
        """
        return await self._collection.distinct("filename", {"session_id": session_id})
        
