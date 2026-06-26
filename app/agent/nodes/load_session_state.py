# app/agent/nodes/load_session_state.py
#
# Always the first real node (after START). Syncs the durable
# MongoDB session record into graph state. Does NOT re-check
# ownership (already verified at the route layer before the graph
# was invoked). Does NOT write back to MongoDB — read-only sync.

import logging

from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.state import RFPAnalyzerState
from app.repository.session_repository import SessionRepository

logger = logging.getLogger("app.agent.nodes.load_session_state")


async def load_session_state(
    state: RFPAnalyzerState, runtime: Runtime[AgentContext]
) -> dict:
    repo = SessionRepository(runtime.context.db)
    session = await repo.get_session(state["session_id"])

    if session is None:
        raise RuntimeError(
            f"load_session_state: session {state['session_id']} not "
            f"found — this should have been caught at the route layer "
            f"before the graph was invoked."
        )

    return {
        "document_confirmed": session["document_confirmed"],
    }