# `app/agent/state.py`

## 1. Purpose

Declares `AgentState`, the `TypedDict` checkpointed by LangGraph per
`thread_id` (session). It carries request identity, conversation context, the
routing plan, memory/product results and diagnostics.

## 2. Imports

`typing.Any`, `typing.TypedDict` only.

## 3. `ChatMessage`

```python
class ChatMessage(TypedDict):
    role: str      # "user" | "assistant"
    content: str
```

## 4. `AgentState` fields

### Identity

| Field | Meaning |
|---|---|
| `customer_id` | Mem0 `user_id` scope |
| `session_id` | LangGraph thread id |
| `user_message` | Current message |

### Conversation

| Field | Meaning |
|---|---|
| `conversation_history` | Last 12 messages, checkpointed |

### Routing

| Field | Written by | Meaning |
|---|---|---|
| `route` | `route_query` | Serialised `RouteDecision` |
| `pending_steps` | `route_query`, every node | Remaining ordered plan |
| `visited_steps` | every node | Audit trail / `execution_path` |
| `router_usage` | `route_query` | source, model, input tokens, latency, confident |
| `route_guards` | `route_query` | Deterministic guards applied |

### Memory retrieval

| Field | Meaning |
|---|---|
| `customer_memories` | Retrieved memory texts |
| `memory_status` | `success`, `empty`, `error`, `not_configured`, `unknown` |
| `memory_error` | Retrieval error message |

### Understanding

| Field | Meaning |
|---|---|
| `requirements` | Grounded extraction for this message |
| `previous_requirements` | Last turn's resolved requirements |
| `resolved_requirements` | Final requirements after precedence |
| `memory_applied` | Fields filled only from memory |
| `relaxed_preferences` | Memory filters dropped to avoid an empty result |
| `llm_status` | `success`, `degraded`, `error`, `unknown` |
| `llm_error` | Last Ollama error |

### Products

| Field | Meaning |
|---|---|
| `products` | Catalogue candidates for this turn |
| `previous_recommendations` | Last turn's shortlist (follow-ups) |
| `product_status` | `success`, `empty`, `error`, `unknown` |
| `recommendations` | Final deterministic selection |

### Response

| Field | Meaning |
|---|---|
| `response` | Customer-facing text |
| `response_kind` | `conversational`, `product`, `comparison`, `explanation`, `memory`, `confirmation`, `error` |

### Memory management

| Field | Meaning |
|---|---|
| `memory_candidates` | Retrieved records considered for correction |
| `memory_actions` | Verified add/update/delete outcomes |
| `memory_events` | Event ids and final statuses |
| `memory_writes` | Aggregate report (`status`, `saved`, `pending`, `skipped`, `failures`, `attempted`, `actions`) |

### Diagnostics

`errors: list[str]` — accumulated, user-visible diagnostics.

## 5. Lifecycle

- `prepare_turn` resets every per-turn field and preserves
  `conversation_history`, `resolved_requirements` and moves
  `recommendations` into `previous_recommendations`.
- Each node consumes one `pending_steps` entry and appends to `visited_steps`.
- `finalize_turn` empties the queue and appends the assistant message.

`customer_memories` holds text only; ids live in `memory_candidates` and
`memory_actions`, so the UI cannot accidentally treat an id as a preference.
