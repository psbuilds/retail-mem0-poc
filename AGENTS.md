# AGENTS.md — Retail Memory Shopping Assistant

## 1. Project objective

Build a small, working Retail Memory Shopping Assistant POC that demonstrates
persistent, personalised customer memory across conversations and application
restarts.

The application must:
- Understand a natural-language shopping request with local Ollama (Llama 3.1).
- Retrieve relevant long-term preferences from Mem0 Cloud through its official
  MCP server.
- Recommend products with deterministic Python filtering and ranking.
- Save stable long-term preferences (and only those) through Mem0 MCP.
- Apply precedence so the current explicit request always wins.
- Never invent products, prices, memories or live-service success.

This is a technical POC, not a production platform. Keep it understandable.

## 2. Agreed technology stack

| Component | Technology | Responsibility |
|---|---|---|
| UI | Streamlit | Chat, customer selection, product cards, memory inspection |
| Agent workflow | LangGraph | One sequential shopping-turn graph |
| Local LLM | Ollama / `llama3.1` | Request understanding, preference extraction, response text |
| Memory | Mem0 Cloud | Persistent customer memory |
| Memory transport | Official Mem0 MCP server (`https://mcp.mem0.ai/mcp`) | Memory tools over Streamable HTTP |
| Product backend | FastAPI | Synthetic catalogue API |
| Product storage | JSON (`backend/data/products.json`) | 28 sample products |
| Recommendations | Python | Deterministic filtering and ranking |
| Tests | Pytest | Unit, mocked-MCP, workflow, e2e, optional live |

Do not replace these technologies without explaining why and getting approval.

## 3. Architecture principles

- Streamlit is the only frontend in this POC. There is **no** chatbot FastAPI
  backend; FastAPI serves only the product catalogue.
- `app/services/chatbot_service.py` is the single chatbot entry point and imports
  no Streamlit code. A future `POST /chat` can call `process_message` unchanged.
- LangGraph, memory, product, recommendation and LLM modules must never import
  Streamlit.
- All application memory operations go through the Mem0 MCP client. Do not use
  the Mem0 SDK or direct REST calls.
- Recommendation selection is deterministic Python. The LLM may only explain
  products already selected.
- Long-term memory (Mem0) and conversation context (LangGraph checkpoint) are
  separate: clearing a chat must not delete preferences.

## 4. Project structure

```
retail-memory-assistant/
├── AGENTS.md
├── README.md
├── pyproject.toml                  # dependencies + dev group (uv source of truth)
├── uv.lock                         # locked resolved dependency graph
├── .python-version                 # pins Python 3.13 for uv
├── requirements.txt                # pip-compatible mirror (keep in sync)
├── pytest.ini
├── run.sh
├── .env.example
├── .gitignore
├── app/
│   ├── main.py                     # terminal smoke test / interactive CLI
│   ├── config/settings.py          # env configuration (pydantic-settings)
│   ├── ui/streamlit_app.py         # Streamlit UI (calls chatbot service only)
│   ├── services/chatbot_service.py # reusable chatbot + status/memory helpers
│   ├── agent/
│   │   ├── state.py                # AgentState TypedDict
│   │   ├── prompts.py              # all LLM prompts
│   │   ├── nodes.py                # 7 workflow nodes + validation/grounding
│   │   └── graph.py                # graph wiring + checkpointer
│   ├── memory/
│   │   ├── memory_client.py        # MCP session, tool discovery, invocation
│   │   ├── memory_service.py       # scoped add/search/list/update/delete
│   │   └── memory_manager.py       # persistent vs temporary rules, dedupe
│   ├── products/
│   │   ├── product_client.py       # HTTP client for FastAPI
│   │   └── product_service.py      # candidate fetch + brand cache + health
│   ├── recommendations/recommendation_service.py
│   └── llm/ollama_client.py        # Ollama chat/JSON client
├── backend/
│   ├── api.py                      # FastAPI app + /health
│   ├── routes/products.py          # /products, /products/search, /products/{id}
│   └── data/products.json          # synthetic catalogue
├── tests/                          # pytest suite (see §10)
├── scripts/check_services.py       # diagnostics + optional live Mem0 probe
└── docs/explanations/              # mandatory per-file + architecture docs
```

