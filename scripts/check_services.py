"""Service and memory diagnostics for the POC.

Usage::

    python scripts/check_services.py                 # status of all services
    python scripts/check_services.py --tools         # also list Mem0 MCP tools
    python scripts/check_services.py --live-memory-test --customer customer_001

``--live-memory-test`` performs REAL writes against your Mem0 account through
MCP: it adds a clearly-labelled probe preference, searches for it, checks that a
different customer cannot retrieve it, and then deletes the probe.  It is never
run automatically.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Running this file directly puts scripts/ on sys.path, not the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.settings import get_settings  # noqa: E402
from app.llm.ollama_client import ollama_status  # noqa: E402
from app.memory.memory_client import Mem0MCPClient, Mem0MCPError  # noqa: E402
from app.memory.memory_service import get_memory_service  # noqa: E402
from app.products.product_service import product_api_status  # noqa: E402


def _print_header(title: str) -> None:
    print(f"\n=== {title} ===")


def check_status() -> int:
    """Print Mem0, Ollama and product API status. Returns an exit code."""
    failures = 0

    _print_header("Mem0 Cloud MCP")
    memory_status = get_memory_service().status()
    print(f"configured: {memory_status.configured}")
    print(f"connected : {memory_status.connected}")
    print(f"message   : {memory_status.message}")
    if memory_status.tools:
        print(f"tools     : {', '.join(memory_status.tools)}")
    if not memory_status.connected:
        failures += 1

    _print_header("Ollama")
    ok, message = ollama_status()
    print(message)
    if not ok:
        failures += 1

    _print_header("Product API")
    ok, message = product_api_status()
    print(message)
    if not ok:
        failures += 1

    _print_header("Query router")
    settings = get_settings()
    if settings.jev_configured and settings.router_use_jev:
        print(f"Jev configured ({settings.jev_model}); routing uses Jev + guards")
    else:
        print(
            "Deterministic rules mode: TYPESAFE_API_KEY is not set or "
            "ROUTER_USE_JEV=false"
        )

    return 1 if failures else 0


def list_tools() -> int:
    """Discover and print the live Mem0 MCP tools and input schemas."""
    client = Mem0MCPClient()
    try:
        tools = client.list_tools(refresh=True)
    except Mem0MCPError as exc:
        print(f"MCP tool discovery failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps([tool.__dict__ for tool in tools], indent=2, default=str))
    return 0


def live_memory_test(customer_id: str) -> int:
    """Run a real add/search/isolation/delete round-trip through Mem0 MCP."""
    service = get_memory_service()
    other_customer = f"{customer_id}_isolation_probe"
    probe = (
        "POC probe: prefers test-brand headphones with wireless connectivity. "
        f"created {datetime.now(timezone.utc).isoformat()}"
    )

    print(f"Adding probe memory for {customer_id} ...")
    added = service.add_memory(customer_id, probe, metadata={"source": "check_services_probe"})
    if not added.ok:
        print(f"FAIL add_memory: {added.error}")
        return 1
    if not added.event_id:
        print("FAIL: add_memory returned no event id, so completion cannot be verified")
        return 1

    # Mem0 writes are asynchronous: wait for the real event instead of assuming
    # the memory exists as soon as the tool accepts it.
    print(f"Waiting for Mem0 event {added.event_id} ...")
    event = service.wait_for_event(added.event_id)
    if not event.ok:
        print(f"FAIL memory event: status={event.status} error={event.error}")
        return 1
    print(f"OK add_memory event {event.status}")

    print("Searching for the probe as the same customer ...")
    found = service.search_memories(customer_id, "test-brand headphones", limit=20)
    if not found.ok:
        print(f"FAIL search_memories: {found.error}")
        return 1
    same_customer_texts = [record.text for record in found.records]
    if not any("POC probe" in text for text in same_customer_texts):
        print("FAIL: probe memory was not returned to the same customer")
        print(json.dumps(same_customer_texts, indent=2))
        return 1
    print("OK search_memories returned the probe")

    print(f"Checking isolation by searching as {other_customer} ...")
    other = service.search_memories(other_customer, "POC probe test-brand headphones", limit=20)
    if not other.ok:
        print(f"FAIL isolation search: {other.error}")
        return 1
    if any("POC probe" in record.text for record in other.records):
        print("FAIL: another customer could retrieve the probe memory")
        return 1
    print("OK isolation: other customer did not retrieve the probe")

    probe_record = next(
        record for record in found.records if "POC probe" in record.text
    )
    if probe_record.memory_id:
        deleted = service.delete_memory(customer_id, probe_record.memory_id)
        if deleted.ok:
            print("OK delete_memory cleaned up the probe")
        else:
            print(f"WARN cleanup failed, delete manually: {deleted.error}")
    else:
        print("WARN memory id missing; probe left in the account for manual cleanup")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Check POC services")
    parser.add_argument("--tools", action="store_true", help="List live Mem0 MCP tools")
    parser.add_argument(
        "--live-memory-test",
        action="store_true",
        help="Run a real Mem0 add/search/isolation/delete probe",
    )
    parser.add_argument("--customer", default="customer_001")
    args = parser.parse_args()

    if args.live_memory_test:
        return live_memory_test(args.customer)
    if args.tools:
        return list_tools()
    return check_status()


if __name__ == "__main__":
    raise SystemExit(main())
