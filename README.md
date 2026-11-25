# Retail Memory Shopping Assistant

A small, working AI engineering POC that shows how a shopping assistant can
remember a customer's preferences **across conversations and application
restarts** using **Mem0 Cloud** through its official **MCP** server, local
**Ollama (Llama 3.1)** for language understanding, **LangGraph** for orchestration,
**FastAPI** for the product catalogue, deterministic Python for recommendations,
and **Streamlit** for the chat UI.

> The product catalogue is synthetic sample data. Prices and ratings are
> illustrative and do not represent live inventory or current market prices.

---

## 1. Problem statement

Most chat assistants forget everything when a conversation ends. A useful retail
assistant should remember stable preferences — for example "I usually prefer Sony
wireless headphones with noise cancellation" — and apply them the next time the
customer asks for a recommendation, without forcing them to repeat themselves.
At the same time, one-off requests such as "show me JBL under ₹3,000 this time"
must override the remembered brand **only for that purchase** and must not become
a permanent preference.

This POC demonstrates exactly that behaviour, end to end, with a real persistent
memory service.

## 2. Features

- Natural-language shopping requests understood by local Llama 3.1.
- Long-term preferences stored and retrieved through Mem0 Cloud MCP.
- Persistent vs temporary preference rules (temporary budgets/gifts are not saved).
- Explicit precedence: current request → conversation context → memory → defaults.
- Follow-up messages ("Only Sony", "Under ₹5,000") refine the same request.
- Deterministic catalogue filtering and ranking (the LLM never picks products).
- Product cards with name, brand, price, rating and features.
- Per-customer memory isolation (`customer_001`, `customer_002`, `customer_003`).
- Memory inspection and deletion in the sidebar.
- Honest service status: Mem0, Ollama and product API are checked independently.
- Works from the terminal too (`python -m app.main`), because the chatbot service
  is frontend-independent.

## 3. Architecture

```mermaid
flowchart LR
    U[Customer] --> S[Streamlit UI]
    S -->|process_message| CS[chatbot_service]
    CS --> G[LangGraph workflow]

    G -->|1 retrieve| MEM[memory_service]
    MEM --> MC[Mem0 MCP client]
    MC -->|Streamable HTTP + Token auth| M0[(Mem0 Cloud MCP)]

    G -->|2 understand + 5 explain| OL[Ollama client]
    OL --> L[(Llama 3.1)]

    G -->|3 candidates| PS[product_service]
    PS --> PA[FastAPI catalogue]
    PA --> J[(products.json)]

    G -->|4 filter + rank| REC[recommendation_service]

    G -->|6 save preferences| MM[memory_manager]
    MM --> MEM
```

Responsibilities:

| Layer | Responsibility |
|---|---|
| `app/ui/streamlit_app.py` | Render chat, customer selector, product cards, memory panel. No business logic. |
| `app/services/chatbot_service.py` | Validate input, invoke LangGraph, return `ChatResponse`; status and memory helpers. Imports no Streamlit. |
| `app/agent/*` | State, prompts, nodes, graph compilation, checkpointer. |
| `app/memory/memory_client.py` | Persistent MCP session, auth, tool discovery, schema adaptation, parsing. |
| `app/memory/memory_service.py` | Scoped add/search/list/update/delete, isolation, ownership checks. |
| `app/memory/memory_manager.py` | Persistent-vs-temporary rules, de-duplication, writes. |
| `app/products/*` | Product API HTTP client and candidate retrieval. |
| `app/recommendations/*` | Hard filtering and transparent ranking. |
| `app/llm/ollama_client.py` | Ollama chat/JSON calls with explicit errors. |
| `backend/*` | Product catalogue API and JSON data. |

A future FastAPI chatbot backend can call the same
`process_message(customer_id, session_id, message)` function; it is not
implemented in this POC by design.

## 4. Technology stack

| Component | Technology |
|---|---|
| Frontend | Streamlit 1.67 |
| Agent orchestration | LangGraph 1.2 |
| Local LLM | Ollama, `llama3.1` (configurable) |
| Memory | Mem0 Cloud, official MCP server |
| MCP client | Official Python `mcp` SDK (Streamable HTTP) |
| Product API | FastAPI 0.165 + Uvicorn |
| Product storage | JSON (28 synthetic products) |
| Recommendations | Pure Python |
| Tests | Pytest 9 |
| Configuration | `.env` + pydantic-settings |
| Environment/dependencies | uv (`pyproject.toml` + `uv.lock`, Python 3.13) |

## 5. Project structure

```
pyproject.toml                    # dependencies + dev group (uv source of truth)
uv.lock                           # locked dependency graph
.python-version                   # Python 3.13 pin for uv
requirements.txt                  # pip-compatible mirror
app/
  main.py                         # CLI smoke test
  config/settings.py              # environment configuration
  ui/streamlit_app.py             # Streamlit UI
  services/chatbot_service.py     # reusable chatbot entry point
  agent/{state,prompts,nodes,graph}.py
  memory/{memory_client,memory_service,memory_manager}.py
  products/{product_client,product_service}.py
  recommendations/recommendation_service.py
  llm/ollama_client.py
backend/
  api.py                          # FastAPI app, /health
  routes/products.py              # catalogue endpoints
  data/products.json              # synthetic catalogue
tests/                            # pytest suite
scripts/check_services.py         # diagnostics / live Mem0 probe
docs/explanations/                # per-file + architecture documentation
```