## 5. Module responsibilities and separation of concerns

- **UI** renders state and calls `chatbot_service`. It contains no memory,
  product, recommendation or prompt logic.
- **chatbot_service** validates identifiers, invokes the compiled graph, and
  returns a `ChatResponse`. It also exposes status and memory-management helpers
  so the UI never talks to MCP directly.
- **agent** owns state, prompts, node order and LangGraph compilation.
- **memory_client** owns MCP transport/auth/tool schemas. It exposes a
  synchronous facade and logs no credentials.
- **memory_service** owns customer scoping and result parsing. It hides MCP
  tool names/payload shapes from the rest of the app.
- **memory_manager** owns what may be remembered and de-duplication.
- **product_client** owns HTTP details for the catalogue API.
- **product_service** fetches a candidate superset and caches brand names.
- **recommendation_service** owns all filtering/ranking.
- **ollama_client** owns Ollama HTTP and safe JSON parsing.
- **backend** owns catalogue storage and filtering; application code never reads
  `products.json`.

## 6. Memory rules (Mem0 Cloud + MCP)

Verified integration facts (from official docs, checked before implementation):
- Endpoint: `https://mcp.mem0.ai/mcp`, Streamable HTTP transport.
- Auth: `Authorization: Token <MEM0_API_KEY>` (documented API-key method).
- Tools exposed by the hosted server include `add_memory`, `search_memories`,
  `get_memories`, `get_memory`, `update_memory`, `delete_memory`,
  `delete_all_memories`, `delete_entities`, `list_entities`.
- Customer scope: Mem0 `user_id`.

Implementation rules:
- Use the Python MCP SDK client (`mcp`) and Streamable HTTP transport.
- Discover tools and their input schemas at runtime (`list_tools`) and adapt
  arguments to the discovered schema; never guess required parameters.
- Always scope reads/writes with `user_id = customer_id`. Refuse an operation if
  the server schema accepts neither `user_id` nor `filters`.
- Drop any returned record that names a different `user_id` (defence in depth).
- Verify ownership before update/delete.
- Keep one session per process behind a background event-loop task; do not open a
  new MCP session on every Streamlit rerun.
- Never log `MEM0_API_KEY`.
- Never present mocked results as live results.

What to store:
- **Persistent:** preferred brands, preferred product type, feature preferences
  (wireless, noise cancellation, lightweight), usual budget range, repeatedly
  expressed preferences.
- **Temporary:** budget for this purchase, a one-off brand request, gifts, trips,
  temporary colours/features.

Rules:
- Do not store every message. Store at most 3 explicit preferences per turn.
- Do not store a candidate labelled `temporary`, containing temporary wording
  ("this time", "for this purchase", "gift", "trip"), or a near-duplicate.
- Do not infer a permanent preference from one ambiguous request.
- If retrieval fails, do not write (dedupe cannot be guaranteed) and say so.
- Never claim a preference was remembered unless the MCP call returned success.

Precedence: **current explicit instruction > current conversation context >
long-term memory > application defaults.**

Soft-memory rule: values filled only from memory (`wireless`,
`noise_cancellation`) may be relaxed if they would otherwise produce zero
results for an explicit request. The relaxation must be reported to the customer.
Explicit current values are never relaxed.

## 7. LangGraph workflow

One graph, sequential nodes:

1. `prepare_turn` — append user message, reset per-turn fields, keep history.
2. `retrieve_memory` — `search_memories` scoped to the customer.
3. `understand_request` — Ollama JSON extraction → pydantic validation →
   deterministic grounding → precedence resolution.
4. `search_products` — fetch candidate superset (category + budget) from FastAPI.
5. `recommend_products` — deterministic filter/rank; soft-memory relaxation.
6. `generate_response` — Ollama explains only the selected products.
7. `update_memory` — Ollama preference extraction → memory rules → Mem0 writes.

State: `customer_id`, `session_id`, `user_message`, `conversation_history`,
`customer_memories`, `memory_status`, `memory_error`, `requirements`,
`previous_requirements`, `resolved_requirements`, `memory_applied`,
`relaxed_preferences`, `products`, `product_status`, `recommendations`,
`response`, `llm_status`, `llm_error`, `memory_candidates`, `memory_writes`,
`errors`.

