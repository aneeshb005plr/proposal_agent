# RFP Analyzer

An AI agent that evaluates proposals, RFP responses, and similar
submission documents against user-supplied evaluation criteria,
producing a structured scoring table, overall score, and executive
summary — built on LangGraph, FastAPI, and MongoDB.

**Start here if you're new to this codebase.** This document orients
you and gets you running locally; it deliberately does NOT duplicate
the deep-dive reference docs listed at the bottom — read this first,
then go to whichever of those actually matches what you're working on.

---

## What this agent does (per its own spec)

1. Collects evaluation criteria from the user (pasted text, or an
   uploaded criteria document)
2. Collects a submission document to evaluate
3. Scores it, criterion by criterion, 0–5, with evidence-based
   rationale citing specific pages/slides/sections
4. Renders a scoring table, overall score, and executive summary
5. Supports natural follow-ups: additional outputs from an existing
   evaluation, criteria changes, evaluating a new document — all
   without needing to restart the conversation

It's accessible through **three clients** today, all hitting the
same underlying REST API: **curl/Postman** (raw testing), a
**Streamlit app** (`streamlit_app.py`, combined chat+file input box),
and **Microsoft Teams** (via the Microsoft 365 Agents SDK).

---

## Architecture, in one picture

```
                        ┌──────────────────────────┐
   curl / Streamlit ───▶│                            │
                        │      FastAPI (app/api/)     │
   Microsoft Teams  ───▶│  sessions · chat · documents │
   (via M365 Agents SDK)│  knowledge · teams            │
                        └──────────┬───────────────────┘
                                   │
                        ┌──────────▼───────────────────┐
                        │   app/services/                │
                        │   chat_service, submission_,     │
                        │   session_, knowledge_, teams_     │
                        └──────────┬───────────────────┘
                                   │
                     ┌─────────────▼──────────────┐
                     │   app/agent/ — the LangGraph  │
                     │   graph.py, state.py, nodes/    │
                     └─────────────┬──────────────┘
                                   │
                     ┌─────────────▼──────────────┐
                     │   app/repository/ (Mongo)      │
                     │   sessions, messages, knowledge, │
                     │   submissions, teams_conversations,│
                     │   knowledge_sources, sync_jobs      │
                     └────────────────────────────┘
```

**Everything the agent does turn-to-turn flows through the LangGraph**
(`app/agent/graph.py`) — this is the real heart of the system. Read
`rfp_analyzer_graph_structure.md` for the complete node-by-node
picture; this README only summarizes it.

---

## Folder structure

```
app/
  api/            # thin HTTP routes — no business logic
    sessions.py, chat.py, documents.py, knowledge.py, teams.py
  services/        # business logic — routes call these, never repos directly
    session_service.py, chat_service.py, submission_service.py,
    knowledge_service.py, teams_service.py
  repository/      # Mongo access, one file per resource
    session_repository.py, message_repository.py, submission_repository.py,
    criteria_upload_repository.py, knowledge_repository.py,
    teams_conversation_repository.py, job_repository.py, source_repository.py
  agent/           # the LangGraph itself
    graph.py        # wiring — start here
    state.py         # RFPAnalyzerState + DEFAULT_OVERWRITE_FIELDS
    context.py        # AgentContext (db, sync_db)
    criteria_extraction.py
    setup.py          # register_agent_hooks() — the post-upload hook wiring
    nodes/             # one file per graph node
  documents/        # parser.py, chunker.py — reused by uploads AND by
                    # the /internal/documents/process callback route
  knowledge/        # graph_client.py — NARROWED SCOPE, now used ONLY
                    # by risk_words.py (see its own docstring); the
                    # sync pipeline that used to live here has been
                    # DELETED, replaced by the separate knowledge-sync-worker
    risk_words.py
  security/
    encryption.py     # encrypts knowledge source secrets before storage
                      # — shares REGISTRY_ENCRYPTION_KEY with the worker
  teams/            # Microsoft 365 Agents SDK bootstrapping only —
                    # zero business logic, see its own docstrings
  auth/
    claims_resolver.py   # IDENTITY only (Entra JWT or dev dummy headers)
    authorization.py      # AUTHORIZATION — require_admin, require_internal_service
  schema/
    knowledge_source.py    # CreateSourceRequest/UpdateSourceRequest/SourceSummaryResponse
  config.py          # Pydantic Settings — single source of truth
  database.py         # Mongo connection setup
main.py               # app factory, lifespan (Mongo, checkpointer, Teams,
                       # risk words, hook registration — all wired here)
streamlit_app.py        # separate client, project root, not inside app/
```