Every Python file has a detailed explanation in `docs/explanations/`; start at
[docs/explanations/README.md](docs/explanations/README.md).

## 6. Data flow

1. The customer sends a message in Streamlit.
2. `chatbot_service.process_message` validates IDs and invokes the compiled graph
   with `thread_id = session_id`.
3. `retrieve_memory` calls Mem0 MCP `search_memories` scoped with
   `user_id = customer_id`.
4. `understand_request` asks Ollama for JSON requirements, validates them with
   Pydantic, verifies each field against the literal message, then resolves
   precedence.
5. `search_products` fetches a candidate superset from FastAPI (category/budget).
6. `recommend_products` filters and ranks deterministically. Memory-derived
   wireless/noise-cancellation filters are relaxed only if the result would
   otherwise be empty, and the relaxation is shown to the customer.
7. `generate_response` asks Ollama to explain the selected products. It is given
   only catalogue data and told not to invent anything.
8. `update_memory` asks Ollama for possible preferences, applies memory rules,
   de-duplicates, and writes accepted persistent preferences through Mem0 MCP.
9. Streamlit renders the reply, product cards and the real memory-write result.

## 7. Memory workflow

```mermaid
sequenceDiagram
    participant UI as Streamlit
    participant CS as chatbot_service
    participant G as LangGraph
    participant MS as memory_service
    participant MCP as Mem0 MCP
    participant MM as memory_manager

    UI->>CS: process_message(customer, session, message)
    CS->>G: invoke(thread_id=session)
    G->>MS: search_memories(customer, message)
    MS->>MCP: tool search_memories (user_id filter)
    MCP-->>MS: matching memories
    MS-->>G: MemoryResult(ok, records)
    Note over G: resolve requirements using<br/>current > context > memory > defaults
    G->>MM: candidate preferences
    MM->>MM: keep persistent, drop temporary/duplicates
    MM->>MS: add_memory(customer, text)
    MS->>MCP: tool add_memory (user_id)
    MCP-->>MS: created memory
    MS-->>G: saved / failure
    G-->>CS: response + memory_writes
    CS-->>UI: ChatResponse
```

Rules:
- **Persistent:** preferred brands, product types, feature preferences, usual
  budget range, repeatedly expressed preferences.
- **Temporary:** a budget for this purchase, a one-off brand, a gift, a trip, a
  temporary colour.
- Temporary items are never automatically promoted to memory.
- If retrieval fails, the assistant continues without personalisation and does
  **not** save (de-duplication cannot be guaranteed). It never claims success.

Customer isolation uses Mem0's `user_id` scope. Reads and writes always include
the customer ID, and any returned record naming a different user is discarded.
Update/delete verify ownership first.

## 8. Mem0 Cloud MCP configuration

1. Create a Mem0 Platform account and get an API key from
   <https://app.mem0.ai/dashboard/api-keys>.
2. Create your local env file from the template (`.env` is git-ignored):
   ```bash
   cp .env.example .env
   ```
3. Put the key in `.env` at the project root:
   ```dotenv
   MEM0_MCP_URL=https://mcp.mem0.ai/mcp
   MEM0_API_KEY=your-key-here
   ```
   `.env` is read from the **current working directory**, so start the app from
   the project root. Restart Streamlit after editing it (settings are cached per
   process).
4. The client connects over **Streamable HTTP** and sends
   `Authorization: Token <MEM0_API_KEY>` — the API-key method documented by Mem0.
   Tool names and input schemas are discovered at runtime; nothing is guessed.
5. Verify:
   ```bash
   uv run python scripts/check_services.py            # connection status
   uv run python scripts/check_services.py --tools    # live tools + schemas
   uv run python scripts/check_services.py --live-memory-test
   ```
   The live probe adds a labelled memory, searches it, verifies another customer
   cannot retrieve it, then deletes it.

No Mem0 SDK or direct REST API is used by application code, and there is no local
Mem0 server.

## 9. Ollama setup

```bash
# macOS app (or: ollama serve on a standard install)
open -a Ollama
ollama pull llama3.1
ollama list
```

Configure in `.env` if needed:
```dotenv
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1
```

If Ollama or the model is missing, the assistant uses a deterministic extraction
fallback and clearly says the LLM is unavailable; it never pretends a generated
answer succeeded.

## 10. Python environment and dependencies (uv)

Requires Python 3.11+; `.python-version` pins 3.13 and uv installs it if needed.
Dependencies live in `pyproject.toml`, and `uv.lock` pins the full resolved set.

```bash
uv sync                       # creates .venv and installs locked deps (incl. dev)
cp .env.example .env          # then set MEM0_API_KEY
uv run python --version       # sanity check inside the project environment
```

Useful uv commands:

