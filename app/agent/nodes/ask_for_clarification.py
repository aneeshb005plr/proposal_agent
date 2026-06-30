# app/agent/nodes/ask_for_clarification.py
#
# Handles Category D: unclear what the user wants post-evaluation.
# Per the spec's Optional Outputs Policy — does NOT proactively
# list/suggest options. Asks plainly what they'd like to do, leaving
# it open-ended rather than offering a menu.

from app.agent.state import RFPAnalyzerState

_RESPONSE = (
    "Could you clarify what you'd like to do next — for example, "
    "request something based on this evaluation, adjust the "
    "criteria, or evaluate a different document?"
)


async def ask_for_clarification(state: RFPAnalyzerState) -> dict:
    return {"response_to_user": _RESPONSE}