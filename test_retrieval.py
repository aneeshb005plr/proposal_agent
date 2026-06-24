import asyncio
from fastapi import FastAPI
from app.database import connect_to_mongo, close_mongo_connection
from app.services import knowledge_service

async def test():
    app = FastAPI()
    await connect_to_mongo(app)
    sync_db = app.state.mongo_sync_db

    # Use a query that should genuinely match something in your
    # real synced knowledge files — e.g. something you know is in
    # the brand/tone-of-voice/proposal style documents
    query = "what tone of voice should proposals use"

    results = await knowledge_service.retrieve_relevant_knowledge(
        sync_db, query=query, k=5
    )

    print(f"\nFound {len(results)} result(s)\n")
    for i, doc in enumerate(results):
        print(f"--- Result {i+1} ---")
        print("Content (first 300 chars):", doc.page_content[:300])
        print("Metadata:", doc.metadata)
        print()

    await close_mongo_connection(app)

asyncio.run(test())