# `app/config/settings.py`

## 1. File purpose

Centralises configuration. All components read settings from here instead of
calling `os.environ` directly, which keeps defaults, validation and secret handling
in one place.

## 2. Imports and dependencies

| Import | Why |
|---|---|
| `functools.lru_cache` | Cache one settings object per process |
| `pydantic.Field` | Mark the API key as non-repr |
| `pydantic_settings.BaseSettings`, `SettingsConfigDict` | Load environment variables and `.env` |

No project modules are imported, so settings can be used by every layer without
cycles.

## 3. Classes

### `Settings(BaseSettings)`

Configuration model. `model_config`:

- `env_file=".env"` — loads project-local `.env` when present;
- `extra="ignore"` — unrelated environment variables are ignored;
- `case_sensitive=False` — `MEM0_API_KEY` maps to `mem0_api_key`.

Important attributes:

| Attribute | Default | Meaning |
|---|---|---|
| `product_api_url` | `http://localhost:8000` | FastAPI catalogue base URL |
| `product_api_timeout_seconds` | `10.0` | Catalogue HTTP timeout |
| `ollama_base_url` | `http://localhost:11434` | Ollama server |
| `ollama_model` | `llama3.1` | Model for extraction and replies |
| `ollama_timeout_seconds` | `120.0` | LLM timeout |
| `mem0_mcp_url` | `https://mcp.mem0.ai/mcp` | Official hosted MCP endpoint |
| `mem0_api_key` | `""` | Secret key; `repr=False` so it is not printed |
| `mem0_timeout_seconds` | `30.0` | MCP timeout |
| `typesafe_api_key` | `""` | Secret Jev key; `repr=False` |
| `jev_base_url` | `https://api.typesafe.ai/v1/systemone` | System One endpoint |
| `jev_model` | `jev-latest` | Jev model alias |
| `jev_timeout_seconds` | `2.0` | Router timeout before rules fallback |
| `jev_retry_attempts` | `1` | Additional attempts on timeout/429/5xx |
| `router_use_jev` | `True` | Set false to force rules mode |
| `router_confidence_threshold` | `0.5` | Noul/Choice threshold |
| `router_send_history_turns` | `2` | Prior turns sent to Jev |
| `memory_event_timeout_seconds` | `15.0` | Max wait for an async Mem0 write |
| `memory_event_poll_interval_seconds` | `1.0` | Event polling interval |
| `app_env` | `development` | Environment label |
| `max_recommendations` | `5` | Ranking limit |
| `memory_top_k` | `20` | Memory search/list limit |

Methods/properties:

#### `mem0_configured` (property) → `bool`
True when a non-blank key exists. It does **not** validate the key; validation
requires a real connection.

#### `jev_configured` (property) → `bool`
True when a non-blank `TYPESAFE_API_KEY` exists. It does not validate the key.

#### `startup_warnings() -> list[str]`
Returns human-readable warnings: a missing `MEM0_API_KEY` and a missing
`TYPESAFE_API_KEY` (the router then runs in deterministic rules mode). Messages
never contain key values. Used by diagnostics/UI.

## 4. Functions and methods

### `get_settings() -> Settings`

Returns a cached `Settings` instance (`@lru_cache(maxsize=1)`). Streamlit reruns
therefore do not re-read `.env` or rebuild configuration on every interaction.

## 5. Execution workflow

```
import app.config.settings
  -> module loads, defines Settings
first get_settings()
  -> Settings() reads environment + .env
  -> object cached
later get_settings()
  -> same object returned
```

## 6. Input and output

Input environment:
```dotenv
OLLAMA_MODEL=llama3.1
MEM0_API_KEY=secret
MAX_RECOMMENDATIONS=3
```

Output:
```python
Settings(
    ollama_model="llama3.1",
    mem0_api_key="secret",
    max_recommendations=3,
    ...
)
```

## 7. Interaction with other modules

Used by `memory_client`, `memory_service`, `product_client`, `product_service`,
`ollama_client`, `agent/graph`, `agent/nodes` and `chatbot_service`.

## 8. Important implementation decisions

- The API key has no default value and is excluded from `repr`, reducing the risk
  of accidental logging.
- Defaults allow the app to start without `.env`; memory then reports
  `not_configured` instead of crashing.
- There is no custom validation that requires the key at import time, because the
  product/LLM parts of the POC must remain usable without Mem0 credentials.

## 9. Example execution

```python
from app.config.settings import get_settings

settings = get_settings()
print(settings.ollama_model)          # llama3.1
print(settings.mem0_configured)       # False when no key is set
```
