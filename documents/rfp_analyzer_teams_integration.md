# RFP Analyzer — Teams Integration (Design & Build Tracker)

**Status: FULLY SCAFFOLDED against confirmed real SDK signatures,
with a thorough edge-case review pass completed.** Every SDK
construction call (config, adapter, downloader, agent app) has been
verified against direct inspection of the installed package (see
Section 3a). A dedicated edge-case review found and fixed several
real bugs (Section 3b). Genuinely still open: (1) whether Teams'
nested attachment downloadUrl requires bearer-token auth, (2) a full
end-to-end test against a real Teams client, which nothing short of
that can confirm for the attachment path specifically.

This is a satellite document to `rfp_analyzer_graph_structure.md` —
the core RFP Analyzer graph/backend is UNCHANGED and UNAFFECTED by
anything in this file. Teams is purely an additional client sitting
in front of the existing `chat_service`/`submission_service` layer,
same relationship Streamlit already has.

---

## 1. Confirmed facts (verified against current Microsoft docs)

- **Microsoft 365 Agents SDK is orchestrator-agnostic** — wraps an
  existing LangGraph app, does not replace it. Package family:
  `microsoft_agents.hosting.core`, `.teams`, `.fastapi`,
  `.authentication.msal`.
- **This is the "Custom Engine Agent" pattern**, not a "declarative
  agent" (which would use Copilot's own orchestrator — not
  applicable here, since RFP Analyzer already has its own graph).
- **Azure Bot is a standalone Azure resource** — creatable directly
  ("Create a resource" → search "Azure Bot" → Create), no Foundry
  hub/project required. Foundry's "Hosted agents" feature can ALSO
  create one on your behalf as a byproduct of ITS OWN managed-hosting
  flow, but that's irrelevant here since RFP Analyzer is self-hosted
  in Kubernetes — you're using the plain, direct creation path.
- **Real-time message routing does NOT go through the manifest.**
  The manifest only matters at install time (tells Teams "this app
  ↔ this App ID"). Live routing: Teams → Bot Framework Connector
  Service → looks up the Azure Bot resource registered under that
  App ID → forwards to that resource's configured "Messaging
  endpoint" (a plain URL, entirely your choice, e.g.
  `https://.../rfpanalyzer/api/messages`) → your backend.
