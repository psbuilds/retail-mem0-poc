# `app/products/product_service.py`

## 1. File purpose

Application-level product access. It decides *what* to fetch from the catalogue
and caches brand names used by request understanding and memory parsing. It does
not perform recommendation filtering.

## 2. Imports and dependencies

| Import | Why |
|---|---|
| `Any` | Product dictionaries |
| `ProductAPIClient`, `ProductAPIError` | HTTP access/errors |

## 3. Classes

### `ProductService`

Attributes:
- `client: ProductAPIClient`;
- `_brands: list[str] | None` — cache populated on first successful call.

Methods:

#### `available_brands() -> list[str]`
Returns sorted unique catalogue brands, fetched from `GET /products` once. Used to:
- recognise brand mentions in the current message;
- parse brands from retrieved memories;
- keep brand knowledge out of hardcoded lists.

Raises `ProductAPIError` when the API is down; callers treat that as "no brands
known".

#### `fetch_products(category=None, max_budget=None, query=None) -> list[dict]`
Calls `client.list_products(category=..., max_price=..., query=...)`.

Deliberately fetches a **superset**: only category and budget are pushed to the
API. Brand, wireless, noise cancellation and features are applied by the
recommendation engine, so:
- precedence is enforced in one place;
- temporary brand overrides work without a second API design;
- excluded features/ids can be handled even if the API doesn't support them.

Raises `ProductAPIError` on failure.

#### `get_product(product_id) -> dict`
Direct passthrough for a single product (useful for future features/tests).

#### `health() -> dict`
Product API health payload.

## 4. Functions and methods

### `product_api_status() -> tuple[bool, str]`
Creates a `ProductAPIClient`, calls `health()`, and returns:
- `(True, "Product API online (28 products)")`;
- `(False, "<actual error>")`.

It never raises, so the UI can display status safely.

## 5. Execution workflow

```
search_products node
  -> ProductService.fetch_products(category, max_budget)
       -> ProductAPIClient.list_products(...)
       -> HTTP GET /products
       -> candidate products

understand_request node
  -> ProductService.available_brands()
       -> first call fetches /products and caches brand names
       -> later calls reuse the cache
```

## 6. Input and output

Input:
```python
service.fetch_products(category="headphones", max_budget=3000)
```

Output: 7 products (JBL, boAt, Sony under ₹3,000) — a superset from which the
recommendation engine selects.

## 7. Interaction with other modules

- Called by `agent/nodes.py` (`search_products`, `understand_request`).
- Called by `chatbot_service.product_api_status` via the helper function.
- Calls `ProductAPIClient`; imports no recommendation or UI code.

## 8. Important implementation decisions

- The brand cache is instance-level, so tests with a fresh `ProductService` start
  clean. The production graph shares one instance, so the catalogue is not fetched
  repeatedly just to list brands.
- No filtering by brand here: that would duplicate recommendation logic and could
  conflict with precedence (e.g. a temporary JBL request).
- Errors propagate; the node decides how to degrade.
- `product_api_status` is deliberately non-throwing for UI use.

## 9. Example execution

```python
from app.products.product_service import ProductService

service = ProductService()
print(service.available_brands())          # ['Anker', 'JBL', 'Realme', ...]
candidates = service.fetch_products(category="headphones", max_budget=5000)
print(len(candidates))
```
