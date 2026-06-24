# A standalone test script — NOT part of the app itself, just for
# manually verifying pipeline.py works against your temp SharePoint
# folder before wiring it into anything else (a scheduler, an API
# route, etc.)

import asyncio
from fastapi import FastAPI

from app.database import connect_to_mongo, close_mongo_connection
from app.knowledge.graph_client import graph_client
from app.knowledge.pipeline import sync_knowledge_base


async def test():
    # connect_to_mongo expects a FastAPI app instance, since it
    # stores connections on app.state — see app/database.py
    app = FastAPI()
    await connect_to_mongo(app)

    db = app.state.mongo_db  # the async database we need to pass in

    # graph_client also needs to be connected before we use it —
    # confirm this matches how you tested it standalone earlier
    await graph_client.connect()

    print("Running knowledge sync...")
    result = await sync_knowledge_base(db)

    print("\n=== Sync Result ===")
    print(result)
    if result.errors:
        print("\nErrors encountered:")
        for err in result.errors:
            print(f"  - {err}")

    # Confirm what actually landed in the database — this is the
    # part that tells us if it REALLY worked, not just "ran without
    # crashing"
    chunk_count = await db["knowledge_chunks"].count_documents({})
    print(f"\nTotal documents now in knowledge_chunks: {chunk_count}")

    sample = await db["knowledge_chunks"].find_one({})
    if sample:
        print("\n=== Sample chunk ===")
        print("text (first 200 chars):", sample.get("text", "")[:200])
        print("embedding length:", len(sample.get("embedding", [])))
        print("metadata keys:", [k for k in sample.keys() if k not in ("_id", "text", "embedding")])
    else:
        print("\nNo documents found in knowledge_chunks — nothing was inserted.")

    await graph_client.disconnect()
    await close_mongo_connection(app)


asyncio.run(test())