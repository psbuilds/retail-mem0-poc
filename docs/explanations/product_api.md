# `backend/api.py`

## 1. File purpose

Creates the FastAPI application that serves the synthetic product catalogue. It is
the only backend in the POC; there is deliberately no chatbot API.

## 2. Imports and dependencies

| Import | Why |
|---|---|
| `fastapi.FastAPI` | Application object |
| `backend.routes.products` | Catalogue router and loader |

## 3. Module-level objects

### `app = FastAPI(...)`

- Title: "Retail Memory Assistant — Product Catalogue API".
- Description states the catalogue is synthetic and not live inventory.
- Version `1.0.0`.
- Includes `products.router`, which adds the `/products` prefix and tag.

## 4. Functions and methods

### `health() -> dict[str, object]`

`GET /health`, tagged `health`.

Returns:
```json
{
  "status": "ok",
  "service": "product-catalogue",
  "catalogue_version": "1.0.0",
  "product_count": 28,
  "currency": "INR"
}
```
It reads the cached catalogue via `products.load_catalogue()`, so a missing or
invalid JSON file surfaces as an error rather than a misleading healthy response.

## 5. Execution workflow

```
uv run uvicorn backend.api:app
  -> FastAPI app created
  -> router registered
  -> GET /health -> load_catalogue (cached) -> counts
  -> GET /products... -> routes/products.py
```

## 6. Input and output

Input: HTTP GET `/health`.
Output: JSON status payload (shown above).

## 7. Interaction with other modules

- `app/products/product_client.py` calls this service over HTTP.
- `tests/test_products.py` uses `TestClient(app)` without a network.
- `tests/test_end_to_end.py` injects a `TestClient` into `ProductAPIClient`.
- Nothing in the chatbot imports this module directly in production.

## 8. Important implementation decisions

- `/health` reports the real loaded product count and catalogue version, which
  helps distinguish "process is up" from "catalogue is usable".
- No CORS/database/auth: the POC is local and read-only.
- The app contains no chatbot route, respecting the agreed architecture.

## 9. Example execution

```bash
uv run uvicorn backend.api:app --reload --port 8000
curl http://localhost:8000/health
```

Interactive docs: <http://localhost:8000/docs>.
