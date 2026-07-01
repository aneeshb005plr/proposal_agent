# streamlit_app.py
#
# Location: project root, alongside main.py (NOT inside app/ — this
# is a separate HTTP client of the API, same relationship any future
# Teams bot or web frontend would have, not part of the FastAPI
# service itself).
#
# Run with: streamlit run streamlit_app.py
#
# Depends on three routes that did not previously exist and were
# added alongside this file:
#   GET  /api/sessions             — list this user's sessions
#   GET  /api/sessions/{id}/chat/history — full message history
#   (existing) POST /api/sessions, POST /api/sessions/{id}/chat,
#   POST /api/sessions/{id}/documents
#
# Uses the dummy-header auth path (X-User-Id / X-User-Email) per
# claims_resolver.py — swap for real auth headers/tokens once this
# points at a non-dev environment.

import requests
import streamlit as st

API_BASE = "http://localhost:8000/api"
DUMMY_USER_ID = "streamlit-user-001"
HEADERS = {"X-User-Id": DUMMY_USER_ID, "X-User-Email": "streamlit@pwc.com"}

st.set_page_config(page_title="RFP Analyzer", layout="wide")


# ── API helpers — all failures surface as a visible st.error, never
# silently swallowed, since a hidden API failure here would look
# identical to "the agent just didn't respond" to the user. ────────

def api_create_session() -> str | None:
    try:
        resp = requests.post(f"{API_BASE}/sessions", headers=HEADERS, timeout=10)
        resp.raise_for_status()
        return resp.json()["session_id"]
    except requests.RequestException as e:
        st.error(f"Could not create a new session: {e}")
        return None


def api_list_sessions() -> list[dict]:
    try:
        resp = requests.get(f"{API_BASE}/sessions", headers=HEADERS, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        st.error(f"Could not load session list: {e}")
        return []


def api_get_history(session_id: str) -> list[dict]:
    try:
        resp = requests.get(
            f"{API_BASE}/sessions/{session_id}/chat/history",
            headers=HEADERS, timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        st.error(f"Could not load chat history: {e}")
        return []


def api_send_message(session_id: str, message: str) -> str | None:
    try:
        resp = requests.post(
            f"{API_BASE}/sessions/{session_id}/chat",
            headers=HEADERS, json={"message": message}, timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["response"]
    except requests.RequestException as e:
        st.error(f"Message failed to send: {e}")
        return None


def api_upload_document(session_id: str, filename: str, file_bytes: bytes) -> dict | None:
    try:
        resp = requests.post(
            f"{API_BASE}/sessions/{session_id}/documents",
            headers=HEADERS,
            files={"file": (filename, file_bytes)},
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        st.error(f"Upload failed: {e}")
        return None


# ── Session state bootstrap ─────────────────────────────────────────

if "session_id" not in st.session_state:
    st.session_state.session_id = None
if "messages" not in st.session_state:
    st.session_state.messages = []
if "uploaded_this_run" not in st.session_state:
    st.session_state.uploaded_this_run = set()


def switch_to_session(session_id: str) -> None:
    """Loads full history for a session and makes it the active one.
    Called both when picking an existing session from the sidebar and
    right after creating a new one."""
    st.session_state.session_id = session_id
    st.session_state.messages = api_get_history(session_id)
    st.session_state.uploaded_this_run = set()


# ── Sidebar — session list + new-session button ────────────────────

with st.sidebar:
    st.title("RFP Analyzer")

    if st.button("+ New session", use_container_width=True):
        new_id = api_create_session()
        if new_id:
            switch_to_session(new_id)
            st.rerun()

    st.divider()
    st.caption("Your sessions")

    sessions = api_list_sessions()

    if not sessions:
        st.caption("No sessions yet — create one above.")

    for s in sessions:
        sid = s["session_id"]
        is_active = sid == st.session_state.session_id
        label = f"{'🟢 ' if is_active else ''}{sid[:8]}…  ·  {s['created_at'][:10]}"
        if s.get("document_confirmed"):
            label += "  ✅"
        if st.button(label, key=f"session_{sid}", use_container_width=True):
            switch_to_session(sid)
            st.rerun()


# ── Main panel ───────────────────────────────────────────────────────

if st.session_state.session_id is None:
    st.info("Create a new session or pick one from the sidebar to get started.")
    st.stop()

st.caption(f"Session: `{st.session_state.session_id}`")

# Document upload — available at any point in the flow; the backend
# (via classify_mid_flow_intent / request_document / upload-after-
# confirmation policy) decides what happens with it, this UI doesn't
# try to guess or restrict when uploading makes sense.
uploaded_file = st.file_uploader(
    "Upload a proposal / RFP response document", key="doc_uploader"
)
if uploaded_file is not None and uploaded_file.name not in st.session_state.uploaded_this_run:
    result = api_upload_document(
        st.session_state.session_id, uploaded_file.name, uploaded_file.getvalue()
    )
    if result:
        st.session_state.uploaded_this_run.add(uploaded_file.name)
        st.success(f"Uploaded {result['filename']} ({result.get('chunks_stored', 0)} chunks)")

# Chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Chat input
if prompt := st.chat_input("Message"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            reply = api_send_message(st.session_state.session_id, prompt)
        if reply:
            st.markdown(reply)
            st.session_state.messages.append({"role": "assistant", "content": reply})