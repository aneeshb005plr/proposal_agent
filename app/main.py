# app/main.py

from contextlib import asynccontextmanager
from fastapi import FastAPI

from app.database import connect_to_mongo, close_mongo_connection
from app.checkpointer import connect_checkpointer, close_checkpointer
from app.repository.criteria_upload_repository import CriteriaUploadRepository
# ... other existing imports (routers, etc.)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Existing startup — unchanged
    await connect_to_mongo(app)
    await connect_checkpointer(app)

    # NEW — one-time TTL index setup for the criteria-upload buffer.
    # Must run AFTER connect_to_mongo (needs app.state.mongo_db to
    # exist). Safe to call on every startup — create_index is a
    # no-op if the index already exists with the same spec.
    criteria_upload_repo = CriteriaUploadRepository(app.state.mongo_db)
    await criteria_upload_repo.ensure_indexes()

    yield

    # Existing shutdown — unchanged
    await close_mongo_connection(app)
    await close_checkpointer(app)


app = FastAPI(lifespan=lifespan)

# ... existing router includes (app.include_router(...))