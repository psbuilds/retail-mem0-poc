# `app/agent/nodes.py`

## 1. File purpose

Implements all seven LangGraph nodes and the helper logic they share:

- Pydantic validation of LLM requirement output;
- deterministic grounding of fields against the literal message;
- deterministic fallback extraction when Ollama fails;
- parsing memory sentences into preference hints;
- precedence resolution;
- memory-derived soft-filter relaxation.

## 2. Imports and dependencies

| Import | Why |
|---|---|
| `re`, `dataclass`, `Any` | Helpers and dependency bundle |
| `pydantic.BaseModel`, `Field`, `ValidationError`, `field_validator` | Validate model JSON |
| `app.agent.prompts.*` | System/user prompts |
| `app.agent.state.AgentState` | State type |
| `app.config.settings` | `Settings`/`get_settings` |
| `app.llm.ollama_client.OllamaClient`, `OllamaError` | LLM calls/errors |
| `app.memory.memory_manager.MemoryManager`, `PreferenceCandidate` | Memory rules |
| `app.memory.memory_service.MemoryService` | Memory operations |
| `app.products.product_client.ProductAPIError` | Catalogue error type |
| `app.products.product_service.ProductService` | Candidate retrieval/brands |
| `app.recommendations.recommendation_service.recommend` | Ranking |

## 3. Classes

### `RequirementExtraction(BaseModel)`

Validated shape of the model's extraction:

| Field | Default |
|---|---|
| `is_shopping_request` | `True` |
| `refines_previous_request` | `False` |
| `category` | `None` |
| `brand` | `None` |
| `max_budget` | `None` |
| `wireless` | `None` |
| `noise_cancellation` | `None` |
| `required_features` | `[]` |
| `excluded_features` | `[]` |

Validators:
- `normalise_category` maps headphone/over-ear/on-ear → `headphones`,
  earbud/earphone/tws/in-ear → `earbuds`, anything else → `None`.
- `clean_brand` strips whitespace/empties.
- `sane_budget` drops values ≤ 0.
- `clean_features` strips values and caps the list at 6 items.

### `WorkflowDeps` (dataclass)

Injected services:

| Attribute | Type |
|---|---|
| `memory_service` | `MemoryService` |
| `product_service` | `ProductService` |
| `llm_client` | `OllamaClient` |
| `settings` | `Settings` |
| `memory_manager` | `MemoryManager | None` |

`__post_init__` creates a `MemoryManager(memory_service)` when none is supplied.
Tests provide fakes implementing the same methods.

## 4. Helper functions

### `parse_budget(text: str) -> float | None`
Regex-based extraction of the first rupee amount in contexts such as
`₹3,000`, `under 3000`, `budget around 5k`, `less than ₹2,299`. Commas are removed
and `k`/`thousand` multiplies by 1000. Returns `None` when no amount is present.

### `extract_features(message: str) -> list[str]`
Maps keyword mentions to catalogue feature names: lightweight, long battery, extra
bass, foldable, multipoint, hi-res, detachable cable, sweat resistant.

### `match_brand(message: str, known_brands: list[str]) -> str | None`
Case-insensitive whole-word match against catalogue brands. Returns the first
brand mentioned, or `None`. Catalogue brands are fetched from the product API
(cached), so brand knowledge is not duplicated in this module.

### `parse_memory_preferences(memories, known_brands) -> dict`
Derives hints from retrieved memory sentences:
- brand with the highest whole-word mention count, only if a unique winner;
- noise cancellation / ANC → `True`;
- wireless/Bluetooth → `True`, wired → `False`;
- budget via `parse_budget`;
- category from earbud/earphone/TWS vs headphone words.

Ambiguous memories (e.g. equal mentions of two brands) are skipped rather than
guessed. These hints never override explicit values.

### `heuristic_requirements(message, known_brands) -> RequirementExtraction`
Fallback used when Ollama fails or output fails validation. Extracts category,
brand, budget, wireless, ANC, excluded ANC and simple features deterministically.
Sets `refines_previous_request` when the message is short and contains a
constraint but no category.

### `ground_requirements(extraction, message, known_brands) -> RequirementExtraction`
The key reliability step. Every structured field must be supported by the current
message:
- category only when a category word appears;
- brand only when a catalogue brand appears;
- budget only when the message contains a digit;
- wireless only when wireless/Bluetooth/wired appears;
- ANC only when ANC/noise cancellation appears;
- features only from keyword mapping;
- excluded features only for "without/no noise cancellation".

Unsupported values become `None`, so they cannot leak from the previous turn or
from memory into the "current" extraction.

### `resolve_requirements(current, previous, memory_preferences, default_category="headphones")`
Applies precedence:
1. current explicit value;
2. previous value when `refines_previous_request` and the category did not change;
3. memory hint;
4. default `headphones`.

It also resolves `required_features`, `excluded_features`, and initialises
`excluded_brands`/`excluded_product_ids` to empty lists. If the message switches
category, previous values are not reused.

### `describe_requirements(requirements) -> str`
Human-readable summary used by deterministic fallback replies, e.g.
`"JBL headphones, under ₹3,000, wireless"`.

