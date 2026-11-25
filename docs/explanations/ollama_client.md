# `app/llm/ollama_client.py`

## 1. File purpose

Reusable client for a local Ollama server. It isolates HTTP details and safe JSON
parsing, and raises explicit errors so the workflow can report LLM failures
instead of pretending a reply was generated.

## 2. Imports and dependencies

| Import | Why |
|---|---|
| `json` | Parse model JSON |
| `Any` | Payloads |
| `httpx` | HTTP client |
| `Settings`, `get_settings` | Base URL, model, timeout |

## 3. Classes

### `OllamaError(RuntimeError)`
Base class.

### `OllamaUnavailableError(OllamaError)`
Connection refused/timeout: the server is not reachable.

### `OllamaInvalidJSONError(OllamaError)`
Model output could not be parsed into a JSON object.

### `OllamaClient`

Attributes: `base_url`, `model`, `timeout`, optional `_client` (injected for
tests). Constructor accepts `settings`.

## 4. Functions and methods

### `extract_json_object(text) -> dict`
- Strips fenced code blocks (```).
- Tries `json.loads` on the whole text.
- Otherwise takes the substring from the first `{` to the last `}` and parses it,
  tolerating short prose around the object.
- Raises `OllamaInvalidJSONError` if parsing fails or the result is not an object.

### `_post(path, payload) -> dict`
- POSTs JSON to `{base_url}{path}` using the injected client or a short-lived
  `httpx.Client`.
- `httpx.HTTPError` → `OllamaUnavailableError` naming the base URL.
- 404 → `OllamaError("model ... is not installed. Run: ollama pull ...")`.
- Other ≥400 → `OllamaError` with status/body.
- Invalid JSON/shape → `OllamaError`.

### `is_available() -> bool`
Calls `/api/tags`, then checks for an exact model name or `name:tag` prefix. False
on any connection/JSON error.

### `list_models() -> list[str]`
Returns installed model names, or `[]` when unreachable. Used for status messages.

### `chat(messages, json_mode=False, temperature=0.2) -> str`
Sends:
```json
{"model": "...", "messages": [...], "stream": false,
 "options": {"temperature": 0.2}, "format": "json"}
```
(`format` only when `json_mode`). Extracts `message.content`, raises
`OllamaError` if empty.

### `chat_json(messages, temperature=0.1) -> dict`
Calls `chat(json_mode=True)` and parses with `extract_json_object`.

### `ollama_status(model=None) -> tuple[bool, str]`
Returns UI-safe status:
- online with the requested model;
- online but the model is missing (lists that fact);
- not reachable.

## 5. Execution workflow

```
understand_request / update_memory
  -> chat_json -> chat(json_mode=True) -> _post -> /api/chat
               -> extract_json_object

generate_response
  -> chat -> _post -> /api/chat
```

## 6. Input and output

Input:
```python
client.chat_json([
  {"role": "system", "content": "Return JSON ..."},
  {"role": "user", "content": "Show me Sony headphones under 5000"},
])
```

Output:
```python
{"category": "headphones", "brand": "Sony", "max_budget": 5000, ...}
```

Failure:
```python
OllamaUnavailableError("Ollama is not reachable at http://localhost:11434: ...")
```

## 7. Interaction with other modules

Used by `agent/nodes.py` (understanding, response generation, preference
extraction) and by `chatbot_service.check_services` through `ollama_status`.
Prompts live in `agent/prompts.py`, not here.

## 8. Important implementation decisions

- JSON mode is requested but never trusted: parsing and schema validation happen
  afterwards, and deterministic grounding checks the fields against the message.
- Low temperatures (0.1 for extraction, 0.3 for replies) reduce format drift.
- The model name is configurable; no other model is silently substituted.
- Errors are typed so the workflow can distinguish "server down" from "invalid
  JSON" while both produce honest user-facing messages.
- No streaming in the POC; the response is awaited and validated.

## 9. Example execution

```bash
ollama pull llama3.1
```

```python
from app.llm.ollama_client import OllamaClient, ollama_status

print(ollama_status())  # (True, 'Ollama online (llama3.1)')
reply = OllamaClient().chat([
    {"role": "user", "content": "Say hello in one short sentence."}
])
print(reply)
```
