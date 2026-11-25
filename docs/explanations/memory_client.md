# `app/memory/memory_client.py`

## 1. File purpose

The transport layer for Mem0 Cloud MCP. It:

- opens and maintains an authenticated Streamable HTTP session;
- discovers the tools the live server exposes and their input schemas;
- invokes tools with schema-adapted arguments;
- parses tool results safely;
- classifies connection/authentication/tool errors.

Only this file knows MCP details. It never logs the API key and never uses the
Mem0 SDK or REST API.

## 2. Imports and dependencies

| Import | Why |
|---|---|
| `asyncio`, `concurrent.futures`, `threading`, `AsyncExitStack` | Dedicated event loop + one session-owning task |
| `json` | Parse tool results, schema errors |
| `dataclass`, `field`, `Any`, `Callable`, `Coroutine` | Types |
| `app.config.settings.Settings`, `get_settings` | Endpoint, key, timeout |
| `mcp` SDK (imported inside `_open_session`) | `ClientSession`, `create_mcp_http_client`, `streamable_http_client` |

The MCP imports are local to `_open_session` so importing this module (and running
unit tests with a fake session) does not require transport setup.

## 3. Classes

### Exceptions

- `Mem0MCPError(RuntimeError)` — base for MCP failures.
- `Mem0NotConfiguredError` — no `MEM0_API_KEY`.
- `Mem0AuthenticationError` — 401/403/authentication required.
- `Mem0ToolNotFoundError` — server does not expose the requested tool.

### `ToolInfo` (frozen dataclass)

| Attribute | Meaning |
|---|---|
| `name` | MCP tool name |
| `description` | Server description |
| `input_schema` | JSON schema from `list_tools` |

Properties:
- `properties` → `input_schema["properties"]` (or `{}`);
- `required` → `input_schema["required"]` (or `[]`).

### `ToolCallResult`

| Attribute | Meaning |
|---|---|
| `tool` | Invoked tool name |
| `payload` | Parsed result (structured content or JSON/text) |
| `is_error` | Always `False` for successful calls; errors raise |

### `Mem0MCPClient`

Important attributes:
- `settings`, `url`, `timeout`;
- `_client_factory` — test seam returning a session-like object;
- `_loop`, `_loop_thread`, `_queue`, `_runner_future`;
- `_connected`, `_session`, `_tools` (cache), `_last_error`;
- `_runner_lock` for thread-safe start/restart.

## 4. Functions and methods

### `_stringify_exception(exc) -> str`
Walks `__cause__`/`__context__` and joins type+message, so nested MCP/HTTP errors
are visible in one string. Guards against cycles.

### `api_key` (property)
Returns the stripped key. Used only to build the auth header.

### `_auth_headers() -> dict[str, str]`
Raises `Mem0NotConfiguredError` if the key is missing. Otherwise returns:
```python
{
  "Authorization": "Token <MEM0_API_KEY>",
  "Accept": "application/json, text/event-stream",
}
```
This is the documented API-key method for the hosted server. The key is never
logged.

### `_ensure_loop()` / `_run_loop(loop)`
Starts one daemon thread running an asyncio event loop (`mem0-mcp-loop`). Reused for
all sessions in the process.

### `_ensure_runner()`
Starts the session task if not already connected:
- creates an `asyncio.Queue`;
- creates a `ready` future;
- schedules `_runner(ready)` on the loop with `run_coroutine_threadsafe`;
- waits (with timeout) for the session to initialise.
If the runner previously ended, a new one is started. A hung operation is not
force-cancelled; subsequent calls time out and report it.

### `_consume_runner_exception(future)`
Retrieves the runner exception so asyncio does not warn about an unretrieved
exception.

### `_runner(ready)`
The single task that owns the MCP session:

1. If a test `_client_factory` exists, use it; otherwise `_open_session`.
2. Mark connected, set `ready`.
3. Loop: take `(operation, future)` from the queue, await
   `operation(self._session)`, deliver the result or exception to the caller.
4. On `None`, exit and close the stack.
5. On any error, set `ready` exception (classified) and propagate.
6. Finally, close the `AsyncExitStack`.

All session operations happen in this task, satisfying anyio's requirement that
the Streamable HTTP task group is entered and exited in the same task.

