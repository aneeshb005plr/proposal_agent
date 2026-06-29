# app/agent/nodes/generate_summary.py
#
# UPDATED: now grounds the executive summary in REAL, retrieved
# tone-of-voice/brand knowledge chunks (already indexed and
# confirmed working via knowledge_repository.py), rather than only
# instructing "be neutral and professional" generically. This is
# the same retrieval-augmented-generation pattern Proposal Content
# Generator uses for its own drafts — grounding generation in
# PwC's actual written standard, not just the model's generic sense
# of "professional."

import logging

from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.state import RFPAnalyzerState
from app.llm import llm
from app.repository.token_usage_repository import TokenUsageRepository
from app.services.knowledge_service import retrieve_relevant_knowledge

logger = logging.getLogger("app.agent.nodes.generate_summary")

_SYSTEM_PROMPT = """You are writing an Executive Summary for a proposal/RFP evaluation.

Relevant PwC tone-of-voice and brand writing guidance, retrieved from official sources:
{knowledge_context}

You are given the per-criterion scores and rationales below. Write ONE concise paragraph that covers, in this order:
1. Overall performance
2. Strongest areas (cite the specific criteria that scored well, with brief reasons)
3. Most significant gaps (cite the specific criteria that scored poorly or had thin evidence)
4. Recommended focus areas for improvement

Rules:
- Follow the PwC tone-of-voice guidance above for HOW you write this.
- Remain neutral, objective, and professional throughout.
- Avoid subjective or emotional language (no words like "impressive", "disappointing", "excellent", "concerning" — describe facts and scores plainly).
- Base everything on the scores and rationales given — do not introduce new claims not supported by them.
- Write exactly ONE paragraph. No headers, no bullet points, no lists.

Scoring results:
{scoring_summary}"""


def _format_scoring_for_prompt(scoring_results: dict) -> str:
    lines = []
    for s in scoring_results.get("scores", []):
        lines.append(
            f"- {s['criterion']}: {s['score']}/5 (confidence: {s['confidence']}) "
            f"— {s['rationale']}"
        )
    total = scoring_results.get("total_score")
    max_possible = scoring_results.get("max_possible")
    percentage = scoring_results.get("percentage")
    lines.append(f"\nOverall: {total}/{max_possible} ({percentage}%)")
    return "\n".join(lines)


async def generate_summary(
    state: RFPAnalyzerState, runtime: Runtime[AgentContext]
) -> dict:
    scoring_results = state.get("scoring_results")

    if not scoring_results:
        logger.error(
            "generate_summary: no scoring_results found for session %s "
            "despite stage being evaluated", state["session_id"],
        )
        return {
            "executive_summary": (
                "Unable to generate a summary — no scoring results available."
            ),
        }

    knowledge_chunks = await retrieve_relevant_knowledge(
        runtime.context.sync_db,
        query="professional neutral objective tone of voice for proposal evaluation summaries",
        k=3,
    )
    knowledge_context = (
        "\n\n".join(c.page_content for c in knowledge_chunks)
        if knowledge_chunks
        else "(no specific guidance retrieved — use standard professional tone)"
    )

    prompt = _SYSTEM_PROMPT.format(
        knowledge_context=knowledge_context,
        scoring_summary=_format_scoring_for_prompt(scoring_results),
    )

    response = await llm.ainvoke([SystemMessage(content=prompt)])

    token_repo = TokenUsageRepository(runtime.context.db)
    await token_repo.record_llm_call(
        session_id=state["session_id"],
        user_id=state["user_id"],
        node="generate_summary",
        response=response,
    )

    logger.info("Executive summary generated for session %s", state["session_id"])

    return {"executive_summary": response.content}