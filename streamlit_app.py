# streamlit_app.py
#
# Location: project root, alongside main.py (NOT inside app/ — this
# is a separate HTTP client of the API, same relationship any future
# Teams bot or web frontend would have).
#
# Run with: streamlit run streamlit_app.py
#
# REQUIRES Streamlit >= 1.41 — st.chat_input's accept_file parameter
# (used below to let a document be attached directly in the chat box)
# was merged into Streamlit in January 2025. Check with:
#   python -c "import streamlit; print(streamlit.__version__)"
# and `pip install --upgrade streamlit` if it's older.
#
# Depends on these API routes:
#   POST /api/sessions                          — create session
#   GET  /api/sessions                           — list sessions
#   GET  /api/sessions/{id}/chat/history          — full history
#   POST /api/sessions/{id}/chat                  — send a message
#   POST /api/sessions/{id}/documents             — upload a document
#     (response includes "confirmation_message" per the post-upload
#     hook wired in app/agent/setup.py)

import requests
import streamlit as st

API_BASE = "http://localhost:8000/api"
DUMMY_USER_ID = "streamlit-user-001"
HEADERS = {"X-User-Id": DUMMY_USER_ID, "X-User-Email": "streamlit@pwc.com"}

ACCEPTED_FILE_TYPES = ["pdf", "docx", "pptx", "txt"]

st.set_page_config(page_title="RFP Analyzer", page_icon="📋", layout="wide")

st.markdown(
    """
    <style>
    .block-container { padding-top: 2rem; }
    [data-testid="stChatMessage"] { padding: 0.75rem 0; }
    .session-meta { color: #6b7280; font-size: 0.8rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ── API helpers — every failure surfaces as a visible st.error, never
# silently swallowed, since a hidden API failure would look identical
# to "the agent just didn't respond." ───────────────────────────────

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
            headers=HEADERS, json={"message": message}, timeout=180,
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
            timeout=180,
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


def switch_to_session(session_id: str) -> None:
    st.session_state.session_id = session_id
    st.session_state.messages = api_get_history(session_id)


def render_message(role: str, content: str) -> None:
    avatar = "📋" if role == "assistant" else None
    with st.chat_message(role, avatar=avatar):
        st.markdown(content)


# ── Sidebar — session list + new-session button ────────────────────

with st.sidebar:
    st.markdown("## 📋 RFP Analyzer")
    st.caption("Proposal & RFP evaluation assistant")

    if st.button("➕ New session", use_container_width=True, type="primary"):
        new_id = api_create_session()
        if new_id:
            switch_to_session(new_id)
            st.rerun()

    st.divider()
    st.caption("SESSIONS")

    sessions = api_list_sessions()

    if not sessions:
        st.caption("No sessions yet — create one above.")

    for s in sessions:
        sid = s["session_id"]
        is_active = sid == st.session_state.session_id
        status_dot = "🟢" if is_active else "⚪"
        status_tag = " ✅ evaluated" if s.get("document_confirmed") else ""

        label = f"{status_dot} {sid[:8]}…"
        if st.button(label, key=f"session_{sid}", use_container_width=True):
            switch_to_session(sid)
            st.rerun()
        st.markdown(
            f"<div class='session-meta'>&nbsp;&nbsp;{s['created_at'][:10]}{status_tag}</div>",
            unsafe_allow_html=True,
        )


# ── Main panel ───────────────────────────────────────────────────────

if st.session_state.session_id is None:
    st.markdown("### Welcome 👋")
    st.write(
        "Create a new session from the sidebar to start evaluating a "
        "proposal or RFP response — share your evaluation criteria, "
        "upload a document, and I'll score it for you."
    )
    st.stop()

st.caption(f"Session `{st.session_state.session_id}`")

chat_container = st.container()
with chat_container:
    for msg in st.session_state.messages:
        render_message(msg["role"], msg["content"])

# ── Combined text + file input, in ONE box at the bottom ────────────
prompt = st.chat_input(
    "Message, or attach a document to evaluate...",
    accept_file=True,
    file_type=ACCEPTED_FILE_TYPES,
)

if prompt:
    user_text = prompt.text.strip() if prompt.text else ""
    attached_files = prompt["files"] if prompt["files"] else []

    # Show the user's turn immediately, before any network calls —
    # makes the app feel responsive even while a slow upload/
    # evaluation is still running in the background.
    if user_text:
        st.session_state.messages.append({"role": "user", "content": user_text})
    for f in attached_files:
        st.session_state.messages.append(
            {"role": "user", "content": f"📎 Attached: {f.name}"}
        )
    with chat_container:
        if user_text:
            render_message("user", user_text)
        for f in attached_files:
            render_message("user", f"📎 Attached: {f.name}")

    # ── Upload first, if a file was attached — with a clear
    # in-progress indicator, since parsing + chunking + embedding a
    # real document can take several seconds. ───────────────────────
    for f in attached_files:
        with chat_container:
            with st.chat_message("assistant", avatar="📋"):
                with st.spinner(f"Uploading and processing {f.name}..."):
                    result = api_upload_document(
                        st.session_state.session_id, f.name, f.getvalue()
                    )
                if result:
                    confirmation = result.get("confirmation_message") or (
                        f"Received {result['filename']} "
                        f"({result.get('chunks_stored', 0)} chunks processed)."
                    )
                    st.markdown(confirmation)
                    st.session_state.messages.append(
                        {"role": "assistant", "content": confirmation}
                    )

    # ── Then send the text message, if any — with its own
    # in-progress indicator, since evaluation itself (map-reduce
    # path especially) can take significant time. ───────────────────
    if user_text:
        with chat_container:
            with st.chat_message("assistant", avatar="📋"):
                with st.spinner("Thinking..."):
                    reply = api_send_message(st.session_state.session_id, user_text)
                if reply:
                    st.markdown(reply)
                    st.session_state.messages.append(
                        {"role": "assistant", "content": reply}
                    )