Checkpointing: LangGraph `InMemorySaver`, `thread_id = session_id`. History is
capped at 12 messages. Conversation context is intentionally in-process; long-term
memory survives restarts.

## 8. Product recommendation rules

Supported filters:
- category, brand, maximum budget, wireless, noise cancellation;
- required features, excluded features, excluded brands, excluded product IDs.

Ranking: rating descending, then price ascending, then product ID for stability.
Return an empty list when nothing matches. Never substitute suggestions that are
not in the catalogue. Only recommend products returned by the product API.

## 9. Coding conventions

- Clear names, small functions, type hints, explicit error handling.
- No unnecessary abstractions, databases or infrastructure.
- Config and secrets only from environment variables.
- Keep prompts in `app/agent/prompts.py`; keep Ollama HTTP in
  `app/llm/ollama_client.py`.
- Follow existing import/format style; comment non-obvious logic (precedence,
  grounding, soft-memory relaxation, MCP session ownership).

## 10. Running, testing and debugging

Setup (uv is the primary workflow; `pyproject.toml` + `uv.lock` are authoritative):
```bash
uv sync                 # creates .venv from uv.lock, includes dev group
cp .env.example .env    # set MEM0_API_KEY
ollama list             # ensure llama3.1 is installed (ollama pull llama3.1)
```
For environments without uv: `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`.

Run:
```bash
uv run uvicorn backend.api:app --reload --port 8000
uv run streamlit run app/ui/streamlit_app.py
./run.sh               # starts both, or reuses a running API
```

Diagnostics:
```bash
uv run python scripts/check_services.py
uv run python scripts/check_services.py --tools
uv run python scripts/check_services.py --live-memory-test --customer customer_001
uv run python -m app.main --customer customer_001 --message "Suggest headphones"
```

Tests:
```bash
uv run pytest                       # unit + mocked MCP + e2e
uv run pytest -m live               # real Mem0 (needs MEM0_API_KEY)
```

Dependency changes: edit `pyproject.toml` (or `uv add`), run `uv lock`, then
`uv sync`; update `requirements.txt` as the pip mirror.
Live tests write a probe memory to the Mem0 account and clean it up.

Debugging order: product API health → Ollama tags → Mem0 status/tools → one CLI
message → Streamlit.

## 11. Error handling requirements

Report, never hide:
- MCP connection/auth/tool/timeout failures (memory unavailable; continue
  without personalisation, do not fabricate preferences or save success).
- Ollama unavailable (fall back to deterministic extraction and an explicit
  "LLM unavailable" response; never claim generation succeeded).
- Product API unavailable (no products; explicit message).
- Invalid or empty LLM JSON (validation error → fallback).
- Empty recommendation results (explain, suggest loosening constraints).
- Missing customer ID/session ID, empty message, invalid identifiers.

## 12. Documentation requirements (mandatory)

- Every Python source file has a Markdown explanation in `docs/explanations/`
  covering purpose, imports, classes, functions/parameters/returns, execution
  flow, inputs/outputs, module interactions, decisions and examples.
- Every `__init__.py` package marker is documented too.
- Maintain `docs/explanations/README.md`, `overall_architecture.md`,
  `application_workflow.md`, `memory_workflow.md`, `langgraph_workflow.md`,
  `recommendation_workflow.md`, `data_flow.md`, and
  `configuration_and_project_files.md`.
- Update docs in the same phase as code changes. Describe the actual code.
- Use Mermaid diagrams where useful.

## 13. Known constraints

- Mem0 Cloud has no key in this environment, so live MCP operations remain
  unverified here. The code is implemented against the documented tools and
  schemas; live tests are ready to run once a key is set.
- Mem0's extraction is hosted and may be asynchronous; a just-added memory may
  not appear in an immediate search.
- Conversation context is in-process; restarting Streamlit loses the current
  thread, not long-term memory.
- The catalogue is synthetic sample data, not live inventory or pricing.

## 14. Final principle

Correctness, simplicity, honesty about failures, and clear explanations over
unnecessary complexity. Do not claim an integration works unless it was actually
verified.
