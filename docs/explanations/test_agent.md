# `tests/test_agent.py`

## 1. File purpose

Workflow-level tests with deterministic fakes. They cover the behaviours the POC
exists to demonstrate: memory personalisation, explicit overrides, temporary
preferences, follow-up context, and graceful failure of each dependency.

## 2. Imports and dependencies

`pytest`, `process_message`, and the fakes/helpers from `tests/conftest.py`
(`FakeLLM`, `FakeMemoryService`, `FakeProductService`, `make_deps`, `make_graph`).

## 3. Classes

No test classes. Helper `_sony_memories()` returns a fake memory store with:
- "Usually prefers Sony headphones with noise cancellation."
- "Always prefers wireless headphones."

## 4. Tests

| Test | Setup | Assertions |
|---|---|---|
| `test_new_customer_without_memories` | empty memory | `memory_status="empty"`, headphones returned |
| `test_existing_customer_preferences_are_applied` | Sony/ANC memories, generic request | every recommendation is Sony + ANC, resolved brand Sony |
| `test_current_request_overrides_persistent_memory` | Sony memories, JBL ≤3000 message, temporary preference candidates | brand/budget resolved from current request; all results JBL ≤3000; nothing saved; skip reasons mention temporary; fake store unchanged |
| `test_persistent_preference_is_saved` | persistent candidate | `memory_saved` equals the preference; fake add logged for that customer |
| `test_follow_up_conversation_context` | per-message LLM outputs, same session | wireless persists, then Sony, then budget 5000; results Sony ≤5000 wireless |
| `test_new_session_does_not_reuse_conversation_but_keeps_memories` | memory + two sessions | fresh session still applies Sony (from memory), not previous chat constraints |
| `test_memory_retrieval_failure_continues` | `fail_search=True` | `memory_status="error"`, products still recommended, writes skipped, diagnostic present |
| `test_product_api_failure_does_not_fabricate_products` | `FakeProductService(fail=True)` | `product_status="error"`, no recommendations, explicit catalogue error |
| `test_ollama_failure_is_reported_and_fallback_used` | `fail_json`/`fail_chat` | deterministic Sony/wireless ≤5000 result, `llm_status="error"`, response says LLM unavailable, memory writes error |
| `test_empty_message_is_rejected` | whitespace | `ok=False`, friendly response |
| `test_invalid_identifiers_are_rejected` | empty/invalid ids | `ok=False` |
| `test_heuristic_requirement_parser` (parametrised) | 3 messages | category/budget parsing for 3k, ₹2,299, no budget |

## 5. Execution workflow

```
make_deps(settings, catalogue, memory, llm, product_service)
make_graph(deps)  # fresh InMemorySaver
process_message(customer, session, message, graph=graph)
  -> real nodes + real memory rules + real recommendation engine
  -> fakes at memory/product/LLM boundaries
```

## 6. Input and output

Example:
```python
result = process_message("customer_001", "session-c",
                         "Show me JBL headphones under ₹3,000 this time.", graph=graph)
result.requirements["brand"]      # "JBL"
result.requirements["max_budget"] # 3000
result.memory_saved               # []
result.memory_writes["skipped"]   # includes "(temporary requirement)"
```

## 7. Interaction with other modules

Exercises `agent/graph`, `agent/nodes`, `agent/prompts`, `memory_manager`,
`recommendation_service`, `chatbot_service`. It replaces Mem0, Ollama and FastAPI
with fakes.

## 8. Important decisions

- Tests use exact customer messages in `requirements_by_message` so LLM extraction
  is deterministic; the real grounding logic still runs.
- Override tests assert both the resolved requirement and the recommendation
  constraints, catching precedence bugs that a text assertion would miss.
- Temporary-budget tests assert the *fake Mem0 store* is unchanged, not just the
  response text.
- Failure tests assert honest statuses/errors, not merely "no crash".
- Follow-up tests reuse one graph/session to exercise the checkpointer.

## 9. Example execution

```bash
uv run pytest tests/test_agent.py -q
```
