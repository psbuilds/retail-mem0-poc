"""Streamlit chat interface for the Retail Memory Shopping Assistant.

The UI only collects input, calls ``app.services.chatbot_service`` and renders
the structured result.  All memory, product, LLM and graph logic lives outside
this file, so a future FastAPI chatbot backend can reuse it unchanged.

Run with:
    streamlit run app/ui/streamlit_app.py
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import streamlit as st

# When Streamlit runs this file directly its directory (app/ui) is on sys.path,
# not the project root.  Add the root so `import app...` resolves.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.chatbot_service import (  # noqa: E402
    ChatResponse,
    check_services,
    clear_customer_memories,
    delete_customer_memory,
    list_customer_memories,
    process_message,
)

CUSTOMERS = ["customer_001", "customer_002", "customer_003"]
MAX_MESSAGES_PER_CUSTOMER = 200


def new_session_id(customer_id: str) -> str:
    """Create a short, unique conversation id for LangGraph checkpointing."""
    return f"{customer_id}-{uuid.uuid4().hex[:8]}"


def init_state() -> None:
    """Initialise per-browser-session state once."""
    if "chats" not in st.session_state:
        st.session_state.chats = {}
    if "customer_id" not in st.session_state:
        st.session_state.customer_id = CUSTOMERS[0]
    if "service_status" not in st.session_state:
        st.session_state.service_status = None
    if "memories" not in st.session_state:
        st.session_state.memories = {}
    if "notice" not in st.session_state:
        st.session_state.notice = None


def chat_for(customer_id: str) -> dict:
    """Return (and create) the chat history container for one customer.

    Histories are stored per customer, so switching customers never shows the
    previous customer's messages.
    """
    chats = st.session_state.chats
    if customer_id not in chats:
        chats[customer_id] = {"session_id": new_session_id(customer_id), "messages": []}
    return chats[customer_id]


def render_status_sidebar() -> None:
    """Render service statuses, with an explicit refresh action."""
    st.sidebar.subheader("Connection status")
    if st.sidebar.button("Check connections", use_container_width=True):
        with st.spinner("Checking Mem0 MCP, Ollama and the product API..."):
            st.session_state.service_status = check_services()

    if st.session_state.service_status is None:
        # Check once per browser session; the button above forces a refresh.
        with st.spinner("Checking services..."):
            st.session_state.service_status = check_services()

    status = st.session_state.service_status
    for label, key in (
        ("Mem0 MCP", "memory"),
        ("Ollama", "ollama"),
        ("Product API", "product_api"),
        ("Query router", "router"),
    ):
        entry = status.get(key, {})
        if entry.get("ok"):
            st.sidebar.success(f"{label}: online")
        else:
            st.sidebar.error(f"{label}: unavailable")
        st.sidebar.caption(entry.get("message", ""))

    memory_tools = status.get("memory", {}).get("tools") or []
    if memory_tools:
        with st.sidebar.expander("Discovered Mem0 tools"):
            for tool in memory_tools:
                st.write(f"- `{tool}`")


def render_memory_sidebar(customer_id: str) -> None:
    """Render real Mem0 memories with refresh and deletion controls."""
    st.sidebar.subheader("Customer memory")
    st.sidebar.caption(f"Scope: `{customer_id}` — only this customer's memories.")

    refresh = st.sidebar.button("Refresh memories", use_container_width=True)
    if refresh or customer_id not in st.session_state.memories:
        with st.spinner("Reading memories from Mem0 Cloud via MCP..."):
            st.session_state.memories[customer_id] = list_customer_memories(customer_id)

    result = st.session_state.memories.get(customer_id)
    if result is None:
        return

    if not result.ok:
        st.sidebar.error("Memory retrieval failed")
        st.sidebar.caption(result.error or "Unknown error")
        st.sidebar.caption(
            "Recommendations continue without memory personalisation. "
            "No preference is being invented."
        )
        return

    if not result.records:
        st.sidebar.info("No long-term preferences stored for this customer yet.")
        return

    for record in result.records:
        with st.sidebar.container(border=True):
            st.write(record.text)
            if record.memory_id:
                if st.button(
                    "Delete",
                    key=f"delete-{customer_id}-{record.memory_id}",
                    use_container_width=True,
                ):
                    deletion = delete_customer_memory(customer_id, record.memory_id)
                    if deletion.ok:
                        st.session_state.memories.pop(customer_id, None)
                        st.session_state.notice = ("success", "Memory deleted.")
                    else:
                        st.session_state.notice = (
                            "error",
                            f"Delete failed: {deletion.error}",
                        )
                    st.rerun()

    with st.sidebar.expander("Delete all memories for this customer"):
        confirm = st.checkbox(
            "I understand this permanently deletes this customer's memories",
            key=f"confirm-delete-all-{customer_id}",
        )
        if st.button(
            "Delete all memories",
            disabled=not confirm,
            key=f"delete-all-{customer_id}",
            use_container_width=True,
        ):
            deletion = clear_customer_memories(customer_id)
            st.session_state.memories.pop(customer_id, None)
            st.session_state.notice = (
                ("success", "All memories deleted.")
                if deletion.ok
                else ("error", f"Delete failed: {deletion.error}")
            )
            st.rerun()


def render_sidebar() -> str:
    """Render the whole sidebar and return the selected customer id."""
    st.sidebar.title("Shopping Assistant")
    st.sidebar.caption("Mem0 Cloud memory over MCP + local Ollama")

    selected = st.sidebar.selectbox(
        "Signed in as",
        CUSTOMERS,
        index=CUSTOMERS.index(st.session_state.customer_id)
        if st.session_state.customer_id in CUSTOMERS
        else 0,
        help="Each customer has an isolated Mem0 memory scope.",
    )

    if st.session_state.notice:
        level, message = st.session_state.notice
        (st.sidebar.success if level == "success" else st.sidebar.error)(message)
        st.session_state.notice = None

    render_status_sidebar()
    st.sidebar.divider()
    render_memory_sidebar(selected)
    st.sidebar.divider()

    if st.sidebar.button("New conversation", use_container_width=True):
        chat = chat_for(selected)
        chat["session_id"] = new_session_id(selected)
        chat["messages"] = []
        st.sidebar.success("Conversation cleared. Long-term memories are kept.")
        st.rerun()

    st.sidebar.caption(
        "Clearing the conversation does not delete Mem0 preferences. "
        "Memories survive app restarts."
    )
    return selected


def render_product(product: dict) -> None:
    """Render one recommendation card using catalogue data only."""
    with st.container(border=True):
        left, right = st.columns([3, 1])
        with left:
            st.markdown(f"**{product.get('name', 'Unknown product')}**")
        with right:
            st.markdown(f"**₹{product.get('price', 0):,}**")
        badges = [
            str(product.get("brand", "")),
            f"⭐ {product.get('rating', '-')}",
            "Wireless" if product.get("wireless") else "Wired",
        ]
        if product.get("noise_cancellation"):
            badges.append("Noise cancellation")
        st.caption(" · ".join(badge for badge in badges if badge))
        features = product.get("features") or []
        if features:
            st.write("Features: " + ", ".join(features))


def render_assistant_message(message: dict) -> None:
    """Render an assistant reply, its products and diagnostics."""
    st.markdown(message.get("content", ""))
    for product in message.get("recommendations", []):
        render_product(product)

    meta = message.get("meta") or {}
    saved = meta.get("memory_saved") or []
    if saved:
        st.success("Saved to Mem0: " + "; ".join(saved))
    elif meta.get("memory_writes", {}).get("status") == "no_new_memories":
        st.caption("Nothing new to remember from this turn.")

    pending = meta.get("memory_pending") or []
    if pending:
        st.warning(
            "Mem0 is still processing, so this is not confirmed as saved: "
            + "; ".join(pending)
        )

    failures = (meta.get("memory_writes") or {}).get("failures") or []
    if failures:
        st.error("Memory operation failed: " + "; ".join(failures))

    route = meta.get("route") or {}
    if route:
        st.caption(
            f"Route: {route.get('intent', 'unknown')} · "
            f"{route.get('source', 'unknown')} · "
            f"products={route.get('wants_products')} · "
            f"memory={route.get('needs_personalisation')}"
        )

    if meta.get("relaxed_preferences"):
        st.caption(
            "Relaxed remembered preference because no product matched: "
            + ", ".join(meta["relaxed_preferences"])
        )

    errors = meta.get("errors") or []
    if errors:
        with st.expander("Diagnostics"):
            for error in errors:
                st.warning(error)
            st.caption(
                f"memory={meta.get('memory_status')} · llm={meta.get('llm_status')} · "
                f"products={meta.get('product_status')}"
            )
            if meta.get("requirements"):
                st.json(meta["requirements"])


def render_chat(customer_id: str) -> None:
    """Render the conversation and handle a new message."""
    chat = chat_for(customer_id)
    messages = chat["messages"]

    for message in messages:
        with st.chat_message(message["role"]):
            if message["role"] == "assistant":
                render_assistant_message(message)
            else:
                st.markdown(message["content"])

    prompt = st.chat_input("Ask for headphones, or tell me your preferences...")
    if not prompt:
        return

    messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Thinking and checking memory..."):
            result: ChatResponse = process_message(
                customer_id, chat["session_id"], prompt
            )
        assistant_message = {
            "role": "assistant",
            "content": result.response,
            "recommendations": result.recommendations,
            "meta": {
                "memory_saved": result.memory_saved,
                "memory_pending": result.memory_pending,
                "memory_writes": result.memory_writes,
                "route": result.route,
                "route_guards": result.route_guards,
                "router_usage": result.router_usage,
                "memory_status": result.memory_status,
                "llm_status": result.llm_status,
                "product_status": result.product_status,
                "errors": result.errors,
                "requirements": result.requirements,
                "relaxed_preferences": result.relaxed_preferences,
            },
        }
        render_assistant_message(assistant_message)

    messages.append(assistant_message)
    messages[:] = messages[-MAX_MESSAGES_PER_CUSTOMER:]

    # Memory writes may have changed the sidebar listing.
    if result.memory_saved:
        st.session_state.memories.pop(customer_id, None)


def main() -> None:
    """Application entry point."""
    st.set_page_config(
        page_title="Retail Memory Shopping Assistant",
        page_icon="🛍️",
        layout="centered",
    )
    init_state()
    customer_id = render_sidebar()

    if customer_id != st.session_state.customer_id:
        st.session_state.customer_id = customer_id
        chat_for(customer_id)

    st.title("Retail Memory Shopping Assistant")
    st.caption(
        "Ask for headphones. I remember stable preferences with Mem0 Cloud and apply "
        "them in later conversations. Sample catalogue only."
    )

    render_chat(customer_id)


if __name__ == "__main__":
    main()
