# `app/agent/nodes.py`

## 1. Purpose

Implements all nine LangGraph nodes plus the deterministic helpers they share:

- Pydantic validation and grounding of requirement extraction;
- brand polarity (negated brands are exclusions);
- precedence resolution and follow-up refinement;
- step-queue consumption and dispatch;
- unified memory management (add, correct, update, delete) with truthful
  reporting;
- mode-aware response generation;
- turn finalisation.

## 2. Imports

| Import | Why |
|---|---|
| `re`, `dataclass`, `Any` | Helpers and dependency bundle |
| `pydantic.*` | Validate model JSON |
| `app.agent.prompts.*` | Requirements, product, conversation and comparison prompts |
| `app.agent.state.AgentState` | State type |
| `app.config.settings.Settings` | Configuration |
| `OllamaClient`, `OllamaError` | Local LLM |
| `MemoryAction`, `MemoryManager`, `MemoryWriteReport` | Memory policy and reporting |
| `MemoryService` | Memory operations |
| `ProductAPIError`, `ProductService` | Catalogue |
| `recommend` | Deterministic ranking |
| `QueryRouter` | Routing |
| `brand_polarity` | Positive/excluded brand detection |

## 3. Constants

| Constant | Value | Purpose |
|---|---|---|
| `MAX_HISTORY_MESSAGES` | 12 | Bound checkpointed history |
| `MAX_VISITED_STEPS` | 12 | Hard bound against plan loops |
| `PLANNED_NODES` | 7 names | Steps a router plan may contain |

## 4. Classes

### `RequirementExtraction(BaseModel)`

Fields: `is_shopping_request`, `refines_previous_request`, `category`, `brand`,
`excluded_brands`, `max_budget`, `wireless`, `noise_cancellation`,
`required_features`, `excluded_features`.

Validators normalise categories, strip brands, cap `excluded_brands` at 5,
drop non-positive budgets and cap feature lists at 6.

### `WorkflowDeps`

| Attribute | Type |
|---|---|
| `memory_service` | `MemoryService` |
| `product_service` | `ProductService` |
| `llm_client` | `OllamaClient` |
| `settings` | `Settings` |
| `memory_manager` | `MemoryManager \| None` |
| `router` | `QueryRouter \| None` |

`__post_init__` creates a `MemoryManager` and a `QueryRouter` when absent.

## 5. Helpers

### `parse_budget(text) -> float | None`
First rupee amount (`₹3,000`, `under 3000`, `5k`), `k`/`thousand` multiplies by
1000, non-positive → `None`.

### `extract_features(message) -> list[str]`
Keyword → catalogue feature name (lightweight, long battery, extra bass,
foldable, multipoint, hi-res, detachable cable, sweat resistant).

### `match_brand(message, known_brands) -> str | None`
Returns the positively mentioned brand via `brand_polarity`, so a negated brand
is never returned.

### `parse_memory_preferences(memories, known_brands) -> dict`
Hints only: unique top brand, ANC, wireless, budget, category. Ambiguous
memories are skipped.

### `heuristic_requirements(message, known_brands) -> RequirementExtraction`
Deterministic fallback when Ollama fails. Sets category, positive/excluded
brands, budget, wireless, ANC, features and `refines_previous_request` for short
constraint messages.

### `ground_requirements(extraction, message, known_brands) -> RequirementExtraction`
Every field must be supported by the literal message. Brand polarity is resolved
deterministically: `"I don't like JBL anymore"` → `brand=None`,
`excluded_brands=["JBL"]`.

### `resolve_requirements(current, previous, memory_preferences, default_category="headphones") -> dict`
Precedence: current > previous (only when refining and the category did not
change) > memory hint > default. `excluded_brands` merges current exclusions with
the previous exclusions when refining. `excluded_product_ids` starts empty.

### `describe_requirements(requirements) -> str`
Human-readable summary used by deterministic fallback replies.

### `_consume(state, name) -> dict`
Pops this node's step, appends the node to `visited_steps`, and records an error
when the plan order is wrong or the node was not scheduled. Truncates the queue
to `["finalize_turn"]` once `MAX_VISITED_STEPS` is reached.

### `next_step(state) -> str`
Returns `END` when the queue is empty, `finalize_turn` for unknown steps, and
`finalize_turn`/`END` correctly if finalisation already ran.

### `_with_errors(updates, extra) -> dict`
Appends diagnostics while preserving the step-queue updates.

### `_apply_followup(extraction, message, route, previous_recommendations) -> RequirementExtraction`
For `followup_action == "refine_search"` it sets `refines_previous_request` and,
for "cheaper/cheapest", sets `max_budget` just below the cheapest previously
recommended product.

### `_response_kind(state) -> str`
Priority:

