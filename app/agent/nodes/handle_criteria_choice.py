# app/agent/nodes/handle_criteria_choice.py
#
# Handles the user's reply to "same criteria or new ones?" — the
# intermediate stage reset_for_new_document routes to when the
# choice wasn't specified upfront.

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field

from app.agent.context import AgentContext
from app.agent.state import RFPAnalyzerState
from app.llm import llm
from app.repository.token_usage_repository import TokenUsageRepository

logger = logging.getLogger("app.agent.nodes.handle_criteria_choice")

_SYSTEM_PROMPT = """The user was asked whether they want to reuse their existing evaluation criteria for a new document, or provide new criteria. Classify their reply.

Respond using the structured format provided."""


class CriteriaChoiceResult(BaseModel):
    keep_criteria: bool = Field(
        description="True if the user wants to reuse the existing criteria, False if they want new ones"
    )


async def handle_criteria_choice(
    state: RFPAnalyzerState, runtime: Runtime[AgentContext]
) -> dict:
    last_message = state["messages"][-1].content if state["messages"] else ""

    structured_llm = llm.with_structured_output(
        CriteriaChoiceResult, include_raw=True
    )

    try:
        result = await structured_llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=last_message),
        ])
        parsed: CriteriaChoiceResult = result["parsed"]
        raw_message = result.get("raw")

        if raw_message is not None and getattr(raw_message, "usage_metadata", None):
            token_repo = TokenUsageRepository(runtime.context.db)
            await token_repo.record_llm_call(
                session_id=state["session_id"],
                user_id=state["user_id"],
                node="handle_criteria_choice",
                response=raw_message,
            )
    except Exception as e:
        logger.warning(
            "handle_criteria_choice: include_raw path failed (%s) — "
            "falling back without token logging.", e,
        )
        plain_structured_llm = llm.with_structured_output(CriteriaChoiceResult)
        parsed = await plain_structured_llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=last_message),
        ])

    if parsed.keep_criteria:
        return {
            "stage": "awaiting_document",
            "response_to_user": (
                "Great — I'll keep the same criteria. Please upload "
                "the new document when you're ready."
            ),
        }

    return {
        "criteria": None,
        "criteria_confirmed": False,
        "criteria_weights": {},
        "stage": "awaiting_criteria",
        "response_to_user": "No problem — please share the new evaluation criteria.",
    }