## 5. Node functions

All nodes take `(state: AgentState, deps: WorkflowDeps)` and return a partial dict.

### `prepare_turn`
- Appends `{"role": "user", "content": user_message}` to history.
- Keeps the last `MAX_HISTORY_MESSAGES` (12).
- Resets per-turn fields: memories, statuses, requirements, products,
  recommendations, response, errors, candidates, writes, relaxed/memory-applied.
- Does **not** reset `resolved_requirements`, which `understand_request` reads as
  previous context.

### `retrieve_memory`
- Calls `deps.memory_service.search_memories(customer_id, message, limit=memory_top_k)`.
- Success with records → `memory_status="success"`.
- Success with none → `"empty"`.
- Failure containing "not configured"/`MEM0_API_KEY` → `"not_configured"`,
  otherwise `"error"`.
- Adds a diagnostic to `errors`; never invents memory text.

### `understand_request`
1. Loads catalogue brands (`deps.product_service.available_brands()`), using an
   empty list if the API is down.
2. Calls `llm_client.chat_json` with the requirement prompts and context.
3. Validates with `RequirementExtraction`, then grounds against the message.
4. On `OllamaError`, `ValidationError` or `ValueError`: sets
   `llm_status="degraded"`, uses `heuristic_requirements`, and records the error.
5. Parses memory hints and resolves precedence.
6. Computes `memory_applied` (fields whose resolved value came only from memory).

Returns `requirements`, `previous_requirements`, `resolved_requirements`,
`memory_applied`, `llm_status`, `llm_error`, `errors`.

### `search_products`
Calls `deps.product_service.fetch_products(category=..., max_budget=...)`.
On `ProductAPIError`: `products=[]`, `product_status="error"`, diagnostic.
Otherwise `success`/`empty`.

### `recommend_products`
Calls `recommend(products, resolved_requirements, limit=max_recommendations)`.
If empty and `memory_applied` contains `noise_cancellation`/`wireless`, retries
without those memory filters. Records `relaxed_preferences` and keeps the relaxed
requirements only when products were found. Explicit constraints are never
relaxed.

### `generate_response`
- Product API error → fixed honest message, no LLM call.
- No recommendations → deterministic explanation of the requirements.
- Otherwise calls Ollama with `RESPONSE_SYSTEM_PROMPT` and only selected products.
- Ollama failure → message stating products were found but the explanation is
  unavailable, plus the error; sets `llm_status="error"`.

### `update_memory`
- Appends the assistant reply to history (capped).
- Skips writing when memory retrieval was not `success`/`empty` (can't dedupe).
- Calls Ollama preference extraction; validates the list and builds
  `PreferenceCandidate` objects (max 5 considered, at most 3 typically returned).
- Calls `MemoryManager.remember` with existing memory texts for dedupe.
- Returns `memory_candidates`, a real `memory_writes` report and diagnostics.
- Extraction failure → `memory_writes.status="error"`, nothing claimed as saved.

## 6. Execution workflow

```
prepare_turn
 -> retrieve_memory
 -> understand_request  (LLM -> validate -> ground -> memory hints -> resolve)
 -> search_products      (FastAPI)
 -> recommend_products   (filter/rank, maybe relax memory filters)
 -> generate_response    (LLM explains selected products)
 -> update_memory        (LLM candidates -> rules -> Mem0 MCP)
```

## 7. Input and output

Example extraction:
```python
{"category": "headphones", "brand": "JBL", "max_budget": 3000}
```
after grounding:
```python
RequirementExtraction(category="headphones", brand="JBL", max_budget=3000.0,
                      wireless=None, noise_cancellation=None, ...)
```
after resolution with memory (Sony/ANC):
```python
{"category": "headphones", "brand": "JBL", "max_budget": 3000.0,
 "wireless": True, "noise_cancellation": True, ...}
```
with `memory_applied={"noise_cancellation": True, "wireless": True}`.

## 8. Interaction with other modules

Called by `agent/graph.py`; calls memory, product, LLM, prompts and recommendation
modules. It imports no Streamlit and no MCP client directly.

## 9. Important implementation decisions

- **Grounding** makes LLM output safe for precedence: a field that the message
  does not mention is `None`, even if the model guessed it.
- **Fallback extraction** keeps the assistant useful when Ollama is down, while the
  response explicitly reports the LLM failure.
- **Soft memory** prevents a remembered feature from making an explicit request
  impossible; relaxation is visible in the UI/prompt.
- **No write when retrieval failed** avoids duplicate preferences.
- Budget is treated as temporary at the memory layer regardless of the extraction
  label if it contains temporary wording; persistent "usual budget" wording can be
  saved.

## 10. Example execution

```python
deps = WorkflowDeps(memory_service=ms, product_service=ps,
                    llm_client=OllamaClient(), settings=get_settings())
state = {"customer_id": "customer_001", "session_id": "s1",
         "user_message": "Show me wireless Sony headphones under ₹5,000"}
state.update(prepare_turn(state, deps))
state.update(retrieve_memory(state, deps))
state.update(understand_request(state, deps))
```
