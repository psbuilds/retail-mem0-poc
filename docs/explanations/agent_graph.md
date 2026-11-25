# `app/agent/graph.py`

## 1. File purpose

Builds and compiles the single LangGraph workflow and wires production services.
It defines node order, the checkpointer and the process-wide graph singleton.

## 2. Imports and dependencies

| Import | Why |
|---|---|
| `functools.lru_cache`, `partial` | Cache the graph; bind dependencies to node functions |
| `langgraph.checkpoint.memory.InMemorySaver` | Per-session conversation state |
| `langgraph.graph.START`, `END`, `StateGraph` | Graph construction |
| `app.agent.nodes` | Node functions |
| `WorkflowDeps` | Service bundle |
| `AgentState` | State schema |
| `get_settings` | Configuration |
| `OllamaClient` | Default LLM |
| `MemoryManager` | Default memory rules |
| `get_memory_service` | Process-wide MCP-backed memory service |
| `ProductService` | Default product access |

## 3. Classes

No classes are defined. The file exposes functions and a compiled graph object.

## 4. Functions and methods

### `build_default_deps() -> WorkflowDeps`
Creates the production bundle:
- one `MemoryService` singleton (`get_memory_service()`), so one MCP session per
  process;
- `ProductService()` (HTTP client, cached brands);
- `OllamaClient()`;
- `get_settings()`;
- `MemoryManager(memory_service)`.

### `build_graph(deps=None, checkpointer=None) -> Any`
Parameters:
- `deps` — injectable services (tests use fakes); defaults to production.
- `checkpointer` — defaults to `InMemorySaver()`.

Implementation:
```python
builder = StateGraph(AgentState)
builder.add_node("prepare_turn", partial(nodes.prepare_turn, deps=deps))
...
builder.add_edge(START, "prepare_turn")
...
builder.add_edge("update_memory", END)
return builder.compile(checkpointer=checkpointer)
```

Returns a compiled graph invoked as:
```python
graph.invoke(state, config={"configurable": {"thread_id": session_id}})
```

`partial(..., deps=deps)` produces a one-argument callable `(state) -> dict`, which
is the LangGraph node signature.

### `get_graph() -> Any`
`@lru_cache(maxsize=1)`; returns the shared compiled graph for `chatbot_service`.
Caching is important for Streamlit: the graph (and its checkpointer) must persist
across reruns so follow-up messages see the same thread state.

## 5. Execution workflow

```
build_graph(deps, checkpointer)
  create StateGraph(AgentState)
  register 7 nodes
  add sequential edges START -> ... -> END
  compile with checkpointer
  return graph

graph.invoke({customer_id, session_id, user_message},
             config={"configurable": {"thread_id": session_id}})
  load/mount checkpoint for thread
  run nodes in order
  save final state
```

## 6. Input and output

Input:
```python
{"customer_id": "customer_001", "session_id": "s1", "user_message": "Suggest headphones"}
```

Output: the final `AgentState` mapping (response, recommendations, memory writes,
statuses, errors).

## 7. Interaction with other modules

- `chatbot_service.process_message` calls `get_graph()`.
- Nodes call memory, product, LLM and recommendation modules through `deps`.
- No Streamlit import.

## 8. Important implementation decisions

- One graph, sequential edges: easy to trace and test; no multi-agent complexity.
- `InMemorySaver` rather than a database: conversation context is intentionally
  in-process, while long-term memory is Mem0's job.
- Thread id is the session id, so separate chats never share context.
- Dependency injection (`WorkflowDeps`) lets tests replace every external service
  without monkeypatching module globals.
- The default graph is cached so reruns reuse the checkpointer and the MCP
  connection.

## 9. Example execution

```python
from app.agent.graph import get_graph
graph = get_graph()
state = graph.invoke(
    {"customer_id": "customer_002", "session_id": "demo-1",
     "user_message": "Show me JBL headphones under ₹3,000"},
    config={"configurable": {"thread_id": "demo-1"}},
)
print(state["response"])
```

For tests:
```python
from app.agent.graph import build_graph
graph = build_graph(deps=fake_deps)   # fresh MemorySaver
```
