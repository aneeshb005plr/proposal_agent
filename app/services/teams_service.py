# app/services/teams_service.py
#
# Translation layer between a Teams message/attachment and
# RFP-Analyzer's existing session-based logic. Deliberately thin —
# every real operation (send a chat message, upload a document)
# delegates to the SAME chat_service/submission_service functions
# the REST API already uses. This file's real jobs:
#   1. Resolve which session_id a Teams conversation currently maps
#      to — INCLUDING automatic rollover to a new session once the
#      mapping is older than settings.TEAMS_SESSION_STALE_DAYS (see
#      below for why this is the PRIMARY mechanism, not the explicit
#      command).
#   2. Decide whether an incoming attachment is a criteria document
#      or a submission document (deterministic by stage — see
#      rfp_analyzer_graph_structure.md)
#   3. Handle an EXPLICIT, secondary user command to start a new
#      session before the staleness window naturally elapses
#
# WHY AUTOMATIC STALENESS ROLLOVER IS THE PRIMARY MECHANISM:
# the org's own Teams chat-clearing policy means a user returning to
# a conversation after enough idle time may find Teams has already
# cleared their visible chat history — if our backend's session
# mapping still pointed at the old session, the agent would respond
# referencing criteria/documents from a conversation the user can no
# longer even see. Aligning our own session boundary to roughly the
# same window (TEAMS_SESSION_STALE_DAYS, configurable since the
# exact org policy value isn't independently confirmed by us) avoids
# that mismatch automatically, with no user action required. The
# explicit "new conversation" command below is a genuine but
# secondary feature — useful for resetting early, not the mechanism
# that solves the core staleness problem.
#
# "Evaluate a different document" / "use new criteria" needs NO
# special command at all — the graph's existing
# classify_mid_flow_intent / classify_post_evaluation_intent already
# handle this naturally, WITHIN one session, same as Streamlit/curl.
#
# See rfp_analyzer_teams_integration.md for full design rationale.

import logging
from datetime import datetime, timedelta, timezone

from app.agent.graph import build_graph
from app.config import settings
from app.documents.parser import parse_document
from app.repository.criteria_upload_repository import CriteriaUploadRepository
from app.repository.teams_conversation_repository import TeamsConversationRepository
from app.services import chat_service, session_service, submission_service

logger = logging.getLogger("app.services.teams_service")

_NEW_SESSION_COMMANDS = {"new conversation", "new session", "/new", "start over"}


async def resolve_session_for_conversation(
    db, conversation_id: str, aad_object_id: str
) -> str:
    """
    Returns the session_id this conversation maps to. Creates a NEW
    session on first contact, AND automatically rolls over to a new
    session once the existing mapping is older than
    settings.TEAMS_SESSION_STALE_DAYS — see module docstring.
    """
    teams_repo = TeamsConversationRepository(db)
    state = await teams_repo.get_conversation_state(conversation_id)

    is_stale = (
        state is not None
        and datetime.now(timezone.utc) - state["updated_at"]
        > timedelta(days=settings.TEAMS_SESSION_STALE_DAYS)
    )

    if state is None or is_stale:
        session_id = await session_service.create_session(db, aad_object_id)
        await teams_repo.set_active_session(conversation_id, session_id, aad_object_id)
        logger.info(
            "Teams conversation %s → %s session %s",
            conversation_id,
            "new" if state is None else "auto-rolled-over (stale) to new",
            session_id,
        )
        return session_id

    # Still active — refresh updated_at so the staleness clock resets
    # on real usage, not just from when the mapping was first created.
    await teams_repo.set_active_session(
        conversation_id, state["active_session_id"], aad_object_id
    )
    return state["active_session_id"]


async def _start_new_session(db, conversation_id: str, aad_object_id: str) -> str:
    """Shared by both handle_new_session_command (plain text) and
    handle_teams_attachment (when the command arrives alongside a
    file — see that function for why the attachment is still
    processed against the NEW session, not discarded)."""
    new_session_id = await session_service.create_session(db, aad_object_id)
    teams_repo = TeamsConversationRepository(db)
    await teams_repo.set_active_session(conversation_id, new_session_id, aad_object_id)
    logger.info(
        "Teams conversation %s explicitly started new session %s",
        conversation_id, new_session_id,
    )
    return new_session_id


async def handle_new_session_command(
    db, conversation_id: str, aad_object_id: str
) -> str:
    await _start_new_session(db, conversation_id, aad_object_id)
    return (
        "Starting a new session — please share your evaluation "
        "criteria whenever you're ready."
    )


