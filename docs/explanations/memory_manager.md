# `app/memory/memory_manager.py`

## 1. Purpose

Application memory policy plus truthful reporting. Mem0 performs extraction,
deduplication, merging and superseding (`infer=true`), so this module no longer
runs a duplicate Ollama extraction pipeline or fuzzy duplicate detection.

Responsibilities:

- decide whether a sentence is a durable preference;
- decide what a correction/forget request means;
- select only retrieved records as correction targets;
- report saved/pending/failed based on the real Mem0 event.

## 2. Imports

| Import | Why |
|---|---|
| `re` | Brand rewriting, marker patterns |
| `dataclass`, `field` | `MemoryAction`, `MemoryWriteReport`, `CorrectionPlan` |
| `MemoryRecord`, `MemoryResult`, `MemoryService` | Memory operations |
| `CATALOGUE_BRAND_HINTS`, `brand_polarity` | Deterministic brand polarity |

## 3. Constants

`TEMPORARY_MARKERS`, `PERSISTENT_MARKERS`, `_MIN_CANDIDATE_LENGTH = 8`,
`_BUDGET_PATTERN`, `_PRODUCT_TOKEN_PATTERN`, `_STABLE_BUDGET_PATTERN`,
`_WITHDRAWAL_PATTERN`.

## 4. Functions

### `normalise_text(text) -> str`
Lowercase, strip punctuation, collapse whitespace.

### `is_temporary(text) -> bool`
True when any temporary marker appears.

### `has_persistent_marker(text) -> bool`
True for stable-preference language.

## 5. Classes

### `MemoryAction`

| Field | Meaning |
|---|---|
| `kind` | `add`, `update`, `delete` |
| `status` | `saved`, `deleted`, `pending`, `failed`, `not_found`, `skipped` |
| `text` | Preference text or rewritten text |
| `memory_id` | Record id for update/delete |
| `event_id` | Mem0 event id for adds |
| `error` | Failure detail |

`ok` is true only for `saved`/`deleted`. `as_dict()` serialises for state and UI.

### `MemoryWriteReport`

Aggregates actions plus skipped reasons.

| Property | Meaning |
|---|---|
| `saved` | Texts whose write definitely completed |
| `pending` | Accepted but unfinished |
| `failures` | `failed` and `not_found` actions |
| `attempted` | Every text attempted |
| `ok` | No failures and no pending |
| `status` | `success`, `pending`, `partial`, `error`, `no_new_memories` |

`as_dict()` produces `memory_writes` with `status`, `saved`, `pending`,
`skipped`, `failures`, `attempted`, `actions`.

### `CorrectionPlan`

`mode` (`delete`/`replace`), `old_brand`, `new_brand`, `targets` (validated
`MemoryRecord` objects).

### `MemoryManager`

`__init__(memory_service=None)`.

#### `should_store(message) -> (bool, reason)`
Rejects too-short text, temporary scope, withdrawal instructions, one-off
budgets without product context/stable wording, and statements without stable
preference language.

#### `preference_text(message) -> str`
Splits on sentence punctuation, drops clauses containing a withdrawal verb, and
returns the remaining preference text. `"Forget Sony. I prefer JBL."` →
`"I prefer JBL."`; a pure `"Forget that I prefer Sony."` → `""`.

#### `store(customer_id, text, metadata=None) -> MemoryAction`
Calls `add_memory`, then:

- tool failure → `failed`;
- no event id → `pending` (completion unverified — never saved);
- `wait_for_event` success → `saved` with the event's stored text;
- terminal failure → `failed`;
- timeout → `pending`.

#### `remember(customer_id, text, metadata=None) -> MemoryWriteReport`
Applies `should_store`, then `store`. Used by callers that want the aggregate
report.

#### `correction_plan(message, candidates) -> CorrectionPlan`
1. Brand list = brands found in candidates ∪ catalogue hints.
2. `brand_polarity(message, brands)` → preferred/excluded.
3. Explicit `delete` → the target is the brand named in the withdrawal clause
   (not a brand introduced afterwards), falling back to excluded/preferred brands;
   mode `delete`.
4. Otherwise new = preferred, old = first excluded; if only a new brand is
   mentioned, the old brand is the first *different* brand in the candidates.
5. `replace` only when both old and new exist.
6. Targets = candidates whose text contains a target token, capped at 5. No
   identifiable target → empty targets (never guess).

#### `apply_correction(customer_id, plan) -> list[MemoryAction]`
- Empty targets → one `not_found` action with an explanatory error.
- `replace` → `update_memory` with the old brand rewritten to the new brand.
- `delete` → `delete_memory` per target.
- Each action reports the real `MemoryResult`.

## 6. Execution order in a turn

`manage_memory` calls corrections first, then preference storage (unless the
correction already stored the new preference), so retrieval after this node sees
the updated memory state.

A message can do both: `"Forget Sony. I prefer JBL."` deletes the Sony record and
stores `"I prefer JBL."` — the withdrawal clause is stripped before the policy
check, so the new preference is not silently skipped.

## 7. Error handling

| Situation | Result |
|---|---|
| Mem0 rejects the add | `failed` + error |
| No event id | `pending` + "completion could not be verified" |
| Event FAILED | `failed` + event error |
| Event still pending at timeout | `pending` |
| Correction search fails | `failed` action, nothing changed |
| No matching record | `not_found`, nothing changed |
| Update/delete rejected | `failed` with the service error |

A correction is never reported as successful unless the underlying Mem0 call
succeeded.

## 8. Example

```python
manager = MemoryManager(memory_service)
report = manager.remember("customer_001", "I usually prefer Sony headphones")
report.status                 # "success" only after the event completes
report.saved                  # ["User usually prefers Sony headphones"]
```
