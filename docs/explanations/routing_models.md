# `app/routing/models.py`

## 1. Purpose

Defines the single validated routing contract used by the rest of the
application. Raw Jev answers never travel past the classifier.

## 2. Imports

| Import | Why |
|---|---|
| `typing.Literal` | Closed sets for intent, follow-up action and source |
| `pydantic.BaseModel`, `Field`, `field_validator` | Validation and defaults |

## 3. Types

### `Intent`

`greeting | general_question | product_search | product_followup |
preference_statement | memory_question | memory_correction`

### `FollowupAction`

`none | refine_search | compare | explain`

`refine_search` means a new catalogue query is wanted. `compare` and `explain`
reuse the previous shortlist and must not trigger a search.

### `RouteSource`

`jev | rules | jev+guards | fallback`

## 4. `RouteDecision`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `intent` | `Intent` | `general_question` | Main request category |
| `wants_products` | `bool` | `False` | Needs the catalogue pipeline |
| `refers_to_previous` | `bool` | `False` | Follows up on earlier recommendations |
| `states_preference` | `bool` | `False` | States a durable preference |
| `changes_memory` | `bool` | `False` | Corrects/replaces/forgets a saved preference |
| `needs_personalisation` | `bool` | `False` | Answer requires saved memories |
| `followup_action` | `FollowupAction` | `none` | Refine, compare or explain |
| `probabilities` | `dict[str, float]` | `{}` | Noul probabilities for diagnostics |
| `source` | `RouteSource` | `rules` | Where the decision came from |
| `confident` | `bool` | `True` | Model certainty above the configured band |
| `model` | `str \| None` | `None` | Jev model id |
| `input_tokens` | `int` | `0` | Jev usage for cost reporting |
| `latency_ms` | `int` | `0` | End-to-end Jev latency |
| `guards` | `list[str]` | `[]` | Deterministic guards applied |

Flags are independent: a single message can state a preference **and** ask for
products (`states_preference=True`, `wants_products=True`).

## 5. Validation

`_valid_probabilities` keeps only numeric values in `0..1`; malformed entries are
dropped rather than trusted. Pydantic rejects unknown intent/follow-up labels,
which raises `ValidationError` in the classifier and triggers the rules fallback.

`minimum_certainty` returns the smallest distance from 0.5 (scaled 0..1) across
all probabilities; 1.0 when no probabilities are present.

## 6. Example

```python
RouteDecision(
    intent="memory_correction",
    wants_products=True,
    states_preference=True,
    changes_memory=True,
    needs_personalisation=True,
    followup_action="none",
    source="rules",
)
```
