# app/agent/nodes/handle_off_topic.py
#
# Handles requests unrelated to RFP evaluation. Politely redirects
# without attempting to answer the unrelated request — same
# compliance-boundary reasoning established for the sibling agents
# in this project: an off-topic answer generated in this agent's
# voice would blur what this agent is actually authorized to do.
# Does NOT touch stage or any workflow field.

from app.agent.state import RFPAnalyzerState

_RESPONSE = (
    "I'm focused specifically on evaluating proposals and RFP "
    "responses against criteria you provide. I'm not able to help "
    "with that request, but I'd be glad to help with an evaluation "
    "whenever you're ready."
)


async def handle_off_topic(state: RFPAnalyzerState) -> dict:
    return {"response_to_user": _RESPONSE}