# app/agent/nodes/validate_output.py
#
# Runs after generate_summary, checking the agent's OWN generated
# prose against risk_words — NOT the submission document itself
# (that's the user's content, not ours to police against PwC's
# internal compliance rules; see design discussion on why validation
# scope is limited to agent-generated text only).
#
# Checks TWO things:
#   1. generate_summary's executive_summary (entirely free prose)
#   2. run_evaluation's per-criterion rationale text (agent-written
#      sentences describing/citing the document, even though
#      tightly evidence-bound)
#
# Does NOT silently rewrite anything — flags violations visibly,
# consistent with Proposal Content Generator's approach and RFP
# Analyzer's own "draft-only, human review required" principle.
# Uses get_blocked_matches() — substring matching, deliberately
# over-flags rather than under-flags (see risk_words.py docstring
# for why that's the accepted, safer failure mode for a compliance
# guardrail).
#
# No LLM call — pure deterministic check, no token logging needed.

import logging

from app.agent.state import RFPAnalyzerState
from app.knowledge.risk_words import get_blocked_matches

logger = logging.getLogger("app.agent.nodes.validate_output")


async def validate_output(state: RFPAnalyzerState) -> dict:
    violations: list[dict] = []

    executive_summary = state.get("executive_summary") or ""
    summary_matches = get_blocked_matches(executive_summary)
    if summary_matches:
        violations.append({
            "source": "executive_summary",
            "matches": [
                {"word": word, "suggestion": suggestion}
                for word, suggestion in summary_matches
            ],
        })

    scoring_results = state.get("scoring_results") or {}
    for s in scoring_results.get("scores", []):
        rationale = s.get("rationale", "")
        rationale_matches = get_blocked_matches(rationale)
        if rationale_matches:
            violations.append({
                "source": f"rationale for '{s.get('criterion')}'",
                "matches": [
                    {"word": word, "suggestion": suggestion}
                    for word, suggestion in rationale_matches
                ],
            })

    if violations:
        logger.warning(
            "validate_output: %d violation source(s) found for "
            "session %s: %s",
            len(violations), state["session_id"], violations,
        )
    else:
        logger.info(
            "validate_output: no violations found for session %s",
            state["session_id"],
        )

    return {"validation_violations": violations}