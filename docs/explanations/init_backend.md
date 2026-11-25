# `backend/__init__.py`

## 1. File purpose

Marks `backend` as a Python package so `uvicorn backend.api:app` (or
`uv run uvicorn backend.api:app`) and `from backend.api import app` work.

## 2. Imports and dependencies

None. Empty on purpose; importing the package must not start the FastAPI app or load
the catalogue.

## 3. Classes

None.

## 4. Functions and methods

None.

## 5. Execution workflow

Package import is a no-op. Uvicorn imports `backend.api`, which creates the app and
registers routes.

## 6. Input and output

Input: `import backend`.
Output: empty package module.

## 7. Interaction with other modules

Contains `api.py`, `routes/products.py` and `data/products.json`. The application
talks to it over HTTP.

## 8. Important implementation decisions

- Empty to keep CLI/test imports side-effect free.
- Backend remains separate from the chatbot package by design.

## 9. Example execution

```bash
uv run uvicorn backend.api:app --port 8000
```
