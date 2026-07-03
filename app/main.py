# app/main.py
# Application factory – create_app() builds and returns FastAPI instance.
# All logic lives here. Root main.py is just the uvicorn entry point.

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import connect_to_mongo, close_mongo_connection
from app.knowledge.graph_client import graph_client
from app.checkpointer import connect_checkpointer, close_checkpointer
from app.repository.criteria_upload_repo import CriteriaUploadRepository
from app.repository.session_repo import SessionRepository  # add import
from app.repository.message_repository import MessageRepository  # add import


from app.knowledge.risk_words import load_risk_words
from app.api.router import router
from app.agent.setup import register_agent_hooks


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info(
        "Starting %s [env=%s]",
        settings.AGENT_NAME,
        settings.ENVIRONMENT,
    )

    try:
        # — Initialize MongoDB
        await connect_to_mongo(app)

        # — Initialize Graph Client for SharePoint
        await graph_client.connect()

        # — Load Risk Words
        await load_risk_words()

        # — Initialize Checkpointer
        connect_checkpointer(app)

        register_agent_hooks() 

        # NEW — one-time TTL index setup for the criteria-upload buffer.
        # Must run AFTER connect_to_mongo (needs app.state.mongo_db to exist).
        # Safe to call on every startup — create_index is a no-op if the
        # index already exists with the same spec.
        criteria_upload_repo = CriteriaUploadRepository(app.state.mongo_db)
        await criteria_upload_repo.ensure_indexes()

        session_repo = SessionRepository(app.state.mongo_db)
        await session_repo.ensure_indexes()

        message_repo = MessageRepository(app.state.mongo_db)
        await message_repo.setup_indexes()

        app.state.ready = True
        logger.info("%s startup complete", settings.AGENT_NAME)

    except Exception:
        logger.exception("Startup failed for %s", settings.AGENT_NAME)

        # Best-effort cleanup of anything that did succeed
        await graph_client.disconnect()
        await close_mongo_connection(app)
        close_checkpointer(app)
        raise

    yield

    logger.info("Shutting down %s...", settings.AGENT_NAME)
    app.state.ready = False

    # — Cleanup
    await graph_client.disconnect()
    await close_mongo_connection(app)
    close_checkpointer(app)

    logger.info("%s shutdown complete", settings.AGENT_NAME)


def create_app() -> FastAPI:
    """
    Application factory – builds and returns the configured FastAPI instance.
    Called by uvicorn in root main.py.
    Also called in tests to get a fresh app per test run.
    """

    enable_docs = settings.ENABLE_SWAGGER and not settings.IS_PRODUCTION

    app = FastAPI(
        title=settings.AGENT_NAME,
        lifespan=lifespan,
        docs_url="/docs" if enable_docs else None,
        redoc_url="/redoc" if enable_docs else None,
        openapi_url="/openapi.json" if enable_docs else None,
    )

    # — Initial state – flipped to True at end of startup
    app.state.ready = False

    # — CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Test claims resolver
    from app.auth.claims_resolver import get_current_user, UserClaims
    from fastapi import Depends

    @app.get("/whoami")
    async def whoami(
        user: UserClaims = Depends(get_current_user),
    ):
        return user

    # — Routers
    app.include_router(router)

    return app