# app/agent/state.py
#
# RFPAnalyzerState — shared state every node reads from and writes
# partial updates to. Reducer choices follow confirmed current
# LangGraph guidance: Annotated + add_messages for anything that
# must accumulate across turns; plain overwrite for everything that
# represents CURRENT position/status, not history.
#
# Large content (submission document chunks) is deliberately NOT
# stored here — only session_id and filenames. The scoring node
# fetches chunk content from submission_chunks by session_id when
# it runs. This avoids the confirmed real production failure mode
# where checkpoints balloon because raw content was stored directly
# in state (180KB checkpoints, 400ms+ writes, observed in a real
# LangGraph deployment).

from typing import Annotated, Literal, Optional

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

Stage = Literal[
    "awaiting_criteria",
    "awaiting_criteria_confirmation",
    "awaiting_document",
    "ready_to_evaluate",
    "evaluated",
]

Intent = Literal["social", "off_topic", "task_relevant"]


class RFPAnalyzerState(TypedDict):
    # Accumulates — conversation history. Reducer appends.
    messages: Annotated[list[BaseMessage], add_messages]

    # Identity and ownership — set once at the start of every turn
    # by load_session_state, sourced from the route layer's already-
    # verified session_id/user_id (ownership already checked before
    # the graph is ever invoked — this node does not re-check it).
    session_id: str
    user_id: str

    # Plain overwrite — set by classify_intent, read by the combined
    # router immediately after.
    intent: Optional[Intent]

    # Plain overwrite — current position in the workflow
    stage: Stage

    # Plain overwrite — REPLACED wholesale on a new submission, never
    # merged. A mid-flow change is a deliberate last-write-wins
    # replacement, per RFP Analyzer's own stated rule.
    criteria: Optional[str]
    criteria_confirmed: bool

    # Plain overwrite — mirrors the MongoDB session record so nodes
    # can read it without a DB round-trip mid-turn.
    document_confirmed: bool
    uploaded_filenames: list[str]

    criteria_weights: dict[str, float]   # empty dict if no weighting given


    validation_violations: list[dict]   # empty list if none found



    # Plain overwrite — populated only once evaluation actually runs.
    scoring_results: Optional[dict]
    executive_summary: Optional[str]

    # Plain overwrite — what gets sent back to the caller (Streamlit/
    # Teams) at the end of this turn.
    response_to_user: Optional[str]