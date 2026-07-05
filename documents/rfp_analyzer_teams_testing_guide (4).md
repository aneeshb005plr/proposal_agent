# RFP Analyzer — Teams Testing & Integration Guide

**Written for someone with zero prior Teams bot testing experience.**
Read this top to bottom the first time — it's ordered from
"easiest, needs nothing" to "hardest, needs everything," and each
stage tells you exactly what it does and doesn't prove.

Companion doc: `rfp_analyzer_teams_integration.md` — the code
itself and architecture decisions. This document is purely about
**how to actually run and verify it.**

---

## The four testing tiers, at a glance

| Tier | Needs | Proves | Doesn't prove |
|---|---|---|---|
| A — Direct unit testing | Nothing (just Python) | Your own logic (session mapping, disambiguation) is correct | Anything about the SDK, Teams, or real network calls |
| B — Agents Playground | Your app running locally + **a real Azure Bot resource's credentials** (confirmed required — anonymous mode does not work, see below) | The SDK wiring, Activity handling, plain-text conversation flow | **File attachments — confirmed impossible to test here** |
| C — Real Azure Bot + devtunnel | The same real Azure Bot resource, plus a devtunnel | Everything, including real Teams file attachments, with your code still running on your own machine | Nothing — this is the closest to real |
| D — Real deployment (K8s + Ocelot + Teams catalog) | Everything above, working | The actual production path | — |

**Start at Tier A. Only move to the next tier once the current one passes.** Skipping ahead means you can't tell whether a failure is your logic, the SDK wiring, or the network.

---

## TIER A — Direct unit testing (no bot, no SDK, no Teams, no Azure)

**What this tests:** the actual decision-making code in
`app/services/teams_service.py` — session mapping, staleness
rollover, attachment disambiguation, the new-session command. None
of this needs a running server, the `microsoft_agents` package, or
anything Teams-related at all — you're calling plain Python
functions directly.

**Why start here:** it's the fastest possible feedback loop (runs in
milliseconds, no network), and it isolates bugs in *your* logic from
bugs in the SDK integration — which matters a lot, since the SDK
layer is the part built on the least amount of directly-verified
information in this whole project.

### Setup

