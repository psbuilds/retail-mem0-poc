# `tests/conftest.py`

## 1. File purpose

Shared pytest fixtures and deterministic fakes. It lets the workflow run with no
network, no Mem0 account and no Ollama, while still using the real catalogue data.

## 2. Imports and dependencies

| Import | Why |
|---|---|
| `json`, `Path`, `re`, `Any` | Load catalogue, parse prompts |
| `pytest` | Fixtures |
| `build_graph`, `WorkflowDeps` | Assemble test graphs |
| `Settings` | Isolated configuration |
| `OllamaUnavailableError` | Simulate Ollama failure |
| `MemoryManager` | Real memory rules with fake service |
| `MemoryRecord`, `MemoryResult` | Memory shapes |

`CATALOGUE_PATH` points at `backend/data/products.json`.

## 3. Fixtures

### `catalogue` (session scope)
Loads the real catalogue once and returns the product list. Tests therefore assert
against actual sample data, not a second copy.

### `settings`
Returns:
```python
Settings(mem0_api_key="test-key", mem0_mcp_url="https://mcp.mem0.ai/mcp",
         ollama_model="llama3.1", max_recommendations=5, memory_top_k=20)
```
The dummy key means tests never depend on a developer's `.env`.

## 4. Fake classes

### `FakeLLM`
Attributes:
- `requirements` — default extraction JSON;
- `requirements_by_message` — per-exact-message extraction JSON;
- `preferences` — preference extraction result;
- `response` — generated reply text;
- `fail_json`, `fail_chat` — force `OllamaUnavailableError`;
- call counters.

Methods:
- `_current_message(messages)` — regex-extracts the quoted customer message from
  the requirement/preference prompt.
- `chat_json(messages, temperature=0.1)` — fails if configured; returns the
  per-message/default requirements when the system prompt contains "shopping
  requirements", otherwise `{"preferences": ...}`.
- `chat(messages, json_mode=False, temperature=0.2)` — returns `response` or raises.

This makes scenario tests read like the real conversation (message → extraction).

### `FakeMemoryService`
Implements the subset of `MemoryService` used by the graph:
- in-process `{customer: [MemoryRecord]}` store, initially populated only for
  `customer_001` when memories are supplied;
- `fail_search` / `fail_add` flags;
- `added` log of `(customer, text, metadata)`.

Methods:
- `search_memories(customer, query, limit)` → records for that customer or an error
  result.
- `list_memories(customer, limit, page)`.
- `add_memory(customer, text, metadata, infer)` → appends a record and returns
  success, or a failure result.
- `texts_for(customer)` — helper for assertions.

Because records are stored per customer, isolation tests are meaningful.

### `FakeProductService`
Uses the real catalogue list.
- `available_brands()` → sorted brands or raises `ProductAPIError` when `fail=True`.
- `fetch_products(category, max_budget, query)` → category/budget filtering (the
  same superset behaviour as the real service) and records fetch calls.

## 5. Functions

### `make_deps(settings, products, memory=None, llm=None, product_service=None) -> WorkflowDeps`
Builds a dependency bundle with fakes; creates `MemoryManager(memory)` so memory
rules run for real against the fake store.

### `make_graph(deps)`
Calls `build_graph(deps=deps)` with a fresh `InMemorySaver`, giving each test its own
conversation state.

## 6. Execution workflow

```
pytest starts
  -> catalogue/settings fixtures created as needed
  -> test builds fakes and deps
  -> make_graph
  -> process_message / node calls
  -> assertions on ChatResponse and fake call logs
```

## 7. Input and output

Input:
```python
deps = make_deps(settings, catalogue, memory=FakeMemoryService(), llm=FakeLLM())
graph = make_graph(deps)
```

Output: a compiled graph usable exactly like production.

## 8. Interaction with other modules

Imports application modules under test (graph, nodes, manager, models) but no
network clients. Test modules import these fakes and helpers.

## 9. Important implementation decisions

- All tests use the real `MemoryManager` and recommendation engine; only external
  boundaries are faked. This keeps precedence/memory-rule tests meaningful.
- `requirements_by_message` lets a single graph support multi-turn follow-up tests
  with different model outputs per message.
- `fail_search`/`fail_add`/`fail` flags make error-path tests explicit.
- No live-service fixtures: live tests read the environment and skip themselves.
