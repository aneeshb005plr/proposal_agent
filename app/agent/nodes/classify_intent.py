# app/agent/nodes/classify_intent.py
#
# Runs after load_session_state, on EVERY turn. Deterministic check
# FIRST for obvious greetings/farewells/thanks — zero LLM cost. LLM
# fallback only for ambiguous input.
#
# CORRECTED: now passes a small window of recent messages, not just
# the bare latest message. Found via real testing: a short
# confirmation-style reply ("yes that's right") was being classified
# correctly, but only by accident — the low-confidence-defaults-to-
# task_relevant fallback happened to catch it, not genuine
# understanding. A terser reply ("yes", one word) could plausibly be
# misclassified without context. Same fix pattern as
# recap_and_confirm.py — small window, not full history, consistent
# with avoiding the unbounded-state-growth failure mode found
# earlier in this build.

import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.state import RFPAnalyzerState
from app.llm import llm
from app.repository.token_usage_repository import TokenUsageRepository

logger = logging.getLogger("app.agent.nodes.classify_intent")

_RECENT_MESSAGE_WINDOW = 3

_GREETING_PATTERN = re.compile(
    r"^\s*(hi|hello|hey|good morning|good afternoon|good evening)[\s!.,]*$",
    re.IGNORECASE,
)
_FAREWELL_PATTERN = re.compile(
    r"^\s*(bye|goodbye|see you|talk later|that'?s all|i'?m done)[\s!.,]*$",
    re.IGNORECASE,
)
_THANKS_PATTERN = re.compile(
    r"^\s*(thanks|thank you|thx|appreciated?)[\s!.,]*$",
    re.IGNORECASE,
)

_CLASSIFICATION_PROMPT_TEMPLATE = """Classify the user's LATEST message into exactly one category, using the recent conversation below for context.

The agent is a proposal/RFP evaluation tool. Its current stage: {stage}
- "awaiting_criteria": expecting the user to provide evaluation criteria (a list of things to score against, e.g. "pricing, timeline")
- "awaiting_criteria_confirmation": expecting a confirmation or adjustment of proposed criteria
- "awaiting_document": expecting a document upload acknowledgment
- "evaluated": expecting a follow-up about a completed evaluation

Categories:
- social: greetings, thanks, farewells, small talk
- off_topic: unrelated to evaluating an RFP/proposal, AND unrelated to this agent's own documented reference material (company policies, evaluation standards, proposal templates, internal guidance specifically indexed for this agent). This is the DEFAULT for any general knowledge question — current events, public figures, world facts, general trivia, or anything a generic search engine would answer. Only use knowledge_question, never off_topic's neighbor, when in doubt.
- knowledge_question: SPECIFICALLY a question about content that would plausibly live in THIS agent's own indexed SharePoint knowledge base — e.g. "what's our standard fee escalation clause", "do we have a template for X", "what does our policy say about Y". Do NOT use this category for general-knowledge questions (world facts, public figures, current events, anything not specific to this agent's own internal reference material) — those are off_topic, even if a knowledge base technically exists and COULD theoretically contain unrelated content. When genuinely uncertain whether a question is asking about this agent's specific internal material versus general knowledge, default to off_topic, not knowledge_question.
- task_relevant: providing/confirming criteria, uploading/referencing a document, confirming/adjusting the current step, or asking about evaluation results already produced

IMPORTANT: if the latest message plausibly matches what the CURRENT STAGE is expecting (e.g. a list of criteria while awaiting_criteria), classify it task_relevant even if the recent conversation included an unrelated off-topic exchange before it — each message should be judged on its own content first, not on the tone of what came immediately before it.

If genuinely unsure, respond with task_relevant.

Category:"""


async def classify_intent(
    state: RFPAnalyzerState, runtime: Runtime[AgentContext]
) -> dict:
    last_message = state["messages"][-1].content if state["messages"] else ""
    stripped = last_message.strip()

    if (
        _GREETING_PATTERN.match(stripped)
        or _THANKS_PATTERN.match(stripped)
        or _FAREWELL_PATTERN.match(stripped)
    ):
        # Deterministic match — no LLM call, nothing to log. These
        # patterns are checked on the bare message regardless of
        # context, since "hi" is "hi" whatever came before it.
        intent = "social"
    else:
        intent = await _classify_with_llm(state, runtime)

    logger.info("Classified intent as '%s' for session %s", intent, state["session_id"])
    return {"intent": intent}


async def _classify_with_llm(
    state: RFPAnalyzerState, runtime: Runtime[AgentContext]
) -> str:
    recent_messages = state["messages"][-_RECENT_MESSAGE_WINDOW:]
    system_prompt = _CLASSIFICATION_PROMPT_TEMPLATE.format(stage=state["stage"])

    response = await llm.ainvoke(
        [SystemMessage(content=system_prompt)] + recent_messages
    )
    
    token_repo = TokenUsageRepository(runtime.context.db)
    await token_repo.record_llm_call(
        session_id=state["session_id"],
        user_id=state["user_id"],
        node="classify_intent",
        response=response,
    )

    raw = response.content.strip().lower()
    valid_intents = {"social", "off_topic", "task_relevant"}
    if raw not in valid_intents:
        logger.warning(
            "classify_intent: LLM returned unexpected value %r — "
            "defaulting to task_relevant", raw,
        )
        return "task_relevant"

    return raw