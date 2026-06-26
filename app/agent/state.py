# app/agent/state.py
#
# RFPAnalyzerState — the shared state every node reads from and
# writes partial updates to. Field-by-field reducer choices follow
# the confirmed current LangGraph guidance: accumulate only what
# genuinely needs to grow across turns (messages); everything else
# is plain overwrite, including workflow position and confirmation
# flags, which must reflect the CURRENT state, not a history of it.
#
# Large content (submission document chunks) is deliberately NOT
# stored here — only session_id and filenames. The scoring node
# fetches actual chunk content from submission_chunks by session_id
# when it runs. This avoids the confirmed real production failure
# mode where checkpoints balloon in size because raw content was
# stored directly in state (180KB checkpoints, 400ms+ writes,
# observed in a real LangGraph production deployment).

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


class RFPAnalyzerState(TypedDict):
    # Accumulates — conversation history. Reducer appends, never
    # overwrites wholesale.
    messages: Annotated[list[BaseMessage], add_messages]

    # Identity and ownership — populated once at the start of every
    # turn by load_session_state, from the real MongoDB session
    # record (the durable source of truth — see session_repository.py)
    session_id: str
    user_id: str

    # Plain overwrite — current position in the workflow
    stage: Stage

    # Plain overwrite — criteria text is REPLACED wholesale on a new
    # submission, never merged/accumulated. A mid-flow change is a
    # deliberate last-write-wins replacement, not an addition.
    criteria: Optional[str]
    criteria_confirmed: bool

    # Add to RFPAnalyzerState:
    intent: Optional[Literal["social", "off_topic", "task_relevant"]]

    # Plain overwrite — mirrors the MongoDB session record's
    # document_confirmed field, kept in state so routing logic can
    # read it without a DB round-trip on every node
    document_confirmed: bool
    uploaded_filenames: list[str]

    # Plain overwrite — populated only once evaluation actually runs.
    # None until then, never partially filled.
    scoring_results: Optional[dict]
    executive_summary: Optional[str]

    # Plain overwrite — what gets sent back to the caller (Streamlit/
    # Teams) at the end of this turn. Set by whichever node produced
    # the user-facing response for this turn.
    response_to_user: Optional[str]