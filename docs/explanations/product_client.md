# `app/products/product_client.py`

## 1. File purpose

HTTP client for the FastAPI product catalogue. It is the only application module
that knows the API's URL paths and query parameters. It converts HTTP problems
into one explicit `ProductAPIError`.

## 2. Imports and dependencies

| Import | Why |
|---|---|
| `httpx` | HTTP client |
| `Any` | Untyped JSON payloads |
| `Settings`, `get_settings` | Base URL and timeout |

## 3. Classes

### `ProductAPIError(RuntimeError)`
Raised for connection failures, 404s, HTTP errors and invalid JSON. The message is
safe to show in the UI (it contains the base URL, not secrets).

### `ProductAPIClient`

Attributes:
- `base_url` — stripped of trailing slash (default `PRODUCT_API_URL`);
- `timeout` — seconds (`PRODUCT_API_TIMEOUT_SECONDS`);
- `_client` — optional injected `httpx.Client`/TestClient.

Constructor also accepts `settings` for test isolation.

## 4. Functions and methods

### `_request(method, path, **kwargs) -> Any`
- Builds `base_url + path`.
- Uses the injected client when present (its own timeout applies), otherwise a
  short-lived `httpx.Client(timeout=...)`.
- Catches `httpx.HTTPError` → `ProductAPIError` naming the base URL.
- 404 → `ProductAPIError` for the path.
- ≥400 → `ProductAPIError` with status and up to 200 chars of body.
- Invalid JSON → `ProductAPIError`.

### `health() -> dict`
`GET /health`; returns status, service, catalogue version, product count, currency.

### `list_products(...) -> list[dict]`
Parameters: `category`, `brand`, `max_price`, `wireless`,
`noise_cancellation`, `features`, `query`.

Builds only the parameters that are set:
- booleans become lowercase strings (`true`/`false`);
- features are sent as repeated `features` query parameters;
- `query` maps to the API's `q`.

Validates that `payload["products"]` is a list, otherwise raises
`ProductAPIError`. Returns the product dictionaries unchanged.

### `search_products(query) -> list[dict]`
Convenience wrapper for `list_products(query=query)`, matching the API's
`/products/search` semantics.

### `get_product(product_id) -> dict`
`GET /products/{id}`; validates that the payload is a dict.

## 5. Execution workflow

```
ProductService.fetch_products(...)
  -> ProductAPIClient.list_products(...)
       -> params filtered for None
       -> GET {base}/products
       -> validate JSON shape
       -> return products
```

## 6. Input and output

Input:
```python
client.list_products(category="headphones", max_price=3000, wireless=True)
```

HTTP:
```
GET http://localhost:8000/products?category=headphones&max_price=3000&wireless=true
```

Output:
```python
[{"product_id": "HP007", "name": "JBL Tune 520BT ...", "price": 2499, ...}]
```

Error:
```python
ProductAPIError("Product API is unavailable at http://localhost:8000: ...")
```

## 7. Interaction with other modules

Used by `ProductService` and by tests (`TestClient` injection). `agent/nodes.py`
never imports it directly; it catches `ProductAPIError` raised through the
service.

## 8. Important implementation decisions

- No retries: a failure is reported immediately, and the workflow has a defined
  degraded response.
- No caching of catalogue results (only brand names are cached by the service), so
  recommendations always reflect the current API response.
- Injected clients manage their own timeout, which also keeps FastAPI `TestClient`
  free of Starlette's timeout deprecation warning.
- All transport errors become one typed exception for simple node handling.

## 9. Example execution

```python
from app.products.product_client import ProductAPIClient, ProductAPIError

client = ProductAPIClient()
try:
    print(client.health())
    for product in client.list_products(brand="JBL", max_price=3000):
        print(product["name"], product["price"])
except ProductAPIError as exc:
    print("Catalogue unavailable:", exc)
```
