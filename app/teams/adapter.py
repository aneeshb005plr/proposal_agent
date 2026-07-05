# app/teams/adapter.py
#
# REWRITTEN against confirmed ground truth from direct inspection of
# the installed SDK (see sdk_inspect.py / sdk_inspect_2.py output).
# Prior versions guessed at AgentApplication accepting storage=/
# adapter= directly — WRONG. Both belong inside ApplicationOptions.
# CloudAdapter also lives under microsoft_agents.hosting.fastapi in
# this project's installed packages (the .hosting.aiohttp import
# path failed — that module isn't installed/packaged here).

import logging

from fastapi import FastAPI
from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.hosting.core import ApplicationOptions, Authorization, MemoryStorage
from microsoft_agents.hosting.fastapi import CloudAdapter

from app.config import settings
from app.teams.config import SERVICE_CONNECTION_NAME, build_agent_auth_configuration
from app.teams.downloader import TeamsAttachmentDownloader

logger = logging.getLogger("app.teams.adapter")


def connect_teams_adapter(app: FastAPI) -> None:
    """
    Called once from main.py's lifespan, alongside connect_to_mongo /
    connect_checkpointer. Stores everything on app.state for later
    access by app/teams/agent_app.py and app/api/teams.py.

    NOTE — storage: MemoryStorage() is the SDK's own internal turn/
    conversation-state bookkeeping, UNRELATED to RFP-Analyzer's own
    session state (which lives in the LangGraph checkpointer, as it
    always has). In-memory means this SDK-internal state does not
    survive a pod restart — acceptable for now since nothing
    RFP-Analyzer-specific lives there; a Mongo/Cosmos-backed Storage
    implementation exists in the SDK (microsoft_agents.storage.cosmos)
    if this ever needs to survive restarts.
    """
    auth_config = build_agent_auth_configuration()

    connection_manager = MsalConnectionManager(
        connections_configurations={SERVICE_CONNECTION_NAME: auth_config}
    )
    storage = MemoryStorage()
    adapter = CloudAdapter(connection_manager=connection_manager)
    authorization = Authorization(storage, connection_manager)

    # Built ONCE here, stored on app.state, and reused directly by
    # app/teams/agent_app.py — NOT constructed a second time there.
    # Still also registered in ApplicationOptions.file_downloaders
    # below, in case any SDK-internal behavior depends on that
    # registration existing, even though this project calls it
    # directly rather than relying on that automatic wiring.
    attachment_downloader = TeamsAttachmentDownloader(connection_manager)

    options = ApplicationOptions(
        adapter=adapter,
        storage=storage,
        bot_app_id=settings.TEAMS_APP_ID,
        file_downloaders=[attachment_downloader],
    )

    app.state.teams_storage = storage
    app.state.teams_connection_manager = connection_manager
    app.state.teams_adapter = adapter
    app.state.teams_authorization = authorization
    app.state.teams_options = options
    app.state.teams_attachment_downloader = attachment_downloader

    logger.info("Teams SDK adapter initialized")


def get_teams_adapter(app: FastAPI) -> CloudAdapter:
    adapter = getattr(app.state, "teams_adapter", None)
    if adapter is None:
        raise RuntimeError(
            "Teams adapter not initialized. "
            "connect_teams_adapter(app) must run during app startup."
        )
    return adapter