# `app/routing/rules.py`

## 1. Purpose

Deterministic layer around Jev:

- **shortcuts** for unambiguous messages (greetings, explicit corrections);
- **recall guards** that supplement a Jev decision;
- a complete **rule-based fallback** when Jev is unavailable;
- **brand polarity**, reused by request understanding so a negated brand becomes
  an exclusion and never a requirement.

## 2. Constants

| Constant | Meaning |
|---|---|
| `CATALOGUE_BRAND_HINTS` | Brand names used to recognise mentions *without* calling the product API |
| `CATEGORY_WORDS` | headphone/earbud/... synonyms |
| `SHOPPING_VERBS` | show, suggest, recommend, looking for, want, need, buy, find, options, alternatives, shortlist, browse |
| `DURABLE_MARKERS` | usually, always, generally, normally, i prefer, i like, ... |
| `TEMPORARY_MARKERS` | this time, this purchase, as a gift, for my trip, ... |
| `PERSONALISATION_PHRASES` | based on my preferences, what you know about me, for me, ... |

"prefer" is deliberately **not** a shopping verb: "I usually prefer Sony
headphones" is a preference statement, not a catalogue request.

## 3. Functions

### `mentions_product(text) -> bool`

True for any category word, catalogue brand hint, or numeric budget hint.

### `is_greeting_only(message) -> bool`

True only for a bare greeting (`Hi`, `Hello there!`, `Good morning`) with no
product content. `"Hi, show me Sony headphones"` is not a greeting.

### `has_temporary_scope(message)` / `has_durable_marker(message)`

Marker checks used by guards and by `MemoryManager.should_store`.

### `new_preference_after_withdrawal(message) -> bool`

Splits the message on `, . ; ! ?` and checks whether any clause **without** a
delete instruction contains durable-preference language.

| Message | Result |
|---|---|
| `Forget Sony. I prefer JBL.` | `True` (delete Sony **and** store JBL) |
| `Forget Sony, I prefer JBL now` | `True` |
| `Forget that I prefer Sony.` | `False` |

### `explicit_memory_operation(message) -> "delete" | "correct" | None`

- `delete`: `forget`, `stop remembering`, `don't remember that`, `remove this memory`.
- `correct`: `no longer prefer/like/want ...`, `don't like ... anymore`,
  `changed my mind`, `switched to/from`.

Ordinary negative preferences ("I don't like in-ear models") are **not**
corrections, because they do not refer to a saved preference.

### `detect_followup_action(message) -> str`

Priority: `compare` → `explain` → `refine_search` → `none`.
Examples: "Which is better?" → compare; "Tell me more about the second one" →
explain; "Show me cheaper ones" → refine_search.

### `brand_polarity(message, known_brands=None) -> (preferred, excluded)`

Scans each brand occurrence and looks 28 characters back for a negator
(`not`, `no longer`, `never`, `without`, `instead of`, `don't like`,
`not a fan of`, `anything but`, ...).

| Message | Result |
|---|---|
| `I prefer JBL, not Sony.` | `("JBL", ["Sony"])` |
| `I don't like JBL anymore.` | `(None, ["JBL"])` |
| `Show me Sony headphones.` | `("Sony", [])` |
| `Anything but boAt.` | `(None, ["boAt"])` |

A brand can never be both preferred and excluded.

### `apply_guards(decision, message, has_previous_context=False)`

| Guard | Trigger | Effect |
|---|---|---|
| `shopping_verb_with_product` | shopping signal but `wants_products` false | sets `wants_products` |
| `refinement_of_previous_request` | short constraint, previous shortlist exists, no memory operation, not a durable statement or explanatory question | sets `wants_products`, `refers_to_previous`, `followup_action="refine_search"` |
| `durable_preference_marker` | usually/always/prefer/... | sets `states_preference` |
| `temporary_scope_suppressed` | temporary scope with no durable marker | clears `states_preference` |
| `explicit_memory_delete` / `_correct` | explicit operation | sets `changes_memory` |
| `withdrawal_is_not_a_preference` | delete with no separate preference clause | clears `states_preference` |
| `negated_existing_brand` | preference + a negated brand | sets `changes_memory` |
| `correction_requires_updated_memory` | correction | sets `needs_personalisation` |
| `personalisation_phrase` | based on my preferences / for me | sets `needs_personalisation` |

Guards only add capabilities or record scope. They do not discard a valid Jev
decision, except the documented temporary-scope rule. Any guard that fires sets
`source="jev+guards"` and is listed in `RouteDecision.guards`.

### `heuristic_route(message, has_previous_context=False) -> RouteDecision`

Complete fallback classifier:

1. explicit memory operation → `memory_correction`;
2. shopping signal → `product_search`, or `product_followup` for a follow-up;
3. short constraint with previous context → refine;
4. memory question phrases without a product request → `memory_question`;
5. durable preference → `preference_statement`;
6. greeting → `greeting`;
7. otherwise `general_question`.

`states_preference` is false for a pure `forget` instruction (no new preference
clause) and for a correction with no positive brand, so withdrawal wording alone
is never stored. A withdrawal followed by a new preference keeps
`states_preference=True`.

### `lower_question(lowered) -> bool`

Small helper: contains `?` or question phrasing (`what do you`, `do you`, ...).

## 4. Execution order inside the router

1. `is_greeting_only` / `explicit_memory_operation` shortcut check.
2. Jev (if configured) or `heuristic_route`.
3. `apply_guards` for Jev decisions.
4. `_reconcile` and `build_step_plan` in `classifier.py`.

## 5. Known limitations

- Brand hints duplicate the six catalogue brands for guard purposes only; the
  product API remains the catalogue source of truth.
- Feature-only corrections ("I don't like in-ear anymore") have no brand target,
  so the correction is reported as not found rather than guessing a record.
- `CATEGORY_WORDS` and markers are English-only.
