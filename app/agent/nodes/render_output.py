# app/agent/nodes/render_output.py
#
# CORRECTED: removed the user-facing violations notice entirely.
# Two confirmed reasons: (1) flagged words are OUR generated text,
# not the user's responsibility to review or fix — surfacing them
# as "please review" was backwards; (2) the verbatim spec explicitly
# states "Do not add any other suggestions or follow-up text" after
# the Executive Summary, and the violations notice was exactly that
# — a real spec violation we hadn't caught until cross-checking
# against the verbatim instructions directly.
#
# Violations are still logged internally (validate_output.py's
# existing logger.warning call) for compliance/audit purposes —
# just never shown in the user-facing rendered output.

import logging

from app.agent.state import RFPAnalyzerState

logger = logging.getLogger("app.agent.nodes.render_output")

_MANDATORY_CLOSING_BLOCK = """If you would like to:
- Create a polished PowerPoint presentation
- Export this analysis to PDF or Word
- Edit or refine this content

Please use the option available below."""


def _build_scoring_table(scoring_results: dict) -> str:
    rows = ["| Criterion | Score (0-5) | Rationale |", "|---|---|---|"]
    for s in scoring_results.get("scores", []):
        rationale = s.get("rationale", "").replace("|", "\\|").replace("\n", " ")
        rows.append(f"| {s['criterion']} | {s['score']} | {rationale} |")
    return "\n".join(rows)


def _build_overall_score(scoring_results: dict) -> str:
    total = scoring_results.get("total_score")
    max_possible = scoring_results.get("max_possible")
    percentage = scoring_results.get("percentage")
    return f"**{total}/{max_possible} ({percentage}%)**"


async def render_output(state: RFPAnalyzerState) -> dict:
    scoring_results = state.get("scoring_results") or {}
    executive_summary = state.get("executive_summary") or ""
    violations = state.get("validation_violations") or []

    if violations:
        # Internal-only — never shown to the user. Already also
        # logged by validate_output.py itself; this second log line
        # confirms render_output saw and deliberately suppressed
        # them from the final output, useful for audit trail clarity.
        logger.warning(
            "render_output: %d violation source(s) present, "
            "suppressed from user-facing output per spec compliance: %s",
            len(violations), violations,
        )

    sections = [
        "### 1. Scoring Table",
        _build_scoring_table(scoring_results),
        "",
        "### 2. Overall Score",
        _build_overall_score(scoring_results),
        "",
        "### 3. Executive Summary",
        executive_summary,
        "",
        _MANDATORY_CLOSING_BLOCK,
    ]

    final_output = "\n".join(sections)

    logger.info("render_output: final output assembled for session %s", state["session_id"])

    return {"response_to_user": final_output}