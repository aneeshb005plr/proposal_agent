# app/agent/nodes/render_output.py
#
# Final node in the evaluation chain (run_evaluation →
# generate_summary → validate_output → render_output → END).
# Assembles the exact required output format per the verbatim spec:
#   1. Scoring Table
#   2. Overall Score
#   3. Executive Summary
# Then appends the MANDATORY, VERBATIM closing text block — exactly
# as specified, character for character, with NO other follow-up
# text after it. This is a hard formatting requirement from the
# spec, not a stylistic choice — implemented as a fixed string, not
# generated or paraphrased by an LLM, since "exactly the following
# text" leaves no room for variation.
#
# If validate_output found violations, they are surfaced visibly to
# the human reviewer (per "draft-only, human review required") —
# NOT silently fixed, consistent with Proposal Content Generator's
# approach.
#
# No LLM call — pure deterministic formatting.

import logging

from app.agent.state import RFPAnalyzerState

logger = logging.getLogger("app.agent.nodes.render_output")

# Verbatim, per the spec — character for character, never paraphrased.
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


def _build_violations_notice(violations: list[dict]) -> str:
    if not violations:
        return ""
    lines = ["\n⚠️ The following items were flagged for your review:"]
    for v in violations:
        words = ", ".join(
            f"\"{m['word']}\"" + (
                f" (consider: \"{m['suggestion']}\")"
                if m['suggestion']
                else " (no suggested alternative)"
            )
            for m in v["matches"]
        )
        lines.append(f"- In {v['source']}: {words}")
    return "\n".join(lines) + "\n"


async def render_output(state: RFPAnalyzerState) -> dict:
    scoring_results = state.get("scoring_results") or {}
    executive_summary = state.get("executive_summary") or ""
    violations = state.get("validation_violations") or []

    sections = [
        "### 1. Scoring Table",
        _build_scoring_table(scoring_results),
        "",
        "### 2. Overall Score",
        _build_overall_score(scoring_results),
        "",
        "### 3. Executive Summary",
        executive_summary,
    ]

    if violations:
        sections.append(_build_violations_notice(violations))

    sections.append("")
    sections.append(_MANDATORY_CLOSING_BLOCK)

    final_output = "\n".join(sections)

    logger.info("render_output: final output assembled for session %s", state["session_id"])

    return {"response_to_user": final_output}