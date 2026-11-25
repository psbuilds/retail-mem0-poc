# `app/products/__init__.py`

## 1. File purpose

Marks `app/products` as the package for the product API client and application
product service.

## 2. Imports and dependencies

None. Empty so importing the package does not read settings or create HTTP clients.

## 3. Classes

None.

## 4. Functions and methods

None.

## 5. Execution workflow

Package import is a no-op; `ProductService` and `ProductAPIClient` are instantiated
explicitly by the graph/test code.

## 6. Input and output

Input: `import app.products`.
Output: empty package module.

## 7. Interaction with other modules

Contains `product_client.py` and `product_service.py`, used by `agent/nodes.py` and
`chatbot_service.py` (status helper).

## 8. Important implementation decisions

- Empty to keep imports dependency-free.
- No re-exports; explicit imports make the data source obvious.

## 9. Example execution

```python
from app.products.product_service import ProductService
```
