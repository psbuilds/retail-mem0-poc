# `app/services/__init__.py`

## 1. File purpose

Marks `app/services` as the package for reusable application services, currently
`chatbot_service.py`.

## 2. Imports and dependencies

None. Empty to avoid importing the LangGraph graph or MCP client when the package
is referenced.

## 3. Classes

None.

## 4. Functions and methods

None.

## 5. Execution workflow

Importing `app.services` does nothing beyond package creation. Callers import
`app.services.chatbot_service` explicitly.

## 6. Input and output

Input: `import app.services`.
Output: empty package module.

## 7. Interaction with other modules

Contains `chatbot_service.py`, used by the Streamlit UI and `app/main.py`. It is
the boundary between frontends and the agent/memory/product layers.

## 8. Important implementation decisions

- Empty to keep imports side-effect free.
- No convenience re-exports, which would indirectly import the graph and settings.

## 9. Example execution

```python
from app.services.chatbot_service import process_message
```