---

## The evaluation workflow, briefly

```
awaiting_criteria → awaiting_criteria_confirmation → awaiting_document
                                                            │
                                                    ready_to_evaluate
                                                            │
                                              run_evaluation → generate_summary
                                                    → validate_output → render_output
                                                            │
                                                        evaluated
                                    (Additional outputs, criteria changes, and
                                     new-document requests all handled from here
                                     — and from earlier stages too, via
                                     classify_mid_flow_intent — without
                                     restarting the conversation)
```

`classify_intent` runs on **every** turn first — routing to social/
off-topic/knowledge-question handling before anything task-specific,
regardless of stage. Full detail, including every non-obvious design
decision and the real production incidents that shaped it:
`rfp_analyzer_graph_structure.md`.

---

## Quickstart (local dev)

```bash
git clone <this repo>
cd rfp-analyzer
pip install -r requirements.txt

cp .env.example .env
# fill in MONGODB_URI, GENAI_BASE_URL/GENAI_API_KEY, GRAPH_* (used
# only by risk_words.py now — see below), ENTRA_TENANT_ID/
# ENTRA_CLIENT_ID, INTERNAL_SERVICE_TOKEN, REGISTRY_ENCRYPTION_KEY

uvicorn app.main:app --reload --port 8000
```

Dev-mode auth: with `ENVIRONMENT != production`, requests without a
real `Authorization: Bearer` token fall back to `X-User-Id`/
`X-User-Email` headers — no real Entra token needed for local testing.

```bash
curl -X POST http://localhost:8000/api/sessions \
  -H "X-User-Id: test" -H "X-User-Email: test@pwc.com"
```

---

## Running the Streamlit client

```bash
# Streamlit >= 1.41 required — st.chat_input's accept_file feature
streamlit run streamlit_app.py
```
Sidebar lists sessions; the chat box accepts text and file attachments
together (upload runs first, then the message, in one submission).

---

## Configuration reference — the important ones

*(See `app/config.py` for the complete, authoritative list —
this is a curated summary, not exhaustive.)*

| Setting | Purpose |
|---|---|
| `MONGODB_URI` / `MONGODB_DB_NAME` | This agent's own isolated database |
| `GENAI_BASE_URL` / `GENAI_API_KEY` / `GENAI_LLM_MODEL` / `GENAI_EMBEDDINGS_MODEL` | The shared GenAI service |
| `ENTRA_TENANT_ID` / `ENTRA_CLIENT_ID` | This API's OWN App Registration — validates bearer tokens sent TO this API |
| `GRAPH_CLIENT_ID` / `GRAPH_CLIENT_SECRET` / `GRAPH_TENANT_ID` / `SHAREPOINT_*` | Used ONLY by `app/knowledge/risk_words.py` now (fetches one known file at startup) — NOT for knowledge-base sync anymore, that moved to the worker |
| `INTERNAL_SERVICE_TOKEN` | Checked by `require_internal_service`, protects `POST /api/knowledge/internal/documents/process` — must match the SAME value stored for this agent in the worker's `agent_registry` |
| `REGISTRY_ENCRYPTION_KEY` | Encrypts knowledge source secrets before storing them (`POST /api/knowledge/sources`) — **must be the same literal value as the worker's own setting**, or the worker cannot decrypt what this API writes |
| `TEAMS_APP_ID` / `TEAMS_APP_PASSWORD` / `TEAMS_TENANT_ID` | Azure Bot resource credentials — see the Teams docs below before touching these |
| `UPLOAD_AFTER_CONFIRMATION_POLICY` | `"invalidate"` (default) — re-uploading after `document_confirmed` clears prior chunks; see graph doc Section 9.10/9.12 for why this matters |

---

## Knowledge base sync — now handled by a separate service

**This agent no longer runs its own SharePoint sync inline.** That
work moved to a separate, shared repo — `knowledge-sync-worker` —
which serves every QuickSuite agent from one process. This agent's
own role is now three routes, all admin-only:

- `POST /api/knowledge/sources` — register a new knowledge source
  (any source_type, e.g. SharePoint) — secrets sent PLAIN in the
  request, encrypted here before storage (see `secret_fields` in the
  request body)
