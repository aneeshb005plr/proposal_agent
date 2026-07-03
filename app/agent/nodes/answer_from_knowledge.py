# app/agent/nodes/answer_from_knowledge.py

import logging

from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.state import RFPAnalyzerState
from app.llm import llm
from app.repository.token_usage_repository import TokenUsageRepository
from app.services.knowledge_service import retrieve_relevant_knowledge

logger = logging.getLogger("app.agent.nodes.answer_from_knowledge")

_RELEVANCE_THRESHOLD = 2

_SYSTEM_PROMPT = """You answer questions using ONLY the reference material provided below — never your own general knowledge, never assumptions, never anything not explicitly present in this material.

Reference material:
{knowledge_chunks}

CRITICAL: Before answering, check whether the reference material actually addresses the question's SUBJECT MATTER at all — not just whether it contains any text. If the material is about a completely different topic than what's being asked (e.g. the question is about a public figure or world fact, and the material is about proposal evaluation policy), you MUST say you don't have information on that topic. Do NOT construct an answer by loosely connecting unrelated material to the question, and do NOT claim the material "mentions" something related to the question unless it explicitly and directly does.

Rules:
- If the material clearly and directly answers the question, answer concisely and cite which part you're drawing from.
- If the material is only tangentially or coincidentally related, treat this the same as having no relevant material — decline, do not stretch a loose connection into an answer.
- If the material contains NOTHING relevant to the question's actual subject, say plainly that this isn't something you have information on.
- Never present outside/general knowledge as if it came from this material, and never attribute a claim to "the material" unless it is directly, verifiably present there."""

_NO_KNOWLEDGE_FALLBACK = (
    "I don't have information on that in what's available to me. "
    "I'm best suited to help with evaluating proposals and RFP "
    "responses against criteria you provide."
)


async def answer_from_knowledge(
    state: RFPAnalyzerState, runtime: Runtime[AgentContext]
) -> dict:
    last_message = state["messages"][-1].content if state["messages"] else ""

    # FIXED: retrieve_relevant_knowledge takes sync_db (sync client),
    # returns list[Document] — NOT a dict with a "text" key. Access
    # chunk content via .page_content, per langchain_core.documents.Document.
    chunks = await retrieve_relevant_knowledge(
        runtime.context.sync_db, query=last_message, k=15
    )

    if len(chunks) < _RELEVANCE_THRESHOLD:
        logger.info(
            "answer_from_knowledge: only %d chunk(s) retrieved for "
            "session %s — below threshold, declining rather than "
            "answering from thin/no grounding.",
            len(chunks), state["session_id"],
        )
        return {"response_to_user": _NO_KNOWLEDGE_FALLBACK}

    knowledge_text = "\n\n".join(c.page_content for c in chunks)
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