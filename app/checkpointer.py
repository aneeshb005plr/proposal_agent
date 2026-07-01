# app/checkpointer.py
#
# CONFIRMED: AsyncMongoDBSaver was removed in langgraph-checkpoint-
# mongodb v0.3.0. We use the sync MongoDBSaver, with its own
# dedicated sync MongoClient — separate from app.state.mongo_client
# (our existing AsyncMongoClient, used exclusively by our own
# repositories) and separate from app.state.mongo_sync_db (used
# exclusively by knowledge_repository.py for vector search).
#
# This is the THIRD MongoDB connection in the app, each confined to
# exactly one consumer that genuinely requires it — not because we
# want three connections, but because three different libraries each
# independently require a sync client for different reasons, and we
# keep each requirement narrowly scoped rather than sharing one sync
# client across unrelated concerns.
#
# Pattern matches connect_to_mongo(app) in database.py — sets
# app.state directly, rather than returning values for the caller
# to assign. Kept consistent rather than introducing a different
# style for one extra file.

import logging

from fastapi import FastAPI
from langgraph.checkpoint.mongodb import MongoDBSaver
from pymongo import MongoClient

from app.config import settings

logger = logging.getLogger("app.checkpointer")


def connect_checkpointer(app: FastAPI) -> None:
    """
    Builds the checkpointer and its dedicated sync MongoClient,
    storing both on app.state. Called once from the lifespan at
    startup, alongside connect_to_mongo(app).

    No .setup() call needed — MongoDBSaver creates its required
    indexes automatically on construction.
    """
    sync_client = MongoClient(
        settings.MONGODB_URI,
        maxPoolSize=5,  # small, dedicated pool — checkpoint traffic only
    )

    checkpointer = MongoDBSaver(
        client=sync_client,
        db_name=settings.MONGODB_DB_NAME,
    )

    app.state.checkpointer = checkpointer
    app.state.checkpointer_sync_client = sync_client

    logger.info("MongoDBSaver checkpointer ready (indexes auto-created)")


def close_checkpointer(app: FastAPI) -> None:
    """Closes the dedicated sync MongoClient. Called from lifespan shutdown."""
    sync_client = getattr(app.state, "checkpointer_sync_client", None)
    if sync_client is not None:
        sync_client.close()
        logger.info("Checkpointer sync MongoClient closed")


def get_checkpointer(app: FastAPI) -> MongoDBSaver:
    """
    Accessor for use when compiling the graph (app/agent/graph.py).
    Raises clearly if called before connect_checkpointer() has run —
    same defensive pattern as get_database()/get_sync_database() in
    database.py.
    """
    checkpointer = getattr(app.state, "checkpointer", None)
    if checkpointer is None:
        raise RuntimeError(
            "Checkpointer not initialized. "
            "connect_checkpointer(app) must run during app startup."
        )
    return checkpointer