# LangGraph workflow

## Why LangGraph

The turn has a fixed, auditable order: retrieve memory, understand, fetch, rank,
explain, update memory. LangGraph makes that order explicit and checkpoints state
per conversation, which is how "Only Sony" and "Under ₹5,000" refine an earlier
request without the UI resending history.

## Graph

```mermaid
flowchart LR
    A[prepare_turn] --> B[retrieve_memory]
    B --> C[understand_request]
    C --> D[search_products]
    D --> E[recommend_products]
    E --> F[generate_response]
    F --> G[update_memory]
    G --> H([END])
    S([START]) --> A
```

There are no conditional edges: every node runs every turn, and each node reports
its own status. This POC favours a readable graph over branching.

## State (`AgentState`)

```python
class AgentState(TypedDict, total=False):
    customer_id: str
    session_id: str
    user_message: str
    conversation_history: list[ChatMessage]
    customer_memories: list[str]
    memory_status: str
    memory_error: str | None
    requirements: dict
    previous_requirements: dict
    resolved_requirements: dict
    memory_applied: dict
    relaxed_preferences: list[str]
    products: list[dict]
    product_status: str
    recommendations: list[dict]
    response: str
    memory_candidates: list[dict]
    memory_writes: dict
    llm_status: str
    llm_error: str | None
    errors: list[str]
```

| Field | Written by | Meaning |
|---|---|---|
| `customer_id`, `session_id`, `user_message` | service input | Identity of the turn |
| `conversation_history` | `prepare_turn`, `update_memory` | Last 12 chat messages for context |
| `customer_memories` | `retrieve_memory` | Real Mem0 results (texts only) |
| `memory_status` | `retrieve_memory` | `success`, `empty`, `error`, `not_configured` |
| `requirements` | `understand_request` | Grounded extraction for this message |
| `previous_requirements` | `understand_request` | Resolution carried from the prior turn |
| `resolved_requirements` | `understand_request`, `recommend_products` | Final requirements after precedence/relaxation |
| `memory_applied` | `understand_request` | Fields filled only from memory |
| `relaxed_preferences` | `recommend_products` | Memory filters that had to be dropped |
| `products` | `search_products` | Candidate catalogue entries |
| `product_status` | `search_products` | `success`, `empty`, `error` |
| `recommendations` | `recommend_products` | Final deterministic selection |
| `response` | `generate_response` | Customer-facing text |
| `llm_status` | `understand_request`, `generate_response` | `success`, `degraded`, `error` |
| `memory_candidates` | `update_memory` | Raw preference candidates |
| `memory_writes` | `update_memory` | Real Mem0 write report |
| `errors` | multiple nodes | Diagnostics shown in the UI |

Per-turn fields (`products`, `recommendations`, `response`, `errors`,
`memory_writes`, `customer_memories`, statuses, `memory_applied`,
`relaxed_preferences`) are reset by `prepare_turn`. Persistent fields
(`conversation_history`, `resolved_requirements`) are kept for follow-ups.

## Checkpointing and sessions

```python
builder.compile(checkpointer=InMemorySaver())
graph.invoke(state, config={"configurable": {"thread_id": session_id}})
```

- `thread_id` is the session id, so each chat has its own state.
- `InMemorySaver` lives in the Streamlit server process. Restarting the app clears
  conversation context; Mem0 preferences remain.
- The UI's "New conversation" button assigns a new session id, so the old thread is
  never read again. It does not touch Mem0.
- Histories are capped at 12 messages to bound state size.

## Nodes in detail

### `prepare_turn`
Appends the user message and resets per-turn fields. It deliberately leaves
`resolved_requirements` intact for the understanding node to read as the previous
turn's context.

### `retrieve_memory`
```python
result = memory_service.search_memories(customer_id, user_message, limit=memory_top_k)
```
Returns memory texts, or `memory_status="error"`/`"not_configured"` with an error.
No placeholder memories are ever created.

### `understand_request`
1. Gets catalogue brand names (cached by `ProductService`; empty if the API is
   down).
2. Calls `OllamaClient.chat_json` with `REQUIREMENTS_SYSTEM_PROMPT`.
3. Validates with `RequirementExtraction`.
4. Grounds fields against the message (`ground_requirements`).
5. Parses memory hints (`parse_memory_preferences`).
6. Resolves precedence (`resolve_requirements`).

If Ollama or validation fails: `llm_status="degraded"`, a deterministic
`heuristic_requirements` extraction is used, and an error is recorded.

### `search_products`
Fetches category + budget candidates from the product API. Failure sets
`product_status="error"` and an empty list.

### `recommend_products`
Calls `recommendation_service.recommend`. On an empty result with memory-only
wireless/ANC constraints, it retries with those set to `None`, records
`relaxed_preferences`, and keeps the relaxed requirements only if products were
found.

### `generate_response`
Branches on status:
- catalogue error → honest no-catalogue message;
- empty results → deterministic no-match explanation;
- otherwise Ollama explanation;
- Ollama error → honest partial message listing count + error.

### `update_memory`
Appends the assistant message, extracts preference candidates with Ollama, applies
`MemoryManager` rules, and writes via Mem0. If retrieval was unavailable, it skips
writing with an explicit reason.

## Follow-up example

Session `customer_002-x`:

| Message | Grounded extraction | Resolved requirements |
|---|---|---|
| "Show me wireless headphones." | category=headphones, wireless=true | wireless=true, category=headphones |
| "Only Sony." | brand=Sony, refines=true | + brand=Sony, keeps wireless |
| "Under ₹5,000." | budget=5000, refines=true | + budget=5000, keeps brand/wireless |

Products: only Sony wireless models ≤ ₹5,000, ranked by rating then price.

## Testing

`tests/test_agent.py` builds the graph with fake memory/product/LLM services and
covers new customers, existing preferences, overrides, temporary budgets,
follow-ups, and every failure mode. `tests/test_end_to_end.py` uses the real
catalogue through FastAPI TestClient while faking Mem0 and Ollama.
