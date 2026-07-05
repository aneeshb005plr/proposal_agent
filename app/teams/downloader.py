# app/teams/downloader.py
#
# Implements InputFileDownloader against CONFIRMED ground truth:
#   - download_files(self, context: TurnContext) -> list[InputFile]
#     — signature verified via direct inspection of the installed
#     package.
#   - Token acquisition: connection_manager.get_connection(name) ->
#     AccessTokenProviderBase, then .get_access_token(resource_url,
#     scopes, force_refresh=False) -> str — verified via direct
#     inspection.
#
# CORRECTED after checking real Microsoft Teams file-share docs:
# Teams attachments for a user SHARING A FILE WITH the bot use a
# SPECIFIC content type, application/vnd.microsoft.teams.file.download.info,
# where the attachment's TOP-LEVEL content_url may just be a
# SharePoint web-page link (not directly GET-able), while the REAL,
# directly-downloadable URL is NESTED inside attachment.content["downloadUrl"].
# A prior version of this file used attachment.content_url
# unconditionally — silently wrong for this specific, very common
# Teams content type. Fixed by checking content_type first and
# preferring the nested downloadUrl when present, falling back to
# content_url only for other attachment shapes.
#
# STILL GENUINELY UNCONFIRMED, can only be resolved by a real test
# against a real Teams client (attachment handling is confirmed
# untestable via Playground/dev tunnels under any circumstances):
#   - Whether the nested downloadUrl requires the SAME bearer-token
#     auth as a plain content_url GET, or is already a pre-signed/
#     temporary URL needing no auth at all. Docs show a plain GET
#     with no explicit auth header in examples, which may mean no
#     token is required for THIS specific URL — the code below still
#     attempts one anyway (any extra Authorization header sent to an
#     unauthenticated pre-signed URL is typically harmless, but this
#     is not certain either).

import logging

import httpx
from microsoft_agents.hosting.core import InputFile, InputFileDownloader, TurnContext

from app.teams.config import SERVICE_CONNECTION_NAME

logger = logging.getLogger("app.teams.downloader")

TEAMS_FILE_DOWNLOAD_INFO_CONTENT_TYPE = "application/vnd.microsoft.teams.file.download.info"


def _resolve_download_url(attachment) -> str | None:
    """
    Prefers the nested content.downloadUrl for Teams' specific
    file-share content type; falls back to the top-level
    content_url for any other attachment shape.
    """
    if attachment.content_type == TEAMS_FILE_DOWNLOAD_INFO_CONTENT_TYPE:
        content = attachment.content or {}
        nested_url = content.get("downloadUrl") if isinstance(content, dict) else None
        if nested_url:
            return nested_url
        logger.warning(
            "Attachment has Teams file-download-info content_type but "
            "no nested downloadUrl — falling back to content_url, "
            "which may not be directly downloadable."
        )
    return attachment.content_url or None


class TeamsAttachmentDownloader(InputFileDownloader):

    def __init__(self, connection_manager):
        self._connection_manager = connection_manager

    async def download_files(self, context: TurnContext) -> list[InputFile]:
        """Downloads any file attachments on the current turn's
        activity. Never raises — a failed individual attachment is
        logged and skipped, so one bad download doesn't take down
        the whole turn; callers should check for an empty/short
        result rather than assume success."""
        attachments = context.activity.attachments or []
        downloaded: list[InputFile] = []

        for attachment in attachments:
            download_url = _resolve_download_url(attachment)
            if not download_url:
                logger.warning(
                    "Attachment %r has no usable download URL — skipped.",
                    getattr(attachment, "name", "<unnamed>"),
                )
                continue

            try:
                token_provider = self._connection_manager.get_connection(
                    SERVICE_CONNECTION_NAME
                )
                token = await token_provider.get_access_token(
                    resource_url=download_url, scopes=[]
                )

                async with httpx.AsyncClient(follow_redirects=True) as client:
                    response = await client.get(
                        download_url,
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    response.raise_for_status()

                downloaded.append(
                    InputFile(
                        content=response.content,
                        content_type=attachment.content_type,
                        content_url=download_url,
                    )
                )
                logger.info(
                    "Downloaded Teams attachment: %s (%d bytes)",
                    attachment.name, len(response.content),
                )
            except Exception as e:
                # Never let one bad attachment crash the whole turn —
                # log clearly and continue; the caller (agent_app.py)
                # already has a defensive branch for an empty result.
                logger.error(
                    "Failed to download attachment %r: %s",
                    getattr(attachment, "name", "<unnamed>"), e,
                )
                continue

        return downloaded