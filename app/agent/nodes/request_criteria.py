# app/agent/nodes/request_criteria.py
#
# Runs when stage == "awaiting_criteria". Handles two cases in one
# combined LLM call:
#   1. User's message already contains criteria → extract, advance
#      stage to awaiting_criteria_confirmation
#   2. No criteria present → ask for them, stage unchanged
#
# TOKEN LOGGING NOTE: with_structured_output()'s plain return value
# is the parsed Pydantic object itself, which structurally has no
# usage_metadata field — confirmed, this isn't a gap to search
# around, it's how the method works. include_raw=True is the
# documented fix (returns {"raw": AIMessage, "parsed": ...}), and is
# used here. HOWEVER: a recent, currently-open bug (langchain#35041,
# Feb 2026) reports include_raw leaking into the underlying OpenAI
# SDK call and being rejected, specifically for langchain-openai —
# our exact integration path via the PwC GenAI shared service. NOT
# CONFIRMED to affect our specific installed versions — check with
# `pip show langchain-openai langchain-core` before trusting this
# fully. Wrapped defensively below so a failure here degrades to
# "no token logging for this call" rather than crashing the node.

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field

from app.agent.context import AgentContext
from app.agent.state import RFPAnalyzerState
from app.llm import llm
from app.repository.token_usage_repository import TokenUsageRepository

logger = logging.getLogger("app.agent.nodes.request_criteria")

_SYSTEM_PROMPT = """You help collect evaluation criteria for a proposal/RFP scoring tool.

Look at the user's message. Determine:
- Does it contain actual evaluation criteria (things to score a document against, e.g. "technical approach", "pricing", "timeline", possibly with weights)?
- If yes: extract them as a clean, readable list.
- If no (e.g. they just said "let's start", "I want to evaluate something", or anything generic): there are no criteria present.

Respond using the structured format provided."""


class CriteriaExtraction(BaseModel):
    criteria_found: bool = Field(
        description="True if the user's message contains actual evaluation criteria"
    )
    extracted_criteria: str = Field(
        default="",
        description="The criteria, cleanly formatted as a list. Empty if criteria_found is False.",
    )


async def request_criteria(
    state: RFPAnalyzerState, runtime: Runtime[AgentContext]
) -> dict:
    last_message = state["messages"][-1].content if state["messages"] else ""

    structured_llm = llm.with_structured_output(
        CriteriaExtraction, include_raw=True
    )

    try:
        result = await structured_llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=last_message),
        ])
        parsed: CriteriaExtraction = result["parsed"]
        raw_message = result.get("raw")

        if raw_message is not None and getattr(raw_message, "usage_metadata", None):
            token_repo = TokenUsageRepository(runtime.context.db)
            await token_repo.record_llm_call(
                session_id=state["session_id"],
                user_id=state["user_id"],
                node="request_criteria",
                response=raw_message,
            )
        else:
            logger.warning(
                "request_criteria: no usage_metadata available on raw "
                "response — token usage not logged for this call. "
                "See known include_raw issue (langchain#35041)."
            )

    except Exception as e:
        # include_raw may itself fail outright if the bug applies to
        # our installed versions — fall back to plain
        # with_structured_output (no token logging this call) rather
        # than crash the node entirely.
        logger.warning(
            "request_criteria: include_raw path failed (%s) — "
            "falling back to plain structured output without token "
            "logging for this call.", e,
        )
        plain_structured_llm = llm.with_structured_output(CriteriaExtraction)
        parsed = await plain_structured_llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=last_message),
        ])

    if parsed.criteria_found:
        logger.info("Criteria extracted for session %s", state["session_id"])
        return {
            "criteria": parsed.extracted_criteria,
            "criteria_confirmed": False,
            "stage": "awaiting_criteria_confirmation",
            "response_to_user": (
                f"Got it. Here's what I have for your evaluation criteria:\n\n"
                f"{parsed.extracted_criteria}\n\n"
                f"Does this include everything you want in the evaluation? "
                f"Let me know if anything needs to be added or adjusted."
            ),
        }

    return {
        "response_to_user": (
            "Please share your evaluation criteria — you can paste them "
            "directly, or describe what you'd like the proposal scored on "
            "(for example: technical approach, cost, timeline)."
        ),
    }