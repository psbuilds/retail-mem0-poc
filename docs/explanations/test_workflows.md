# `tests/test_workflows.py`

## 1. Purpose

Runs the compiled LangGraph graph for every required scenario using fakes for
Ollama, Mem0 and the catalogue, and asserts both behaviour and the actual
execution path.

## 2. Helpers

| Helper | Purpose |
|---|---|
| `build(...)` | Assemble deps + graph with optional seeded memories/LLM/product fake |
| `run(graph, message, customer, session)` | Call the public `process_message` |
| `SONY_MEMORIES` | Two seeded preferences for personalisation tests |
| `PREVIOUS_PATH_PRODUCT` | Expected product-path node order |

## 3. Coverage

### Normal conversation
Greetings and definitions:
- `response_kind="conversational"`;
- path is `prepare_turn → route_query → generate_response → finalize_turn`;
- no product fetches, no memory searches, no writes.

### Memory questions
- retrieved records appear in the answer;
- retrieval failure is reported, not replaced by invented memories;
- another customer sees no memories.

### Preference storage
- durable statement → `manage_memory → generate_response → finalize_turn`;
- confirmed write is reported and no product API call happens;
- pending event → `memory_pending`, response says it cannot confirm;
- failed write → explicit failure and `memory_writes.status="error"`;
- temporary purchase request writes nothing.

### Corrections
- forget deletes only matching records;
- "no longer prefer" deletes;
- no match → `not_found`, nothing deleted;
- negated brand is excluded from the next search;
- "I prefer JBL, not Sony" updates the record, retrieves the updated memory and
  recommends JBL; path order proves manage → retrieve → search.

### Product search
- earbuds/budget filtering with no memory calls;
- personalised recommendation retrieves memory and applies brand/ANC;
- combined preference + search stores the preference and appends the real
  outcome to the single product response;
- "cheaper ones" recomputes a budget below the previous minimum;
- compare/explain reuse the shortlist without a new fetch.

### Invariants
Every turn:
- visits exactly one `generate_response`;
- ends at `finalize_turn`;
- has a non-empty response and `response_kind`;
- appends exactly one assistant message per user message.
