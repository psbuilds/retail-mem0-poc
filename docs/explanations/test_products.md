# `tests/test_products.py`

## 1. File purpose

Unit tests for the FastAPI catalogue. They use `TestClient(app)` (no network) and
verify health, listing, lookup, every required filter and empty-result behaviour.

## 2. Imports and dependencies

| Import | Why |
|---|---|
| `pytest` | Fixture |
| `fastapi.testclient.TestClient` | In-process HTTP client |
| `backend.api.app` | Application under test |

## 3. Classes

No classes. `client` is a module-scoped fixture returning `TestClient(app)`.

## 4. Tests

| Test | What it verifies |
|---|---|
| `test_health_reports_catalogue` | `/health` 200, status ok, ≥20 products, INR |
| `test_list_products` | ≥20 products, all required brands, ordering starts with highest rating |
| `test_get_product_by_id` | `/products/HP001` returns the Sony product |
| `test_invalid_product_id_returns_404` | unknown ID → 404 |
| `test_brand_filter_is_case_insensitive` | `brand=sony` returns only Sony |
| `test_category_filter` | all results are earbuds |
| `test_budget_filter` | all prices ≤ 3000 |
| `test_wireless_and_noise_cancellation_filters` | both booleans respected |
| `test_feature_filters` | repeated `features` params require both features |
| `test_search_endpoint_and_query` | `/products/search?q=jbl` returns JBL |
| `test_no_matching_products_returns_empty_list` | impossible combination returns `{"count": 0, "products": []}` |

## 5. Execution workflow

```
TestClient(app)
  -> request handled in-process by FastAPI
  -> router -> filter_products -> catalogue
  -> response asserted
```

No server is started and no port is used.

## 6. Input and output

Example:
```python
client.get("/products", params={"brand": "sony"})
# 200, {"count": 5, "products": [...]}
```

## 7. Interaction with other modules

Tests `backend/api.py` and `backend/routes/products.py`. The application's
`ProductAPIClient` is tested indirectly by end-to-end tests.

## 8. Important decisions

- Tests assert on real catalogue expectations (brands, price bounds), not just
  status codes.
- Empty results are asserted as a normal 200 response, matching the API contract.
- The module-scoped client keeps the test run fast.
- No live uv run uvicorn process is required, so tests are deterministic.

## 9. Example execution

```bash
uv run pytest tests/test_products.py -q
```
