# `app/routing/__init__.py`

## 1. Purpose

Marks `app/routing` as a package and re-exports the router's public surface so
callers can import from one place.

## 2. Exports

| Name | Source |
|---|---|
| `QueryRouter` | `app.routing.classifier` |
| `build_step_plan` | `app.routing.classifier` |
| `RouteDecision` | `app.routing.models` |
| `Intent` | `app.routing.models` |
| `FollowupAction` | `app.routing.models` |

## 3. Imports and side effects

Importing the package imports the classifier, which imports the Jev HTTP client
and the rules module. No network call, settings read or client construction
happens at import time; `QueryRouter` creates its client in `__init__`.

## 4. Example

```python
from app.routing import QueryRouter

router = QueryRouter(settings)
decision, plan = router.route("Show me earbuds under 5000")
```

## 5. Interactions

`app/agent/graph.py`, `app/agent/nodes.py` and `app/memory/memory_manager.py`
(brand polarity) import from this package. The package is documented as a whole
in [query_router.md](query_router.md).
