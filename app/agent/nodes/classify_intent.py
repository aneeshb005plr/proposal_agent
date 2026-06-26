# app/agent/nodes/classify_intent.py
#
# Runs after load_session_state, on EVERY turn. Deterministic check
# FIRST for obvious greetings/farewells/thanks — zero LLM cost. LLM
# fallback only for ambiguous input, with token usage logged via
# TokenUsageRepository — consistent with every other LLM-calling
# node in this graph, so token visibility has no gaps.
#
# NOTE: this node currently uses the same llm client as generation
# nodes. Flagged as a candidate for a cheaper/smaller model if one
# becomes available via the PwC GenAI shared service — classification
# is exactly the kind of mechanical task that doesn't need the same
# model capability as actual reasoning/generation. Not yet acted on
# pending confirmation a cheaper model option exists.

import logging
import re

from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.state import RFPAnalyzerState
from app.llm import llm
from app.repository.token_usage_repository import TokenUsageRepository

logger = logging.getLogger("app.agent.nodes.classify_intent")

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

_CLASSIFICATION_PROMPT = """Classify the user's message into exactly one category. Respond with ONLY the category name.

Categories:
- social: greetings, thanks, farewells, small talk unrelated to the task
- off_topic: a request unrelated to evaluating an RFP/proposal against criteria
- task_relevant: anything related to providing/confirming evaluation criteria, uploading/referencing a document, or asking about evaluation results

If genuinely unsure, respond with task_relevant.

User message: "{message}"

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
        # Deterministic match — no LLM call, nothing to log.
        intent = "social"
    else:
        intent = await _classify_with_llm(stripped, state, runtime)

    logger.info("Classified intent as '%s' for session %s", intent, state["session_id"])
    return {"intent": intent}


async def _classify_with_llm(
    message: str, state: RFPAnalyzerState, runtime: Runtime[AgentContext]
) -> str:
    prompt = _CLASSIFICATION_PROMPT.format(message=message)
    response = await llm.ainvoke([HumanMessage(content=prompt)])

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