- **The messaging endpoint receives a fixed Activity JSON shape**
  via POST, authenticated via a JWT in the Authorization header
  (proves the request came from Microsoft's infrastructure — this
  is NOT the end user's personal identity token). The SDK's job is
  validating this JWT correctly and parsing/constructing Activity
  objects — non-trivial security-critical plumbing, not just "read
  JSON."
- **The end user's identity comes for free, no SSO required:**
  `activity.from.aadObjectId` — a real, stable Entra Object ID GUID
  — is present on every incoming Activity by default, confirmed from
  real captured payloads. `from.name` (display name) and
  `conversation.tenantId` also present. Full SSO/OAuth (the
  SNOW-ticket enterprise flow) is a SEPARATE, heavier mechanism only
  needed if you want a live, callable Graph token on the user's
  behalf — NOT needed just to identify who's talking.
- **Email specifically**: not present by default. Requires either
  full user SSO, OR a lighter app-only Graph call
  (`TeamsInfo.get_member` using the BOT's own credentials + an
  admin-consented `User.Read.All` application permission) — no
  per-user consent screen either way for the app-only route.
- **File attachments ONLY work in Teams "personal" (1:1) scope** —
  not channels, not group chats. Given RFP content confidentiality,
  personal scope is the natural fit anyway.
- **File attachments CANNOT be tested via the Agents Playground or
  dev tunnels** — confirmed limitation. Real end-to-end upload
  testing requires a genuinely registered bot + a real Teams client.
  Plain text chat CAN be tested via Playground/dev tunnel first.
- **Per-environment separation is required, not optional** — each
  of dev/test/stage/prod needs its OWN Azure Bot resource (own App
  ID, own secret, own messaging endpoint), because a Messaging
  endpoint is a single fixed URL per Bot resource with no way to
  branch by environment. Only the prod build's manifest/app package
  goes into the org-wide Teams Admin Center catalog; dev/test/stage
  stay at manual sideload (installed one tester at a time).

---

## 2. Decisions made in this conversation

| Decision | Chosen | Reasoning |
|---|---|---|
| Conversation-to-session mapping | **One Teams conversation = one session by default**, with an explicit reset command | The graph already has a full mechanism for "evaluate something else" (`classify_post_evaluation_intent` Category C) — this matches that continuation model. Teams has no sidebar, so "switch to an old session" has no natural UI surface; deferred rather than built speculatively. |
| Reset mechanism | A recognized text command (e.g. `new evaluation`) intercepted **before** the graph, since creating a new session is a cross-session operation the graph's `thread_id`-scoped API can't express | Mirrors what Streamlit's "+ New session" button does client-side, via text instead of a UI element, since Teams has no button equivalent by default |
| Getting back to an OLD session after reset | **Deferred, not built.** `/sessions` list + switch-back command was discussed as a future option (plain text or Adaptive Card) | Real added complexity (Adaptive Card decision, or plain-text session picker); no evidence yet that users need this — build only if it turns out to matter |
| Identity source | `activity.from.aadObjectId` directly, NO full SSO flow | Sufficient for `user_id` mapping; SSO only buys a callable Graph token, which RFP Analyzer doesn't need |
| Email | Deferred — app-only Graph call (`TeamsInfo.get_member`) is the recommended lighter-weight approach IF ever needed | Not required for core functionality; adds one more admin-consent step, so only build if a real need for email surfaces |
| Attachment disambiguation (criteria doc vs. proposal doc) | **Deterministic by `stage` wherever stage already implies the answer** (`awaiting_criteria`→criteria doc, `awaiting_document`→proposal doc); only a plain clarifying question (NOT a new LLM classification node) for the genuinely ambiguous remaining stages | Consistent with this build's established "deterministic first" principle (`request_document`'s file-presence check, etc.) — avoids inventing new LLM-based machinery for what's mostly a solved problem via existing state |
| Hosting model | **Self-hosted in Kubernetes**, NOT Foundry "Hosted agents" | Existing infra decision — the Foundry hosted-container path is a genuine alternative but not the one in use here |
| Gateway | Sits behind existing Ocelot orchestrator microservice, via a NEW route entry in a `config-rfpanalyzer.json`-style file | Same pattern as this service's other exposed routes — **Ocelot config file itself not yet seen/confirmed** |

---

## 3. Explicitly open / unconfirmed — DO NOT build against these yet

- **Ocelot route configuration** — `config-rfpanalyzer.json`'s real
  schema (Ocelot version, `ReRoutes` vs `Routes`, path template
  format, auth-per-route options) has NOT been seen. The Teams
  messaging endpoint route entry cannot be written correctly without
  this.
- **Terraform module outputs** — confirmed to exist ("I got the
  terraform module as well") but its actual output names (App ID,
  secret, messaging endpoint parameterization per environment) have
  NOT been reviewed. Config field names in the code below are
  placeholders pending this.
- **`microsoft_agents` SDK exact API surface** — architecture
  (what data needs extracting from an Activity, how it dispatches)
  is solid; exact class/method names (`TeamsActivityHandler`,
  `CloudAdapter`, the FastAPI hosting helper's real signature,
  attachment-download helper) have NOT been verified against a real
  installed package version. Any code referencing these needs a
  real `pip install` + inspection pass first.
- **`app/config.py`'s actual settings pattern** — not yet seen in
  this conversation; new Teams-related config fields (App ID,
  secret, etc.) need to match whatever convention already exists
  there (e.g. does `settings` read from env vars directly, from a
  K8s-mounted secret file, from Azure Key Vault via Terraform-set
  env vars?).
- **`app/services/session_service.py`'s exact `create_session`
  signature** — assumed `(db, user_id) -> session_id` based on
  earlier-confirmed code, should still be re-verified in this
  context since `teams_service.py` calls it directly.

---

## 3a. SDK API — NOW CONFIRMED (previously the biggest unknown)

Verified against real, current Microsoft documentation and official
samples — the following is accurate, not guessed:

