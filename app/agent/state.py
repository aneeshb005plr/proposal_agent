from typing import Annotated, Literal, Optional
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


Stage = Literal[
    "awaiting_criteria",
    "awaiting_criteria_confirmation",
    "awaiting_document",
    "ready_to_evaluate",
    "evaluated",
]


class RFPAnalyzerState(TypedDict):
    # Accumulates — conversation history, never overwritten wholesale
    messages: Annotated[list[BaseMessage], add_messages]

    # Plain overwrite — current position in the workflow
    stage: Stage

    # Plain overwrite — the criteria themselves, as raw user-provided
    # text/structure. Only replaced wholesale on an explicit new
    # submission, never merged/accumulated.
    criteria: Optional[str]
    criteria_confirmed: bool

    # Plain overwrite — document_confirmed mirrors what's already
    # tracked in MongoDB (sessions collection) but is kept in graph
    # state too, since the graph's own routing logic needs to read
    # it on every turn without a separate DB round-trip per node.
    document_confirmed: bool

    # NOT the chunks themselves — just identifiers. Per the confirmed
    # production lesson: large content must not live in checkpointed
    # state. The actual submission_chunks are fetched from MongoDB,
    # by session_id, inside the scoring node only when needed.
    session_id: str
    uploaded_filenames: list[str]

    # Plain overwrite — populated only once evaluation actually runs
    scoring_results: Optional[dict]
    executive_summary: Optional[str]