# `backend/routes/products.py`

## 1. File purpose

Owns catalogue loading and filtering, and defines the product endpoints. Keeping
filtering here means the application never reads `products.json` and the backend
can be tested independently.

## 2. Imports and dependencies

| Import | Why |
|---|---|
| `json` | Read the catalogue |
| `lru_cache` | Load/parse the file once |
| `Path` | Resolve the data file path |
| `Any` | Product dictionaries |
| `APIRouter`, `HTTPException`, `Query` | Routing, 404 and query documentation |

`CATALOGUE_PATH = backend/data/products.json` relative to the module.

## 3. Classes

No classes. `router = APIRouter(prefix="/products", tags=["products"])`.

## 4. Functions and methods

### `load_catalogue() -> dict`
`@lru_cache(maxsize=1)`. Reads and parses the JSON file.
- `FileNotFoundError` → `RuntimeError` naming the path.
- `json.JSONDecodeError` → `RuntimeError` with details.
Caching avoids re-reading the file per request.

### `all_products() -> list[dict]`
Returns `load_catalogue()["products"]`.

### `_matches_features(product, required) -> bool`
True when every required feature is present, case-insensitive.

### `filter_products(...) -> list[dict]`
Pure filtering helper used by both list and search endpoints. Supports:
- `category` (case-insensitive);
- `brand` (case-insensitive);
- `max_price`, `min_price` (inclusive);
- `wireless`, `noise_cancellation` (exact boolean);
- `features` (list, comma-separated values split, all required);
- `q` (text match against name, brand, features).

Results are sorted by rating desc then price asc, which matches the
recommendation engine's documented ranking.

### `_filter_from_query(...)`
Thin wrapper so `/products` and `/products/search` share one implementation.

### `list_products(...) -> dict`
`GET /products` (also `GET /products/` hidden from the schema). Returns:
```json
{"count": 7, "products": [...]}
```
All query parameters are optional and documented via `Query(..., description=...)`.
`max_price`/`min_price` reject negatives with FastAPI validation.

### `search_products(...) -> dict`
`GET /products/search`. Same parameters/response as `/products`. It is declared
**before** `/products/{product_id}` so "search" is not captured as an ID.

### `get_product(product_id) -> dict`
`GET /products/{product_id}`. Case-insensitive ID match; raises HTTP 404 with
`"Product <id> not found"` when absent.

## 5. Execution workflow

```
GET /products?brand=JBL&max_price=3000
  -> FastAPI parses/validates query params
  -> _filter_from_query
  -> filter_products
       -> all_products -> load_catalogue (cached)
       -> apply each condition
       -> rank rating desc, price asc
  -> {"count": n, "products": [...]}

GET /products/HP001
  -> loop over products
  -> return product or 404
```

## 6. Input and output

Input:
```
GET /products?category=headphones&wireless=true&noise_cancellation=true&max_price=10000
```

Output:
```json
{
  "count": 3,
  "products": [
    {"product_id": "HP019", "name": "Anker Soundcore Space One ANC Headphones",
     "brand": "Anker", "category": "headphones", "price": 4999, "rating": 4.5,
     "wireless": true, "noise_cancellation": true, "features": ["Bluetooth", "..."]}
  ]
}
```

## 7. Interaction with other modules

- Mounted by `backend/api.py`.
- Called over HTTP by `app/products/product_client.py`.
- Tested directly by `tests/test_products.py` and through `TestClient` in
  `tests/test_end_to_end.py`.

## 8. Important implementation decisions

- The catalogue is loaded once and filtered in memory; 28 products need no
  database.
- All filters are optional and combinable; "no matches" returns an empty list with
  HTTP 200, not an error.
- Unknown product IDs return 404, which the client converts to
  `ProductAPIError`.
- Query parameter descriptions double as API documentation at `/docs`.
- Feature matching is subset semantics (all required features must exist), not
  partial text matching.

## 9. Example execution

```bash
curl "http://localhost:8000/products?brand=jbl&max_price=3000"
curl "http://localhost:8000/products/search?category=earbuds&noise_cancellation=true"
curl "http://localhost:8000/products/HP001"
```
