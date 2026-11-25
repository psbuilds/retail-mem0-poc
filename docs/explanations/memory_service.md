# `app/memory/memory_service.py`

## 1. File purpose

Application-facing memory API. It hides MCP tool names and payload shapes and
guarantees customer scoping. Callers get `MemoryResult` objects with an explicit
success flag, records and error text.

## 2. Imports and dependencies

| Import | Why |
|---|---|
| `json` | Verify the customer id appears in scoping filters |
| `dataclass`, `field` | Result/status models |
| `lru_cache` | One service per process |
| `Any` | Raw payloads |
| `Settings`, `get_settings` | Limits/configuration |
| `Mem0MCPClient`, `Mem0MCPError`, `Mem0NotConfiguredError`, `Mem0ToolNotFoundError`, `ToolInfo` | Transport |

## 3. Classes

### `MemoryRecord` (frozen dataclass)

| Attribute | Meaning |
|---|---|
| `memory_id` | Mem0 id/memory_id/uuid (or empty) |
| `text` | Memory sentence |
| `metadata` | Metadata dict |
| `score` | Search similarity if provided |
| `created_at` | Creation timestamp if provided |
| `user_id` | Owner if the server returned it |

### `MemoryResult`

| Attribute | Meaning |
|---|---|
| `ok` | Operation genuinely succeeded |
| `operation` | `add`, `search`, `list`, `update`, `delete`, `delete_all` |
| `records` | Parsed records |
| `error` | Human-readable failure |
| `tool` | Tool used |
| `raw` | Parsed MCP payload |

### `MemoryServiceStatus`

`configured`, `connected`, `message`, `tools`. Used by the UI/diagnostics.

### `MemoryService`

Attributes: `settings`, `client`. Methods described below.

## 4. Functions and methods

### `_user_filter(customer_id) -> dict`
Returns the documented Mem0 filter:
```python
{"AND": [{"user_id": customer_id}]}
```

### `_first_present(payload, keys)`
Returns the first non-empty value among keys; used to tolerate naming variations
(`id`/`memory_id`/`uuid`, `memory`/`text`/`content`).

### `extract_raw_records(payload) -> list[dict]`
Finds records in a top-level list, `{"results": [...]}`, `{"memories": [...]}`,
`{"data": [...]}`, `{"items": [...]}`, or a nested `result`/`response`. Returns an
empty list rather than fabricating anything when nothing is found.

### `to_memory_record(raw) -> MemoryRecord | None`
Converts a raw dict; returns `None` when no usable text exists. Coerces score to
float when possible and keeps `user_id`.

### `MemoryService.status() -> MemoryServiceStatus`
- Missing key → `configured=False`, setup message.
- Otherwise calls `list_tools()` (connect on first use).
- MCP error → `connected=False` with the real message.
- Success → connected with tool names.

### `_scoped_arguments(tool, candidates, customer_id) -> dict`
Builds arguments via the client's schema adapter and **refuses** to call if the
result contains neither a `filters` dict mentioning the customer nor
`user_id == customer_id`. Preventative isolation.

### `_records_from_result(result, customer_id) -> list[MemoryRecord]`
Parses records and drops any whose returned `user_id` differs from the customer.
This is defence in depth against a server returning mixed results.

### `add_memory(customer_id, text, metadata=None, infer=True) -> MemoryResult`
- Validates customer id and non-empty text.
- Offers both `text` and `messages` candidates; the tool schema decides.
- Sends `user_id`.
- Parses created records.
- Any MCP error → `ok=False` with the error.

### `search_memories(customer_id, query, limit=None) -> MemoryResult`
- Sends `query`, `filters` (user scope), `user_id`, `limit` and `top_k` candidates
  so either documented schema variant works.
- Returns scoped records or an error.

### `list_memories(customer_id, limit=None, page=1) -> MemoryResult`
- Sends `filters`, `user_id`, `page`, `page_size`/`limit`/`top_k` candidates.
- Used by the UI's memory inspector and by ownership checks.

### `_owns_memory(customer_id, memory_id) -> bool`
Lists up to 100 memories for the customer and requires the id to be present.
Raises `Mem0MCPError` if listing fails, because ownership cannot be confirmed.

### `update_memory(customer_id, memory_id, text) -> MemoryResult`
Verifies ownership, then calls `update_memory` with id/text candidates. Reports a
missing tool or schema failure honestly.

### `delete_memory(customer_id, memory_id) -> MemoryResult`
Verifies ownership, then calls `delete_memory`. Refuses foreign ids. This is the
safety mechanism for the per-memory Delete button.

### `delete_all_memories(customer_id) -> MemoryResult`
Calls `delete_all_memories` with the customer filter and `confirm=True` as a
candidate. `_scoped_arguments` ensures the call is scoped; a global delete is
never attempted.

### `get_memory_service() -> MemoryService`
`@lru_cache(maxsize=1)`. One service/client per process, so Streamlit reruns reuse
the MCP session.

## 5. Execution workflow

```
search_memories(customer, query)
  -> client.get_tool("search_memories")   # discover schema
  -> _scoped_arguments(...)               # build + verify scope
  -> client.call_tool("search_memories", args)
  -> _records_from_result(...)            # parse + drop foreign users
  -> MemoryResult(ok=True, records=...)
```

## 6. Input and output

Input:
```python
service.add_memory("customer_001", "Usually prefers Sony headphones.")
```

Success:
```python
MemoryResult(ok=True, operation="add",
             records=[MemoryRecord(memory_id="...", text="Usually prefers Sony headphones.",
                                   user_id="customer_001")],
             tool="add_memory")
```

Failure:
```python
MemoryResult(ok=False, operation="add",
             error="Mem0 MCP request failed: ...", tool="add_memory")
```

## 7. Interaction with other modules

- `agent/nodes.py` uses search/add.
- `memory_manager.py` uses add for accepted preferences.
- `chatbot_service.py` exposes list/delete/clear to the UI.
- It is the only module that calls `Mem0MCPClient`.

## 8. Important implementation decisions

- **Fail closed on scoping**: if the schema cannot express a customer scope, the
  operation is refused.
- **Ownership before mutation**: update/delete verify the memory belongs to the
  customer, preventing accidental cross-customer edits.
- **No fabricated success**: `ok=True` only after a successful MCP call; parser
  fallbacks return empty lists rather than invented memories.
- **Tolerant naming**: different documented result/tool key variants are supported
  without changing the app.
- Errors are returned, not raised, for normal service failures; only programmer
  misuse (missing arguments) returns an error result too.

## 9. Example execution

```python
service = get_memory_service()
result = service.list_memories("customer_001")
if result.ok:
    for record in result.records:
        print(record.memory_id, record.text)
else:
    print("Memory unavailable:", result.error)
```
