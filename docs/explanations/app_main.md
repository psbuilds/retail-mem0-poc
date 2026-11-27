# `app/main.py`

## 1. File purpose

A terminal entry point that exercises the chatbot without Streamlit. It proves
the service layer is frontend-independent and gives a quick way to debug one
message when the UI is not running.

## 2. Imports and dependencies

| Import | Why |
|---|---|
| `argparse` | Parse `--customer`, `--session`, `--message` |
| `json` | Pretty-print saved memory texts |
| `app.services.chatbot_service.process_message` | Reusable chatbot entry point |
| `ChatResponse` | Type for `_print_response` |

It does **not** import Streamlit, memory clients, LangGraph or product modules.

## 3. Classes

No classes are defined.

## 4. Functions and methods

### `_print_response(result: ChatResponse) -> None`

Prints the assistant reply, recommendations (name, price, rating, product ID),
the route (`intent`, `source`, `response_kind`, Jev input tokens), the service
status line, saved memories, **pending** memories and errors.

- Parameters: `result` — a `ChatResponse`.
- Returns: nothing.
- Internal operations: iterates `result.recommendations`, formats prices with
  thousands separators, reads `route`, `response_kind`, `memory_saved`,
  `memory_pending`, `errors`, `memory_status`, `llm_status`, `product_status`.
  Pending writes are printed separately so an unconfirmed write is never shown as
  saved.
- Errors: none raised; it only prints.

### `main() -> int`

- Builds the argument parser and reads arguments.
- If `--message` is supplied, sends exactly one message and exits 0.
- Otherwise runs an interactive loop until `exit`, `quit`, EOF or Ctrl+C.
- Empty input is ignored.
- Returns `0`.

Defaults: customer `customer_001`, session `cli-session-1`.

## 5. Execution workflow

```
main()
  parse args
  if --message:
      process_message(customer, session, message)
      _print_response(...)
  else:
      print instructions
      loop: input -> process_message -> _print_response
```

`process_message` runs the entire LangGraph workflow, so the CLI exercises the
same code path as the UI.

## 6. Input and output

Input:
```bash
python -m app.main --customer customer_001 --message "Suggest headphones for me"
```

Output (illustrative):
```
Assistant: Here are some Sony headphones that fit your request.

Recommendations (from the deterministic engine):
  - Sony WH-CH520 Wireless On-Ear Headphones | ₹4,490 | rating 4.3 | HP003

[route=product_search | source=rules | kind=product | tokens=-]
[memory=success | llm=success | products=success]
[pending in Mem0 (not confirmed): ["I usually prefer Sony headphones"]]
```

## 7. Interaction with other modules

Calls only `chatbot_service.process_message`. All memory, product, LLM and graph
work happens behind that function.

## 8. Important implementation decisions

- No interactive `input` is used when `--message` is given, so the module works in
  scripts and CI.
- Conversation context in the interactive loop lasts only for the process; Mem0
  preferences persist. The prompt text says so.
- No UI formatting library is used, keeping the module dependency-free.

## 9. Example execution

```bash
uv run python -m app.main \
  --customer customer_001 \
  --session cli-demo \
  --message "Show me JBL headphones under ₹3,000 this time."
```

In an environment without `MEM0_API_KEY`, the output includes a memory failure
note and `memory=not_configured`, while product recommendations still work.
