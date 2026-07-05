import logging
import os
from typing import List, Optional, Tuple, Type

from pydantic import Field, SecretStr, computed_field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

DEFAULT_SECRETS_PATH: str = os.environ.get(
    "SECRETS_VOLUME_PATH",
    "/var/app/secrets",
)

# Configure logging at import time using env var directly.
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)

logger = logging.getLogger("app.config")


class Settings(BaseSettings):
    """Application settings - single source of truth."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        secrets_dir=DEFAULT_SECRETS_PATH if os.path.isdir(DEFAULT_SECRETS_PATH) else None,
        case_sensitive=True,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # General
    # ------------------------------------------------------------------
    ENVIRONMENT: str = "development"
    DEBUG: bool = False
    SECRETS_VOLUME_PATH: str = DEFAULT_SECRETS_PATH

    # ------------------------------------------------------------------
    # Agent Identity
    # ------------------------------------------------------------------
    AGENT_ID: str = "rfp_analyzer"
    AGENT_NAME: str = "RFP Analyzer"

    # ------------------------------------------------------------------
    # GenAI Shared Service (OpenAI compatible)
    # ------------------------------------------------------------------
    GENAI_BASE_URL: Optional[str] = None
    GENAI_API_KEY: Optional[SecretStr] = None
    GENAI_LLM_MODEL: str = "azure.gpt-4.1"
    GENAI_EMBEDDINGS_MODEL: str = "azure.text-embedding-3-small"
    GENAI_TEMPERATURE: float = Field(default=0.3, ge=0.0, le=2.0)
    GENAI_MAX_TOKENS: int = Field(default=4096, gt=0, le=128000)

    # ------------------------------------------------------------------
    # MongoDB
    # ------------------------------------------------------------------
    MONGODB_URI: str = "mongodb://localhost:27017"
    MONGODB_DB_NAME: str = "agent_rfp_analyzer"

    # ------------------------------------------------------------------
    # Entra ID
    # ------------------------------------------------------------------
    ENTRA_TENANT_ID: Optional[str] = None
    ENTRA_CLIENT_ID: Optional[str] = None

    # ------------------------------------------------------------------
    # Microsoft Graph
    # ------------------------------------------------------------------
    GRAPH_CLIENT_ID: str = ""
    GRAPH_CLIENT_SECRET: str = ""
    GRAPH_TENANT_ID: str = ""


    # CORRECTION: ENTRA_TENANT_ID/ENTRA_CLIENT_ID are NOT dead — confirmed
# actively used in app/auth/claims_resolver.py, referenced directly
# in the JWKS client URI, _EXPECTED_ISSUER, and _EXPECTED_AUDIENCE.
# They validate bearer tokens sent TO this agent's own REST API
# (ENTRA_CLIENT_ID = this app's OWN App Registration, used as the
# expected token audience). DO NOT REMOVE. My earlier instruction to
# delete them was wrong — asserted without verification, corrected
# once claims_resolver.py was actually shared.
#
# This also resolves the original open question precisely:
#   - TEAMS_APP_ID must stay a SEPARATE field from ENTRA_CLIENT_ID —
#     the Azure Bot resource needs its OWN, distinct App Registration
#     (it authenticates Bot Framework Connector traffic TO this app,
#     a structurally different purpose/audience than validating
#     bearer tokens sent BY API callers).
#   - TEAMS_TENANT_ID is a genuine candidate to just REUSE
#     ENTRA_TENANT_ID's value — both are almost certainly the same
#     Azure AD tenant (your organization), just different App
#     Registrations within it. CONFIRM this assumption, but if true,
#     this collapses to one fewer new field.
 
    # ------------------------------------------------------------------
    # Microsoft Teams (Microsoft 365 Agents SDK)
    #
    # TEAMS_TENANT_ID intentionally separate from ENTRA_TENANT_ID for
    # now, pending confirmation they're the same tenant — if
    # confirmed, delete this field and have build_teams_sdk_config()
    # read settings.ENTRA_TENANT_ID directly instead.
    #
    # TEAMS_SESSION_STALE_DAYS: our backend's own session lifetime
    # for a Teams conversation, meant to track your org's actual
    # Teams chat-clearing policy — mentioned as "around 3 days" but
    # NOT independently confirmed by us; kept configurable rather
    # than hardcoded specifically because of that uncertainty.
    # Change this the moment the real policy value is confirmed
    # (IT/admin center), no code change needed.
    # ------------------------------------------------------------------
    TEAMS_TENANT_ID: str = ""
    TEAMS_APP_ID: str = ""
    TEAMS_APP_PASSWORD: Optional[SecretStr] = None
    TEAMS_SESSION_STALE_DAYS: int = 3



    SHAREPOINT_SITE_ID: str = ""
    SHAREPOINT_DRIVE_ID: str = ""

    SHAREPOINT_KNOWLEDGE_FOLDER: str = "AI tool files/RFP Analyzer"
    EXCLUDED_FROM_INDEXING: List[str] = []

    # ------------------------------------------------------------------
    # Chunking
    # ------------------------------------------------------------------
    CHUNK_SIZE_TOKENS: int = Field(default=750, ge=1, le=10000)
    CHUNK_OVERLAP_TOKENS: int = Field(default=100, ge=0, le=10000)

    # ------------------------------------------------------------------
    # Session / Upload
    # ------------------------------------------------------------------
    MAX_UPLOADED_FILES_PER_SESSION: int = 5
    UPLOAD_AFTER_CONFIRMATION_POLICY: str = "invalidate"

    # ------------------------------------------------------------------
    # Feature Flags
    # ------------------------------------------------------------------
    ENABLE_SWAGGER: bool = True

    # ------------------------------------------------------------------
    # CORS
    # ------------------------------------------------------------------
    CORS_ORIGINS: List[str] = Field(default_factory=lambda: ["*"])

    # ------------------------------------------------------------------
    # Source precedence
    # ------------------------------------------------------------------
    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            file_secret_settings,
            env_settings,
            dotenv_settings,
        )

    # ------------------------------------------------------------------
    # Derived flags
    # ------------------------------------------------------------------
    @computed_field
    @property
    def IS_DEVELOPMENT(self) -> bool:
        return self.ENVIRONMENT.lower() == "development"

    @computed_field
    @property
    def IS_TESTING(self) -> bool:
        return self.ENVIRONMENT.lower() == "testing"

    @computed_field
    @property
    def IS_PRODUCTION(self) -> bool:
        return self.ENVIRONMENT.lower() == "production"

    # ------------------------------------------------------------------
    # Production validation
    # ------------------------------------------------------------------
    @model_validator(mode="after")
    def _validate_required_in_production(self) -> "Settings":
        if not self.IS_PRODUCTION:
            return self

        api_key_ok = (
            self.GENAI_API_KEY is not None
            and self.GENAI_API_KEY.get_secret_value().strip() != ""
        )

        required = {
            "MONGODB_URI": self.MONGODB_URI != "mongodb://localhost:27017",
            "GENAI_BASE_URL": bool(
                self.GENAI_BASE_URL and self.GENAI_BASE_URL.strip()
            ),
            "GENAI_API_KEY": api_key_ok,
        }

        missing = [name for name, ok in required.items() if not ok]

        if missing:
            raise ValueError(
                f"Missing required production settings: {', '.join(missing)}"
            )

        if self.DEBUG:
            raise ValueError("DEBUG must be False in production")

        return self

    # ------------------------------------------------------------------
    # Safe representation for logging
    # ------------------------------------------------------------------
    def safe_dump(self) -> dict:
        """
        Return a dict representation safe to log.
        Masks API keys and credentials embedded in connection URIs.
        """

        # mode="json" serializes SecretStr automatically
        data = self.model_dump(mode="json")

        for key in ("GENAI_API_KEY",):
            if data.get(key) is not None:
                data[key] = "***"

        for url_key in ("MONGODB_URI",):
            url = getattr(self, url_key, None)
            if url and "@" in url:
                try:
                    scheme, rest = url.split("://", 1)
                    _, host_part = rest.split("@", 1)
                    data[url_key] = f"{scheme}://***@{host_part}"
                except ValueError:
                    pass

        return data


# ----------------------------------------------------------------------
# Module-level singleton
# ----------------------------------------------------------------------

settings = Settings()

logger.info(
    "Settings loaded for ENVIRONMENT=%s (DEBUG=%s)",
    settings.ENVIRONMENT,
    settings.DEBUG,
)

logger.debug("Full settings: %s", settings.safe_dump())



 
# ------------------------------------------------------------------
# Add to _validate_required_in_production's `required` dict, ONLY
# once Teams is actually being deployed to production — leaving
# this commented out for now so production startup doesn't start
# failing before the Teams integration is actually ready:
# ------------------------------------------------------------------
#
#         teams_password_ok = (
#             self.TEAMS_APP_PASSWORD is not None
#             and self.TEAMS_APP_PASSWORD.get_secret_value().strip() != ""
#         )
#         required["TEAMS_APP_ID"] = bool(self.TEAMS_APP_ID)
#         required["TEAMS_APP_PASSWORD"] = teams_password_ok
 
 
# ------------------------------------------------------------------
# Add to safe_dump()'s masked-keys tuple, alongside GENAI_API_KEY:
# ------------------------------------------------------------------
#
#         for key in ("GENAI_API_KEY", "TEAMS_APP_PASSWORD"):
#             if data.get(key) is not None:
#                 data[key] = "***"