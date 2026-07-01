# app/agent/nodes/handle_social.py

import logging

from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.state import RFPAnalyzerState
from app.llm import llm
from app.repository.token_usage_repository import TokenUsageRepository

logger = logging.getLogger("app.agent.nodes.handle_social")

_SYSTEM_PROMPT = """You are a proposal/RFP evaluation assistant. The user just sent a social message (greeting, thanks, farewell, small talk) — not a task-relevant request.

Reply warmly and briefly (1-2 sentences), staying in character as a proposal evaluation tool. If it fits naturally, gently note you're ready to help with evaluation criteria/documents whenever they are — but don't force this into every reply (e.g. a "thanks" doesn't need to be redirected back to work, a plain acknowledgment is fine).

Do not use markdown formatting. Do not ask a question unless it's a natural, brief one."""

_STATIC_FALLBACK = (
    "Glad to help! Let me know when you're ready to get started with an evaluation."
)


async def handle_social(
    state: RFPAnalyzerState, runtime: Runtime[AgentContext]
) -> dict:
    try:
        response = await llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            state["messages"][-1],
        ])
        text = response.content.strip() or _STATIC_FALLBACK

        if getattr(response, "usage_metadata", None):
            token_repo = TokenUsageRepository(runtime.context.db)
            await token_repo.record_llm_call(
                session_id=state["session_id"],
                user_id=state["user_id"],
                node="handle_social",
                response=response,
            )
    except Exception as e:
        # Never let a social reply fail the whole turn — fall back
        # to the original static response rather than erroring out
        # on what should be the cheapest, lowest-risk path in the
        # graph.
        logger.warning(
            "handle_social: LLM call failed (%s) — using static fallback.", e
        )
        text = _STATIC_FALLBACK

    return {"response_to_user": text}