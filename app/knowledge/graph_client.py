# app/knowledge/graph_client.py
#
# Microsoft Graph REST client for SharePoint knowledge sync.
#
# CORRECTED DESIGN — see ADR-A007 in the architecture doc. This uses
# raw HTTP calls (httpx) against documented Graph REST endpoints,
# NOT the msgraph-sdk package. An earlier draft built on msgraph-sdk's
# generated builder pattern, using path-based colon addressing
# (root:/{path}:/children) to resolve our nested SharePoint folder.
# That could not be confirmed with certainty against current SDK docs.
# This raw-REST approach, adapted from a real, production-tested
# internal reference implementation, removes that ambiguity entirely —
# every URL hit here is a documented Graph endpoint, called directly.
#
# Uses Graph's ROOT-LEVEL delta endpoint, not a subfolder-scoped one.
# Subfolder-scoped delta has a known inconsistency where deleted items
# do not reliably carry the "deleted" facet — root-level delta does
# not have this problem. We filter to our knowledge folder by path
# in application code instead.
#
# Required Entra app registration permissions (application, not
# delegated): Sites.Read.All, Files.Read.All.

import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

import httpx

from app.config import settings

logger = logging.getLogger("app.knowledge.graph_client")

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

# Office creates these as lock/temp files when a document is open —
# always skip, attempting to parse them would just produce garbage.
HIDDEN_PREFIXES = ("~$", ".~", "._")


class GraphClientNotConfiguredError(Exception):
    """
    Raised when a Graph operation is attempted before
    GRAPH_CLIENT_ID / GRAPH_CLIENT_SECRET / GRAPH_TENANT_ID /
    SHAREPOINT_SITE_ID have real values filled in. Raised at the
    point of use, not at import time — the rest of the app must be
    able to boot and run without SharePoint sync configured yet.
    """
    pass


class GraphAPIError(Exception):
    """Raised when a Graph API call fails after retries are exhausted."""
    pass


def _require_graph_config() -> None:
    missing = [
        name for name, value in [
            ("GRAPH_CLIENT_ID", settings.GRAPH_CLIENT_ID),
            ("GRAPH_CLIENT_SECRET", settings.GRAPH_CLIENT_SECRET),
            ("GRAPH_TENANT_ID", settings.GRAPH_TENANT_ID),
            ("SHAREPOINT_SITE_ID", settings.SHAREPOINT_SITE_ID),
        ]
        if not value
    ]
    if missing:
        raise GraphClientNotConfiguredError(
            f"Missing required Graph config: {', '.join(missing)}. "
            f"Fill these in .env before using SharePoint sync."
        )


