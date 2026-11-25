# `app/config/__init__.py`

## 1. File purpose

Marks `app/config` as a package containing centralised configuration
(`settings.py`).

## 2. Imports and dependencies

None. Intentionally empty; `app.config.settings` is imported explicitly, so no
configuration is loaded as a side effect of importing the package.

## 3. Classes

None.

## 4. Functions and methods

None.

## 5. Execution workflow

Importing `app.config` executes this empty file and creates the package namespace.
`get_settings()` is only called when a module imports it.

## 6. Input and output

Input: `import app.config`.
Output: empty package module.

## 7. Interaction with other modules

Contains `settings.py`, which is used by memory, product, LLM, agent and service
modules. No module imports `app.config` directly except through
`app.config.settings`.

## 8. Important implementation decisions

- Empty to avoid eager `.env` loading at package import time.
- Settings remain a single explicit import, keeping test configuration predictable.

## 9. Example execution

```python
from app.config.settings import get_settings
```