### `_open_session(stack) -> ClientSession`
```python
headers = self._auth_headers()
http_client = create_mcp_http_client(headers=headers)
await stack.enter_async_context(http_client)
read, write = await stack.enter_async_context(
    streamable_http_client(self.url, http_client=http_client))
session = await stack.enter_async_context(ClientSession(read, write))
await session.initialize()
return session
```
`AsyncExitStack` keeps the HTTP client, transport and session alive until the
runner ends.

### `_submit(operation, timeout=None) -> Any`
Queues an async operation on the session task and waits for its
`concurrent.futures.Future`. Timeouts raise `Mem0MCPError`; other exceptions are
classified and re-raised.

### `_classify_error(exc) -> Mem0MCPError`
Maps:
- 401 / "authentication required" / "unauthor" → `Mem0AuthenticationError`;
- 403 / "forbidden" → `Mem0AuthenticationError` with the 403 message;
- everything else → `Mem0MCPError`.

### `list_tools(refresh=False) -> list[ToolInfo]`
Calls `session.list_tools()` once (unless `refresh`), caches by name, and returns
`ToolInfo` objects. Handles both `input_schema` and `inputSchema` attribute names.

### `get_tool(name) -> ToolInfo`
Returns a discovered tool or raises `Mem0ToolNotFoundError` listing available
tools. This is how the app avoids guessing tool names.

### `build_arguments(tool, candidates) -> dict`
Schema adaptation:
- drops unknown keys (e.g. `text` when the schema only accepts `messages`);
- drops `None` values;
- raises if a required argument is missing (including the schema in the message).

This supports different documented variants of `add_memory`/`search_memories`
without hardcoding one server version.

### `call_tool(name, arguments=None, timeout=None) -> ToolCallResult`
- Resolves the tool (and therefore its schema).
- Builds arguments.
- Invokes `session.call_tool(name, payload)` through `_submit`.
- Normalises the result.

### `_normalise_result(name, result) -> ToolCallResult`
- Reads `is_error`/`isError`.
- Prefers `structured_content`; otherwise joins text content and attempts
  `json.loads`, falling back to `{"text": ...}`.
- Raises `Mem0MCPError` for error results instead of returning a fake success.

### `check_connection() -> tuple[bool, str]`
Returns a UI-safe status:
- not configured → `(False, "…MEM0_API_KEY missing")`;
- auth/connection error → `(False, actual message)`;
- success → `(True, "Mem0 MCP connected — tools: …")`.

### `last_error` (property)
Returns the most recent runner error, for diagnostics.

### `close()`
Queues `None` to stop the runner, stops the event loop and joins the thread (best
effort). Called by diagnostics/tests; the app keeps the session for its lifetime.

## 5. Execution workflow

```
list_tools()/call_tool()
  -> _ensure_runner()
       -> _ensure_loop() (once)
       -> schedule _runner(ready), wait for initialise
  -> enqueue operation
  -> _runner awaits operation(session)
  -> MCP server responds
  -> result/future returned
  -> normalise / classify
```

## 6. Input and output

Input:
```python
client.call_tool("search_memories", {"query": "Sony", "limit": 5})
```

Output:
```python
ToolCallResult(
  tool="search_memories",
  payload={"results": [{"id": "...", "memory": "Prefers Sony", "user_id": "customer_001"}]},
  is_error=False,
)
```

## 7. Interaction with other modules

`memory_service.py` is the only caller. It provides customer scoping and higher
level operations. No other module imports this client.

## 8. Important implementation decisions

- **One persistent session per process** avoids reconnecting on every Streamlit
  rerun.
- **Tool discovery + schema adaptation** prevents guessing tool names or argument
  formats, as required by the project instructions.
- **Single-task session ownership** keeps anyio task groups valid.
- **Explicit failures**: authentication, timeout and tool errors are typed and
  surfaced; nothing is retried as a fake success or switched to another provider.
- **Defensive result parsing** supports structured content, JSON text and plain
  text without inventing records.
- The API key is only placed in the request header; error strings from HTTP
  libraries are surfaced without printing the configured key.

## 9. Example execution

```python
client = Mem0MCPClient()
try:
    tools = client.list_tools()
    print([tool.name for tool in tools])
    result = client.call_tool("get_memories", {
        "filters": {"AND": [{"user_id": "customer_001"}]},
        "page": 1,
        "page_size": 20,
    })
    print(result.payload)
except Mem0NotConfiguredError:
    print("Set MEM0_API_KEY in .env")
finally:
    client.close()
```

In tests, `Mem0MCPClient(settings=..., client_factory=lambda: fake_session)`
exercises this real queueing/schema code without network access.
