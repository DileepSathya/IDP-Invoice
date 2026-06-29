"""
Streamlit Chat UI for the Invoice Chatbot.
Run: streamlit run ui.py
"""

import streamlit as st
import requests

API_URL = "http://localhost:8000/chat"

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Invoice Assistant",
    page_icon="🧾",
    layout="centered",
)

st.title("🧾 Invoice Assistant")
st.caption("Ask anything about your invoices — amounts, counts, vendors, status, and more.")

# ── Session state ─────────────────────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []

if "show_debug" not in st.session_state:
    st.session_state.show_debug = False

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Settings")
    st.session_state.show_debug = st.toggle("Show debug info (intent + raw data)", value=False)

    st.divider()
    st.subheader("💡 Try asking...")
    quick_questions = [
        "Total invoice amount",
        "How many invoices are processing?",
        "Show invoices needing human review",
        "List all Surabhi invoices",
        "Average invoice value",
        "Month-wise invoice trend",
        "How many invoices failed?",
        "Show January 2026 invoices",
    ]
    for q in quick_questions:
        if st.button(q, use_container_width=True, key=f"quick_{q}"):
            st.session_state["_pending_query"] = q

    st.divider()
    if st.button("🗑️ Clear chat", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

# ── Display chat history ──────────────────────────────────────────────────────
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("debug_info"):
            with st.expander("🔍 Debug info"):
                st.json(msg["debug_info"])

# ── Handle quick-question button clicks ──────────────────────────────────────
pending = st.session_state.pop("_pending_query", None)

# ── Chat input ────────────────────────────────────────────────────────────────
user_input = st.chat_input("Ask about your invoices...") or pending

if user_input:
    # Show user message
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # Call the API
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            try:
                payload = {
                    "message": user_input,
                    "history": [
                        {"role": m["role"], "content": m["content"]}
                        for m in st.session_state.messages[:-1]   # exclude the just-added user msg
                    ],
                    "debug": st.session_state.show_debug,
                }
                resp    = requests.post(API_URL, json=payload, timeout=30)
                resp.raise_for_status()
                data    = resp.json()
                answer  = data.get("answer", "Sorry, I could not process that.")
                debug   = {"intent": data.get("intent"), "raw": data.get("raw")} \
                          if st.session_state.show_debug else None

            except requests.exceptions.ConnectionError:
                answer = "⚠️ Could not connect to the API. Make sure `api.py` is running on port 8000."
                debug  = None
            except Exception as e:
                answer = f"⚠️ Unexpected error: {e}"
                debug  = None

        st.markdown(answer)
        if debug and (debug.get("intent") or debug.get("raw")):
            with st.expander("🔍 Debug info"):
                st.json(debug)

    # Save assistant message
    st.session_state.messages.append({
        "role":       "assistant",
        "content":    answer,
        "debug_info": debug,
    })
