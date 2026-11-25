# `tests/test_recommendations.py`

## 1. File purpose

Pure unit tests for the deterministic recommendation engine. They verify every
supported filter, the ranking contract and the empty-result behaviour without any
API, LLM or memory dependency.

## 2. Imports and dependencies

`pytest` and the three engine functions (`filter_products`, `rank_products`,
`recommend`).

## 3. Classes

No classes. The `product(...)` helper builds a compact product dict, and the
`products` fixture defines five products:
- A Sony ₹9,000, 4.6, lightweight
- B Sony ₹5,000, 4.6
- C JBL ₹3,000, 4.2, no ANC
- D boAt ₹2,000, 4.2, wired, no ANC
- E Anker ₹4,000, 4.8, extra bass + lightweight

## 4. Tests

| Test | Expectation |
|---|---|
| `test_brand_filter` | `brand=jbl` → C only |
| `test_budget_filter` | ≤4000 → C, D, E |
| `test_wireless_and_noise_cancellation_filter` | A, B, E |
| `test_required_features` | lightweight → A, E |
| `test_excluded_features` | no lightweight → B, C, D |
| `test_excluded_product_ids_and_brands` | A, JBL removed → B, D, E |
| `test_ranking_by_rating_then_price` | E (4.8), then B/A (4.6, cheaper first), then D/C (4.2, cheaper first) |
| `test_recommend_applies_limit` | top 2 → E, B |
| `test_no_matching_results` | unknown brand and tiny budget → `[]` |
| `test_input_products_are_not_mutated` | input list unchanged after recommend |

## 5. Execution workflow

```
fixture products
  -> filter_products / rank_products / recommend
  -> assert ids and order
```

## 6. Input and output

Input:
```python
recommend(products, {"wireless": True, "noise_cancellation": True}, limit=5)
```
Output ids:
```python
["E", "B", "A"]
```

## 7. Interaction with other modules

Tests only `app/recommendations/recommendation_service.py`. The end-to-end suite
runs the same engine on real catalogue data.

## 8. Important decisions

- Products are tiny synthetic dicts so failures point at the rule, not at catalogue
  data.
- Ranking tests cover the price tie-break explicitly, which is easy to get wrong.
- No-match behaviour is asserted as an empty list (no fallback suggestions).
- Immutability is tested because the UI/LLM receive the same objects.

## 9. Example execution

```bash
uv run pytest tests/test_recommendations.py -q
```