1. `error` when the catalogue failed;
2. `comparison` / `explanation` when a compare/explain follow-up has a previous
   shortlist (even though `wants_products` is false);
3. `product` when `wants_products`;
4. `memory` for memory questions/personalisation without memory actions;
5. `confirmation` when memory actions ran;
6. `conversational` otherwise.

### `_memory_answer(state) -> str`
Grounded only in `customer_memories`. Reports a retrieval failure explicitly;
lists actual records otherwise; never invents a preference.

### `_confirmation_answer(state) -> str`
Builds the reply from `memory_actions` statuses:

| Status | Wording |
|---|---|
| `saved` + add | "Saved to your long-term preferences: ..." |
| `saved` + update | "Updated your saved preference to: ..." |
| `deleted` | "Removed the saved preference: ..." |
| `pending` | "Mem0 is still processing ... I can't confirm it is saved yet" |
| `not_found` | the manager's "no saved memory matching ... nothing was changed" |
| `failed` | "I couldn't complete that memory change: ..." |

No LLM call, so a failed or pending write can never be presented as saved.

### `_comparison_answer(state, kind, deps) -> str`
Uses `previous_recommendations` only. For "explain", an ordinal ("the second
one") narrows the list to that product. Falls back to a deterministic product
summary when Ollama is down.

## 6. Nodes

### `prepare_turn`
Appends the user message, saves the previous turn's `recommendations` into
`previous_recommendations`, resets every per-turn field (including route, plan,
memory actions/events and response kind), and seeds `visited_steps` with
`["prepare_turn"]`. `conversation_history` and `resolved_requirements` persist.

### `route_query`
Calls `QueryRouter.route` with the message, history and previous shortlist.
Stores `route`, `pending_steps`, `router_usage` and `route_guards`. A defensive
`except` produces a conversational plan rather than failing the turn.

### `retrieve_memory`
Scoped Mem0 search. Success with records → `success`; none → `empty`; failure →
`error` (or `not_configured` when the API key is missing). Adds a diagnostic and
never invents memory text.

### `understand_request`
Ollama extraction → Pydantic validation → grounding → follow-up refinement →
memory hints → precedence. On `OllamaError`/`ValidationError`/`ValueError` it
sets `llm_status="degraded"`, uses `heuristic_requirements` and records why.

### `search_products`
Calls `ProductService.fetch_products(category, max_budget)`. `ProductAPIError`
sets `product_status="error"` with an empty list.

### `recommend_products`
Deterministic filtering/ranking. If nothing matches and memory supplied
wireless/ANC constraints, retries without those and reports
`relaxed_preferences`. Explicit current constraints are never relaxed.

### `manage_memory`
One node for every memory operation.

1. If `route.changes_memory`: scoped `search_memories` → `correction_plan`
   (validated candidate ids only) → `apply_correction` (delete or update).
2. If `route.states_preference` and no correction already stored the new
   preference: `should_store` policy → `store` → poll the Mem0 event.
3. Returns `memory_candidates`, `memory_actions`, `memory_events` and the
   aggregate `memory_writes` report.

A correction with no matching record produces `not_found` (counted as a failure,
never as success). A failed correction search is reported as a failed action.
Retrieval failure does not block a write.

### `generate_response`
Chooses one mode via `_response_kind` and produces exactly one response:

- **error** — catalogue unreachable;
- **memory** — grounded memory answer;
- **confirmation** — truthful memory outcome;
- **comparison/explanation** — previous products only;
- **conversational** — Ollama, with an honest fallback if Ollama is down;
- **product** — deterministic results explained by Ollama; empty results get a
  deterministic no-match message. When memory actions also ran, the real memory
  outcome is appended so a combined turn still reports it.

### `finalize_turn`
Appends the assistant reply (capped at 12 messages) and pops the final queue
entry, leaving `pending_steps` empty so `next_step` returns `END`.

## 7. Execution order

The router decides. Typical orders:

```
greeting:            generate_response → finalize_turn
preference:          manage_memory → generate_response → finalize_turn
memory question:     retrieve_memory → generate_response → finalize_turn
product:             understand_request → search_products → recommend_products
                     → generate_response → finalize_turn
correction + shop:   manage_memory → (retrieve_memory) → understand_request
                     → search_products → recommend_products → generate_response
                     → finalize_turn
```

## 8. Error handling summary

| Failure | Reported as |
|---|---|
| Ollama extraction down | `llm_status="degraded"` + deterministic extraction |
| Product API down | `product_status="error"` + explicit message |
| Mem0 search down | `memory_status="error"` + diagnostic; turn continues |
| Mem0 write rejected | action `failed` + diagnostic |
| Mem0 event pending | action `pending`; never "saved" |
| Mem0 event failed | action `failed` with the event error |
| Correction target missing | action `not_found`; nothing deleted |
| Ollama chat down | honest response naming the failure |
