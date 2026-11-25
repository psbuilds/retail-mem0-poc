# `scripts/check_services.py`

## 1. File purpose

Operational diagnostics. It verifies the three external dependencies and provides
an explicit, opt-in live Mem0 MCP round-trip. It never runs live writes unless
asked.

## 2. Imports and dependencies

| Import | Why |
|---|---|
| `argparse` | CLI flags |
| `json` | Print tool schemas and memory texts |
| `sys`, `Path` | Exit codes/stderr; add the project root to `sys.path` |
| `datetime`, `timezone` | Unique probe label |
| `ollama_status` | Ollama check |
| `Mem0MCPClient`, `Mem0MCPError` | Tool discovery |
| `get_memory_service` | Memory operations |
| `product_api_status` | Catalogue check |

## 3. Classes

No classes.

## 4. Startup path handling

When the file is executed as `uv run python scripts/check_services.py`, Python puts
`scripts/` on `sys.path`, not the project root. The module therefore inserts
`Path(__file__).resolve().parents[1]` before importing `app.*`. Without this, the
documented command fails with `ModuleNotFoundError: No module named 'app'`.

## 5. Functions and methods

### `_print_header(title)`
Prints a formatted section header.

### `check_status() -> int`
Prints:
- Mem0: configured, connected, message, discovered tools;
- Ollama: status message and whether `llama3.1` is installed;
- Product API: online and product count, or the real error.

Returns `1` if any service is unavailable, else `0`. Missing Mem0 credentials
count as a failure so the report is honest.

### `list_tools() -> int`
Calls `Mem0MCPClient().list_tools(refresh=True)` and prints each tool's name,
description and input schema as JSON. Returns `1` on an MCP error. This is how the
actual live tool contract is inspected.

### `live_memory_test(customer_id) -> int`
Real write/read/isolation/delete probe:

1. `add_memory(customer_id, <labelled probe>)`.
2. `search_memories(customer_id, "test-brand headphones")`; requires the probe in
   results.
3. `search_memories(other_customer, ...)`; requires **no** probe result.
4. Deletes the probe by memory id when an id is returned.

Each step prints `OK`/`FAIL`/`WARN` and returns non-zero on failure. If cleanup is
impossible it warns rather than pretending success.

### `main() -> int`
Parses:
- `--tools`
- `--live-memory-test`
- `--customer` (default `customer_001`)

and dispatches. Default is `check_status()`.

## 6. Execution workflow

```
uv run python scripts/check_services.py
  -> check_status
       -> memory_service.status -> MCP tool discovery
       -> ollama_status
       -> product_api_status
  -> exit 0/1
```

## 7. Input and output

```bash
uv run python scripts/check_services.py
```
```
=== Mem0 Cloud MCP ===
configured: True
connected : True
message   : Mem0 MCP connected
tools     : add_memory, delete_all_memories, ...
```

```bash
uv run python scripts/check_services.py --live-memory-test --customer customer_001
```
```
Adding probe memory for customer_001 ...
OK add_memory
Searching for the probe as the same customer ...
OK search_memories returned the probe
Checking isolation by searching as customer_001_isolation_probe ...
OK isolation: other customer did not retrieve the probe
OK delete_memory cleaned up the probe
```

## 8. Interaction with other modules

Uses `memory_service`, `memory_client`, `ollama_client` and `product_service`. It
does not touch Streamlit or the graph.

## 9. Important implementation decisions

- Live writes are opt-in and clearly labelled, so normal diagnostics never modify
  the Mem0 account.
- The isolation probe is a real second customer id, matching the project's
  isolation requirement.
- Failures print the actual error; there is no fallback provider or mocked result.
- Exit codes make the script usable in CI once credentials exist.

## 10. Example execution

In this development environment the status command reports Mem0 as not configured
(no key) while Ollama and the product API are online. The live test is therefore
BLOCKED here, not passed.
