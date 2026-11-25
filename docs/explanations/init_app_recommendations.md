# `app/recommendations/__init__.py`

## 1. File purpose

Marks `app/recommendations` as the package for deterministic recommendation logic.

## 2. Imports and dependencies

None. Empty so importing the package does not import any service or configuration.

## 3. Classes

None.

## 4. Functions and methods

None.

## 5. Execution workflow

Package import is a no-op. Functions are imported explicitly from
`recommendation_service.py`.

## 6. Input and output

Input: `import app.recommendations`.
Output: empty package module.

## 7. Interaction with other modules

Contains `recommendation_service.py`, used by `agent/nodes.py` and tests.

## 8. Important implementation decisions

- Empty to keep the pure recommendation module easy to import in isolation.
- No re-exports that could blur the single responsibility.

## 9. Example execution

```python
from app.recommendations.recommendation_service import recommend
```
