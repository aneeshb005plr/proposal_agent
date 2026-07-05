# app/api/teams.py
#
# Teams' single entry point (per Bot Framework's Activity protocol
# — ALL Teams interaction types, text or file attachment, arrive
# here as Activity JSON; there is no separate "upload route" the
# way the REST API has). This route is intentionally minimal — it
# delegates entirely to the SDK's own start_agent_process, which
# handles JWT validation and Activity parsing/dispatch to the
# handlers registered in app/teams/agent_app.py. All real decision
# logic lives in app/services/teams_service.py, reached indirectly
# via those handlers.
#
# Confirmed pattern (microsoft_agents.hosting.fastapi.start_agent_process)
# per real Microsoft documentation — mirrors the aiohttp equivalent
# shown in official samples. NOT yet run against a live installed
# package in this project — verify signature matches on first use.

import logging

from fastapi import APIRouter, Request
from microsoft_agents.hosting.fastapi import start_agent_process

from app.teams.adapter import get_teams_adapter
from app.teams.agent_app import get_teams_agent_app

logger = logging.getLogger("app.api.teams")

router = APIRouter(prefix="/api", tags=["teams"])


@router.post("/messages")
async def teams_messages(request: Request):
    """
    The one messaging endpoint configured on the Azure Bot resource
    (behind Ocelot — see rfp_analyzer_teams_integration.md Section 3
    for the still-unconfirmed gateway route entry). start_agent_process
    handles JWT validation, Activity parsing, and dispatches to
    whichever handler in app/teams/agent_app.py matches the incoming
    activity type.
    """
    adapter = get_teams_adapter(request.app)
    agent_app = get_teams_agent_app(request.app)
    return await start_agent_process(request, agent_app, adapter)