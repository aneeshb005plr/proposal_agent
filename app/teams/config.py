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
    Builds the config MsalConnectionManager expects, sourced from
    this project's existing settings object.

    ANONYMOUS MODE FOR LOCAL TESTING: if TEAMS_APP_ID isn't set (no
    real Azure Bot resource configured yet — the normal case for
    Tier A/B local testing per the testing guide), this builds an
    ANONYMOUS AgentAuthConfiguration instead of a real ClientSecret
    one. Confirmed root cause of a real error: leaving tenant_id
    blank/empty and still requesting AuthTypes.client_secret does
    NOT skip real auth — something in the chain still attempted a
    genuine OIDC discovery call against a placeholder all-zero GUID
    tenant (00000000-0000-0000-0000-000000000000), which Azure
    correctly rejects (AADSTS900021). The SDK's own anonymous_allowed
    field is the actual, correct way to signal "don't attempt real
    auth at all" — matching the Agents Playground docs' own claim
    that anonymous testing needs "no other configuration."

    This means: running against the Agents Playground WITHOUT real
    Teams credentials configured now works (anonymous mode); running
    against a REAL Azure Bot resource (Tier C+ in the testing guide)
    requires TEAMS_APP_ID/TEAMS_APP_PASSWORD/TEAMS_TENANT_ID to
    actually be set, which switches this back to the real
    ClientSecret path automatically.
    """
    if not settings.TEAMS_APP_ID:
        logger.warning(
            "TEAMS_APP_ID not set — configuring for ANONYMOUS auth "
            "(local Agents Playground testing only). This will NOT "
            "work against a real Azure Bot resource — set "
            "TEAMS_APP_ID / TEAMS_APP_PASSWORD / TEAMS_TENANT_ID "
            "before testing with a real Teams client (see "
            "rfp_analyzer_teams_testing_guide.md, Tier C)."
        )
        return AgentAuthConfiguration(
            anonymous_allowed=True,
            connection_name=SERVICE_CONNECTION_NAME,
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
        auth_type=AuthTypes.client_secret,
        client_id=settings.TEAMS_APP_ID,
        tenant_id=tenant_id,
        client_secret=app_password,
        connection_name=SERVICE_CONNECTION_NAME,
    )