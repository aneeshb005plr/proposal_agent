# app/checkpointer.py
#
# CONFIRMED (not just plausible): AsyncMongoDBSaver was removed in
# langgraph-checkpoint-mongodb v0.3.0. We use the sync MongoDBSaver,
# with its own dedicated sync MongoClient — separate from our
# existing AsyncMongoClient (app.state.mongo_client), which remains
# exclusively for our own repositories.
#
# This mirrors the exact same sync/async split already established
# in app/repository/knowledge_repository.py for MongoDBAtlasVectorSearch
# — some MongoDB+LangChain/LangGraph integrations require a sync
# client; we confine that requirement narrowly to where it's
# actually needed, rather than letting it spread.
#
# A small, dedicated connection pool (maxPoolSize=5) is used since
# this client serves only checkpoint reads/writes, not general
# query traffic.

import logging

from langgraph.checkpoint.mongodb import MongoDBSaver
from pymongo import MongoClient

from app.config import settings

logger = logging.getLogger("app.checkpointer")


def build_checkpointer() -> tuple[MongoDBSaver, MongoClient]:
    """
    Returns (checkpointer, sync_client). The caller is responsible
    for storing sync_client somewhere it can be closed at shutdown
    (e.g. app.state), same as our other connections.

    No .setup() call needed — MongoDBSaver creates its required
    indexes automatically on construction.
    """
    sync_client = MongoClient(
        settings.MONGODB_URI,
        maxPoolSize=5,
    )

    checkpointer = MongoDBSaver(
        client=sync_client,
        db_name=settings.MONGODB_DB_NAME,
    )

    logger.info("MongoDBSaver checkpointer ready (indexes auto-created)")
    return checkpointer, sync_client