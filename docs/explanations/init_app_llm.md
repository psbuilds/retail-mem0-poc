# `app/llm/__init__.py`

## 1. File purpose

Marks `app/llm` as the package containing the reusable Ollama client.

## 2. Imports and dependencies

None. Empty so importing the package does not read configuration or contact
Ollama.

## 3. Classes

None.

## 4. Functions and methods

None.

## 5. Execution workflow

Package import is a no-op; `OllamaClient()` is created when the graph wires its
dependencies or a test constructs it.

## 6. Input and output

Input: `import app.llm`.
Output: empty package module.

## 7. Interaction with other modules

Contains `ollama_client.py`, used by `agent/nodes.py`, `agent/graph.py`,
`chatbot_service.py` (status) and tests.

## 8. Important implementation decisions

- Empty to avoid import-time HTTP setup.
- Prompt text deliberately lives in `app/agent/prompts.py`, not here.

## 9. Example execution

```python
from app.llm.ollama_client import OllamaClient
```
