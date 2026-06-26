# app/agent/nodes/load_session_state.py
#
# Always the first node. Syncs the durable MongoDB session record
# (sessions collection — the real source of truth for
# document_confirmed, uploaded_file_count) into graph state, so every
# subsequent node in this turn can read it directly from state
# without a separate DB call. Does NOT write back to MongoDB — this
# is a read-only sync at the start of a turn.

import logging

from pymongo.asynchronous.database import AsyncDatabase

from app.agent.state import RFPAnalyzerState
from app.repository.session_repository import SessionRepository

logger = logging.getLogger("app.agent.nodes.load_session_state")


async def load_session_state(
    state: RFPAnalyzerState, db: AsyncDatabase
) -> dict:
    """
    Reads the session record by session_id (already verified owned
    by user_id at the route layer before the graph was ever
    invoked — this node does not re-check ownership, it trusts the
    caller already did).

    Returns a partial update — only the fields this node is actually
    responsible for syncing.
    """
    repo = SessionRepository(db)
    session = await repo.get_session(state["session_id"])

    if session is None:
        # Should not happen if the route layer did its job — but
        # fail loudly rather than silently proceeding with stale/
        # missing state if it somehow does.
        raise RuntimeError(
            f"load_session_state: session {state['session_id']} "
            f"not found — this should have been caught at the route "
            f"layer before the graph was invoked."
        )

    return {
        "document_confirmed": session["document_confirmed"],
    }