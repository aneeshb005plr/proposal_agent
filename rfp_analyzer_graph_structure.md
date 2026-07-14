# RFP Analyzer — Complete Build Reference (Single Source of Truth)

**Read this first if you're new to this project.** This document
describes the ENTIRE current build: what it does, how the graph is
wired, every repository/service it depends on, every bug that's
been found and fixed (and why), and everything still open. It is
written so a developer with zero prior context can understand how
this was built and why specific decisions were made — not just
what the code currently looks like.

Companion documents:
- `rfp_analyzer_test_documentation.md` — the regression test suite
- `quicksuite_reusable_infrastructure_reference.md` — shared
  platform infrastructure applicable to any agent, not RFP-Analyzer
  specific

**Keep this document current.** Whenever a node, route, repository
method, or state field changes, update this doc in the same pass —
it's only useful if it reflects reality.

---

## 1. What this agent does, and where the spec came from

RFP Analyzer scores a submission document (proposal, RFP response,
slide deck, etc.) against user-supplied evaluation criteria, and
produces a structured scoring table, overall score, and executive
summary. It was originally specified for Copilot Studio; this build
ports that spec to a LangGraph-based FastAPI service.

The **verbatim spec** describes a strictly linear flow only:
```
collect criteria (once) → confirm (once) → collect document (once)
→ evaluate → render output
```
Two hard constraints from the spec: *"Do NOT request the criteria
again once received and confirmed"* and *"Never request the same
document twice."* It also has an **Optional Outputs Policy**: never
proactively offer additional outputs (slide summary, improvement
suggestions, risk highlights); only produce them if explicitly
requested, and only after an evaluation is already complete.

**The spec is silent, not prohibitive, on everything else** — mid-
flow course correction (changing your mind about criteria/document
before the first evaluation), post-evaluation changes (new criteria,
new document), and general knowledge questions unrelated to the
active evaluation. All of these were deliberately built as
extensions beyond the spec's literal linear scope, based on real
testing showing users naturally do these things. This is documented
throughout this file wherever it applies — treat any deviation from
the strict linear spec as intentional, not a bug, unless flagged
otherwise.

---

## 2. State schema — `app/agent/state.py`

```python
class RFPAnalyzerState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]   # accumulates
    session_id: str
    user_id: str

    intent: Optional[Literal["social", "off_topic", "knowledge_question", "task_relevant"]]
    stage: Literal[
        "awaiting_criteria",
        "awaiting_criteria_confirmation",
        "awaiting_document",
        "awaiting_new_document_criteria_choice",
        "ready_to_evaluate",
        "evaluated",
    ]

    criteria: Optional[str]
    criteria_confirmed: bool
    criteria_weights: dict[str, float]

    document_confirmed: bool
    uploaded_filenames: list[str]

    scoring_results: Optional[dict]
    executive_summary: Optional[str]
    response_to_user: Optional[str]

    # classify_post_evaluation_intent
    post_eval_category: Optional[str]           # additional_output | criteria_change | new_document | unclear
    post_eval_output_description: Optional[str]

    # classify_mid_flow_intent
    mid_flow_category: Optional[str]             # on_script | new_document | criteria_edit | unclear

    # SHARED between classify_mid_flow_intent AND
    # classify_post_evaluation_intent — deliberately generalized so
    # reset_for_new_document behaves identically regardless of which
    # gate triggered it. NOT prefixed by which node set them.
    keep_criteria: Optional[bool]
    keep_criteria_specified: Optional[bool]
```

