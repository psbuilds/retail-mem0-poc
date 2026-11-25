# `app/recommendations/recommendation_service.py`

## 1. File purpose

Pure, deterministic product filtering and ranking. This is the only module allowed
to decide which products are recommended. The LLM explains its output but cannot
alter it.

## 2. Imports and dependencies

Only `typing.Any` and `typing.Iterable`. No project imports, so it is trivially
unit-testable and cannot accidentally call an API or the LLM.

## 3. Classes

No classes. `DEFAULT_LIMIT = 5`.

## 4. Functions and methods

### `_as_lower_set(values) -> set[str]`
Normalises strings/lists into a case-insensitive set, dropping blanks.

### `_as_list(value) -> list[str]`
Coerces a scalar, list, tuple or set into a clean list of strings. Supports
requirement values that arrive as a string or an array.

### `filter_products(products, requirements) -> list[dict]`
Applies hard filters, returning original product dicts (never copies/modifies):

| Key | Rule |
|---|---|
| `category` | case-insensitive equality |
| `brand` | string/list membership, case-insensitive |
| `excluded_brands` | brand not present |
| `excluded_product_ids` or `exclude_product_ids` | id not present |
| `max_budget` | numeric price ≤ budget; invalid price excluded |
| `wireless` | exact boolean match |
| `noise_cancellation` | exact boolean match |
| `required_features` | all present, case-insensitive |
| `excluded_features` | none present |

### `rank_products(products) -> list[dict]`
Sorts by:
1. rating descending (invalid → 0);
2. price ascending (invalid → infinity);
3. product id ascending (stable).

### `recommend(products, requirements, limit=DEFAULT_LIMIT) -> list[dict]`
Filters, ranks and truncates. Returns `[]` when `limit <= 0` or nothing matches.
No fallback products are generated.

## 5. Execution workflow

```
recommend(products, requirements, limit)
  -> filter_products   # hard constraints
  -> rank_products     # rating desc, price asc, id
  -> [:limit]
```

## 6. Input and output

Input:
```python
products = [...]  # from FastAPI
requirements = {"category": "headphones", "brand": "JBL", "max_budget": 3000}
recommend(products, requirements, limit=5)
```

Output:
```python
[
  {"product_id": "HP007", "name": "JBL Tune 520BT ...", "price": 2499, "rating": 4.2, ...},
  {"product_id": "HP008", "name": "JBL Tune 510BT ...", "price": 1999, "rating": 4.1, ...},
]
```

No match:
```python
[]
```

## 7. Interaction with other modules

Called by `agent/nodes.py` (`recommend_products`) with products from
`ProductService` and requirements from the resolution step. The UI renders its
output; the LLM prompt includes only its output.

## 8. Important implementation decisions

- Deterministic and transparent: no embeddings, scores or LLM judgement.
- Rating-then-price matches the agreed POC rule; product ID makes ties stable
  across runs.
- Filters never mutate catalogue data, so the UI can trust the fields.
- Excluded brands/IDs are supported even though the current agent does not yet set
  them; they are part of the agreed recommendation contract and are tested.
- Empty results are a valid outcome, not an error to hide.

## 9. Example execution

```python
from app.recommendations.recommendation_service import recommend

results = recommend(catalogue, {"wireless": True, "noise_cancellation": True,
                                "max_budget": 6000}, limit=3)
for product in results:
    print(product["name"], product["rating"], product["price"])
```
