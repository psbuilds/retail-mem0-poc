# `app/agent/graph.py`

## 1. Purpose

Compiles the routed LangGraph workflow. `route_query` plans the turn; shared
conditional edges execute only the planned nodes.

## 2. Imports

| Import | Why |
|---|---|
| `functools.lru_cache`, `partial` | One compiled graph per process; inject deps |
| `langgraph.checkpoint.memory.InMemorySaver` | Per-session conversation checkpoints |
| `langgraph.graph.END`, `START`, `StateGraph` | Graph construction |
| `app.agent.nodes`, `WorkflowDeps`, `AgentState` | Nodes, dependencies, state |
| `app.config.settings.get_settings` | Configuration |
| `OllamaClient`, `MemoryManager`, `get_memory_service`, `ProductService` | Production services |
| `QueryRouter` | Jev/rules classifier |

## 3. Functions

### `build_default_deps() -> WorkflowDeps`

Creates the production bundle: the process-wide `MemoryService` (one MCP
session), a `ProductService`, an `OllamaClient`, settings, a `MemoryManager` and
a `QueryRouter`.

### `dispatch_map() -> dict[str, str]`

Maps every allowlisted node name to itself and `END` to `END`. Every destination
`nodes.next_step` can return is registered here.

### `build_graph(deps=None, checkpointer=None) -> CompiledGraph`

Registers nine nodes:

`prepare_turn`, `route_query`, `manage_memory`, `retrieve_memory`,
`understand_request`, `search_products`, `recommend_products`,
`generate_response`, `finalize_turn`.

Edges:

```
START → prepare_turn → route_query
route_query        --conditional(next_step)--> any planned node
manage_memory      --conditional(next_step)--> ...
retrieve_memory    --conditional(next_step)--> ...
understand_request --conditional(next_step)--> ...
search_products    --conditional(next_step)--> ...
recommend_products --conditional(next_step)--> ...
generate_response  --conditional(next_step)--> ...
finalize_turn → END
```

The conditional edges all use the same function, `nodes.next_step`, and the same
destination map. LangGraph requires a conditional-edge declaration per source
node; one shared dispatcher removes the combinatorial edge matrix, not the edge
registration itself.

### `get_graph() -> CompiledGraph`

`lru_cache(maxsize=1)`; used by `chatbot_service`. Tests build their own graph
with fakes.

## 4. Checkpointing

`graph.invoke(..., config={"configurable": {"thread_id": session_id}})`

The in-memory saver keeps conversation history, `resolved_requirements` and the
previous shortlist for follow-ups. Restarting the process clears chat context;
Mem0 preferences persist.

## 5. Failure handling

- A node that raises propagates to `chatbot_service.process_message`, which
  returns an explicit error response.
- Routing failures are caught in `route_query` and downgraded to a conversational
  plan.
- A malformed plan cannot loop: `next_step` sends unknown steps to
  `finalize_turn`, and `_consume` truncates the queue at `MAX_VISITED_STEPS`.

## 6. Example

```python
graph = build_graph(deps=make_deps(...))
state = graph.invoke(
    {"customer_id": "customer_001", "session_id": "s1",
     "user_message": "Show me earbuds under ₹5,000"},
    config={"configurable": {"thread_id": "s1"}},
)
state["visited_steps"]   # prepare_turn, route_query, understand_request, ...
state["response_kind"]   # "product"
```
