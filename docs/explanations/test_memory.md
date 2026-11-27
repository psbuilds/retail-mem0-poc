# `tests/test_memory.py`

## 1. File purpose

Tests the memory layer in two clearly separated modes:

- **Mocked MCP tests** use a fake MCP session injected into the real
  `Mem0MCPClient`, exercising connection queueing, tool discovery, schema
  adaptation, scoping, isolation and error classification without touching Mem0.
- **Live test** (`@pytest.mark.live`) contacts the real Mem0 Cloud MCP server and
  is skipped unless `MEM0_API_KEY` is set.

## 2. Imports and dependencies

`os`, `uuid`, `pytest`, `Settings`, the client exceptions and classes,
`MemoryManager`, `PreferenceCandidate`, `MemoryService`, `extract_raw_records`.

## 3. Fake MCP objects

| Class | Purpose |
|---|---|
| `FakeTool` | name/description/input_schema like an MCP tool |
| `FakeListTools` | `tools` list returned by `list_tools` |
| `FakeText` | text content item |
| `FakeResult` | JSON text content, no structured content, `is_error=False` |
| `FakeMCPSession` | async `list_tools`/`call_tool`; records calls; stores per-user records |

`default_tools()` returns schemas for `add_memory` (messages/user_id),
`search_memories` (query/filters/limit), `get_memories` (filters/page/page_size),
`delete_memory` (memory_id), `delete_all_memories` (filters/confirm).

`FakeMCPSession` implements just enough behaviour to return created records,
filter by user, delete by id and delete all in a scope. It can raise a configured
exception on every call to simulate auth/transport failures.

## 4. Fixtures

- `fake_session` — fresh fake.
- `client` — real `Mem0MCPClient(settings=..., client_factory=lambda: fake_session)`.
- `service` — real `MemoryService` over that client.

The real client's background loop, queue, schema adaptation and result parsing all
run; only the network session is fake.

## 5. Mocked tests

| Test | Verifies |
|---|---|
| `test_tool_discovery` | discovers the seven tools used by the fake transport and their required args |
| `test_add_memory_adapts_to_schema` | sends `messages`, not `text`, because the schema requires messages; sends `user_id` |
| `test_search_memory_is_scoped_to_customer` | filters contain the customer id |
| `test_another_customer_cannot_see_memories` | different customer gets no records |
| `test_records_for_other_users_are_dropped` | defensive filtering of a mixed server response |
| `test_list_memories` | listing returns the stored memory |
| `test_delete_memory_verifies_ownership` | owner deletes successfully |
| `test_delete_foreign_memory_is_refused` | foreign id → error and no deletion |
| `test_delete_all_memories_is_customer_scoped` | only that customer's records removed |
| `test_update_memory_verifies_ownership` | ownership is verified, then the text is updated |
| `test_missing_api_key_is_reported` | status not configured; add fails with setup message |
| `test_authentication_failure_is_classified` | 401 becomes an authentication error |
| `test_missing_scope_argument_is_refused` | schema without user scope is refused |
| `test_call_tool_rejects_unknown_tool` | unknown tool raises `Mem0MCPError` |
| `test_not_configured_client_raises` | real transport raises `Mem0NotConfiguredError` |
| `test_extract_raw_records_handles_wrapped_payloads` | parses top-level/nested result shapes |

| `test_add_memory_returns_event_and_wait_succeeds` | add returns `event_id` + `PENDING`; polling reaches `SUCCEEDED` with text |
| `test_event_timeout_is_pending_not_success` | timeout keeps `PENDING`, `ok=False`, timeout message |
| `test_failed_event_surfaces_its_error` | `FAILED` status and Mem0 error are surfaced |

Memory-manager tests (also mocked):
- temporary statements are rejected by policy and never written;
- one-off budgets are rejected while a usual budget is accepted;
- vague statements are rejected;
- a persistent preference is saved only after its event completes;
- a `PENDING` event is reported as pending, never saved;
- a `FAILED` event is reported as a failure;
- a tool failure produces `failures` and `status="error"`;
- delete corrections target only matching records;
- replace corrections rewrite the brand in the stored text;
- a correction with no matching record changes nothing (`not_found`);
- an invented memory id cannot be deleted.

## 6. Live test

`test_live_mem0_add_search_isolation_and_delete`:
1. skips if `MEM0_API_KEY` is unset;
2. connects and lists tools;
3. adds a labelled probe memory for a unique customer (returns an event id);
4. waits briefly (hosted extraction may be asynchronous);
5. searches as the same customer and requires the probe;
6. searches as a different customer and requires no probe;
7. deletes returned memories.

## 7. Execution workflow

```
pytest tests/test_memory.py
  -> each mocked test builds client/service around FakeMCPSession
  -> real client starts a background loop task with the fake session
  -> service calls tools
  -> assertions on arguments/results/errors
  -> client threads end with the test process

pytest -m live
  -> real Streamable HTTP session to mcp.mem0.ai
```

## 8. Input and output

Input:
```python
service.add_memory("customer_001", "Usually prefers Sony headphones.")
```

Assertions:
```python
fake_session.calls[-1] == ("add_memory", {
    "messages": [{"role": "user", "content": "Usually prefers Sony headphones."}],
    "user_id": "customer_001",
    "metadata": {...},
    "infer": True,
})
```

## 9. Interaction with other modules

Tests `memory_client`, `memory_service` and `memory_manager`. It does not use the
graph or UI.

## 10. Important decisions

- The fake is at the MCP *session* boundary, not at `MemoryService`, so the real
  schema adaptation, scoping and parsing code is covered.
- Live tests are separately marked and skipped by default, so `pytest` results
  never imply live verification.
- The live test cleans up after itself but warns if cleanup is impossible.
- In this environment the live test was skipped (no key); Mem0 status is BLOCKED,
  not passed.

## 11. Example execution

```bash
uv run pytest tests/test_memory.py -q
uv run pytest tests/test_memory.py -m live -q   # needs MEM0_API_KEY
```