- `PATCH /api/knowledge/sources/{source_id}` — update a source
- `GET /api/knowledge/sources` — list this agent's sources (never
  returns secrets, encrypted or plain)
- `POST /api/knowledge/sync` — submits job(s) for the worker to pick
  up (does NOT run the sync itself)
- `GET /api/knowledge/sync/{job_id}` — poll job status

And one machine-to-machine route, called BY the worker, never by a
human:
- `POST /api/knowledge/internal/documents/process` — parses a file
  using THIS agent's own existing parser (`app/documents/parser.py`)

**Full registration example:**
```bash
curl -X POST http://localhost:8000/api/knowledge/sources \
  -H "X-User-Id: test" -H "X-User-Email: test@pwc.com" \
  -d '{
    "source_id": "sharepoint-main",
    "source_type": "sharepoint_graph",
    "config": {
      "site_id": "...", "knowledge_folder": "AI tool files/RFP Analyzer",
      "excluded_from_indexing": ["risk_words.txt"],
      "graph_client_id": "...", "graph_client_secret": "<real, plain>",
      "graph_tenant_id": "..."
    },
    "secret_fields": ["graph_client_secret"]
  }'

curl -X POST http://localhost:8000/api/knowledge/sync \
  -H "X-User-Id: test" -H "X-User-Email: test@pwc.com" \
  -d '{"source_id": "sharepoint-main"}'
```

**Before ANY of this works**, this agent must ALSO be registered
with the worker itself (a separate step, on the worker's own admin
API — see `knowledge-sync-worker`'s README).

**Admin access** to all of the above requires an entry in this
agent's own `admin_users` collection — not yet exposed via a
management route; insert directly for now.

Retrieval (used internally by `generate_summary` and
`answer_from_knowledge`) is completely unaffected by any of this.

---

## Microsoft Teams integration

A genuinely separate, substantial piece — do not attempt without
reading the dedicated docs first (linked below). Short version: the
Microsoft 365 Agents SDK bootstraps in `app/teams/`, with ZERO
business logic there — all real decision logic (session mapping,
attachment disambiguation, the automatic staleness-rollover for long-
idle conversations) lives in `app/services/teams_service.py`.

**Two hard, confirmed platform constraints, not implementation
choices:** file attachments in Teams only work in personal (1:1)
chat scope, and attachment handling categorically cannot be tested
via the local Agents Playground or a dev tunnel — only a real,
registered Azure Bot + a real Teams client can exercise that path.

---

## Testing

Full regression suite, organized in progressive tiers (core flow →
mid-flow intent handling → post-evaluation → session/document
lifecycle → knowledge Q&A): `rfp_analyzer_test_documentation.md`.
Teams-specific testing: `rfp_analyzer_teams_testing_guide.md`.
**Full end-to-end knowledge sync test, spanning THIS agent and the
worker together**: `rfp_analyzer_knowledge_sync_e2e_test.md` — run
this whenever either repo's sync-related code changes.

---

## A note on how this codebase evolved — read this before assuming something is a bug

This build has a long, real incident history — numbered, dated design
decisions and confirmed production bugs, each with its actual root
cause and fix documented, not just described in passing. **Before
"fixing" something that looks wrong, check
`rfp_analyzer_graph_structure.md` Section 8/9 first** — several things
that look like bugs at a glance (e.g. certain words being impossible
to fully filter from generated text, or `classify_post_evaluation_intent`
NOT having the same hard safety override `classify_mid_flow_intent`
has) are confirmed, deliberate, documented decisions — re-investigating
them without new evidence wastes time re-deriving something already
settled.

---

## Companion documents (read the one that matches your task)

| Doc | Read this for |
|---|---|
| `rfp_analyzer_graph_structure.md` | The complete graph, every node, every state field, the full incident history — the single source of truth for "why does the code look like this" |
| `rfp_analyzer_test_documentation.md` | The core regression test suite |
| `rfp_analyzer_teams_integration.md` | Teams architecture, confirmed SDK facts, open items |
| `rfp_analyzer_teams_testing_guide.md` | How to actually test Teams, from zero-infra up to a real client |
| `rfp_analyzer_knowledge_sync_e2e_test.md` | Full agent+worker integration test for knowledge sync |
| `quicksuite_knowledge_worker_design.md` | The separate knowledge-sync-worker service's full design |
| `quicksuite_reusable_infrastructure_reference.md` | What's genuinely shared across every QuickSuite agent, vs. what's RFP-Analyzer-specific |
