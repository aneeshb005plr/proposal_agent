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

_CLASSIFICATION_PROMPT = """Classify the user's LATEST message into exactly one category, using the recent conversation below for context (the latest message is the one you're classifying — earlier messages are context only).

Categories:
- social: greetings, thanks, farewells, small talk unrelated to the task
- off_topic: a request unrelated to evaluating an RFP/proposal against criteria
- task_relevant: anything related to providing/confirming evaluation criteria, uploading/referencing a document, confirming or adjusting something the agent just asked about, or asking about evaluation results

A short reply like "yes", "looks good", or "add X" should be classified based on what it's responding to in the conversation, not in isolation.

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

    response = await llm.ainvoke(
        [SystemMessage(content=_CLASSIFICATION_PROMPT)] + recent_messages
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