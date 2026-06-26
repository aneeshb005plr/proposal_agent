# app/agent/nodes/handle_social.py
#
# Handles greetings, thanks, farewells. Deliberately does NOT touch
# stage, criteria, document_confirmed, or any other workflow field —
# the formal state machine stays exactly where it was. This is a
# pure side-conversation response, not a workflow step.
#
# No LLM call needed — a small set of templated, varied responses is
# sufficient for this narrow category and avoids spending a model
# call on something this simple.

import random

from app.agent.state import RFPAnalyzerState

_RESPONSES = [
    "Happy to help! Let me know when you're ready to share your evaluation criteria.",
    "Of course — I'm here whenever you're ready to continue.",
    "Glad to help. Just let me know how you'd like to proceed with the evaluation.",
]


async def handle_social(state: RFPAnalyzerState) -> dict:
    return {"response_to_user": random.choice(_RESPONSES)}