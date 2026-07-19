# app/teams/config.py
#
# MIGRATED from client_secret to User-Assigned Managed Identity —
# the Azure Bot resource (rfp-analyzer-bot-dev) is no longer a
# client-secret Entra app bot. The client_secret path is fully
# REPLACED here, not kept as a fallback, given this resource has
# been reconfigured.
#
# VERIFIED directly against the installed SDK's real source
# (inspect.getsource on MsalAuth._create_client_application — not a
# secondhand claim):
#
#   if AUTH_TYPE in (AuthTypes.user_managed_identity,
#                     AuthTypes.identity_proxy_manager):
#       return ManagedIdentityClient(
#           UserAssignedManagedIdentity(client_id=CLIENT_ID),
#           http_client=Session(),
#       )
#
# Confirmed: this path reads ONLY client_id. tenant_id, client_secret,
# authority, and scopes are never referenced anywhere in this branch —
# confirmed via a full grep of every TENANT_ID reference across
# MsalAuth's entire source; the authority/tenant-resolution code
# lives exclusively in the OTHER branch (client_secret/certificate/
# federated_credentials), never reached for managed identity. The
# one remaining TENANT_ID reference outside that dead branch
# (_client_rep, building an internal cache-key string) is
# functionally harmless whether tenant_id is set or None — confirmed
# get_access_token() calls self._get_client() with zero arguments,
# so tenant_id defaults to None all the way through regardless.
#
# Also confirmed: AuthTypes.identity_proxy_manager hits the IDENTICAL
# code path as user_managed_identity — not something the original
# migration request mentioned, worth knowing even though
# user_managed_identity is the correct choice here (the Azure
# resource is confirmed specifically as User-Assigned MI, not IDPM).
#
# RUNTIME CONSTRAINT, confirmed general Azure platform behavior, not
# SDK-specific: ManagedIdentityClient acquires tokens via IMDS
# (169.254.169.254), which only exists on real Azure compute (AKS,
# App Service, Container Apps, Azure VM). Managed Identity CANNOT
# acquire tokens when running locally — expected, not a bug. Local
# `uvicorn` runs still build configuration and start up successfully;
# only REAL token acquisition (a real Teams message arriving)
# requires running on real Azure compute. See
# rfp_analyzer_teams_testing_guide.md for what this means for local
# testing (Tier B/C's local-testing assumptions need revisiting given
# this constraint — flagged there, not silently glossed over).
#
# OPEN QUESTION, not yet confirmed either way: does EVERY environment
# (dev/test/stage/prod) use Managed Identity, or only rfp-analyzer-
# bot-dev specifically? If any environment still uses client_secret,
# this file would need a real auth_type branch again, not a full
# replacement. Confirm before assuming this applies uniformly across
# all environments.

import logging

from microsoft_agents.hosting.core import AgentAuthConfiguration, AuthTypes

from app.config import settings

logger = logging.getLogger("app.teams.config")

# Connection name used as the dict key passed to MsalConnectionManager
# — CONFIRMED via real testing to need to be UPPERCASE
# ("SERVICE_CONNECTION"), matching the env-var naming convention
# from official samples.
SERVICE_CONNECTION_NAME = "SERVICE_CONNECTION"


def build_agent_auth_configuration() -> AgentAuthConfiguration:
    """
    Managed-identity-only. TEAMS_APP_ID must be set to the Managed
    Identity's Client ID — confirmed identical to this Bot resource's
    own Microsoft App ID. This is the ONLY value the SDK's real
    token-acquisition path actually reads for this auth type; no
    tenant ID or client secret is used or needed.
    """
    if not settings.TEAMS_APP_ID:
        raise ValueError(
            "TEAMS_APP_ID is not set. For User-Assigned Managed "
            "Identity, this must be the Managed Identity's Client ID "
            "(confirmed identical to the Bot resource's own Microsoft "
            "App ID for rfp-analyzer-bot-dev). No tenant ID or client "
            "secret is used for this auth type — see this module's "
            "docstring for the directly-verified reasoning."
        )

    return AgentAuthConfiguration(
        auth_type=AuthTypes.user_managed_identity,
        client_id=settings.TEAMS_APP_ID,
        connection_name=SERVICE_CONNECTION_NAME,
    )