```python
from microsoft_agents.activity import Activity, load_configuration_from_env
from microsoft_agents.hosting.core import (
    AgentApplication, TurnContext, TurnState, MemoryStorage, Authorization,
)
from microsoft_agents.hosting.aiohttp import CloudAdapter  # adapter class lives here regardless of aiohttp vs FastAPI hosting
from microsoft_agents.hosting.fastapi import start_agent_process  # FastAPI-specific entry point
from microsoft_agents.hosting.teams import TeamsActivityHandler  # only if using the older ActivityHandler style, not needed for AgentApplication's decorator style
from microsoft_agents.authentication.msal import MsalConnectionManager, MsalAuth

# Confirmed real construction pattern:
agents_sdk_config = load_configuration_from_env(environ)
storage = MemoryStorage()
connection_manager = MsalConnectionManager(**agents_sdk_config)
adapter = CloudAdapter(connection_manager=connection_manager)
authorization = Authorization(storage, connection_manager, **agents_sdk_config)
agent_app = AgentApplication[TurnState](
    storage=storage, adapter=adapter, authorization=authorization, **agents_sdk_config
)

@agent_app.activity("message")
async def on_message(context: TurnContext, state: TurnState):
    await context.send_activity(f"you said: {context.activity.text}")

@agent_app.conversation_update("membersAdded")
async def welcome(context: TurnContext, state: TurnState): ...
```

