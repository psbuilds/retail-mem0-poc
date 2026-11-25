# `app/memory/__init__.py`

## 1. File purpose

Marks `app/memory` as the package containing the Mem0 MCP client, memory service
and memory manager.

## 2. Imports and dependencies

None. Empty on purpose: importing the package must not open an MCP session or load
the API key.

## 3. Classes

None.

## 4. Functions and methods

None.

## 5. Execution workflow

Importing `app.memory` is a no-op. A session starts only when
`get_memory_service()` is called and a memory operation runs.

## 6. Input and output

Input: `import app.memory`.
Output: empty package module.

## 7. Interaction with other modules

Contains modules used by `agent/nodes.py`, `chatbot_service.py` and
`scripts/check_services.py`. The UI never imports them directly.

## 8. Important implementation decisions

- Empty to prevent connection side effects during imports (important for tests and
  Streamlit reruns).
- Memory operations stay explicit through `memory_service`.

## 9. Example execution

```python
from app.memory.memory_service import get_memory_service
```
