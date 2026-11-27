# `app/routing/classifier.py`

## 1. Purpose

Orchestrates the routing layers and converts a classification into an ordered,
allowlisted LangGraph step plan. Jev never supplies an executable node name.

## 2. Imports

| Import | Why |
|---|---|
| `Settings`, `get_settings` | Router flags, threshold, timeout, history turns |
| `JevClient`, `JevError`, `JevResponse` | Classification |
| `INTENTS`, `RouteDecision` | Contract and validation |
| `NOUL_IDS`, `build_questions` | Required answer ids and question map |
| `apply_guards`, `explicit_memory_operation`, `heuristic_route`, `is_greeting_only` | Rules layer |
| `ValidationError` | Pydantic failures become fallback |
| `Any` | History/state typing |

## 3. Constants

### `PLANNED_NODES`

`manage_memory`, `retrieve_memory`, `understand_request`, `search_products`,
`recommend_products`, `generate_response`, `finalize_turn`.

`prepare_turn` and `route_query` are fixed entry nodes and are never planned.

### `_SINGLETON_STEPS`

`generate_response`, `finalize_turn` — at most once per turn.

### `_MAX_STATE_CHARS = 4000`

Bounds the text sent to Jev.

## 4. Functions

### `_jev_state(message, history, turns) -> str`

Builds:

```
Recent conversation:
user: ...
assistant: ...
Current customer message:
...
```

The current message is excluded from "recent conversation", at most
`turns * 2` prior messages are included, and no memory text or customer id is
ever added.

### `_decision_from_jev(response, settings) -> RouteDecision`

1. Requires every id in `NOUL_IDS`; missing/invalid → `JevError`.
2. `intent` must be one of `INTENTS`; otherwise `JevError`.
3. Applies `router_confidence_threshold` to convert probabilities into flags.
4. `confident` is true only when every Noul is outside the 0.4–0.6 band **and**
   the intent confidence is at least the threshold.
5. Copies model, token usage and latency for diagnostics/cost.

### `_valid_followup(value) -> str`

Returns the label when supported, otherwise `none`.

### `_reconcile(decision, previous_recommendations)`

Keeps flags, intent and follow-up action consistent:

- `changes_memory` → intent `memory_correction`;
- `compare`/`explain` follow-up → intent `product_followup`;
- `wants_products` → `product_search` or `product_followup`;
- intent claims products but the flag is false → `general_question`;
- compare/explain with no previous shortlist → `wants_products=True` and
  `followup_action="none"` (a fresh search is the only useful answer);
- other follow-ups with no previous shortlist → `refers_to_previous=False`.

### `build_step_plan(decision, previous_recommendations=None) -> list[str]`

```python
if decision.changes_memory or decision.states_preference:
    steps.append("manage_memory")          # corrections before retrieval
if decision.needs_personalisation or decision.intent == "memory_question":
    steps.append("retrieve_memory")
answer_from_previous = (
    decision.refers_to_previous
    and decision.followup_action in {"compare", "explain"}
    and bool(previous)
)
if decision.wants_products and not answer_from_previous:
    steps += ["understand_request", "search_products", "recommend_products"]
steps += ["generate_response", "finalize_turn"]
```

Then the list is filtered to the allowlist and de-duplicated (singletons once).
Properties guaranteed by the tests:

- ends with `finalize_turn`;
- exactly one `generate_response`;
- no duplicates;
- every entry is in `PLANNED_NODES`;
- `manage_memory`, when present, is first.

### `QueryRouter`

`__init__(settings=None, *, jev_client=None)` — creates a `JevClient` when none
is injected.

`route(message, history=None, previous_recommendations=None) -> (RouteDecision, list[str])`

1. `has_context = bool(previous_recommendations)`.
2. Greeting-only or explicit memory instruction → `heuristic_route`, source
   `rules` (Jev is not called; corrections are irreversible).
3. Else if `router_use_jev` and `jev_configured` → Jev, validate, apply guards.
4. Else → `heuristic_route`, source `rules`, guard `jev_not_configured`.
5. Any `JevError`/`ValidationError`/`ValueError` → `heuristic_route`, source
   `fallback`, `confident=False`, guard `jev_unavailable: <type>`.
6. `_reconcile`, then `build_step_plan`.

## 5. Worked examples

| Message | Context | Decision | Plan |
|---|---|---|---|
| `Hi` | none | greeting | generate, finalize |
| `What is noise cancellation?` | none | general_question | generate, finalize |
| `What do you remember about me?` | none | memory_question, perso | retrieve, generate, finalize |
| `I usually prefer Sony headphones` | none | preference_statement | manage, generate, finalize |
| `Forget that I prefer Sony` | none | memory_correction | manage, generate, finalize |
| `Show me earbuds under ₹5,000` | none | product_search | understand, search, recommend, generate, finalize |
| `Recommend headphones based on my preferences` | none | product_search, perso | retrieve, understand, search, recommend, generate, finalize |
| `Which is better?` | shortlist | product_followup, compare | generate, finalize |
| `Show me cheaper ones` | shortlist | product_followup, refine | understand, search, recommend, generate, finalize |
| `I prefer JBL, not Sony. Recommend ...` | none | memory_correction + product | manage, retrieve, understand, search, recommend, generate, finalize |

## 6. Error handling

The router is total for routing failures: it never raises to the graph. The only
exceptions that can escape are programming errors (for example a broken settings
object), which `nodes.route_query` catches defensively and turns into a safe
conversational plan.
