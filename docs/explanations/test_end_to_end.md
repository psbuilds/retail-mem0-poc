# `tests/test_end_to_end.py`

## 1. File purpose

End-to-end workflow tests that use:

- the real LangGraph graph,
- the real FastAPI catalogue through `TestClient`,
- the real `ProductService` and `ProductAPIClient`,
- the real recommendation engine,
- the real memory rules,

while replacing only Mem0 and Ollama with deterministic in-process fakes.

It also verifies that the chatbot service does not import Streamlit.

## 2. Imports and dependencies

`subprocess`, `sys`, `pytest`, `TestClient`, `WorkflowDeps`, `build_graph`,
`Settings`, `MemoryManager`, `ProductAPIClient`, `ProductService`,
`process_message`, `backend.api.app`, and the fakes from `tests/conftest.py`.

## 3. Fixtures

### `real_product_service`
```python
ProductService(ProductAPIClient(
    base_url="http://testserver",
    http_client=TestClient(product_app),
))
```
Calls go through the actual FastAPI route code and real catalogue JSON, with no
network port.

## 4. Functions

### `build_e2e_graph(real_product_service, memory, llm, settings)`
Assembles `WorkflowDeps` and compiles a fresh graph. Mem0/Ollama are fakes; product
and recommendation code are real.

## 5. Tests

### `test_scenario_save_then_recall_across_sessions`
1. Session 1: "I usually prefer Sony headphones with noise cancellation."
2. Preference candidate is persistent → `memory_saved` contains it.
3. Session 2 (new thread, same customer): "Suggest headphones for me."
4. Memory is read from the fake store → all recommendations Sony with ANC.
5. A non-empty generated response is returned.

This mirrors POC scenarios 1 and 2 (with mocked Mem0, clearly labelled).

### `test_scenario_override_and_temporary_budget`
Memory holds Sony/ANC. Message asks for JBL under ₹3,000 "this time".
- Resolved brand/budget come from the current message.
- Recommendations are exactly the two JBL models under ₹3,000 (the memory-derived
  ANC filter is relaxed and disclosed).
- Nothing is saved; the temporary candidate is rejected.

### `test_scenario_customer_isolation`
- `customer_001` receives Sony-constrained recommendations.
- `customer_002` has an empty memory scope, receives non-Sony options, and is not
  brand-constrained. This demonstrates independent scopes in the fake store and
  the workflow; live Mem0 isolation is covered separately by the live test.

### `test_empty_catalogue_result_is_explained`
Sony headphones under ₹500: no matches → empty recommendations and a response
saying no catalogue product matches. No invented alternative.

### `test_chatbot_service_does_not_import_streamlit`
Runs:
```bash
python -c "import sys; import app.services.chatbot_service; print('streamlit' in sys.modules)"
```
and requires `False`. This enforces the future-FastAPI-compatibility requirement.

## 6. Execution workflow

```
TestClient(product_app) -> ProductAPIClient -> ProductService
process_message -> graph -> nodes
  memory: FakeMemoryService
  LLM: FakeLLM
  products: real FastAPI + JSON
  recommendation: real
```

## 7. Input and output

Input:
```python
process_message("customer_001", "conversation-3",
                "Show me JBL headphones under ₹3,000 this time.", graph=graph)
```

Output:
```python
result.recommendations == [
  {"name": "JBL Tune 520BT Wireless On-Ear Headphones", ...},
  {"name": "JBL Tune 510BT Wireless On-Ear Headphones", ...},
]
result.memory_saved == []
```

## 8. Interaction with other modules

Imports almost every application layer except the UI and live memory transport. It
is the closest automated test to the real chat path while remaining credential-free.

## 9. Important decisions

- Mem0 and Ollama are faked for determinism; every other layer is real.
- The tests are explicitly named as mocked-memory scenarios, so they cannot be
  mistaken for live Mem0 verification.
- A new session id is used for the recall test, proving the preference comes from
  memory rather than the LangGraph thread.
- The Streamlit import check is a subprocess so prior test imports cannot mask a
  violation.

## 10. Example execution

```bash
uv run pytest tests/test_end_to_end.py -q
```
