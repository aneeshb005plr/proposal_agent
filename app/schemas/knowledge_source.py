# app/schema/knowledge_source.py
#
# Request/response models for managing THIS agent's own knowledge
# sources. Deliberately GENERIC across source_type — config is a
# freeform dict, and secret_fields names which of ITS keys need
# encryption, rather than hardcoding SharePoint-specific field names
# anywhere in this schema. A future website/API source type needs
# ZERO changes here — just a different config shape and different
# secret_fields list, supplied by the caller.

from pydantic import BaseModel


class CreateSourceRequest(BaseModel):
    source_id: str
    source_type: str  # e.g. "sharepoint_graph" — must match a key
                        # the worker's SOURCE_ADAPTERS registry knows
    config: dict         # PLAIN values — secret_fields below names
                         # which keys get encrypted before storage
    secret_fields: list[str] = []  # e.g. ["graph_client_secret"]
    enabled: bool = True


class UpdateSourceRequest(BaseModel):
    """All fields optional — only provided ones change. config, if
    provided, REPLACES the config dict entirely (not merged) — the
    caller should send the full desired config, same convention as
    a PUT would use, to avoid ambiguity about partial-dict merging
    with encrypted fields mixed in."""
    config: dict | None = None
    secret_fields: list[str] = []  # applies only if config is provided
    enabled: bool | None = None


class SourceSummaryResponse(BaseModel):
    """NEVER includes any field ending in '_encrypted', or any
    decrypted secret — same principle as AgentSummaryResponse.
    config here is already STRIPPED of every *_encrypted key before
    this response is built."""
    source_id: str
    source_type: str
    config: dict
    enabled: bool