async def handle_teams_message(
    db, sync_db, checkpointer, conversation_id: str, aad_object_id: str,
    text: str,
) -> str:
    """Plain text message, no attachment."""
    if text.strip().lower() in _NEW_SESSION_COMMANDS:
        return await handle_new_session_command(db, conversation_id, aad_object_id)

    session_id = await resolve_session_for_conversation(db, conversation_id, aad_object_id)
    return await chat_service.send_message(
        db=db, sync_db=sync_db, checkpointer=checkpointer,
        session_id=session_id, user_id=aad_object_id, message_text=text,
    )


async def handle_teams_attachment(
    db, sync_db, checkpointer, conversation_id: str, aad_object_id: str,
    filename: str, file_bytes: bytes, accompanying_text: str,
) -> str:
    """
    Disambiguates a Teams file attachment between "this is my
    criteria document" and "this is my proposal to evaluate" —
    deterministic by stage wherever the stage already implies the
    answer; a plain clarifying question for the genuinely ambiguous
    remaining stages.

    ALSO checks accompanying_text against _NEW_SESSION_COMMANDS
    first — a prior version only checked this in handle_teams_message
    (plain text, no attachment), meaning "here's my new document,
    /new" would have silently ignored the reset command and just
    uploaded into the OLD session. Fixed: if the command is present,
    a new session is started FIRST, and the attachment is then
    processed against that fresh session (which is always at
    "awaiting_criteria", so it naturally falls into the criteria-
    upload branch below) — a sensible combined action, not two
    conflicting things happening to two different sessions.

    See handle_teams_message's fix note above for the accompanying_text
    bug this branch was already corrected for separately.
    """
    if accompanying_text.strip().lower() in _NEW_SESSION_COMMANDS:
        session_id = await _start_new_session(db, conversation_id, aad_object_id)
        stage, criteria_confirmed = "awaiting_criteria", False
    else:
        session_id = await resolve_session_for_conversation(db, conversation_id, aad_object_id)
        graph = build_graph(checkpointer)
        config = {"configurable": {"thread_id": session_id}}
        snapshot = await graph.aget_state(config)
        stage = snapshot.values.get("stage", "awaiting_criteria") if snapshot.values else "awaiting_criteria"
        criteria_confirmed = snapshot.values.get("criteria_confirmed", False) if snapshot.values else False

    if stage == "awaiting_criteria" and not criteria_confirmed:
        criteria_upload_repo = CriteriaUploadRepository(db)
        parsed_documents = parse_document(file_bytes, filename)
        full_text = "\n\n".join(d.page_content for d in parsed_documents)
        await criteria_upload_repo.set_pending_text(session_id, aad_object_id, full_text)
        logger.info(
            "Teams: treated attachment as criteria document for session %s",
            session_id,
        )
        return await chat_service.send_message(
            db=db, sync_db=sync_db, checkpointer=checkpointer,
            session_id=session_id, user_id=aad_object_id,
            message_text=accompanying_text or "here is my criteria document",
        )

    if stage == "awaiting_document":
        await submission_service.upload_submission_file(
            db, session_id, aad_object_id, filename, file_bytes,
        )
        logger.info(
            "Teams: uploaded submission document for session %s, "
            "forwarding accompanying_text=%r through the real graph",
            session_id, bool(accompanying_text),
        )
        # Real graph invocation — classify_intent → request_document
        # → possible same-turn handoff into run_evaluation if
        # criteria are already confirmed. This is why NOTHING is
        # silently dropped anymore: whatever the user actually typed
        # alongside the file gets genuinely processed, not discarded.
        return await chat_service.send_message(
            db=db, sync_db=sync_db, checkpointer=checkpointer,
            session_id=session_id, user_id=aad_object_id,
            message_text=accompanying_text or "here is the document",
        )

    # GENUINELY ambiguous stage (evaluated, awaiting_criteria_confirmation,
    # awaiting_new_document_criteria_choice) — ask plainly, don't guess.
    #
    # NOTE, a related but separate open question, not solved here:
    # if accompanying_text at one of THESE stages actually clarifies
    # intent (e.g. "I have a different document" at stage=="evaluated"
    # alongside an attachment), the file currently is NOT uploaded at
    # all and the clarification is discarded too — the user has to
    # respond to this clarifying question, then re-attach the file a
    # second time. Worth deciding deliberately whether accompanying_text
    # should ALSO be checked here before falling back to asking —
    # flagged, not fixed, since it changes the disambiguation logic
    # itself rather than just fixing a dropped-parameter bug.
    logger.info(
        "Teams: ambiguous attachment at stage=%s for session %s — asking "
        "the user to clarify rather than guessing.", stage, session_id,
    )
    return (
        "I see you've attached a file, but I'm not sure whether this is "
        "updated evaluation criteria or a new document to evaluate — "
        "could you clarify?"
    )