| Command | Purpose |
|---|---|
| `uv sync` | Create/update `.venv` from `uv.lock` (includes the dev group) |
| `uv sync --no-dev` | Runtime dependencies only |
| `uv lock` | Re-resolve `uv.lock` after changing `pyproject.toml` |
| `uv lock --upgrade` | Upgrade locked versions (review/test afterwards) |
| `uv add <package>` | Add a dependency and update the lock |
| `uv run <cmd>` | Run a command inside the project environment |
| `uv tree` | Inspect the resolved dependency graph |

`requirements.txt` is kept as a pip-compatible mirror for environments without uv
(`pip install -r requirements.txt`); `pyproject.toml` + `uv.lock` are authoritative.

## 11. Running the application

Start the services in separate terminals:

```bash
# 1) Product catalogue API
uv run uvicorn backend.api:app --reload --port 8000

# 2) Streamlit chatbot (Ollama must already be running)
uv run streamlit run app/ui/streamlit_app.py
```

Or use the helper script, which starts the API if it is not already running:

```bash
./run.sh
```

Open <http://localhost:8501>. Mem0 Cloud is remote; no local Mem0 process is
needed.

Terminal-only check:
```bash
uv run python -m app.main --customer customer_001 --message "Suggest headphones for me"
```

Opening the Streamlit page alone does not prove the system works. Check the
sidebar: Mem0 MCP, Ollama and the product API each show their real status.

## 12. Testing

```bash
uv run pytest                 # 62 unit / mocked-MCP / workflow / e2e tests
uv run pytest -m live         # real Mem0 tests; requires MEM0_API_KEY
```

Test files:
- `test_products.py` — product API endpoints and filters (FastAPI TestClient).
- `test_recommendations.py` — deterministic filtering/ranking.
- `test_memory.py` — MCP tool discovery, schema adaptation, customer isolation,
  ownership checks, memory rules. A fake MCP session exercises the real client
  plumbing. The `live` test contacts Mem0 Cloud.
- `test_agent.py` — new/existing customers, override precedence, temporary budget,
  follow-up context, memory/product/Ollama failures.
- `test_end_to_end.py` — full workflow through the real FastAPI catalogue, real
  recommendation engine and real graph; Mem0/Ollama faked.

Live Mem0 verification is deliberately separate from mocked tests. Do not treat a
green mocked suite as proof that the live service works.

## 13. Sample conversations

| Turn | Customer message | What happens |
|---|---|---|
| 1 | "I usually prefer Sony headphones with noise cancellation." | Stable preference extracted and saved through Mem0 MCP. |
| 2 (new session) | "Suggest headphones for me." | Preference retrieved; Sony ANC headphones recommended. |
| 3 | "Show me JBL headphones under ₹3,000 this time." | Current JBL + budget overrides Sony; budget is temporary and not saved. |
| 4 | "Only wireless." | Refines the same request using conversation context. |
| 5 (customer_002) | "Suggest headphones for me." | Independent memory scope; no access to customer_001's preferences. |

## 14. Verification status (honest)

| Area | Status |
|---|---|
| Product API (28 products, filters, 404) | TESTED |
| Recommendation engine | TESTED |
| LangGraph workflow + follow-ups | TESTED |
| Streamlit UI (AppTest, real chat turn) | TESTED |
| Ollama + Llama 3.1 real extraction/generation | VERIFIED LOCALLY |
| Mem0 MCP connection/add/search/isolation | **BLOCKED — no `MEM0_API_KEY` in this environment** |
| Mem0 live end-to-end persistence | **BLOCKED — same reason** |

The Mem0 client is implemented against the official documented endpoint, auth
method and tools, but it has not been executed against the live account here.
Once a key is set, run `scripts/check_services.py --live-memory-test` and
`uv run pytest -m live` to convert those BLOCKED rows into verified results.

## 15. Known limitations

- Mem0 Cloud extraction can be asynchronous; a newly added memory may not appear
  in an immediate search.
- `update_memory` / delete-all capabilities depend on the hosted tool schema;
  failures are reported rather than hidden.
- Conversation context is held in process memory (LangGraph `InMemorySaver`);
  restarting Streamlit clears the active thread. Long-term Mem0 memory persists.
- Preference extraction relies on Llama 3.1 and is grounded by deterministic
  checks; unusual phrasing may be missed rather than guessed.
- Brand matching requires the brand to appear in the message and catalogue.
- Memory-derived feature filters are soft and may be relaxed to avoid empty
  results; the relaxation is always disclosed.
- The catalogue is synthetic, and the app has no authentication — customer IDs
  are a demonstration of memory scoping, not a security boundary.

## 16. Future improvements

- Add a FastAPI `POST /chat` that calls the existing chatbot service (no graph
  rewrite needed).
- Add authentication and map real user accounts to Mem0 `user_id`s.
- Add streaming responses in Streamlit.
- Add more categories and richer catalogue metadata.
- Add an explicit memory-edit UI using the supported `update_memory` tool.
- Add retries/metrics for MCP calls and a persistent LangGraph checkpointer if
  conversation context must survive restarts.
