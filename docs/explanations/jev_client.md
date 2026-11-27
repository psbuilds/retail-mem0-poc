# `app/routing/jev_client.py`

## 1. Purpose

Minimal synchronous client for TypeSafe System One. `httpx` is already a project
dependency and the API is a single documented POST, so no extra SDK is added.

## 2. Verified contract

Request:

```http
POST https://api.typesafe.ai/v1/systemone
Authorization: Bearer <TYPESAFE_API_KEY>
Content-Type: application/json

{"model": "jev-latest", "state": "...", "questions": {...}}
```

Response (Noul and Choice):

```json
{
  "model": "jev-latest",
  "answers": {
    "wants_products": {"type": "noul", "noul": 0.97},
    "intent": {"type": "choice", "choice": "product_search",
                "confidence": 0.98, "probabilities": {"product_search": 0.98}}
  },
  "usage": {"input_tokens": 428, "output_tokens": 73}
}
```

## 3. Imports

| Import | Why |
|---|---|
| `time` | Latency measurement and retry backoff |
| `dataclasses` | `JevResponse` |
| `httpx` | HTTP transport, timeout and transport errors |
| `Settings`, `get_settings` | API key, URL, model, timeout and retry policy |

## 4. Classes

### `JevError(RuntimeError)`

Carries an optional `status_code`. Raised for every unusable outcome.

### `JevResponse`

| Field | Meaning |
|---|---|
| `answers` | Raw answer map |
| `model` | Model id echoed by the API |
| `input_tokens`, `output_tokens` | Usage for cost reporting |
| `latency_ms` | Wall-clock duration |
| `raw` | Full payload (diagnostics only) |

Methods:

- `noul(question_id) -> float | None` — numeric probability in 0..1, else None.
- `choice(question_id) -> str | None` — label, else None.
- `choice_confidence(question_id) -> float` — confidence, 0.0 when absent.

### `JevClient`

`__init__(settings=None, http_client=None)` — the optional HTTP client is a test
seam.

`configured` property: true when `TYPESAFE_API_KEY` is non-empty.

`classify(state: str, questions: dict) -> JevResponse`:
1. Raises `JevError` immediately when not configured.
2. POSTs `{model, state, questions}` with a bearer token.
3. Parses and validates that `answers` is a non-empty object.
4. Returns a `JevResponse`.

## 5. Error mapping and retries

| Condition | Behaviour |
|---|---|
| Timeout / transport error | retry once, then `JevError` |
| HTTP 429 or 5xx | retry once, then `JevError(status_code=...)` |
| HTTP 401/403 | `JevError("Jev authentication failed ...")`, no retry |
| HTTP 402 | `JevError("Jev credits exhausted ...")`, no retry |
| Other 4xx | `JevError` including a truncated response body |
| Non-JSON body | `JevError("Jev returned a non-JSON response")` |
| No `answers` | `JevError("Jev response contains no answers")` |

`JEV_TIMEOUT_SECONDS` (default 2) caps a model call on the critical path.
`JEV_RETRY_ATTEMPTS` (default 1) is the number of *additional* attempts.

## 6. Example

```python
client = JevClient(settings)
response = client.classify("Show me Sony headphones", build_questions())
response.noul("wants_products")   # 0.97
response.choice("intent")         # "product_search"
response.input_tokens             # 428
```

## 7. Why not the SDK

The endpoint, headers and response shape were verified directly. One request
type does not justify a new dependency, and the failure categories above need
explicit mapping regardless of client.