This project already has its own async Mongo client setup
(`app/database.py`'s `connect_to_mongo`) — reuse that directly
rather than importing `motor` raw, so this test script uses the
exact same connection path (pooling, database name resolution,
etc.) as the real running app, not a separate one-off setup that
could behave differently.

```python
# test_teams_service_manual.py — run with: python test_teams_service_manual.py

import asyncio
from fastapi import FastAPI

from app.database import connect_to_mongo, close_mongo_connection
from app.services import teams_service


async def main():
    # Reuses the SAME connection setup main.py's lifespan uses —
    # not a separate motor.motor_asyncio.AsyncIOMotorClient() call.
    app = FastAPI()
    await connect_to_mongo(app)
    db = app.state.mongo_db

    # Fake a Teams conversation_id and user id — these would
    # normally come from a real Activity, but for this tier we're
    # bypassing Teams entirely.
    fake_conversation_id = "test-conversation-001"
    fake_aad_object_id = "test-user-guid-001"

    # ── Test 1: first contact creates a session ──────────────────
    session_id = await teams_service.resolve_session_for_conversation(
        db, fake_conversation_id, fake_aad_object_id
    )
    print(f"Created session: {session_id}")

    # ── Test 2: same conversation resolves to the SAME session ────
    session_id_again = await teams_service.resolve_session_for_conversation(
        db, fake_conversation_id, fake_aad_object_id
    )
    assert session_id == session_id_again, "Should resolve to the same session!"
    print("Second call correctly resolved to the same session")

    # ── Test 3: a plain message actually reaches the real graph ───
    # NOTE: this call also needs sync_db/checkpointer — pull these
    # the same way, from app.state after connect_checkpointer(app)
    # has also run (see rfp_analyzer_test_documentation.md's
    # Prerequisites for the full pattern this project already uses).
    # from app.checkpointer import connect_checkpointer
    # connect_checkpointer(app)
    # reply = await teams_service.handle_teams_message(
    #     db=db, sync_db=app.state.mongo_sync_db, checkpointer=app.state.checkpointer,
    #     conversation_id=fake_conversation_id, aad_object_id=fake_aad_object_id,
    #     text="evaluate against: pricing, timeline",
    # )
    # print(f"Reply: {reply}")

    # ── Test 4: new-session command works ─────────────────────────
    reply = await teams_service.handle_new_session_command(
        db, fake_conversation_id, fake_aad_object_id
    )
    print(f"New-session command reply: {reply}")

    new_session_id = await teams_service.resolve_session_for_conversation(
        db, fake_conversation_id, fake_aad_object_id
    )
    assert new_session_id != session_id, "Should be a DIFFERENT session now!"
    print(f"Confirmed: new session created ({new_session_id} != {session_id})")

    await close_mongo_connection(app)


asyncio.run(main())
```

### What to check at this tier

```
[ ] First contact creates a session and writes a mapping row
[ ] Same conversation_id always resolves to the same session_id
    (until staleness or an explicit reset)
[ ] "new conversation" / "/new" / "start over" actually create a
    NEW session_id, different from the previous one
[ ] Manually set a conversation's updated_at in the DB to
    (now - TEAMS_SESSION_STALE_DAYS - 1 day), then call
    resolve_session_for_conversation again — confirm it creates a
    NEW session automatically (this is the staleness rollover from
    the design discussion — test it directly, don't wait 3 real days)
[ ] Call handle_teams_attachment with fake file bytes at different
    stages (awaiting_criteria, awaiting_document, evaluated) and
    confirm the disambiguation logic picks the right branch each time
```

If any of these fail, **fix them before moving to Tier B** — Tier B
adds a whole extra layer (the SDK) on top, and you don't want to be
debugging two things at once.

---

## TIER B — Microsoft 365 Agents Playground (local, no Azure, no Teams)

**What this tests:** that your `app/teams/` SDK wiring — `AgentApplication`, the activity handlers, `start_agent_process` — actually works, and that plain-text conversations flow correctly end to end. **This tier CANNOT test file attachments — confirmed, not a guess** (Microsoft's own docs and multiple real bug reports confirm attachment handling doesn't work through this tool). Don't waste time trying.

### Install the Playground

It's a Node.js tool, not a Python package (your project stays pure Python — this is a separate CLI you install once on your dev machine). The real package name, confirmed against its npm listing — **not** the guessed name from an earlier draft of this guide:

```bash
npm install -g @microsoft/m365agentsplayground
```

Verify it installed correctly:
```bash
agentsplayground --version
```

(There's also an older, deprecated package, `@microsoft/teams-app-test-tool` — its own npm page says outright *"Use @microsoft/m365agentsplayground instead. This package is maintained for backward compatibility."* If you see that name referenced anywhere else, it's the same tool, just the old name.)

### Before you run anything: you need real credentials, even here

**CORRECTION, based on real testing:** an earlier version of this
guide said anonymous/no-credential testing was possible at this
tier. That's what Microsoft's own docs claim, but it did NOT hold
up in practice — confirmed via two separate real failures AND
direct inspection of the installed SDK's source. `MsalConnectionManager`
unconditionally wraps every connection in a real `MsalAuth`
instance and attempts genuine tenant/client resolution, with zero
reference to any "anonymous" flag anywhere in that path. Whatever
Microsoft's anonymous mode is meant to do, it doesn't work through
the construction this project uses.

**The practical conclusion: get one real Azure Bot resource — even
for this tier.** You already have the Terraform module for this
(see Tier C below for the exact steps) — a free-tier (F0) resource
costs nothing and takes a few minutes. Set its real
`TEAMS_APP_ID` / `TEAMS_APP_PASSWORD` / `TEAMS_TENANT_ID` before
starting your app, for ANY tier from here on, including this one.
The genuine advantage this tier (the Playground) still has over
Tier C is **no devtunnel and no sideloading needed** — you still
don't need a publicly reachable URL or a real Teams client for
plain-text testing, just real credentials configured.

If `TEAMS_APP_ID` is left unset, `build_agent_auth_configuration()`
now raises a clear `ValueError` at startup instead of silently
attempting a doomed anonymous connection — fail fast and obviously,
rather than a confusing runtime 500 on the first message.

### Run your app

Restart your app after applying the config fix above, so the change actually takes effect:

```bash
# From your project root, however you normally start it:
uvicorn app.main:app --reload --port 3978
```

Port `3978` is the Playground's default expectation — using it avoids having to pass extra flags, though any port works if you tell the Playground which one.

### Run the Playground, pointed at your local app

```bash
agentsplayground -e "http://localhost:3978/api/messages" -c "emulator" \
  --client-id "your-real-TEAMS_APP_ID" \
  --client-secret "your-real-TEAMS_APP_PASSWORD" \
  --tenant-id "your-real-TEAMS_TENANT_ID"
```

This opens a chat window in your terminal/browser that looks and
behaves like a real Teams conversation, and talks directly to your
local FastAPI server — **still no devtunnel and no sideloading
needed for this step**, but real credentials ARE required (see
above — anonymous mode does not work with this SDK's actual
construction, confirmed by direct source inspection and real
failures).

Same credentials as your app's own `.env` — you're just also
telling the Playground's own client to authenticate as the same
bot when it calls your endpoint.

Alternatively, environment variables work instead of flags (useful
if you don't want secrets in shell history):
```bash
export BOT_ENDPOINT="http://localhost:3978/api/messages"
export DEFAULT_CHANNEL_ID="emulator"
export AUTH_CLIENT_ID="your-real-TEAMS_APP_ID"
export AUTH_CLIENT_SECRET="your-real-TEAMS_APP_PASSWORD"
export AUTH_TENANT_ID="your-real-TEAMS_TENANT_ID"
agentsplayground
```

### One important flag: the channel ID

By default the Playground uses an `"emulator"` channel, which is a generic testing channel — it does **not** produce Teams-specific data shapes (like the `aad_object_id` field your code depends on for `user_id`). To actually exercise Teams-specific behavior:

```bash
agentsplayground -e "http://localhost:3978/api/messages" -c "msteams"
```

### What to check at this tier

```
[ ] Sending "hi" or joining triggers your welcome message
    (the @agent_app.conversation_update("membersAdded") handler)
[ ] Sending "evaluate against: pricing, timeline" produces the
    real criteria-extraction response — confirms the FULL chain
    (Activity → teams_service.handle_teams_message →
    chat_service.send_message → the actual LangGraph) is wired
    correctly, not just that a reply comes back
[ ] Sending "new conversation" resets things — check your app's
    logs to confirm a new session_id was actually created, not
    just that SOME reply came back
[ ] Check your app's logs for the error-handling fix — if you want
    to test it, temporarily break something (e.g. point GENAI_BASE_URL
    at an invalid URL) and confirm you get the plain "Sorry,
    something went wrong" message, NOT total silence
[ ] Try WITHOUT the -c "msteams" flag first (emulator channel), then
    WITH it — confirm from_property.aad_object_id is actually
    populated when using msteams, since some fields differ by channel
```

**If plain text works here, you've validated ~90% of the integration** — everything except attachment handling and the real Azure/network path.

---

## TIER C — Real Azure Bot + devtunnel (needed for real Teams client + attachments)

**What this tests:** everything, including the one thing Tier B
categorically cannot — real file attachments, since Teams' actual
attachment/consent-card behavior only exists when a genuine Teams
client is involved.

**Your code still runs on your own machine** — devtunnel just makes
your localhost reachable from the internet temporarily, so you don't
need to deploy to Kubernetes just to run this test.

### Step 1 — Create the Azure Bot resource

You already have the Terraform module for this. Apply it for a
`dev`/personal-testing environment. You'll get back:
- An **App ID** (a GUID)
- An **App secret**
- A place to set the **Messaging endpoint** (a URL field on the resource)

Set these into your `.env`/local settings as `TEAMS_APP_ID` /
`TEAMS_APP_PASSWORD` / `TEAMS_TENANT_ID` (per `app/config.py`'s
additions from the integration doc).

### Step 2 — Install and start a devtunnel

```bash
# One-time install: https://aka.ms/devtunnels-install
devtunnel create rfp-analyzer-dev
devtunnel port create rfp-analyzer-dev -p 3978
devtunnel host rfp-analyzer-dev
```

This prints a public URL like `https://abc123.devtunnels.ms`. Keep
this terminal running — closing it kills the tunnel.

### Step 3 — Point the Azure Bot's messaging endpoint at the tunnel

In the Azure Portal (or via Terraform, if parameterized), set the
Bot resource's **Messaging endpoint** to:
```
https://abc123.devtunnels.ms/api/messages
```
(your actual devtunnel URL + `/api/messages`)

### Step 4 — Run your app, with real auth this time

```bash
uvicorn app.main:app --reload --port 3978
```

Since `TEAMS_APP_ID`/`TEAMS_APP_PASSWORD` are now set, your app will
attempt real JWT validation against Microsoft's infrastructure —
this is the first tier where that code path actually gets exercised.

### Step 5 — Sideload the Teams app into your own Teams client

You need a `manifest.json` + two icon PNGs, zipped together:

```json
{
  "$schema": "https://developer.microsoft.com/en-us/json-schemas/teams/v1.17/MicrosoftTeams.schema.json",
  "manifestVersion": "1.17",
  "version": "1.0.0",
  "id": "<YOUR TEAMS_APP_ID GUID HERE>",
  "packageName": "com.yourcompany.rfpanalyzer.dev",
  "developer": {
    "name": "Your Company",
    "websiteUrl": "https://yourcompany.com",
    "privacyUrl": "https://yourcompany.com/privacy",
    "termsOfUseUrl": "https://yourcompany.com/terms"
  },
  "name": { "short": "RFP Analyzer (Dev)", "full": "RFP Analyzer (Development)" },
  "description": {
    "short": "Evaluates proposals against criteria",
    "full": "Evaluates proposals and RFP responses against evaluation criteria you provide"
  },
  "icons": { "color": "color.png", "outline": "outline.png" },
  "accentColor": "#FFFFFF",
  "bots": [
    {
      "botId": "<YOUR TEAMS_APP_ID GUID HERE — SAME as id above>",
      "scopes": ["personal"],
      "supportsFiles": true,
      "isNotificationOnly": false
    }
  ],
  "permissions": ["identity", "messageTeamMembers"],
  "validDomains": []
}
```

`color.png` (192×192) and `outline.png` (32×32, transparent) — any
placeholder images work for dev testing. Zip `manifest.json` +
both icons together (the icons must be at the zip's root, not in a
subfolder).

**Sideload it:**
Teams client → Apps → Manage your apps → Upload a custom app → select
your zip. (If this option is greyed out, your organization may need
an admin to enable custom app uploads — a Teams Admin Center
setting, not something you control from your side.)

### Step 6 — Test for real

```
[ ] Send a plain text message in the sideloaded app's chat — confirm
    it reaches your local server (check logs) and a real reply
    comes back through Teams' actual UI
[ ] Attach a real small document (a .txt or .docx) — THIS is the
    test that could never happen in Tier B. Check logs specifically
    for:
      - Which content_type the attachment actually has
      - Whether _resolve_download_url() picked the nested downloadUrl
        or fell back to content_url — confirms which code path is
        real for your actual Teams tenant/client version
      - Whether the bearer-token GET succeeded, or came back
        401/403 (which would answer the still-open question: does
        the nested downloadUrl need auth at all?)
[ ] Confirm the file's content actually got parsed and stored —
    check submission_chunks / rfp_pending_criteria in Mongo directly
[ ] Attach a file WITH accompanying text ("here it is, evaluate
    against pricing") — confirm the text wasn't dropped (this was
    the real bug found and fixed earlier)
[ ] Send "/new" — confirm a genuinely new session_id shows up
[ ] Deliberately break something (bad DB URI temporarily) and
    confirm you get the graceful error message in the real Teams
    UI, not silence
```

**This is the tier that actually proves the attachment path works.**
Nothing before this can.

---

## TIER D — Real deployment (Kubernetes + Ocelot + org-wide catalog)

Only attempt once Tier C fully passes. The only *new* things here,
versus Tier C:
- Messaging endpoint points at your real Ocelot-fronted public URL
  instead of a devtunnel
- Manifest's `botId` matches your **real, per-environment** Azure
  Bot resource (dev/test/stage/prod each need their own — see the
  integration doc's Section 1)
- Prod's manifest zip gets uploaded to **Teams Admin Center** for
  org-wide catalog distribution; dev/test/stage stay at manual
  sideload, one tester at a time

```
[ ] Confirm Ocelot's config-rfpanalyzer.json actually has a route
    entry forwarding to /api/messages (still an open item per the
    integration doc's Section 3 — resolve this before Tier D)
[ ] Repeat every check from Tier C's Step 6, but through the real
    deployed URL instead of a devtunnel
[ ] Confirm per-environment isolation: dev's bot only reaches dev's
    database, prod's bot only reaches prod's — an easy thing to
    get wrong if any config value is accidentally shared
```

---

## Quick troubleshooting reference

| Symptom | Likely cause | Check |
|---|---|---|
| Playground shows nothing / times out | Your app isn't running, or wrong port | Confirm `uvicorn` is actually up on the port the Playground is pointed at |
| "401 Unauthorized" in Tier C only | Real JWT validation is failing | Confirm `TEAMS_APP_ID`/`TEAMS_APP_PASSWORD`/`TEAMS_TENANT_ID` are set correctly and match the Azure Bot resource exactly |
| Attachment upload silently does nothing | You're still in Tier B | Attachments ONLY work in Tier C+ — this isn't a bug, it's a confirmed platform limitation |
| Sideload option greyed out | Org policy disables custom app upload | Ask a Teams admin to enable it, or use Developer Portal's preview-install flow instead |
| Bot never receives messages at all | Messaging endpoint URL wrong, or devtunnel not running | Re-check the exact URL set on the Azure Bot resource matches your current devtunnel session (it changes if you restart devtunnel without reusing the same tunnel name) |
| File downloads with a 401/403 in Tier C | The nested `downloadUrl` may not need (or may reject) the bearer token you're sending | This is the one still-open question from the code review — try the request without the Authorization header as a test |