class SharePointGraphClient:
    """
    Manages authentication and Graph API calls for syncing this
    agent's SharePoint knowledge folder. One instance per agent
    process — created once, reused across sync runs.
    """

    def __init__(self):
        self._http: Optional[httpx.AsyncClient] = None
        self._access_token: str = ""
        self._token_expires_at: float = 0.0
        self._resolved_drive_id: Optional[str] = None

    async def connect(self) -> None:
        """
        Opens the HTTP client and validates credentials by acquiring
        a token immediately — fails fast and clearly if credentials
        are wrong, rather than failing later on first real Graph call.
        """
        _require_graph_config()
        self._http = httpx.AsyncClient(timeout=30.0)
        await self._refresh_token()

        if settings.SHAREPOINT_DRIVE_ID:
            self._resolved_drive_id = settings.SHAREPOINT_DRIVE_ID
        else:
            self._resolved_drive_id = await self._get_default_drive_id()

        logger.info(
            "Connected to SharePoint via Graph API — site=%s drive=%s",
            settings.SHAREPOINT_SITE_ID, self._resolved_drive_id,
        )

    async def disconnect(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # ── Authentication ──────────────────────────────────────────────

    async def _refresh_token(self) -> None:
        """
        OAuth2 client-credentials flow — standard token endpoint, no
        SDK involved. Refreshes 5 minutes before actual expiry as a
        safety buffer against clock drift / request latency.
        """
        url = TOKEN_URL_TEMPLATE.format(tenant_id=settings.GRAPH_TENANT_ID)
        data = {
            "grant_type": "client_credentials",
            "client_id": settings.GRAPH_CLIENT_ID,
            "client_secret": settings.GRAPH_CLIENT_SECRET,
            "scope": GRAPH_SCOPE,
        }
        response = await self._http.post(url, data=data)
        if response.status_code != 200:
            raise GraphAPIError(
                f"SharePoint auth failed ({response.status_code}): "
                f"{response.text}"
            )
        result = response.json()
        self._access_token = result["access_token"]
        self._token_expires_at = time.time() + result.get("expires_in", 3600) - 300
        logger.debug("Graph token refreshed, expires_in=%s", result.get("expires_in"))

    async def _get_token(self) -> str:
        if time.time() >= self._token_expires_at:
            await self._refresh_token()
        return self._access_token

    # ── Low-level request with retry/backoff ────────────────────────

    async def _graph_get(self, url: str) -> dict:
        """
        Authenticated GET against a Graph endpoint. Retries on 401
        (forces a token refresh first — token may have been rejected
        server-side even though our local expiry check thought it was
        still valid) and backs off on 429 using the Retry-After header.
        """
        for attempt in range(3):
            token = await self._get_token()
            response = await self._http.get(
                url, headers={"Authorization": f"Bearer {token}"}
            )

            if response.status_code == 200:
                return response.json()

            if response.status_code == 401:
                self._token_expires_at = 0  # force refresh next loop
                continue

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 10))
                logger.warning("Graph API rate limited, retrying in %ss", retry_after)
                await self._sleep(retry_after)
                continue

            raise GraphAPIError(
                f"Graph API {response.status_code} on {url}: {response.text}"
            )

        raise GraphAPIError(f"Graph API request failed after retries: {url}")

    @staticmethod
    async def _sleep(seconds: int) -> None:
        import asyncio
        await asyncio.sleep(seconds)

    # ── Drive resolution ─────────────────────────────────────────────

    async def _get_default_drive_id(self) -> str:
        """Resolves the default document library drive ID for the site."""
        data = await self._graph_get(
            f"{GRAPH_BASE}/sites/{settings.SHAREPOINT_SITE_ID}/drive"
        )
        return data["id"]

    # ── Delta sync ────────────────────────────────────────────────────

    def _build_full_sync_url(self) -> str:
        """
        Root-level delta, deliberately NOT scoped to our subfolder —
        see module docstring and ADR-A007 for why subfolder-scoped
        delta is avoided (unreliable delete-facet on deleted items).
        """
        return f"{GRAPH_BASE}/drives/{self._resolved_drive_id}/root/delta"

    async def iter_changes(
        self, delta_link: Optional[str] = None
    ) -> AsyncIterator[dict]:
        """
        Yields raw Graph driveItem dicts — either a full sync (if
        delta_link is None) or only changed/deleted items since the
        last sync (if delta_link is provided from a previous run).

        Captures the new delta_link via self.last_delta_link once
        pagination completes — callers must persist this (in
        knowledge_sources, per the architecture doc) to enable delta
        sync on the next run.
        """
        self.last_delta_link: Optional[str] = None

        url: Optional[str] = delta_link or self._build_full_sync_url()

        while url:
            data = await self._graph_get(url)

            for item in data.get("value", []):
                yield item

            if next_link := data.get("@odata.nextLink"):
                url = next_link
            elif new_delta_link := data.get("@odata.deltaLink"):
                self.last_delta_link = new_delta_link
                url = None
            else:
                url = None

    def is_in_knowledge_folder(self, item: dict) -> bool:
        """
        Filters a root-level delta item down to whether it belongs to
        our configured knowledge folder. Required because root-level
        delta returns every change across the ENTIRE drive, not just
        our folder — see ADR-A007.

        Case-insensitive, since SharePoint folder names can appear
        with inconsistent casing between the configured path and the
        actual API response.
        """
        parent_path = (
            item.get("parentReference", {})
            .get("path", "")
            .split("root:")[-1]
            .strip("/")
        )
        name = item.get("name", "")
        item_path = f"{parent_path}/{name}" if parent_path else name

        normalized_target = settings.SHAREPOINT_KNOWLEDGE_FOLDER.strip("/").lower()
        normalized_item = item_path.strip("/").lower()

        return (
            normalized_item == normalized_target
            or f"/{normalized_target}/" in f"/{normalized_item}/"
            or normalized_item.startswith(f"{normalized_target}/")
        )

    def is_hidden_or_temp(self, filename: str) -> bool:
        """Office lock/temp files — always skip these."""
        return any(filename.startswith(p) for p in HIDDEN_PREFIXES)

    # ── Download ──────────────────────────────────────────────────────

    async def get_download_url(self, item_id: str) -> Optional[str]:
        """
        @microsoft.graph.downloadUrl is an OData instance annotation,
        NOT a standard driveItem property — confirmed it is never
        present on delta or listing responses regardless of $select.
        A plain GET on the specific item returns it automatically.
        """
        url = f"{GRAPH_BASE}/drives/{self._resolved_drive_id}/items/{item_id}"
        try:
            data = await self._graph_get(url)
            return data.get("@microsoft.graph.downloadUrl")
        except GraphAPIError as e:
            logger.warning("Failed to get download URL for %s: %s", item_id, e)
            return None

    async def download_file(self, item_id: str) -> Optional[bytes]:
        """
        Downloads a file's raw bytes. Fetches the pre-signed download
        URL first (separate call, see get_download_url), then a plain
        GET against that URL — the download URL itself is already
        authenticated/pre-signed by Graph, no Bearer token needed on
        this second request.
        """
        download_url = await self.get_download_url(item_id)
        if not download_url:
            return None

        response = await self._http.get(download_url)
        if response.status_code != 200:
            logger.error(
                "Download failed for item %s: %s", item_id, response.status_code
            )
            return None
        return response.content

    # ── Helpers used by the knowledge pipeline ──────────────────────

    @staticmethod
    def compute_content_hash(item: dict, file_bytes: bytes) -> str:
        """
        Prefers Graph's own SHA256/SHA1 hash when present (cheaper —
        no need to hash the bytes ourselves), falls back to computing
        one locally if Graph didn't supply one.
        """
        file_hashes = item.get("file", {}).get("hashes", {})
        return (
            file_hashes.get("sha256Hash")
            or file_hashes.get("sha1Hash")
            or hashlib.sha256(file_bytes).hexdigest()
        )

    @staticmethod
    def parse_last_modified(item: dict) -> Optional[datetime]:
        modified_str = item.get("lastModifiedDateTime")
        if not modified_str:
            return None
        try:
            return datetime.fromisoformat(modified_str.replace("Z", "+00:00"))
        except ValueError:
            return None


# Module-level instance — connect()/disconnect() called from the
# app lifespan, same pattern as app/database.py's MongoDB connection,
# rather than module-level construction doing real network work.
graph_client = SharePointGraphClient()