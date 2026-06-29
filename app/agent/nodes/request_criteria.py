# app/agent/nodes/request_criteria.py
#
# Runs when stage == "awaiting_criteria". Handles two cases in one
# combined LLM call:
#   1. User's message already contains criteria → extract, advance
#      stage to awaiting_criteria_confirmation
#   2. No criteria present → ask for them, stage unchanged
#
# UPDATED: now also captures optional, user-provided weighting per
# the verbatim spec ("Prompt the user to upload or paste their
# evaluation criteria (objectives, requirements, scoring rubric,
# weighting, etc.)" / "If criteria include weighting, calculate
# weighted totals"). Weighting is OPTIONAL and purely user-driven —
# the agent never invents or assumes weights. If the user doesn't
# mention them, has_weighting stays False and run_evaluation uses a
# simple equal-weighted total, exactly as before this change.
#
# TOKEN LOGGING NOTE: see original header — with_structured_output
# requires include_raw=True to expose usage_metadata, with a
# defensive fallback for the open langchain#35041 risk.

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field

from app.agent.context import AgentContext
from app.agent.state import RFPAnalyzerState
from app.llm import llm
from app.repository.token_usage_repository import TokenUsageRepository

logger = logging.getLogger("app.agent.nodes.request_criteria")

_SYSTEM_PROMPT = """You help collect evaluation criteria for a proposal/RFP scoring tool.

Look at the user's message. Determine:
- Does it contain actual evaluation criteria (things to score a document against, e.g. "technical approach", "pricing", "timeline")?
- If yes: extract them as a clean, readable list.
- Did the user ALSO explicitly assign weights/percentages to any criteria (e.g. "technical approach (40%)", "cost weighted at 35%")? Weighting is OPTIONAL — most users will NOT provide it. Only set has_weighting=True if weights are explicitly and unambiguously stated for the criteria. Do not infer or invent weights that weren't given.
- If no criteria present at all (e.g. "let's start", generic messages): there are no criteria present.

Respond using the structured format provided."""


class CriteriaExtraction(BaseModel):
    criteria_found: bool = Field(
        description="True if the user's message contains actual evaluation criteria"
    )
    extracted_criteria: str = Field(
        default="",
        description="The criteria, cleanly formatted as a list. Empty if criteria_found is False.",
    )
    has_weighting: bool = Field(
        default=False,
        description="True ONLY if the user explicitly assigned weights/percentages to criteria. False by default — never inferred.",
    )
    weights: dict[str, float] = Field(
        default_factory=dict,
        description="Criterion name to weight (as a fraction, e.g. 0.4 for 40%), ONLY if has_weighting is True. Empty otherwise.",
    )


async def request_criteria(
    state: RFPAnalyzerState, runtime: Runtime[AgentContext]
) -> dict:
    last_message = state["messages"][-1].content if state["messages"] else ""

    structured_llm = llm.with_structured_output(
        CriteriaExtraction, include_raw=True
    )

    try:
        result = await structured_llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=last_message),
        ])
        parsed: CriteriaExtraction = result["parsed"]
        raw_message = result.get("raw")

        if raw_message is not None and getattr(raw_message, "usage_metadata", None):
            token_repo = TokenUsageRepository(runtime.context.db)
            await token_repo.record_llm_call(
                session_id=state["session_id"],
                user_id=state["user_id"],
                node="request_criteria",
                response=raw_message,
            )
        else:
            logger.warning(
                "request_criteria: no usage_metadata available — "
                "token usage not logged for this call."
            )

    except Exception as e:
        logger.warning(
            "request_criteria: include_raw path failed (%s) — "
            "falling back to plain structured output without token "
            "logging for this call.", e,
        )
        plain_structured_llm = llm.with_structured_output(CriteriaExtraction)
        parsed = await plain_structured_llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=last_message),
        ])

    if parsed.criteria_found:
        logger.info(
            "Criteria extracted for session %s (weighted=%s)",
            state["session_id"], parsed.has_weighting,
        )
        return {
            "criteria": parsed.extracted_criteria,
            "criteria_confirmed": False,
            "stage": "awaiting_criteria_confirmation",
            "criteria_weights": parsed.weights if parsed.has_weighting else {},
            "response_to_user": (
                f"Got it. Here's what I have for your evaluation criteria:\n\n"
                f"{parsed.extracted_criteria}\n\n"
                f"Does this include everything you want in the evaluation? "
                f"Let me know if anything needs to be added or adjusted."
            ),
        }

    return {
        "response_to_user": (
            "Please share your evaluation criteria — you can paste them "
            "directly, or describe what you'd like the proposal scored on "
            "(for example: technical approach, cost, timeline). You can "
            "optionally assign weights if some criteria matter more than "
            "others."
        ),
    }