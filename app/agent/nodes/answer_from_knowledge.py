# app/agent/nodes/answer_from_knowledge.py
#
# REUSABLE PATTERN — designed to be copy-paste-adaptable across
# future agents, per the quicksuite reusable infrastructure
# reference doc's philosophy. Core principle: NEVER let the model
# answer from its own general knowledge when the question implies
# "does our documented material say X" — retrieve first, and
# explicitly decline if nothing relevant comes back, rather than
# guessing or blending in outside knowledge.

import logging

from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.state import RFPAnalyzerState
from app.llm import llm
from app.repository.token_usage_repository import TokenUsageRepository
from app.services.knowledge_service import retrieve_relevant_knowledge

logger = logging.getLogger("app.agent.nodes.answer_from_knowledge")

_RELEVANCE_THRESHOLD = 3  # minimum chunks required before attempting
                           # an answer at all — below this, decline
                           # rather than stretch thin results into
                           # an answer

_SYSTEM_PROMPT = """You answer questions using ONLY the reference material provided below — never your own general knowledge, never assumptions, never anything not explicitly present in this material.

Reference material:
{knowledge_chunks}

Rules:
- If the material clearly answers the question, answer concisely and cite which part of the material you're drawing from.
- If the material is only partially relevant, or doesn't clearly answer the question, say so plainly rather than filling gaps with outside knowledge — partial information is still worth sharing, but be explicit about what's missing.
- If the material contains NOTHING relevant to the question, say plainly that this isn't something you have information on, and do not attempt to answer from general knowledge instead.
- Never present outside/general knowledge as if it came from this material."""

_NO_KNOWLEDGE_FALLBACK = (
    "I don't have information on that in what's available to me. "
    "I'm best suited to help with evaluating proposals and RFP "
    "responses against criteria you provide."
)


async def answer_from_knowledge(
    state: RFPAnalyzerState, runtime: Runtime[AgentContext]
) -> dict:
    last_message = state["messages"][-1].content if state["messages"] else ""

    chunks = await retrieve_relevant_knowledge(
        runtime.context.sync_db, query=last_message, k=5
    )

    if len(chunks) < _RELEVANCE_THRESHOLD:
        logger.info(
            "answer_from_knowledge: only %d chunk(s) retrieved for "
            "session %s — below threshold, declining rather than "
            "answering from thin/no grounding.",
            len(chunks), state["session_id"],
        )
        return {"response_to_user": _NO_KNOWLEDGE_FALLBACK}

    knowledge_text = "\n\n".join(c["text"] for c in chunks)
    system_prompt = _SYSTEM_PROMPT.format(knowledge_chunks=knowledge_text)

    response = await llm.ainvoke([
        SystemMessage(content=system_prompt),
        state["messages"][-1],
    ])

    if getattr(response, "usage_metadata", None):
        token_repo = TokenUsageRepository(runtime.context.db)
        await token_repo.record_llm_call(
            session_id=state["session_id"],
            user_id=state["user_id"],
            node="answer_from_knowledge",
            response=response,
        )

    return {"response_to_user": response.content}