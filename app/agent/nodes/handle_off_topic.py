# app/agent/nodes/handle_off_topic.py

import logging

from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.state import RFPAnalyzerState
from app.llm import llm
from app.repository.token_usage_repository import TokenUsageRepository

logger = logging.getLogger("app.agent.nodes.handle_off_topic")

_SYSTEM_PROMPT = """You are a proposal/RFP evaluation assistant. The user just sent a message unrelated to evaluating proposals/RFPs against criteria.

Politely and briefly (1-2 sentences) explain you're focused specifically on proposal/RFP evaluation, and that you're ready to help with that whenever they are. Do not be preachy or repeat this explanation verbatim every time — vary the phrasing naturally. Do not use markdown formatting."""

_STATIC_FALLBACK = (
    "I'm focused specifically on evaluating proposals and RFP responses "
    "against criteria you provide. I'd be glad to help with an evaluation "
    "whenever you're ready."
)


async def handle_off_topic(
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
                node="handle_off_topic",
                response=response,
            )
    except Exception as e:
        logger.warning(
            "handle_off_topic: LLM call failed (%s) — using static fallback.", e
        )
        text = _STATIC_FALLBACK

    return {"response_to_user": text}