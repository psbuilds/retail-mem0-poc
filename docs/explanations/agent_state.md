# `app/agent/state.py`

## 1. File purpose

Defines the shared LangGraph state for one shopping turn and the type of a stored
chat message. It is the contract between nodes and between the graph and the
service layer.

## 2. Imports and dependencies

| Import | Why |
|---|---|
| `typing.Any` | Requirement values can be strings, numbers, booleans or lists |
| `typing.TypedDict` | LangGraph expects a mapping-like state schema |

No other project modules are imported, avoiding cycles.

## 3. Classes

### `ChatMessage(TypedDict)`

| Key | Type | Meaning |
|---|---|---|
| `role` | `str` | `"user"` or `"assistant"` |
| `content` | `str` | Message text |

Used in `conversation_history` and sent to Ollama as context.

### `AgentState(TypedDict, total=False)`

All keys are optional so nodes can return partial updates. Groups:

**Request identity**
- `customer_id: str` — Mem0 `user_id` scope.
- `session_id: str` — conversation/thread id.
- `user_message: str` — current message.

**Conversation**
- `conversation_history: list[ChatMessage]` — last 12 messages from the
  checkpointer.

**Memory node**
- `customer_memories: list[str]` — real memory texts.
- `memory_status: str` — `success`, `empty`, `error`, `not_configured`.
- `memory_error: str | None` — actual failure message.

**Understanding**
- `requirements: dict[str, Any]` — grounded extraction for this message.
- `previous_requirements: dict[str, Any]` — resolution from the prior turn.
- `resolved_requirements: dict[str, Any]` — final requirements after precedence
  and any relaxation.
- `memory_applied: dict[str, Any]` — fields filled only from memory.
- `relaxed_preferences: list[str]` — memory filters relaxed due to no matches.
- `llm_status: str` — `success`, `degraded`, `error`.
- `llm_error: str | None`.

**Products and recommendations**
- `products: list[dict[str, Any]]` — candidates from the product API.
- `product_status: str` — `success`, `empty`, `error`.
- `recommendations: list[dict[str, Any]]` — deterministic final selection.

**Response and memory writes**
- `response: str` — customer-facing text.
- `memory_candidates: list[dict[str, Any]]` — raw extraction candidates.
- `memory_writes: dict[str, Any]` — `status`, `saved`, `skipped`, `failures`,
  `attempted`, optional `reason`.

**Diagnostics**
- `errors: list[str]` — accumulated per-turn messages for the UI.

## 4. Functions and methods

None. The file contains type declarations only.

## 5. Execution workflow

State lifecycle:
1. `process_message` passes the three identity fields.
2. LangGraph merges them with any checkpointed state for the thread.
3. `prepare_turn` appends the user message and resets per-turn fields.
4. Each node returns a subset of fields; LangGraph updates the state.
5. `update_memory` is last and appends the assistant reply.
6. The final state is checkpointed and returned to `process_message`.

## 6. Input and output

Input (first turn):
```python
{"customer_id": "customer_001", "session_id": "s1", "user_message": "Suggest headphones"}
```

Final state (abridged):
```python
{
  "customer_memories": ["Usually prefers Sony headphones."],
  "memory_status": "success",
  "requirements": {"category": "headphones", "brand": None, ...},
  "resolved_requirements": {"category": "headphones", "brand": "Sony", ...},
  "products": [...],
  "recommendations": [...],
  "response": "...",
  "memory_writes": {"status": "no_new_memories", ...},
  "errors": [],
}
```

## 7. Interaction with other modules

Used by `nodes.py` (read/write), `graph.py` (state schema), and indirectly by
`chatbot_service.py` (response mapping). `state.py` depends on nothing.

## 8. Important implementation decisions

- `total=False` allows partial node updates, which is how LangGraph nodes work.
- Two requirement fields exist: `requirements` (current message only) and
  `resolved_requirements` (after precedence). Keeping them separate makes override
  tests and debugging clear.
- `memory_applied` exists specifically for the soft-memory relaxation rule; it
  records which fields were personalisation rather than explicit instructions.
- No raw MCP payloads or API keys are stored in state.
- Fields are limited to what the workflow actually uses; there are no speculative
  keys.

## 9. Example execution

```python
state: AgentState = {
    "customer_id": "customer_001",
    "session_id": "customer_001-a1b2c3d4",
    "user_message": "Suggest headphones for me.",
}
```
