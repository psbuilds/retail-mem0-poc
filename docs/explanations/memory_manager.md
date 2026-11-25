# `app/memory/memory_manager.py`

## 1. File purpose

Applies the product's memory rules. It decides which extracted candidates may
become permanent memories, prevents duplicates, and performs writes through
`MemoryService`. This is the module that keeps "under ₹3,000 this time" from
becoming a lifetime preference.

## 2. Imports and dependencies

| Import | Why |
|---|---|
| `re` | Text normalisation |
| `dataclass`, `field` | Candidate and write-report models |
| `difflib.SequenceMatcher` | Near-duplicate detection |
| `MemoryResult`, `MemoryService` | Actual memory writes |

No LLM or MCP import: candidates arrive from the agent node; writes go through the
service.

## 3. Constants

### `TEMPORARY_MARKERS`
Phrases such as "this time", "this purchase", "for now", "today", "for a trip",
"as a gift", "one-time", "only for".

### `PERSISTENT_MARKERS`
"usually", "always", "generally", "normally", "prefer", "I like", "I love",
"I tend to", "my favourite", "I'm a fan of".

### Internal thresholds
- `_MIN_CANDIDATE_LENGTH = 8` normalised characters.
- `_SIMILARITY_THRESHOLD = 0.82` for `SequenceMatcher`.

## 4. Classes

### `PreferenceCandidate` (frozen dataclass)

| Attribute | Meaning |
|---|---|
| `text` | Standalone sentence to remember |
| `kind` | `persistent` or `temporary` |
| `confidence` | 0.0–1.0 from the LLM |
| `reason` | LLM explanation (diagnostics) |

### `MemoryWriteReport`

| Attribute | Meaning |
|---|---|
| `saved` | Texts confirmed saved |
| `skipped` | Texts + reason not saved |
| `failures` | Text + error for failed writes |
| `attempted` | Texts sent to Mem0 |

Properties:
- `ok` → no failures;
- `status` → `error` (failures and no saves), `partial` (some saved, some failed),
  `success` (saved), `no_new_memories`.

### `MemoryManager`

`__init__(memory_service=None)` uses a new `MemoryService()` if none is provided.

## 5. Functions and methods

### `normalise_text(text) -> str`
Lowercases, replaces punctuation with spaces, collapses whitespace. Used for
comparisons only; stored text keeps its original wording.

### `is_temporary(text) -> bool`
True when any temporary marker appears.

### `has_persistent_marker(text) -> bool`
True when stable-preference language appears.

### `is_duplicate(candidate, existing) -> bool`
Compares normalised text:
- exact match;
- `SequenceMatcher` ratio ≥ 0.82;
- candidate tokens overlap an existing memory's tokens by ≥ 90% (handles
  "Prefers Sony headphones" vs a longer memory containing the same core words).

### `MemoryManager.select_persistent(candidates, existing_memories) -> (accepted, skipped_reasons)`
Accepts a candidate only if:
1. `kind == "persistent"`;
2. no temporary marker;
3. at least 8 normalised characters;
4. a budget-only sentence must use stable wording ("usually", "normally",
   "generally", "always") — otherwise it is treated as a one-off budget;
5. stable wording **or** confidence ≥ 0.7;
6. not a duplicate of an existing memory or a previously accepted candidate.

Returns accepted `PreferenceCandidate`s and human-readable skip reasons.

### `MemoryManager.remember(customer_id, candidates, existing_memories) -> MemoryWriteReport`
- Runs `select_persistent`.
- For each accepted candidate calls:
  ```python
  memory_service.add_memory(
      customer_id=customer_id,
      text=candidate.text,
      metadata={"source": "retail-memory-assistant", "kind": "persistent_preference"},
  )
  ```
- Records real outcomes in `saved` / `failures`.
- Never marks saved unless `MemoryResult.ok` is true.

## 6. Execution workflow

```
agent update_memory
  -> Ollama candidates
  -> MemoryManager.remember(customer, candidates, existing memory texts)
       -> select_persistent
            temporary? -> skip
            duplicate? -> skip
            weak wording and low confidence? -> skip
            else accept
       -> MemoryService.add_memory (Mem0 MCP)
  -> MemoryWriteReport -> graph.memory_writes -> UI
```

## 7. Input and output

Input:
```python
candidates = [
  PreferenceCandidate("Budget of ₹3,000 for this purchase.", kind="temporary"),
  PreferenceCandidate("Usually prefers Sony headphones.", kind="persistent", confidence=0.95),
]
report = manager.remember("customer_001", candidates, existing_memories=[])
```

Output:
```python
MemoryWriteReport(
  saved=["Usually prefers Sony headphones."],
  skipped=["Budget of ₹3,000 for this purchase. (temporary requirement)"],
  failures=[],
  attempted=["Usually prefers Sony headphones."],
)
```

## 8. Interaction with other modules

Called by `agent/nodes.py` (`update_memory`). Calls `MemoryService.add_memory`.
Its `MemoryWriteReport` becomes `state["memory_writes"]` and `ChatResponse`.

## 9. Important implementation decisions

- No storing of every message: only explicit candidates that pass all rules.
- Temporary classification is checked both from the LLM label and from wording, so
  a mislabeled candidate is still rejected.
- Budget-only memories require stable wording, so a mislabeled "Budget under
  ₹3,000" cannot become a permanent preference, while "Usually has a budget
  around ₹5,000" can.
- Confidence ≥ 0.7 can justify storing a preference phrased without a marker, but
  ambiguous single requests usually score lower and are skipped.
- De-duplication compares against both Mem0-retrieved memories and the current
  batch, preventing repeated writes within one turn.
- Write failures are surfaced verbatim; the manager never retries with another
  provider or claims success.

## 10. Example execution

```python
from app.memory.memory_manager import MemoryManager, PreferenceCandidate

manager = MemoryManager()
report = manager.remember(
    "customer_001",
    [PreferenceCandidate("Usually prefers wireless headphones.", confidence=0.9)],
    existing_memories=[],
)
print(report.status, report.saved)
```