**Config convention confirmed as env-var based, hierarchical,
double-underscore-delimited** (e.g.
`CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENT_ID`) — a
genuinely DIFFERENT convention from this project's existing
`app.config.settings` Pydantic object. Kept deliberately separate
in `app/teams/config.py` rather than force-fit into the existing
pattern (see that file's own docstring for why).

**Still NOT confirmed:**
- Exact attribute path for the sender's Entra ID on `Activity` in
  the Python SDK specifically (`from_property.aad_object_id` is the
  best-guess based on the JSON schema's `from.aadObjectId`, adjusted
  for `from` being a Python reserved word — NOT verified against
  real SDK source/type stubs).
- Real attachment-download mechanism — whether the SDK exposes a
  direct helper, or it needs a raw HTTP call against
  `attachment.content_url` with a manually-acquired bearer token.
  Both are explicitly marked `NotImplementedError` in Section 4's
  code, not silently guessed.

---

## 3b. Edge-case review pass — bugs found and fixed

1. **Real, confirmed bug: accompanying text was silently dropped**
   when a file was attached while `stage == "awaiting_document"` —
   the code uploaded the file and returned a generic "Received
   {filename}" with no regard for what the user typed alongside it
   (e.g. "here it is, please evaluate against pricing and quality"
   lost the criteria detail entirely). **Fixed**: both the criteria-
   document AND submission-document branches now forward
   `accompanying_text` through `chat_service.send_message`, which
   also means Teams can now produce a complete rendered evaluation
   in ONE turn (file + text together) when criteria are already
   confirmed — something REST/Streamlit can't do, since they're
   structurally forced into two separate calls.
2. **Real, confirmed bug: Teams' actual inbound file-share
   attachments use a specific content type**
   (`application/vnd.microsoft.teams.file.download.info`) where the
   REAL, directly-downloadable URL is nested inside
   `attachment.content["downloadUrl"]` — the top-level `content_url`
   may just be a SharePoint web-page link, not something you can GET
   directly. A prior version of the downloader used `content_url`
   unconditionally. **Fixed**: `_resolve_download_url()` checks the
   content type first and prefers the nested URL when present.
3. **Real gap: `on_message`'s entire body had no error handling** —
   any exception (an LLM call failing, a DB hiccup) meant
   `context.send_activity` was never called at all, leaving the user
   with NO reply whatsoever — worse than a REST client's HTTP 500,
   which at least signals failure. **Fixed**: the whole turn is now
   wrapped in try/except, logging the real error and sending the
   user a plain, honest failure message instead of silence.
4. **Real gap: the "new session" command was only checked in the
   plain-text path** — attaching a file with "/new" as the
   accompanying text would have silently ignored the reset request
   and uploaded into the OLD session. **Fixed**: `handle_teams_attachment`
   now checks for the command first; if present, a new session is
   started and the attachment is processed against THAT fresh
   session (always `awaiting_criteria`, so it naturally falls into
   the criteria-upload branch) — a sensible combined action.
5. **Minor: the attachment downloader was being constructed twice**
   — once in `adapter.py` for `ApplicationOptions.file_downloaders`,
   once again in `agent_app.py` for direct calling. Both held the
   same `connection_manager` and were functionally redundant.
   **Fixed**: built once in `adapter.py`, stored on
   `app.state.teams_attachment_downloader`, passed into
   `configure_agent_app` rather than reconstructed.
6. **Minor: no redirect-following on the attachment download HTTP
   GET** — SharePoint/OneDrive-hosted URLs commonly redirect.
   **Fixed**: `httpx.AsyncClient(follow_redirects=True)`.

### Known limitations, deliberately NOT fixed (flagged, not ignored)

- **The ambiguous-attachment-stage case** (`evaluated`,
  `awaiting_criteria_confirmation`, `awaiting_new_document_criteria_choice`)
  still asks the user to clarify and discards the attachment
  entirely, even if accompanying_text would have resolved the
  ambiguity (e.g. "I have a different document" alongside a genuinely
  new attachment at `stage=="evaluated"`). This is a related but
  separate design question from the bugs above — fixing it changes
  the disambiguation logic itself, not just a dropped parameter.
- **No concurrency/race protection** on session resolution — if the
  same Teams conversation receives two messages in rapid succession,
  there's a theoretical race on the staleness-rollover check. Would
  need distributed locking to close fully; not addressed, flagged as
  a known limitation for a first version.
- **Only the first attachment is processed** if a user attaches
  multiple files in one message — deliberate v1 scope limit, not a
  bug, consistent with the "first attachment only" note throughout.
- **Whether the nested `downloadUrl` needs the SAME bearer-token
  auth as a plain `content_url` GET is still unconfirmed** — official
  examples show a plain GET with no explicit auth header, which may
  mean this specific URL doesn't need one at all. The code still
  attempts one anyway; only a real Teams client test can confirm
  whether that's necessary, harmless, or actually wrong.

## 4. Code scaffold — production folder structure, built against the CONFIRMED SDK API above

Structure:
```
app/
  teams/                              ← NEW package, SDK bootstrapping only
    __init__.py
    config.py                         ← SDK's own env-based config loader
    adapter.py                        ← CloudAdapter/MsalConnectionManager/Authorization setup
    agent_app.py                      ← AgentApplication + activity handlers, delegates to teams_service
  repository/
    teams_conversation_repository.py  ← matches existing repository convention
  services/
    teams_service.py                  ← matches existing service convention
  api/
    teams.py                          ← matches existing api convention
```

**Design principle applied throughout:** `app/teams/*` is SDK
plumbing ONLY (mirrors the role `checkpointer.py`/`database.py`
play for their concerns) — zero business logic. All real decision
logic (session mapping, attachment disambiguation, command
interception) lives in `app/services/teams_service.py`, reached
via thin handlers in `agent_app.py`, matching this project's
existing repository/service/api layering exactly.

**Full files delivered as separate artifacts** (not inlined here to
keep this doc readable) — see the files shared alongside this
update: `app/teams/__init__.py`, `app/teams/config.py`,
`app/teams/adapter.py`, `app/teams/agent_app.py`,
`app/repository/teams_conversation_repository.py`,
`app/services/teams_service.py`, `app/api/teams.py`, and a
`main.py` diff showing exactly what to add to the lifespan.

**Two explicit `NotImplementedError` stubs remain** in
`agent_app.py`'s `_download_attachment` — this is the one piece
that genuinely cannot be written correctly without either (a) real
SDK source/type-stub inspection, or (b) a working test against a
real Teams client, since attachment handling is confirmed
untestable via Playground/dev tunnel.

Old draft code below is now SUPERSEDED by the scaffold above — kept
only for historical continuity of the design discussion, not as a
second, conflicting version to implement.

### 4.1 `app/repository/teams_conversation_repository.py`

```python
# Maps a Teams conversation_id to whichever session_id is currently
# "active" for it. See Section 2's decision table for why this
# exists and what it deliberately does NOT do (no full multi-session
# switcher yet).

import logging
from datetime import datetime, timezone
from typing import Optional

from pymongo.asynchronous.database import AsyncDatabase

logger = logging.getLogger("app.repository.teams_conversation_repository")

TEAMS_CONVERSATIONS_COLLECTION = "teams_conversations"


class TeamsConversationRepository:

    def __init__(self, db: AsyncDatabase):
        self._collection = db[TEAMS_CONVERSATIONS_COLLECTION]

    async def get_active_session(self, conversation_id: str) -> Optional[str]:
        doc = await self._collection.find_one({"_id": conversation_id})
        return doc.get("active_session_id") if doc else None

    async def set_active_session(
        self, conversation_id: str, session_id: str, user_id: str
    ) -> None:
        await self._collection.update_one(
            {"_id": conversation_id},
            {"$set": {
                "active_session_id": session_id,
                "user_id": user_id,
                "updated_at": datetime.now(timezone.utc),
            }},
            upsert=True,
        )
        logger.info(
            "Teams conversation %s now points at session %s",
            conversation_id, session_id,
        )
```

### 4.2 `app/services/teams_service.py`

```python
# Translation layer between a Teams Activity and RFP-Analyzer's
# existing session logic. Delegates all real work to
# chat_service/submission_service — does NOT reimplement any
# evaluation/criteria logic itself.

import logging

from app.agent.graph import build_graph
from app.repository.criteria_upload_repository import CriteriaUploadRepository
from app.repository.teams_conversation_repository import TeamsConversationRepository
from app.services import chat_service, session_service, submission_service

logger = logging.getLogger("app.services.teams_service")

_NEW_EVALUATION_COMMANDS = {"new evaluation", "/new", "start new evaluation"}


async def resolve_session_for_conversation(
    db, conversation_id: str, aad_object_id: str
) -> str:
    teams_repo = TeamsConversationRepository(db)
    session_id = await teams_repo.get_active_session(conversation_id)

    if session_id is None:
        session_id = await session_service.create_session(db, aad_object_id)
        await teams_repo.set_active_session(conversation_id, session_id, aad_object_id)
        logger.info(
            "New Teams conversation %s → created session %s for user %s",
            conversation_id, session_id, aad_object_id,
        )

    return session_id


async def handle_new_evaluation_command(
    db, conversation_id: str, aad_object_id: str
) -> str:
    new_session_id = await session_service.create_session(db, aad_object_id)
    teams_repo = TeamsConversationRepository(db)
    await teams_repo.set_active_session(conversation_id, new_session_id, aad_object_id)

    logger.info(
        "Teams conversation %s explicitly reset to new session %s",
        conversation_id, new_session_id,
    )
    return (
        "Starting a new evaluation — your previous conversation history "
        "in this chat stays visible above, but I won't reference it "
        "going forward. Please share your evaluation criteria whenever "
        "you're ready."
    )


async def handle_teams_message(
    db, sync_db, checkpointer, conversation_id: str, aad_object_id: str,
    text: str,
) -> str:
    if text.strip().lower() in _NEW_EVALUATION_COMMANDS:
        return await handle_new_evaluation_command(db, conversation_id, aad_object_id)

    session_id = await resolve_session_for_conversation(db, conversation_id, aad_object_id)
    return await chat_service.send_message(
        db=db, sync_db=sync_db, checkpointer=checkpointer,
        session_id=session_id, user_id=aad_object_id, message_text=text,
    )


async def handle_teams_attachment(
    db, sync_db, checkpointer, conversation_id: str, aad_object_id: str,
    filename: str, file_bytes: bytes, accompanying_text: str,
) -> str:
    session_id = await resolve_session_for_conversation(db, conversation_id, aad_object_id)

    graph = build_graph(checkpointer)
    config = {"configurable": {"thread_id": session_id}}
    snapshot = await graph.aget_state(config)
    stage = snapshot.values.get("stage", "awaiting_criteria") if snapshot.values else "awaiting_criteria"
    criteria_confirmed = snapshot.values.get("criteria_confirmed", False) if snapshot.values else False

    if stage == "awaiting_criteria" and not criteria_confirmed:
        criteria_upload_repo = CriteriaUploadRepository(db)
        from app.documents.parser import parse_document
        parsed_documents = parse_document(file_bytes, filename)
        full_text = "\n\n".join(d.page_content for d in parsed_documents)
        await criteria_upload_repo.set_pending_text(session_id, aad_object_id, full_text)
        logger.info("Teams: treated attachment as criteria document for session %s", session_id)
        return await chat_service.send_message(
            db=db, sync_db=sync_db, checkpointer=checkpointer,
            session_id=session_id, user_id=aad_object_id,
            message_text=accompanying_text or "here is my criteria document",
        )

    if stage == "awaiting_document":
        result = await submission_service.upload_submission_file(
            db, session_id, aad_object_id, filename, file_bytes,
        )
        from app.api import documents as documents_api
        confirmation = None
        if documents_api.post_upload_hook is not None:
            confirmation = await documents_api.post_upload_hook(
                db, checkpointer, session_id, aad_object_id
            )
        return confirmation or f"Received {result['filename']}."

    logger.info(
        "Teams: ambiguous attachment at stage=%s for session %s — asking "
        "the user to clarify rather than guessing.", stage, session_id,
    )
    return (
        "I see you've attached a file, but I'm not sure whether this is "
        "updated evaluation criteria or a new document to evaluate — "
        "could you clarify?"
    )
```

### 4.3 `app/api/teams.py` — has THREE unresolved `NotImplementedError` stubs

```python
# Real SDK calls needed here: parsing/validating the incoming
# Activity, downloading an attachment, sending the reply. None of
# these have been verified against the actual installed
# microsoft_agents package. See Section 3.

import logging

from fastapi import APIRouter, Request, Depends

from app.checkpointer import get_checkpointer
from app.database import get_database, get_sync_database
from app.services import teams_service

logger = logging.getLogger("app.api.teams")

router = APIRouter(prefix="/api", tags=["teams"])


@router.post("/messages")
async def teams_messages(
    request: Request,
    db=Depends(get_database),
    sync_db=Depends(get_sync_database),
):
    checkpointer = get_checkpointer(request.app)

    activity = await _parse_incoming_activity(request)  # UNRESOLVED

    conversation_id = activity.conversation.id
    aad_object_id = activity.from_property.aad_object_id  # field name TBD
    accompanying_text = activity.text or ""

    if activity.attachments:
        attachment = activity.attachments[0]
        file_bytes, filename = await _download_teams_attachment(attachment)  # UNRESOLVED
        reply_text = await teams_service.handle_teams_attachment(
            db=db, sync_db=sync_db, checkpointer=checkpointer,
            conversation_id=conversation_id, aad_object_id=aad_object_id,
            filename=filename, file_bytes=file_bytes,
            accompanying_text=accompanying_text,
        )
    else:
        reply_text = await teams_service.handle_teams_message(
            db=db, sync_db=sync_db, checkpointer=checkpointer,
            conversation_id=conversation_id, aad_object_id=aad_object_id,
            text=accompanying_text,
        )

    await _send_reply(activity, reply_text)  # UNRESOLVED
    return {"status": "ok"}


async def _parse_incoming_activity(request: Request):
    raise NotImplementedError("Wire to real microsoft_agents SDK call")


async def _download_teams_attachment(attachment) -> tuple[bytes, str]:
    raise NotImplementedError("Wire to real microsoft_agents SDK call")


async def _send_reply(activity, text: str) -> None:
    raise NotImplementedError("Wire to real microsoft_agents SDK call")
```

---

## 5. Real-world checklist for the eventual working version

```
[ ] Confirm app/config.py's settings pattern; add TEAMS_APP_ID /
    TEAMS_APP_PASSWORD (or Terraform-provided equivalents) matching
    that convention
[ ] pip install the real microsoft_agents packages; inspect actual
    class/method names; replace the three NotImplementedError stubs
[ ] Review the real Terraform module's outputs — App ID, secret,
    per-environment messaging endpoint parameterization
[ ] Review config-rfpanalyzer.json's real Ocelot schema; add the
    /api/messages route entry matching existing route patterns
[ ] Create the Azure Bot resource (per-environment) via the
    Terraform module
[ ] Set each environment's messaging endpoint to the Ocelot-facing
    URL (NOT the internal K8s service address directly)
[ ] Write manifest.json (App ID must match the Bot resource's),
    package with icons, sideload into own Teams client for dev testing
[ ] Test plain text flow first (works via Playground/dev tunnel)
[ ] Test attachment flow ONLY in a real installed Teams client
    (confirmed: cannot be tested via Playground/dev tunnel)
[ ] Decide whether app-only Graph email lookup (TeamsInfo.get_member)
    is actually needed before building it
```

---

**Next step, your call:** resolve any one of Section 3's open items
(Ocelot config, Terraform outputs, `app/config.py`'s pattern, or
installing the real SDK to check its API surface) — pick whichever
you have easiest access to first, and we confirm that piece
concretely before writing any code against it.
