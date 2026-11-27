# `app/routing/questions.py`

## 1. Purpose

Single source of truth for the typed questions sent to Jev. Question ids are
chosen by the application and echoed back by the API, so the classifier never
depends on model-generated field names.

## 2. Question types

| Type | Returns | Used for |
|---|---|---|
| `noul` | `noul` probability 0–1 | Independent capability flags |
| `choice` | `choice` label + `confidence` | Intent and follow-up action |

## 3. `NOUL_QUESTIONS`

| Id | Statement evaluated |
|---|---|
| `wants_products` | Asks to see/find/compare/refine/be recommended catalogue products |
| `refers_to_previous` | Refers back to products already recommended in this conversation |
| `states_preference` | States a durable brand/type/feature preference |
| `changes_memory` | Asks to correct, replace, withdraw or forget a saved preference |
| `needs_personalisation` | A correct answer requires saved long-term preferences |

Each is defined as `{"type": "noul", "instructions": "..."}`.

## 4. Choice questions

`INTENT_QUESTION` criteria:
`greeting`, `general_question`, `product_search`, `product_followup`,
`preference_statement`, `memory_question`, `memory_correction`.

`FOLLOWUP_QUESTION` criteria:
`none`, `refine_search`, `compare`, `explain`.

Choice criteria are descriptions of each label, matching the documented
`criteria` object (`{"label": "description"}`).

## 5. Functions

### `build_questions() -> dict[str, Any]`

Returns a fresh map of the five Noul questions plus `intent` and
`followup_action`. A fresh copy prevents callers from mutating the module
constants.

### `NOUL_IDS: tuple[str, ...]`

The ids the classifier requires in every response. A missing one is a malformed
response and triggers the fallback.

## 6. Cost and privacy

Seven questions add roughly 80 tokens per request. The request's `state` is
built in `classifier.py` from the current message plus at most two prior turns —
never saved memories or the customer id.
