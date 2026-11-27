# `tests/conftest.py`

## 1. Purpose

Shared fixtures and deterministic fakes so the suite never contacts Mem0, Jev,
Ollama or HTTP. The fakes implement exactly the methods the workflow uses.

## 2. Fixtures

| Fixture | Returns |
|---|---|
| `catalogue` (session scope) | The real `backend/data/products.json` product list |
| `settings` | `Settings(mem0_api_key="test-key", ...)`; `TYPESAFE_API_KEY` stays empty, so routing uses rules unless a test injects `FakeJev` |

`make_deps(settings, products, memory=None, llm=None, product_service=None)`
builds `WorkflowDeps`; `WorkflowDeps.__post_init__` creates the
`MemoryManager` and `QueryRouter` when not supplied.

`make_graph(deps)` compiles a graph with a fresh `InMemorySaver`.

## 3. `FakeLLM`

| Attribute | Purpose |
|---|---|
| `requirements`, `requirements_by_message` | JSON returned by `chat_json` |
| `response` | Text returned by `chat` |
| `fail_json`, `fail_chat` | Simulate Ollama being down |
| `chat_json_calls`, `chat_calls` | Call counters |

It detects the requirement prompt by its system text and extracts the current
message from the user prompt. Preference extraction was removed from the
application, so no preference fake exists.

## 4. `FakeMemoryService`

Mirrors the async write contract:

- `search_memories` — increments `search_calls`, returns seeded records or an
  error when `fail_search`.
- `add_memory` — records the attempt, creates an event id, and returns
  `event_id` + `PENDING`. It does **not** store the record yet.
- `wait_for_event` — `SUCCEEDED` stores the record and returns its id/text;
  `PENDING`/`FAILED` do not store and report the status (`event_status`).
- `update_memory` — rewrites a record in the customer scope.
- `delete_memory` — removes a record in the customer scope; unknown ids fail.
- `texts_for(customer_id)` — stored texts.
- `deleted`, `updated`, `added`, `events` — inspection lists.

Constructor flags: `memories`, `fail_search`, `fail_add`, `event_status`,
`fail_delete`, `fail_update`.

Seeded memories belong to `customer_001`; other customers start empty, which
makes isolation tests explicit.

## 5. `FakeProductService`

Backed by the real catalogue. `available_brands()` returns the catalogue brands;
`fetch_products(category, max_budget, query)` filters the same way as the API and
records each call in `fetches`. `fail=True` raises `ProductAPIError`.

## 6. Why these fakes

- They preserve the real contracts (async events, scoped operations, error
  shapes), so tests exercise application logic rather than a simplified mock.
- They make every failure path (pending write, failed event, catalogue error,
  Ollama outage, memory search failure) reproducible without credentials.
- Live verification is isolated in `@pytest.mark.live` tests skipped unless
  `MEM0_API_KEY`/`TYPESAFE_API_KEY` are exported.