`DEFAULT_OVERWRITE_FIELDS` (see Section 6) also lives in this file
— every field above with no LangGraph reducer MUST have an entry
there, or a brand-new thread risks a `KeyError`/missing-default bug
(see Section 9.1's full incident history).

**Why `keep_criteria`/`keep_criteria_specified` are two plain
booleans, not one `Optional[bool]`:** preemptive avoidance of a
confirmed Azure structured-output strict-mode limitation — see
Section 8.

---

## 3. Context schema — `app/agent/context.py`

```python
@dataclass
class AgentContext:
    db: AsyncDatabase        # async Mongo client — most repositories
    sync_db: Database        # sync Mongo client — knowledge_service's
                              # similarity_search, which sits on top
                              # of a sync-only vector search library
```

Passed at invocation: `graph.ainvoke(..., context=AgentContext(db=db, sync_db=sync_db))`.

---

## 4. Full node list

| Node | File | LLM? | DB? | Notes |
|---|---|---|---|---|
| `load_session_state` | `nodes/load_session_state.py` | No | Yes | Syncs `uploaded_filenames` from `submission_chunks` |
| `classify_intent` | `nodes/classify_intent.py` | Yes (deterministic-first) | Yes | Runs on EVERY turn. Stage-aware (Section 9.4) |
| `handle_social` | `nodes/handle_social.py` | Yes (with static fallback) | Yes | LLM-based since Section 9.7's fix; never fails the turn |
| `handle_off_topic` | `nodes/handle_off_topic.py` | Yes (with static fallback) | Yes | Same pattern as `handle_social` |
| `answer_from_knowledge` | `nodes/answer_from_knowledge.py` | Yes | Yes | NEW — grounded Q&A, reachable from every stage. Section 9.9 |
| `request_criteria` | `nodes/request_criteria.py` | Yes | Yes | Uses shared `extract_criteria()`; consumes+clears criteria-upload buffer |
| `classify_mid_flow_intent` | `nodes/classify_mid_flow_intent.py` | Yes | Yes | Gate in front of `awaiting_criteria_confirmation`/`awaiting_document`. Doc-presence-aware safety override — Section 9.3/9.5 |
| `update_criteria_mid_flow` | `nodes/update_criteria_mid_flow.py` | Yes | Yes | Handles a criteria edit while `awaiting_document` — previously silently dropped, Section 9.6 |
| `recap_and_confirm` | `nodes/recap_and_confirm.py` | Yes | Yes | Confirm / mid-confirmation edits. Same-turn handoff into evaluation if a document is already uploaded — Section 9.11 |
| `request_document` | `nodes/request_document.py` | No | Yes | Deterministic file-presence check only. Now marks `document_confirmed` — Section 9.10. Same-turn handoff to `run_evaluation` (ADR-R004) |
| `run_evaluation` | `nodes/run_evaluation.py` | Yes (adaptive) | Yes | Simple path (small doc) or map-reduce (large doc). Stable `criterion_id` matching — Section 9.2 |
| `generate_summary` | `nodes/generate_summary.py` | Yes | Yes | Knowledge-retrieval grounded (k — see Section 9.9's note on threshold) |
| `validate_output` | `nodes/validate_output.py` | No | No | Risk-word check; internal/log-only |
| `render_output` | `nodes/render_output.py` | No | No | Exact 3-section format + mandatory closing block |
| `classify_post_evaluation_intent` | `nodes/classify_post_evaluation_intent.py` | Yes | Yes | Gate after evaluation completes. Visibility-log (not override) for doc-present `new_document` — Section 9.5 |
| `generate_additional_output` | `nodes/generate_additional_output.py` | Yes | Yes | Category A — reuses existing `scoring_results` |
| `reset_for_criteria_change` | `nodes/reset_for_criteria_change.py` | Yes | Yes | Category B — genuinely MERGES with existing criteria, not overwrite — Section 9.8 |
| `reset_for_new_document` | `nodes/reset_for_new_document.py` | No | Yes | Category C AND mid-flow new-document — reused by both gates. Now resets `document_confirmed` — Section 9.12 |
| `handle_criteria_choice` | `nodes/handle_criteria_choice.py` | Yes | Yes | Handles reply to "same criteria or new?" |
| `ask_for_clarification` | `nodes/ask_for_clarification.py` | No | No | Category D — deliberately non-suggestive |

**Every LLM-calling node follows the same defensive pattern** for
`with_structured_output(..., include_raw=True)` — try the
`include_raw` path for token logging, fall back to plain
`with_structured_output` (no token log) on exception. See Section 8.

---

## 5. Full graph wiring

```
START
  │
  ▼ (fixed edge)
load_session_state
  │
  ▼ (fixed edge)
classify_intent
  │
  ▼ route_after_classification
  │
  ├── intent == "social"            → handle_social                    → END
  ├── intent == "off_topic"         → handle_off_topic                 → END
  ├── intent == "knowledge_question"→ answer_from_knowledge             → END
  ├── intent == "task_relevant", by stage:
  │     "awaiting_criteria"                    → request_criteria                 → END
  │     "awaiting_criteria_confirmation"       → classify_mid_flow_intent
  │     "awaiting_document"                    → classify_mid_flow_intent
  │     "awaiting_new_document_criteria_choice"→ handle_criteria_choice           → END
  │     "evaluated"                            → classify_post_evaluation_intent
  │
  ▼ (from classify_mid_flow_intent) route_after_mid_flow_classification
  │
  ├── mid_flow_category == "new_document"                        → reset_for_new_document → END
  ├── mid_flow_category == "criteria_edit" AND awaiting_document → update_criteria_mid_flow → END
  ├── on_script/unclear, stage == "awaiting_criteria_confirmation"→ recap_and_confirm
  ├── on_script/unclear, stage == "awaiting_document"             → request_document
  │
  ▼ (from recap_and_confirm OR request_document) route_after_document_check
  │  (SAME function, reused by both — see Section 9.11)
  │
  ├── stage == "ready_to_evaluate" → run_evaluation → generate_summary →
  │                                   validate_output → render_output   → END
  ├── else                         → END (waiting for confirmation/upload)
  │
  ▼ (from classify_post_evaluation_intent) route_after_post_eval_classification
  │
  ├── "additional_output" → generate_additional_output → END
  ├── "criteria_change"   → reset_for_criteria_change   → END
  ├── "new_document"      → reset_for_new_document       → END
  └── "unclear"           → ask_for_clarification         → END
```

### Routing functions (current)

```python
def route_after_classification(state: RFPAnalyzerState) -> str:
    if state["intent"] == "social":
        return "handle_social"
    if state["intent"] == "off_topic":
        return "handle_off_topic"
    if state["intent"] == "knowledge_question":
        return "answer_from_knowledge"

    stage_map = {
        "awaiting_criteria": "request_criteria",
        "awaiting_criteria_confirmation": "classify_mid_flow_intent",
        "awaiting_document": "classify_mid_flow_intent",
        "awaiting_new_document_criteria_choice": "handle_criteria_choice",
        "evaluated": "classify_post_evaluation_intent",
    }
    return stage_map[state["stage"]]


def route_after_mid_flow_classification(state: RFPAnalyzerState) -> str:
    category = state.get("mid_flow_category")
    if category == "new_document":
        return "reset_for_new_document"
    if category == "criteria_edit" and state["stage"] == "awaiting_document":
        return "update_criteria_mid_flow"
    if state["stage"] == "awaiting_criteria_confirmation":
        return "recap_and_confirm"
    return "request_document"


def route_after_document_check(state: RFPAnalyzerState) -> str:
    """Reused identically by BOTH request_document and
    recap_and_confirm — see Section 9.11 for why recap_and_confirm
    needed this too."""
    if state["stage"] == "ready_to_evaluate":
        return "run_evaluation"
    return "wait"  # → END


def route_after_post_eval_classification(state: RFPAnalyzerState) -> str:
    category_map = {
        "additional_output": "generate_additional_output",
        "criteria_change": "reset_for_criteria_change",
        "new_document": "reset_for_new_document",
        "unclear": "ask_for_clarification",
    }
    return category_map.get(state["post_eval_category"], "ask_for_clarification")
```

### Why two separate mid-workflow intent gates instead of one

`classify_mid_flow_intent` (before evaluation) and
`classify_post_evaluation_intent` (after) are deliberately NOT
merged, because their category sets genuinely differ:
pre-evaluation has no `additional_output` concept (nothing to draw
from yet), and no separate `criteria_change` category since
`recap_and_confirm` already natively absorbs criteria edits. Both
gates write to the SAME `keep_criteria`/`keep_criteria_specified`
fields and both route `new_document` to the SAME
`reset_for_new_document` node — deliberate reuse, since that node's
logic is identical regardless of which stage triggered it.

**They also carry different risk profiles for the SAME category**
(`new_document`) — see Section 9.5 for why the safety fix applied to
each gate is different in kind, not just degree.

---

## 6. `app/services/chat_service.py`

Two functions, `send_message` (non-streaming) and `stream_message`
(streaming — structured-output nodes will NOT stream token-by-token,
mechanical limitation, see Section 8). Both:

1. Persist the user's message via `MessageRepository` (UI-facing
   history — separate from the LangGraph checkpoint).
2. Call `SessionRepository.mark_session_active(session_id)` —
   clears the session's TTL `expires_at` field. See Section 9.13.
3. Check `graph.aget_state(config)` — if `snapshot.values` is falsy,
   this is a genuinely new thread, and `DEFAULT_OVERWRITE_FIELDS`
   (from `app/agent/state.py`) gets merged into the input. **This
   check must be repeated identically anywhere else that touches
   the checkpoint outside of `chat_service`** — see the post-upload
   hook in Section 7.3, which duplicates this exact logic for the
   same reason.
4. Invoke the graph.
5. Persist the assistant's reply.

### `DEFAULT_OVERWRITE_FIELDS` — CRITICAL, do not regress

Lives in `app/agent/state.py`, imported by `chat_service.py` (and
`app/agent/setup.py`'s post-upload hook — see Section 7.3). Only
passed on a genuinely new thread; on every subsequent call these
fields are OMITTED, or they'd silently overwrite the checkpoint's
real current values (full incident history: Section 9.1). **Every
new state field added to this graph must be added here too.**

---

## 7. Supporting repositories and services

### 7.1 `SessionRepository` — `app/repository/session_repository.py`

Real session management, keyed by `_id: ObjectId`, always owned by
`user_id`. Key methods:
- `create_session(user_id)` — sets `expires_at` (now + `SESSION_TTL_HOURS`, currently 24)
- `mark_session_active(session_id)` — `$unset`s `expires_at`. Called
  from BOTH `chat_service.send_message` AND `documents.py`'s upload
  route (Section 9.13) — a session's first real activity, whichever
  form it takes, must clear the TTL.
- `ensure_indexes()` — TTL index (`expireAfterSeconds=0`, expires
  AT the stored `expires_at` datetime). Called once at startup.
- `get_owned_session` / `get_session` — ownership-checked vs. raw
- `mark_document_confirmed` / `reset_confirmation` — see Sections
  9.10 and 9.12 for why these are now actually called
- `list_sessions_for_user(user_id)` — backs the Streamlit sidebar
  (Section 10)

**TTL duration is currently flat** (one window regardless of
activity level) — a two-tier scheme (short window for genuinely
untouched sessions, long window for active-then-abandoned ones) was
discussed and deliberately deferred; see Section 11's backlog.

### 7.2 `MessageRepository` — `app/repository/message_repository.py`

Plain conversation history, separate from the LangGraph checkpoint
— "what was said," stable and simple, for any UI (Streamlit now,
Teams later) or audit purpose. `get_history(session_id)` returns
oldest-first. `setup_indexes()` must be called once at startup.

### 7.3 The post-upload hook pattern — `app/api/documents.py` + `app/agent/setup.py`

**Problem this solves:** `documents.py` is meant to be
copy-paste-generic — any future agent should be able to reuse it
unmodified. But RFP-Analyzer specifically wants extra behavior after
a successful upload (a chat confirmation message). Hardcoding that
behavior into `documents.py` would leak agent-specific logic into
supposedly-generic infra.

**The pattern:** `documents.py` defines a module-level variable,
`post_upload_hook`, defaulting to `None`. The upload route checks
`if post_upload_hook is not None: confirmation_message =
await post_upload_hook(db, checkpointer, session_id, user_id)` and
includes the result in its response. If nothing is ever assigned to
it, the route behaves as a plain, silent, fully generic upload
route — no agent-specific code runs, nothing to strip out for reuse.

RFP-Analyzer's actual hook lives in `app/agent/setup.py` — NOT in
`main.py` (kept out of the infra-bootstrap file deliberately, same
separation principle as everywhere else) — and is wired in via one
call, `register_agent_hooks()`, from `main.py`'s lifespan.

```python
# app/agent/setup.py
async def _rfp_analyzer_post_upload_hook(db, checkpointer, session_id, user_id) -> str:
    graph = build_graph(checkpointer)
    config = {"configurable": {"thread_id": session_id}}
    snapshot = await graph.aget_state(config)

    # Stage-aware — see Section 9.14 for why a single fixed message
    # was wrong.
    criteria_confirmed = snapshot.values.get("criteria_confirmed", False) if snapshot.values else False
    text = (
        "Received your document — let me know when you'd like me to begin the evaluation."
        if criteria_confirmed else
        "Received your document. Once you share the evaluation criteria you'd like it scored against, I'll begin the evaluation."
    )

    await MessageRepository(db).add_message(session_id, user_id, "assistant", text)

    # SAME is_new_thread check as chat_service — critical if upload
    # is the FIRST thing to ever touch this session's checkpoint.
    is_new_thread = not snapshot.values
    update = {"messages": [AIMessage(content=text)], "session_id": session_id, "user_id": user_id}
    if is_new_thread:
        update.update(DEFAULT_OVERWRITE_FIELDS)
    await graph.aupdate_state(config, update)

    return text

def register_agent_hooks() -> None:
    from app.api import documents as documents_api
    documents_api.post_upload_hook = _rfp_analyzer_post_upload_hook
```

**The response also carries `confirmation_message`**, not just a
side-effect write to `MessageRepository` — the calling client (e.g.
Streamlit) shows it immediately, without needing a second history
fetch to discover it happened. See Section 10.

### 7.4 `extract_criteria()` — `app/agent/criteria_extraction.py`

Shared helper — combines chat text AND any pending uploaded file
text into ONE extraction call. Used by exactly TWO call sites:
`request_criteria` (first-time) and `reset_for_criteria_change` /
`update_criteria_mid_flow` (criteria changes, either post-eval or
mid-flow). **`recap_and_confirm` does NOT use this helper** — it has
its own separate `ConfirmationResult` schema (needs a `confirmed`
boolean this helper has no use for) — a deliberate, working
divergence, not a gap. (This file's docstring previously claimed
`recap_and_confirm` used it too — corrected, Section 9.15.)

**Both real call sites pass EXISTING criteria into the extraction
call**, not just the new chat text — `reset_for_criteria_change`
builds `f"Existing confirmed criteria:\n{state['criteria']}\n\nRequested change:\n{last_message}"`
before calling `extract_criteria`. This was a real, high-severity
bug when missing — see Section 9.8.

### 7.5 `CriteriaUploadRepository` — `app/repository/criteria_upload_repo.py`

Temporary buffer for criteria-document text uploaded via
`POST /sessions/{id}/criteria-document`. TTL-protected
(`PENDING_CRITERIA_TTL_SECONDS = 600`, `created_at` field + Mongo
TTL index) after a confirmed contamination bug — see Section 9.16.

### 7.6 `knowledge_service.py` / `answer_from_knowledge`

```python
async def retrieve_relevant_knowledge(sync_db: Database, query: str, k: int = 15) -> list[Document]:
```
Returns `list[langchain_core.documents.Document]` — access content
via `.page_content`, NOT a dict key. `answer_from_knowledge` (the
node) declines to answer (`_RELEVANCE_THRESHOLD = 3`) if fewer than
3 chunks come back. **Open question, not yet verified:** does
`knowledge_repository.similarity_search` apply any distance/score
cutoff before returning results, or does it always return exactly
`k` regardless of match quality? If the latter, the threshold check
is weaker than intended (closer to "does the knowledge base have
≥3 chunks at all" than "were the top matches actually relevant") —
flagged as open backlog, Section 11.

---

## 8. Confirmed platform-level bugs/limitations (apply to any future node)

- **`dict[str, X]` fields are banned in any `with_structured_output`
  schema** — confirmed Azure/OpenAI strict-mode limitation. Fix:
  `list[Model]` + convert to dict in application code after the call
  returns. Applied to `CriteriaExtraction.weights`, and preemptively
  to `keep_criteria`/`keep_criteria_specified` (two plain booleans,
  not `Optional[bool]`).
- **`include_raw=True` may leak into the underlying SDK call**
  (open bug, `langchain#35041`) — every node wraps in try/except,
  falls back to plain call without token logging on failure.
- **`PydanticSerializationUnexpectedValue` warning is cosmetic** —
  confirmed open, unrelated LangChain bug (`langchain#35538`). Call
  succeeds, parsed value is correct. Do not re-investigate.
- **Structured-output nodes do not stream token-by-token** —
  mechanical, not a bug. `generate_summary` and
  `generate_additional_output` use plain LLM calls and DO stream.
- **Checkpoint overwrite bug** — see Section 6 and 9.1. Any new
  overwrite-type state field must follow the `DEFAULT_OVERWRITE_FIELDS`
  + `is_new_thread` pattern EVERYWHERE the checkpoint is touched,
  not just in `chat_service.py`.
- **LangGraph's real `runs.cancel()` requires LangGraph Platform/
  Server** — this app calls `graph.ainvoke()` directly inside its
  own FastAPI process with a custom `MongoDBSaver`, no LangGraph
  Server involved, so that mechanism isn't reachable here. Even on
  Platform, default cancel behavior is an interrupt, not a hard
  abort, and a separate confirmed bug (`langgraph#5672`) means
  cancelling loses any streamed-but-not-yet-checkpointed output.
  Cooperative (flag-based) cancellation was designed but explicitly
  NOT implemented — deferred, see Section 11.

---

## 9. Real-world testing findings — full incident history, resolved

*(Numbered in the order found, not necessarily severity. Each entry
states the bug, the root cause, and the fix — read this before
touching any of the nodes named, so you don't reintroduce a
previously-fixed failure mode.)*

### 9.1 — Checkpoint overwrite bug
Overwrite-type fields passed on EVERY call would silently clobber
the checkpoint's real stored value on every subsequent turn. Fixed
via `graph.aget_state()` + `is_new_thread` check, confirmed via
isolated empirical testing (both sync and async). This is the
foundational fix everything in Section 6 depends on.

### 9.2 — Map-reduce evidence silently discarded (confirmed 0/10 despite real evidence)
`_parse_criteria_list` didn't strip numeric list markers ("1. ",
"2. ") from recapped criteria. The MAP step's LLM naturally returned
the CLEAN criterion name in its findings; matching relied on exact
string equality against the numbered list — every match silently
failed, evidence was dropped for every batch, with no error or
warning. Retroactively explained an earlier "unreproduced" 0/10
anomaly — it wasn't intermittent, it was deterministic, tied to
whether criteria happened to get recapped with numbering. **Fixed**
by assigning each criterion a stable ID (`C1`, `C2`, ...) that the
model echoes back exactly, decoupling matching from paraphrase-prone
text comparison, plus a normalized-name fallback and an explicit
warning log for any future mismatch (previously totally silent).

### 9.3 — Mid-flow criteria/document changes silently dropped or misread
Three compounding bugs, found via real transcript testing:
1. `request_document` had ZERO text-classification logic — a
   criteria-change message at `awaiting_document` had no code path
   that could see it at all.
2. The criteria-upload buffer (Section 7.5) had no expiry — a stale
   upload from an unrelated earlier session silently resurfaced and
   contaminated a later extraction call (confirmed: contaminating
   content exactly matched an earlier test file, not the actual
   conversation).
3. `recap_and_confirm`'s schema only had two interpretations
   (`confirmed`/`updated_criteria`) — a "different document" signal
   arriving mid-confirmation had nowhere to go, got force-fit into
   `confirmed=True`, discarding the actual intent.

**Fixed**: `classify_mid_flow_intent` gate added in front of both
`awaiting_document` and `awaiting_criteria_confirmation` (bugs 1 and
3); TTL added to the criteria-upload buffer (bug 2, Section 7.5/9.16).

**Deliberate design decision made resolving this:** extend
flexibility to earlier stages rather than restrict post-evaluation
down to the spec's strict literal scope — the spec is silent, not
prohibitive, and real users course-correct before evaluation just as
often as after.

### 9.4 — `classify_intent` misclassified clear task-relevant input as off-topic
`classify_intent`'s prompt had NO awareness of the current stage —
a clear criteria message ("evaluate against: pricing, timeline")
sent shortly after an off-topic exchange was misclassified
`off_topic`, requiring a retry. Root cause: no stage signal meant
the model could anchor on the immediately-preceding off-topic
exchange's tone rather than judging the new message on its own
content. **Fixed**: prompt now receives `{stage}` and explicit
instruction not to let recent off-topic tone override a message that
matches what the current stage expects.

### 9.5 — `classify_mid_flow_intent` re-fired `new_document` on a routine follow-up, destroying a just-uploaded document
**The most severe confirmed bug in this build.** A generic,
contentless acknowledgment ("here you go" / "here is the document")
sent immediately after a resolved "different document" exchange was
misclassified `new_document` AGAIN — reconfirmed via real logs
showing `reset_for_new_document` firing a second time and deleting
215 real, just-uploaded chunks before they were ever evaluated. Root
cause: the classifier had no signal that a document was already
present, and anchored on earlier "different document" phrasing
rather than judging the current message alone.

**Fixed with TWO layers:**
1. Prompt now explicitly states whether a document is `already` /
   `NOT yet` uploaded at this stage, with an instruction to treat
   any plausible confirmation/reference to that document as
   `on_script`, and NOT to re-derive `new_document` intent from
   resolved prior turns.
2. A **hard code-level override**: if `state["uploaded_filenames"]`
   is non-empty, `classify_mid_flow_intent` can NEVER return
   `new_document`, regardless of what the model says — logged as a
   warning if it triggers, so prompt drift stays visible.

**Deliberately asymmetric tradeoff**: a false negative (occasionally
missing a genuine new-document request) is a far smaller cost than
a false positive (silently deleting real data), so the override
favors safety over recall.

**Critically, the SAME hard override is NOT applied to
`classify_post_evaluation_intent`'s equivalent `new_document`
category** — at `stage == "evaluated"`, a document is essentially
ALWAYS present, and `new_document` there is the correct, expected,
common trigger for Category C. A hard override at that gate would
break the normal case, not just guard against a rare misfire. That
gate instead gets a visibility-only log (Section 9.5 continues in
the node table) — deliberately weaker, because the risk profile is
genuinely different, not because the fix is incomplete.

### 9.6 — Criteria edit at `awaiting_document` silently dropped (UX gap, not data loss)
Distinct from 9.3's fixes — even after `classify_mid_flow_intent`
existed, it initially had no category for "user wants to add/change
criteria while waiting to upload a document." **Fixed**: added
`criteria_edit` category (scoped only to `awaiting_document` — at
`awaiting_criteria_confirmation` this is already `on_script`, since
`recap_and_confirm` natively handles it) and a new node,
`update_criteria_mid_flow`, using the same shared `extract_criteria()`
merge pattern established in Section 7.4/9.8.

### 9.7 — `handle_social`/`handle_off_topic` were static
Deliberately made LLM-based for variety, with the original static
text kept as a fallback if the LLM call fails — chosen specifically
so the cheapest, lowest-risk nodes in the graph never fail a whole
turn over a transient LLM error.

### 9.8 — Criteria "change" silently OVERWROTE existing criteria instead of merging
`reset_for_criteria_change` only ever passed the NEW chat message
into `extract_criteria` — never the existing confirmed criteria.
This was MASKED by bug 9.3's stale-upload-buffer contamination bug
(a leftover file happened to supply criteria that looked like a
correct merge). Once the TTL fix (9.16) was applied, this would have
become a genuine, silent data-loss bug: "also evaluate against X"
would have overwritten `state["criteria"]` down to just "X",
discarding everything previously confirmed. **Fixed**: both real
call sites now explicitly build `f"Existing confirmed criteria:\n{...}\n\nRequested change:\n{...}"`
before calling `extract_criteria`, relying on that helper's own
prompt ("may add to, clarify, or override") to genuinely merge or
override as appropriate, not blindly replace.

### 9.9 — No path for general knowledge questions
Previously, any question not fitting `social`/`off_topic`/
`task_relevant` had nowhere sensible to go. **Added**: a fourth
`classify_intent` category, `knowledge_question`, routed to a new
node `answer_from_knowledge`, reachable from EVERY stage (same as
`social`/`off_topic`). Core design principle: retrieve first, and
explicitly DECLINE rather than answer from the model's own general
knowledge if fewer than `_RELEVANCE_THRESHOLD` (3) chunks come back
— built as a reusable pattern, intended to be copy-paste-adaptable
for future agents on this platform.

### 9.10 — `document_confirmed` was never set to `True` anywhere
Confirmed by reading `request_document.py` and `run_evaluation.py`
directly — neither ever called `mark_document_confirmed`. Since
`submission_service.upload_submission_file`'s invalidate-on-reupload
policy is gated behind `session["document_confirmed"]`, this branch
had NEVER executed in this build — every re-upload at any stage just
ADDED chunks alongside existing ones, silently blending unrelated
documents into one evaluation with zero warning. **Fixed**:
`request_document` now calls `mark_document_confirmed` the moment
it commits to evaluating a given file set.

### 9.11 — Confirming criteria never noticed an already-uploaded document
A real, natural flow: user uploads a document FIRST, then provides
criteria. Once confirmed, `recap_and_confirm` always said "please
upload the document" — even though it was already there — and even
after correcting the message, nothing about confirming criteria
would trigger evaluation in that turn; the user had to send one more
essentially pointless message. **Fixed**: `recap_and_confirm` now
checks `state["uploaded_filenames"]` when criteria are confirmed; if
present, sets `stage = "ready_to_evaluate"` directly (no
`response_to_user`, matching `request_document`'s own pattern) and
the SAME `route_after_document_check` function (previously only used
by `request_document`) is now also applied after `recap_and_confirm`
via a new conditional edge, replacing the old unconditional `→ END`.

### 9.12 — `reset_for_new_document` never reset `document_confirmed` in Mongo
The returned LangGraph state dict already set `"document_confirmed": False`
— but that's a different piece of data from the Mongo `sessions`
collection's own `document_confirmed` field, which is what
`upload_submission_file`'s invalidate-policy check actually reads.
Left `True` in Mongo after a reset, this only worked correctly by
accident (the next real upload's invalidate branch would find
nothing to invalidate, since chunks were already cleared) — an
implicit dependency, not a deliberate one. **Fixed**: explicit
`session_repo.reset_confirmation(session_id)` call added.

### 9.13 — Session TTL never triggered by document upload
`mark_session_active` was only ever called from
`chat_service.send_message` — a session whose first real activity
was a document upload (not a chat message) kept silently counting
down to auto-deletion the whole time. **Fixed**: `documents.py`'s
upload route now also calls `mark_session_active`, right after the
ownership check, before any parsing work begins.

### 9.14 — Post-upload confirmation message was stage-unaware
Fixed hardcoded text ("let me know when you'd like me to begin the
evaluation") was actively misleading when a document is uploaded
BEFORE criteria exist — saying "go" at that point doesn't start an
evaluation, it asks for criteria instead. **Fixed**: the hook now
checks `criteria_confirmed` from the live checkpoint before choosing
between two different messages. See Section 7.3.

### 9.15 — Stale docstring in `criteria_extraction.py`
Claimed THREE call sites including `recap_and_confirm`'s adjustment
path; `recap_and_confirm` never actually called this helper (has its
own separate schema/logic, deliberately). Corrected to describe the
real two call sites, with an explicit note on why `recap_and_confirm`
diverges rather than refactoring working, tested code purely for
cosmetic consistency.

### 9.16 — Criteria-upload buffer had no expiry (stale-content contamination)
`CriteriaUploadRepository` was keyed purely by `session_id`, with no
timestamp — a leftover upload from an earlier, unrelated test could
resurface and silently contaminate a much later, unrelated
extraction call. Confirmed: contaminating content ("Technical
approach, Pricing, Timeline") exactly matched an old test file, not
anything in the actual conversation being tested. **Fixed**: TTL
index (`PENDING_CRITERIA_TTL_SECONDS = 600`) + `created_at` field,
reset on every re-upload via the upsert.

---

## 10. Client architecture

`app/api/*` routes are UI-agnostic by design — confirmed by having
built a Streamlit client against the plain REST API with no special
backend accommodations needed. Routes used:

```
POST /api/sessions                                — create session
GET  /api/sessions                                 — list sessions (for a sidebar)
POST /api/sessions/{id}/chat                        — send a message
GET  /api/sessions/{id}/chat/history                 — full message history
POST /api/sessions/{id}/documents                     — upload; response
                                                          includes confirmation_message
DELETE /api/sessions/{id}/documents/{filename}          — remove one file
```

`streamlit_app.py` lives at the project root (NOT inside `app/`) —
a separate HTTP client, same relationship any future Teams bot or
web frontend would have. Uses `st.chat_input(accept_file=True,
file_type=[...])` (requires Streamlit ≥ 1.41) to combine text and
file attachment in one input box, with `st.spinner` feedback shown
inline as its own chat bubble during upload and during message
processing. Currently uses the non-streaming `/chat` route — SSE
streaming exists as a backend route but isn't wired into this client
yet (Section 11 backlog).

---

## 11. Open backlog — not yet resolved

- **Full regression pass** through the test doc's Parts 1–7 has not
  been re-run since the latest batch of fixes (Sections 9.10–9.16) —
  do this before adding further scope, given how much these fixes
  touch overlapping code paths.
- **EMF/WMF PPTX images fail vision description** — needs a Pillow
  conversion to PNG before the vision API call. Untouched.
- **Knowledge-retrieval threshold check may be weaker than intended**
  — depends on whether `knowledge_repository.similarity_search`
  applies any distance/score cutoff; not yet verified against the
  real file. See Section 7.6.
- **Teams/M365 Agents SDK integration** — not started, substantial
  separate scope (own auth model, activity routing).
- **SSE token streaming not wired into the Streamlit client** —
  route exists (`/chat/stream`), client doesn't use it yet.
- **Cancellation (real stop/abort mid-evaluation)** — designed
  (cooperative flag, checked between LLM calls, never mid-await) but
  explicitly NOT implemented — deferred by deliberate choice, not
  oversight. LangGraph's real cancel primitive requires Platform/
  Server, which this app doesn't use (Section 8).
- **Multi-document handling — PARKED, unresolved even in concept.**
  Two very different things this could mean: (1) combined evaluation
  — multiple files treated as one submission, scored together; or
  (2) separate parallel evaluations — competing vendor proposals,
  each scored independently for comparison. Today's actual behavior,
  untested and unintentional either way, is case 1 — `get_session_chunks`
  returns ALL chunks across ALL uploaded files with no per-file
  distinction in scoring. A second pre-confirmation upload (before
  `document_confirmed` is ever true) also currently silently blends
  with the first, same underlying question.
- **Two-tier session TTL** — separate windows for genuinely-untouched
  vs. active-then-abandoned sessions — discussed, deliberately
  deferred in favor of the current flat window.
- **New-session creation spam/rapid-click protection** — discussed
  (either UI-level button-disable, or "reuse the most recent
  completely empty session instead of creating a new one"),
  explicitly deferred.

---

## 12. Quick state inspection snippet

```python
import asyncio
from fastapi import FastAPI
from app.agent.graph import build_graph
from app.checkpointer import connect_checkpointer, get_checkpointer

async def inspect(session_id: str):
    app = FastAPI()
    connect_checkpointer(app)
    checkpointer = get_checkpointer(app)
    graph = build_graph(checkpointer)
    config = {"configurable": {"thread_id": session_id}}
    snapshot = await graph.aget_state(config)
    import json
    print(json.dumps(
        {k: v for k, v in snapshot.values.items() if k != "messages"},
        indent=2, default=str,
    ))

asyncio.run(inspect("<session_id>"))
```

---

**For a new developer picking this up:** read Sections 1–5 for the
what/how, skim Section 9 in full at least once (it's the "why does
it look like this" — every non-obvious design choice traces back to
a specific real incident there), and treat Section 11 as the honest,
current to-do list. Do not re-investigate anything Section 8 or 9
marks as confirmed/fixed/accepted without new evidence.
