# app/teams/config.py
#
# Built against confirmed ground truth (AgentAuthConfiguration's
# real constructor and AuthTypes.client_secret, both verified via
# direct inspection of the installed package).

import logging

from microsoft_agents.hosting.core import AgentAuthConfiguration, AuthTypes

from app.config import settings

logger = logging.getLogger("app.teams.config")

# Connection name used as the dict key passed to MsalConnectionManager
# — "service_connection" chosen to match the naming convention
# confirmed in official samples' env var format
# (CONNECTIONS__SERVICE_CONNECTION__SETTINGS__...).
SERVICE_CONNECTION_NAME = "service_connection"


def build_agent_auth_configuration() -> AgentAuthConfiguration:
    """
    Builds the AgentAuthConfiguration object MsalConnectionManager
    actually expects, sourced from this project's existing settings
    object — not a separate env var convention.
    """
    if not settings.TEAMS_APP_ID:
        logger.error(
            "TEAMS_APP_ID not set — Teams integration will fail to "
            "authenticate. Set TEAMS_APP_ID / TEAMS_APP_PASSWORD in "
            "app/config.py's settings source (env vars, .env, or the "
            "mounted secrets volume)."
        )

    tenant_id = settings.TEAMS_TENANT_ID or settings.ENTRA_TENANT_ID
    if not settings.TEAMS_TENANT_ID:
        logger.info(
            "TEAMS_TENANT_ID not explicitly set — falling back to "
            "ENTRA_TENANT_ID. Set TEAMS_TENANT_ID explicitly if the "
            "Azure Bot resource is registered in a different tenant "
            "than this API's own App Registration."
        )

    app_password = (
        settings.TEAMS_APP_PASSWORD.get_secret_value()
        if settings.TEAMS_APP_PASSWORD is not None
        else ""
    )

    return AgentAuthConfiguration(
        auth_type=AuthTypes.client_secret,  # VERIFY — see module docstring
        client_id=settings.TEAMS_APP_ID,
        tenant_id=tenant_id,
        client_secret=app_password,
        connection_name=SERVICE_CONNECTION_NAME,
    )