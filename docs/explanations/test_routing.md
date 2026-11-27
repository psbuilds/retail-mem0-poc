# `tests/test_routing.py`

## 1. Purpose

Tests the routing contract without network access. `FakeJev` returns scripted
answers in the verified response shape.

## 2. Helpers

| Helper | Purpose |
|---|---|
| `jev_answers(nouls=..., intent=..., followup=...)` | Build a typed answer map |
| `FakeJev` | Records calls; returns answers or raises a scripted error |
| `router_with(jev=None)` | Rules mode by default; Jev mode with a test key when a fake is passed |

## 3. Coverage

### Jev contract
- probabilities become flags at the configured threshold;
- question ids and types are exactly the expected set;
- state contains the message and recent turns but no customer id;
- model/token/latency are propagated.

### HTTP client mapping (`httpx.MockTransport`)
- a verified payload is parsed (bearer header, model, questions, Noul, Choice,
  input tokens);
- 401/403, 402 and other 4xx map to distinct `JevError` messages;
- 429/500/503 retry once and then report the status;
- a connect timeout becomes `JevError("Jev request failed ...")`;
- non-JSON, empty `answers` and unexpected payloads are rejected.

### Failures
- timeout, 401, 402, 429 and 500 each fall back to rules with
  `source="fallback"`, `confident=False` and a `jev_unavailable` guard;
- missing answers and unsupported intent labels fall back;
- low-confidence probabilities are used but marked `confident=False`;
- an unconfigured key never calls Jev and records `jev_not_configured`.

### Shortcuts and guards
- greeting and explicit correction/forget shortcuts never call Jev;
- `explicit_memory_operation` distinguishes delete, correct and ordinary
  negative preferences;
- `brand_polarity` handles `not`, `don't like ... anymore` and `anything but`;
- guards add product search, durable preference, temporary-scope suppression and
  previous-context refinement;
- greeting detection is narrow (a greeting plus a product request is not a
  greeting).

### Scenarios and plans
All 15 required scenarios are parametrised with their expected intent and
required steps. Invariants asserted for every plan:

- ends with `finalize_turn`;
- exactly one `generate_response` and one `finalize_turn`;
- no duplicates;
- every entry is in `PLANNED_NODES`;
- `manage_memory` first when present.

Additional cases: greetings schedule nothing else, comparisons reuse the
previous shortlist, comparisons without a shortlist search, corrections precede
retrieval, and multi-flag messages keep all capabilities.

### Planner hardening
- unknown steps route to `finalize_turn`;
- an exhausted queue returns `END`;
- finalisation cannot run twice;
- the visit budget truncates to `finalize_turn`;
- the heuristic never sends a greeting to search.

### Live
`test_live_jev_returns_typed_answers` is skipped unless `TYPESAFE_API_KEY` is
exported.
