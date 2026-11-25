# `app/ui/streamlit_app.py`

## 1. File purpose

The POC's chat frontend. It handles presentation and user actions only:
customer selection, chat history, product cards, connection status, memory
inspection and memory deletion. All chatbot logic is in the service layer.

## 2. Imports and dependencies

| Import | Why |
|---|---|
| `sys`, `pathlib.Path` | Add the project root to `sys.path` when Streamlit runs the file directly |
| `uuid` | Generate unique session ids |
| `streamlit` | UI framework |
| `app.services.chatbot_service` | `process_message`, `check_services`, memory helpers |

No memory client, graph, prompt, product client or recommendation module is
imported.

Path handling:
```python
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
```
Streamlit puts `app/ui` on `sys.path`, so this makes `import app...` work.

## 3. Classes

No classes. State is held in `st.session_state` with these keys:

| Key | Content |
|---|---|
| `chats` | `{customer_id: {"session_id": str, "messages": list}}` |
| `customer_id` | Currently selected customer |
| `service_status` | Cached `check_services()` result |
| `memories` | `{customer_id: MemoryResult}` for the inspector |
| `notice` | One-shot sidebar success/error message |

Messages are stored per customer, which prevents cross-customer display.

## 4. Functions and methods

### `new_session_id(customer_id: str) -> str`
Returns `f"{customer_id}-{uuid4().hex[:8]}"`. Used for new chats and for the
"New conversation" action. A new id means LangGraph no longer reads the old thread.

### `init_state() -> None`
Creates the session keys on first run. Called once per `main()` execution; all
operations are idempotent.

### `chat_for(customer_id: str) -> dict`
Returns the customer's chat container, creating it with a fresh session id and
empty message list when absent. Ensures one session id per conversation.

### `render_status_sidebar() -> None`
- Provides a "Check connections" button.
- On the first render it automatically calls `check_services()` once and caches
  the result, so every rerun does not reconnect to MCP/Ollama.
- Shows `st.success`/`st.error` plus the real message for Mem0 MCP, Ollama and the
  product API.
- Lists discovered Mem0 tools in an expander when connected.

### `render_memory_sidebar(customer_id: str) -> None`
- "Refresh memories" calls `list_customer_memories(customer_id)`; results are
  cached per customer until refreshed or after a confirmed save.
- If retrieval fails, shows the actual error and an explicit note that
  recommendations continue without memory and nothing is invented.
- Shows each memory's text with a Delete button (when an id exists). Deletion goes
  through `delete_customer_memory`; success removes the cache and reruns.
- "Delete all memories" requires a confirmation checkbox, then calls
  `clear_customer_memories`. It never deletes other customers' memories.

### `render_sidebar() -> str`
Renders the customer selector (three synthetic IDs), one-shot notices, status and
memory panels, and the "New conversation" button. Returns the selected customer.

New conversation behaviour:
- generates a new session id;
- clears that customer's message list;
- leaves Mem0 memories untouched (caption explains this).

### `render_product(product: dict) -> None`
Renders one card inside a bordered container: name, price (`₹` with thousands
separators), brand, rating, wireless/wired, noise cancellation and features.
It reads only catalogue fields; it does not compute or invent anything.

### `render_assistant_message(message: dict) -> None`
Renders:
- reply text;
- product cards;
- a green "Saved to Mem0: …" notice only for confirmed writes;
- "Nothing new to remember" when the write report says so;
- a caption when remembered preferences were relaxed;
- a Diagnostics expander with errors, statuses and resolved requirements.

### `render_chat(customer_id: str) -> None`
- Renders stored messages for this customer.
- Uses `st.chat_input`; empty input does nothing.
- Appends the user message and displays it.
- Calls `process_message(customer_id, chat["session_id"], prompt)` inside a
  spinner.
- Stores the assistant message with recommendations and metadata, renders it, and
  caps the list at 200 messages.
- Invalidates the cached memory list when something was saved, so the sidebar
  refreshes on the next run.

### `main() -> None`
- `st.set_page_config` (title, icon, centered layout).
- `init_state()`, then `render_sidebar()`.
- Updates `st.session_state.customer_id` on customer switch (the chat container is
  already keyed by customer).
- Renders title, short explanation and `render_chat`.

## 5. Execution workflow

```
main
  set page config
  init session state
  render sidebar
    customer select
    auto status check (once)
    memory list for selected customer
    new conversation button
  render title/caption
  render chat for selected customer
    draw prior messages/products
    if chat input:
        process_message -> LangGraph -> Mem0/FastAPI/Ollama
        draw response + cards + memory notice
```

## 6. Input and output

Input: customer selection, chat text, button clicks.
Output: rendered chat, product cards, statuses, memory texts, notices/errors.

## 7. Interaction with other modules

Only `app/services/chatbot_service.py`. That service invokes LangGraph, memory,
product, recommendation and Ollama modules. The UI stays replaceable and a future
FastAPI chatbot endpoint can reuse the same service.

## 8. Important implementation decisions

- Per-customer chat storage: switching customers cannot expose another
  conversation.
- Memory/status results are cached in session state to avoid new MCP connections on
  each rerun.
- No memory data is prefilled; the panel shows exactly what Mem0 returns.
- Clearing chat does not call any memory deletion API.
- The UI never claims a memory was saved unless `memory_saved` is non-empty.
- Product cards show only catalogue data.
- Styling is deliberately minimal; the POC focuses on memory behaviour.

## 9. Example execution

```bash
.venv/bin/uv run streamlit run app/ui/streamlit_app.py
```

1. Select `customer_001`.
2. Send "I usually prefer Sony headphones with noise cancellation."
3. Send "New conversation", then "Suggest headphones for me."
4. Confirm Sony ANC products are recommended and the preference appears in the
   memory panel.

Performed in development: the page served HTTP 200 and Streamlit's `AppTest`
executed a real chat turn without exceptions. A browser-based visual review was
not performed.
