# app/agent/nodes/classify_intent.py
#
# Runs immediately after load_session_state, before route_by_stage,
# on EVERY turn. Confirmed against current LangChain documentation
# (thinking-in-langgraph guide) as the standard pattern: a dedicated
# classification node feeding conditional routing — NOT a supervisor
# architecture, which is reserved for genuinely distinct sub-agent
# capabilities (RFP Analyzer has exactly one capability — evaluation
# — so a supervisor would be solving a problem we don't have).
#
# Deterministic check FIRST for obvious greetings/farewells/thanks —
# zero cost, zero latency. LLM fallback ONLY for ambiguous input.
# This layered approach is a recognized real-world pattern, not an
# invented shortcut — confirmed from multiple current sources
# describing "a rules engine or keyword matcher for simple intents"
# ahead of an LLM-based classifier.
#
# Low-confidence classification defaults to "task_relevant" rather
# than risking a genuinely important message being silently
# misrouted into a throwaway social/off_topic handler — confirmed
# against the explicit guidance "never assume intent with low
# confidence."

import logging
import re

from langchain_core.messages import HumanMessage

from app.agent.state import RFPAnalyzerState
from app.llm import llm

logger = logging.getLogger("app.agent.nodes.classify_intent")

# Deliberately narrow — only catches the OBVIOUS, unambiguous cases.
# Anything not clearly matching one of these falls through to the
# LLM classifier below, rather than risk a false-positive match on
# a message that merely contains "hi" as a substring of something
# task-relevant (e.g. "hi, here are my criteria: ...").
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
- off_topic: a request unrelated to evaluating an RFP/proposal against criteria (e.g. general knowledge questions, unrelated tasks, jokes)
- task_relevant: anything related to providing evaluation criteria, confirming criteria, uploading/referencing a document, or asking about evaluation results

If genuinely unsure, respond with task_relevant.

User message: "{message}"

Category:"""


async def classify_intent(state: RFPAnalyzerState) -> dict:
    """
    Returns a partial update with intent classification stored
    transiently — NOT added to RFPAnalyzerState's persisted schema,
    since this is a per-turn routing decision, not something that
    needs to survive across turns or be checkpointed. We add it as
    an ephemeral field here; see note in graph.py on how
    conditional_edge reads it without it needing to be a permanent
    state field.
    """
    last_message = state["messages"][-1].content if state["messages"] else ""
    stripped = last_message.strip()

    if _GREETING_PATTERN.match(stripped) or _THANKS_PATTERN.match(stripped):
        intent = "social"
    elif _FAREWELL_PATTERN.match(stripped):
        intent = "social"
    else:
        intent = await _classify_with_llm(stripped)

    logger.info("Classified intent as '%s' for session %s", intent, state["session_id"])
    return {"intent": intent}


async def _classify_with_llm(message: str) -> str:
    prompt = _CLASSIFICATION_PROMPT.format(message=message)
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    raw = response.content.strip().lower()

    valid_intents = {"social", "off_topic", "task_relevant"}
    if raw not in valid_intents:
        logger.warning(
            "classify_intent: LLM returned unexpected value %r — "
            "defaulting to task_relevant (low-confidence default)",
            raw,
        )
        return "task_relevant"

    return raw