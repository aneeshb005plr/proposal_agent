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
# — CONFIRMED via real testing to need to be UPPERCASE
# ("SERVICE_CONNECTION"), matching the env-var naming convention
# from official samples (CONNECTIONS__SERVICE_CONNECTION__SETTINGS__...).
# An earlier lowercase version ("service_connection") caused a real
# startup failure — fixed here.
SERVICE_CONNECTION_NAME = "SERVICE_CONNECTION"


def build_agent_auth_configuration() -> AgentAuthConfiguration:
    """
    Builds the config MsalConnectionManager expects, sourced from
    this project's existing settings object.

    NO ANONYMOUS MODE — RETRACTED. An earlier version of this
    function tried AgentAuthConfiguration(anonymous_allowed=True) to
    avoid needing real credentials for local Agents Playground
    testing. CONFIRMED, via direct inspection of real installed SDK
    source AND two separate real runtime failures, that this does
    NOT work: MsalConnectionManager.__init__ unconditionally wraps
    every connection in a real MsalAuth instance regardless of
    anonymous_allowed, and MsalAuth.get_access_token() unconditionally
    calls self._get_client(), which attempts real tenant/client
    resolution with zero reference to anonymous_allowed anywhere in
    its source. Whatever that field actually controls, it is not
    this outbound token-acquisition path.

    CONCLUSION: real TEAMS_APP_ID / TEAMS_APP_PASSWORD / TEAMS_TENANT_ID
    are required for ANY testing tier, including local Agents
    Playground testing — not just a real Teams client. Get a real
    (free-tier F0 is fine) Azure Bot resource via the existing
    Terraform module and use its real credentials everywhere. See
    rfp_analyzer_teams_testing_guide.md, which has been corrected to
    reflect this.
    """
    if not settings.TEAMS_APP_ID:
        raise ValueError(
            "TEAMS_APP_ID is not set. A real Azure Bot resource's "
            "credentials are required for ANY testing tier, including "
            "local Agents Playground testing — confirmed via direct "
            "SDK source inspection that anonymous/no-credential mode "
            "does not work through MsalConnectionManager. Set "
            "TEAMS_APP_ID / TEAMS_APP_PASSWORD / TEAMS_TENANT_ID before "
            "starting this app. See rfp_analyzer_teams_testing_guide.md."
        )

    tenant_id = settings.TEAMS_TENANT_ID or settings.ENTRA_TENANT_ID
    if not settings.TEAMS_TENANT_ID:
        logger.info(
            "TEAMS_TENANT_ID not explicitly set — falling back to "
            "ENTRA_TENANT_ID. Set TEAMS_TENANT_ID explicitly if the "
            "Azure Bot resource is registered in a different tenant "
            "than this API's own App Registration."
        )
    if not tenant_id:
        raise ValueError(
            "Neither TEAMS_TENANT_ID nor ENTRA_TENANT_ID is set — a "
            "real tenant ID is required (this is what caused the "
            "AADSTS900021 / 'TENANT_ID is not set' errors previously)."
        )

    app_password = (
        settings.TEAMS_APP_PASSWORD.get_secret_value()
        if settings.TEAMS_APP_PASSWORD is not None
        else ""
    )
    if not app_password:
        raise ValueError(
            "TEAMS_APP_PASSWORD is not set — a real client secret is "
            "required."
        )

    return AgentAuthConfiguration(
        auth_type=AuthTypes.client_secret,
        client_id=settings.TEAMS_APP_ID,
        tenant_id=tenant_id,
        client_secret=app_password,
        connection_name=SERVICE_CONNECTION_NAME,
    )