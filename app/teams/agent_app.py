# app/teams/agent_app.py
#
# Defines the AgentApplication and its activity handlers.
#
# NO MODULE-LEVEL GLOBALS — configure_agent_app() builds both the
# AgentApplication AND its handlers together via closure. Result
# stored on app.state.teams_agent_app, matching the accessor pattern
# used for the checkpointer/Teams adapter elsewhere.
#
# attachment_downloader is now PASSED IN rather than constructed
# here — a prior version built a second, redundant
# TeamsAttachmentDownloader instance separate from the one already
# registered in ApplicationOptions.file_downloaders (app/teams/adapter.py).
# Both held the same connection_manager and were functionally
# equivalent, but two instances for one job is unnecessary — now
# there is exactly one, built once in adapter.py, shared here.
#
# ERROR HANDLING: on_message's entire body is now wrapped in
# try/except. Previously, ANY exception during a turn (an LLM call
# failing, a DB hiccup, an unexpected None somewhere) would propagate
# all the way up with context.send_activity NEVER being called —
# meaning the user got literally NO reply at all, not even an error
# message, which is worse than a REST client's HTTP 500 (which at
# least signals failure). Fixed: turn failures now log the real
# error and still send the user a plain, honest message.

import logging

from fastapi import FastAPI
from microsoft_agents.hosting.core import AgentApplication, TurnContext, TurnState

from app.services import teams_service

logger = logging.getLogger("app.teams.agent_app")

_TURN_FAILED_MESSAGE = (
    "Sorry, something went wrong processing that. Please try again."
)


def configure_agent_app(
    db, sync_db, checkpointer, options, connection_manager, authorization,
    attachment_downloader,
) -> AgentApplication:
    """
    Builds the AgentApplication and registers its handlers in one
    call. db/sync_db/checkpointer/attachment_downloader are captured
    via closure — no module-level state, no separate wiring step.
    """
    agent_app = AgentApplication(
        options=options,
        connection_manager=connection_manager,
        authorization=authorization,
    )

    @agent_app.conversation_update("membersAdded")
    async def on_members_added(context: TurnContext, state: TurnState):
        """
        Fires when the bot is added to a conversation. Since this
        agent is PERSONAL SCOPE ONLY (no team/groupChat — see
        manifest.json's bots[0].scopes), this event realistically
        only ever fires once per user: when they first add/start a
        chat with the bot. context.activity.from_property here
        reliably reflects the HUMAN USER who did that (not the bot
        itself) — confirmed from real captured Teams payloads earlier
        in this project (from.name held the real display name, e.g.
        "Ashish Sood"). No Graph call or extra permission needed.

        Falls back to "there" if name is ever empty/None for some
        reason, rather than risk a broken "Hi !" greeting.
        """
        user_name = context.activity.from_property.name or "there"
        await context.send_activity(
            f"Hi {user_name}! I'm RFP Analyzer. Share your evaluation "
            f"criteria, or attach a document, whenever you're ready. Send "
            "\"new conversation\" at any point to start a fresh session."
        )

    @agent_app.activity("message")
    async def on_message(context: TurnContext, state: TurnState):
        conversation_id = context.activity.conversation.id
        aad_object_id = context.activity.from_property.aad_object_id
        attachments = context.activity.attachments or []

        try:
            if attachments:
                input_files = await attachment_downloader.download_files(context)

                if not input_files:
                    logger.warning(
                        "Teams: attachment present but download_files "
                        "returned nothing for conversation %s — treating "
                        "as a plain text message instead of failing silently.",
                        conversation_id,
                    )
                    reply_text = await teams_service.handle_teams_message(
                        db=db, sync_db=sync_db, checkpointer=checkpointer,
                        conversation_id=conversation_id, aad_object_id=aad_object_id,
                        text=context.activity.text or "",
                    )
                else:
                    # InputFile has no filename field (confirmed) —
                    # filename comes from the ORIGINAL attachment.
                    filename = attachments[0].name or "uploaded_file"
                    reply_text = await teams_service.handle_teams_attachment(
                        db=db, sync_db=sync_db, checkpointer=checkpointer,
                        conversation_id=conversation_id, aad_object_id=aad_object_id,
                        filename=filename, file_bytes=input_files[0].content,
                        accompanying_text=context.activity.text or "",
                    )
            else:
                reply_text = await teams_service.handle_teams_message(
                    db=db, sync_db=sync_db, checkpointer=checkpointer,
                    conversation_id=conversation_id, aad_object_id=aad_object_id,
                    text=context.activity.text or "",
                )
        except Exception:
            logger.exception(
                "Teams: unhandled error processing turn for conversation %s",
                conversation_id,
            )
            reply_text = _TURN_FAILED_MESSAGE

        await context.send_activity(reply_text)

    logger.info("Teams AgentApplication configured with activity handlers")
    return agent_app


def get_teams_agent_app(app: FastAPI) -> AgentApplication:
    agent_app = getattr(app.state, "teams_agent_app", None)
    if agent_app is None:
        raise RuntimeError(
            "Teams AgentApplication not initialized. "
            "configure_agent_app(...) must run during app startup, "
            "with its result stored on app.state.teams_agent_app."
        )
    return agent_app