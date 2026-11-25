# `app/ui/__init__.py`

## 1. File purpose

Marks `app/ui` as the package that contains the Streamlit frontend.

## 2. Imports and dependencies

None. Empty on purpose: importing `app.ui` must not import Streamlit or run the UI,
which matters for tests and for the frontend-independent service layer.

## 3. Classes

None.

## 4. Functions and methods

None.

## 5. Execution workflow

Normal Python import executes this empty file. Streamlit runs
`app/ui/streamlit_app.py` directly, and that module performs its own setup.

## 6. Input and output

Input: `import app.ui`.
Output: empty package module.

## 7. Interaction with other modules

Contains `streamlit_app.py`, which calls `app.services.chatbot_service`. No other
module imports `app.ui`.

## 8. Important implementation decisions

- Empty so no UI dependency leaks into unit tests or backend code.
- Keeping the UI in its own package makes the architectural boundary explicit.

## 9. Example execution

```bash
uv run streamlit run app/ui/streamlit_app.py
```
