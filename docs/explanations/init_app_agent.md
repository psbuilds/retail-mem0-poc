# `app/agent/__init__.py`

## 1. File purpose

Marks `app/agent` as the LangGraph package (`state`, `prompts`, `nodes`, `graph`).

## 2. Imports and dependencies

None. Intentionally empty so importing `app.agent` does not build the graph, load
prompts or create service connections.

## 3. Classes

None.

## 4. Functions and methods

None.

## 5. Execution workflow

Importing the package creates an empty namespace. The graph is built only when
`app.agent.graph.get_graph()` is called by the chatbot service.

## 6. Input and output

Input: `import app.agent`.
Output: empty package module.

## 7. Interaction with other modules

Contains the workflow modules used by `app.services.chatbot_service`. No other
package imports `app.agent` as a package; submodules are imported explicitly.

## 8. Important implementation decisions

- Empty to avoid import-time graph compilation and MCP/Ollama client creation.
- Keeps startup deterministic and tests isolated.

## 9. Example execution

```python
from app.agent.graph import get_graph
```
