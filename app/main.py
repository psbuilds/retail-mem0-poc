"""Non-UI entry point for a quick chatbot smoke test.

Streamlit is the POC's frontend, but the chatbot service is frontend-agnostic.
This module demonstrates that by talking to it from the terminal::

    python -m app.main --customer customer_001 --message "Suggest headphones"

Run without ``--message`` for a small interactive loop.  This is also useful for
verifying the workflow when the Streamlit page is not running.
"""

from __future__ import annotations

import argparse
import json

from app.services.chatbot_service import ChatResponse, process_message


def _print_response(result: ChatResponse) -> None:
    """Print a chat response in a readable, terminal-friendly way."""
    print(f"\nAssistant: {result.response}")
    if result.recommendations:
        print("\nRecommendations (from the deterministic engine):")
        for product in result.recommendations:
            print(
                f"  - {product['name']} | ₹{product['price']:,} | "
                f"rating {product['rating']} | {product['product_id']}"
            )
    route = result.route or {}
    if route:
        print(
            f"[route={route.get('intent')} | source={route.get('source')} | "
            f"kind={result.response_kind} | tokens={route.get('input_tokens', "-")}]"
        )
    print(
        f"\n[memory={result.memory_status} | llm={result.llm_status} | "
        f"products={result.product_status}]"
    )
    if result.memory_saved:
        print(f"[saved to Mem0: {json.dumps(result.memory_saved)}]")
    if result.memory_pending:
        print(f"[pending in Mem0 (not confirmed): {json.dumps(result.memory_pending)}]")
    if result.errors:
        print("[errors]")
        for error in result.errors:
            print(f"  - {error}")


def main() -> int:
    """Parse arguments and run one message or an interactive session."""
    parser = argparse.ArgumentParser(description="Retail Memory Shopping Assistant CLI")
    parser.add_argument("--customer", default="customer_001", help="Customer ID (Mem0 user_id)")
    parser.add_argument("--session", default="cli-session-1", help="Session/thread ID")
    parser.add_argument("--message", help="Single message to send; omit for interactive mode")
    args = parser.parse_args()

    if args.message:
        _print_response(process_message(args.customer, args.session, args.message))
        return 0

    print(
        f"Shopping assistant for {args.customer} (session {args.session}). "
        "Type 'exit' to quit.\nNote: conversation context is lost when this process ends; "
        "Mem0 preferences persist."
    )
    while True:
        try:
            message = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if message.casefold() in {"exit", "quit"}:
            break
        if not message:
            continue
        _print_response(process_message(args.customer, args.session, message))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
