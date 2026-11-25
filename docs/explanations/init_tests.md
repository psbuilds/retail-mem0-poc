# `tests/__init__.py`

## 1. File purpose

Marks `tests` as a package so shared helpers/fakes in `tests/conftest.py` can be
imported as `tests.conftest` by test modules.

## 2. Imports and dependencies

None. Empty so test collection does not trigger fixture execution or application
imports beyond what each test requests.

## 3. Classes

None.

## 4. Functions and methods

None.

## 5. Execution workflow

Pytest adds the project root to `sys.path`; this marker makes `tests` importable as a
regular package. `conftest.py` is still discovered normally by pytest.

## 6. Input and output

Input: `import tests`.
Output: empty package module.

## 7. Interaction with other modules

Contains `conftest.py`, `test_products.py`, `test_recommendations.py`,
`test_memory.py`, `test_agent.py` and `test_end_to_end.py`.

## 8. Important implementation decisions

- The marker allows `from tests.conftest import FakeLLM, ...`, which keeps fakes in
  one place instead of duplicating them.
- No test code runs at import.

## 9. Example execution

```bash
uv run pytest
```
