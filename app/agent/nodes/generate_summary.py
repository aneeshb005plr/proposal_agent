# app/agent/nodes/generate_summary.py
#
# UPDATED: risk words are now injected directly into the generation
# prompt, so the model avoids them WHILE writing — matching the
# established pattern from Proposal Content Generator ("compliance
# is enforced during generation, not after... validation is a
# lightweight safety net, not the primary enforcement mechanism").
#
# CORRECTED from an earlier, wrong design: validate_output firing
# and surfacing an unexplained word list to the END USER was a
# mistake — the words are OUR generated text, not the user's
# responsibility to review. This fix addresses the actual problem
# at its source (generation) rather than reporting it after the
# fact as if it were the user's issue to solve.

import logging

from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.state import RFPAnalyzerState
from app.knowledge.risk_words import get_risk_words
from app.llm import llm
from app.repository.token_usage_repository import TokenUsageRepository
from app.services.knowledge_service import retrieve_relevant_knowledge

logger = logging.getLogger("app.agent.nodes.generate_summary")

_SYSTEM_PROMPT = """You are writing an Executive Summary for a proposal/RFP evaluation.

Relevant PwC tone-of-voice and brand writing guidance, retrieved from official sources:
{knowledge_context}

The following words/phrases are NOT permitted anywhere in your output. Do not use them under any circumstances — rephrase around them entirely:
{risk_words_list}

You are given the per-criterion scores and rationales below. Write ONE concise paragraph that covers, in this order:
1. Overall performance (including total score and percentage)
2. Key strengths (the specific criteria that scored well, with brief reasons)
3. Key weaknesses (the specific criteria that scored poorly, with brief reasons — distinct from gaps below; weaknesses are areas that were addressed but inadequately)
4. Major gaps (criteria with little to no evidence found at all)
5. Recommended focus areas / opportunities for improvement

Rules:
- Follow the PwC tone-of-voice guidance above for HOW you write this.
- Remain neutral, objective, and professional throughout.
- Avoid subjective or emotional language (no words like "impressive", "disappointing", "excellent", "concerning" — describe facts and scores plainly).
- Do NOT use any of the restricted words/phrases listed above, anywhere in your response.
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

    risk_data = get_risk_words()
    risk_words_list = ", ".join(f'"{w}"' for w in risk_data.blocked)

    prompt = _SYSTEM_PROMPT.format(
        knowledge_context=knowledge_context,
        risk_words_list=risk_words_list,
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