"""Memory layer tests.

Most tests use a fake MCP session injected into the real ``Mem0MCPClient``.  This
exercises the actual connection/queue/schema-adaptation code without contacting
Mem0.  Tests marked ``live`` hit the real service and require MEM0_API_KEY.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

from app.config.settings import Settings
from app.memory.memory_client import (
    Mem0AuthenticationError,
    Mem0MCPClient,
    Mem0MCPError,
    Mem0NotConfiguredError,
)
from app.memory.memory_manager import MemoryManager, PreferenceCandidate
from app.memory.memory_service import MemoryService, extract_raw_records


# ---------------------------------------------------------------------------
# Fake MCP session (mocked transport)
# ---------------------------------------------------------------------------
class FakeTool:
    def __init__(self, name: str, schema: dict[str, Any], description: str = "") -> None:
        self.name = name
        self.description = description
        self.input_schema = schema


class FakeListTools:
    def __init__(self, tools: list[FakeTool]) -> None:
        self.tools = tools


class FakeText:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeResult:
    def __init__(self, payload: dict[str, Any]) -> None:
        import json

        self.content = [FakeText(json.dumps(payload))]
        self.structured_content = None
        self.is_error = False


def default_tools() -> list[FakeTool]:
    return [
        FakeTool(
            "add_memory",
            {
                "type": "object",
                "properties": {
                    "messages": {"type": "array"},
                    "user_id": {"type": "string"},
                    "metadata": {"type": "object"},
                    "infer": {"type": "boolean"},
                },
                "required": ["messages", "user_id"],
            },
        ),
        FakeTool(
            "search_memories",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "filters": {"type": "object"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
            },
        ),
        FakeTool(
            "get_memories",
            {
                "type": "object",
                "properties": {
                    "filters": {"type": "object"},
                    "page": {"type": "integer"},
                    "page_size": {"type": "integer"},
                },
                "required": ["filters"],
            },
        ),
        FakeTool(
            "delete_memory",
            {
                "type": "object",
                "properties": {"memory_id": {"type": "string"}},
                "required": ["memory_id"],
            },
        ),
        FakeTool(
            "delete_all_memories",
            {
                "type": "object",
                "properties": {"filters": {"type": "object"}, "confirm": {"type": "boolean"}},
                "required": ["filters"],
            },
        ),
    ]


class FakeMCPSession:
    """Records calls and returns plausible Mem0 payloads."""

    def __init__(self, tools: list[FakeTool] | None = None, raise_on_call: Exception | None = None):
        self.tools = tools or default_tools()
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.raise_on_call = raise_on_call
        self.stored: dict[str, list[dict[str, Any]]] = {}

    async def list_tools(self) -> FakeListTools:
        return FakeListTools(self.tools)

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> FakeResult:
        arguments = arguments or {}
        self.calls.append((name, arguments))
        if self.raise_on_call:
            raise self.raise_on_call

        if name == "add_memory":
            user_id = arguments["user_id"]
            content = arguments.get("messages", [{}])[0].get("content", arguments.get("text", ""))
            record = {
                "id": f"mem-{uuid.uuid4().hex[:8]}",
                "memory": content,
                "user_id": user_id,
                "metadata": arguments.get("metadata", {}),
            }
            self.stored.setdefault(user_id, []).append(record)
            return FakeResult({"results": [record]})

        if name == "search_memories":
            filters = str(arguments.get("filters", ""))
            user_id = next(
                (key for key in self.stored if f"'user_id': '{key}'" in filters or f'"{key}"' in filters),
                None,
            )
            return FakeResult({"results": self.stored.get(user_id, [])})

        if name == "get_memories":
            filters = str(arguments.get("filters", ""))
            user_id = next(
                (key for key in self.stored if f"'{key}'" in filters or f'"{key}"' in filters),
                None,
            )
            return FakeResult({"results": self.stored.get(user_id, [])})

        if name == "delete_memory":
            for user_id, records in self.stored.items():
                self.stored[user_id] = [
                    record for record in records if record["id"] != arguments["memory_id"]
                ]
            return FakeResult({"message": "deleted"})

        if name == "delete_all_memories":
            filters = str(arguments.get("filters", ""))
            for user_id in list(self.stored):
                if user_id in filters:
                    self.stored[user_id] = []
            return FakeResult({"message": "deleted all"})

        return FakeResult({})


@pytest.fixture
def fake_session() -> FakeMCPSession:
    return FakeMCPSession()


@pytest.fixture
def client(fake_session: FakeMCPSession, settings: Settings) -> Mem0MCPClient:
    return Mem0MCPClient(settings=settings, client_factory=lambda: fake_session)


@pytest.fixture
def service(client: Mem0MCPClient) -> MemoryService:
    return MemoryService(client=client, settings=client.settings)


# ---------------------------------------------------------------------------
# Transport and discovery
# ---------------------------------------------------------------------------
def test_tool_discovery(client: Mem0MCPClient) -> None:
    tools = client.list_tools()
    assert {tool.name for tool in tools} == {
        "add_memory",
        "search_memories",
        "get_memories",
        "delete_memory",
        "delete_all_memories",
    }
    add_tool = client.get_tool("add_memory")
    assert add_tool.required == ["messages", "user_id"]


def test_add_memory_adapts_to_schema(service: MemoryService, fake_session: FakeMCPSession) -> None:
    result = service.add_memory("customer_001", "Usually prefers Sony headphones.")
    assert result.ok, result.error
    name, arguments = fake_session.calls[-1]
    assert name == "add_memory"
    # The real schema requires messages, so the adapter must not send 'text'.
    assert "messages" in arguments
    assert "text" not in arguments
    assert arguments["user_id"] == "customer_001"


def test_search_memory_is_scoped_to_customer(
    service: MemoryService, fake_session: FakeMCPSession
) -> None:
    service.add_memory("customer_001", "Prefers Sony headphones with noise cancellation.")
    result = service.search_memories("customer_001", "Sony")
    assert result.ok
    assert result.records
    _, arguments = fake_session.calls[-1]
    assert "customer_001" in str(arguments["filters"])


def test_another_customer_cannot_see_memories(
    service: MemoryService, fake_session: FakeMCPSession
) -> None:
    service.add_memory("customer_001", "Prefers Sony headphones.")
    other = service.search_memories("customer_002", "Sony")
    assert other.ok
    assert other.records == []


def test_records_for_other_users_are_dropped(service: MemoryService, fake_session: FakeMCPSession) -> None:
    fake_session.stored["customer_002"] = [
        {"id": "leak", "memory": "Other customer's secret", "user_id": "customer_002"}
    ]
    # Simulate a server bug that returns both users' records for customer_001's query.
    fake_session.stored["customer_001"] = [
        {"id": "mine", "memory": "Prefers Sony", "user_id": "customer_001"},
        {"id": "leak", "memory": "Other customer's secret", "user_id": "customer_002"},
    ]
    result = service.list_memories("customer_001")
    assert [record.memory_id for record in result.records] == ["mine"]


def test_list_memories(service: MemoryService) -> None:
    service.add_memory("customer_001", "Prefers wireless headphones.")
    result = service.list_memories("customer_001")
    assert result.ok
    assert result.records[0].text == "Prefers wireless headphones."


def test_delete_memory_verifies_ownership(
    service: MemoryService, fake_session: FakeMCPSession
) -> None:
    added = service.add_memory("customer_001", "Prefers Sony headphones.")
    memory_id = added.records[0].memory_id
    deleted = service.delete_memory("customer_001", memory_id)
    assert deleted.ok, deleted.error
    assert fake_session.stored["customer_001"] == []


def test_delete_foreign_memory_is_refused(service: MemoryService, fake_session: FakeMCPSession) -> None:
    fake_session.stored["customer_002"] = [
        {"id": "foreign", "memory": "Not yours", "user_id": "customer_002"}
    ]
    result = service.delete_memory("customer_001", "foreign")
    assert not result.ok
    assert "does not belong" in (result.error or "")
    assert fake_session.stored["customer_002"]


def test_delete_all_memories_is_customer_scoped(
    service: MemoryService, fake_session: FakeMCPSession
) -> None:
    service.add_memory("customer_001", "Prefers Sony.")
    service.add_memory("customer_002", "Prefers JBL.")
    result = service.delete_all_memories("customer_001")
    assert result.ok, result.error
    assert fake_session.stored["customer_001"] == []
    assert fake_session.stored["customer_002"]


def test_update_memory_verifies_ownership(service: MemoryService, fake_session: FakeMCPSession) -> None:
    added = service.add_memory("customer_001", "Prefers wired headphones.")
    memory_id = added.records[0].memory_id
    # The fake server does not implement update_memory, so the service should
    # report the missing tool rather than pretending success.
    result = service.update_memory("customer_001", memory_id, "Prefers wireless headphones.")
    assert not result.ok
    assert "does not expose" in (result.error or "")


def test_missing_api_key_is_reported() -> None:
    settings = Settings(mem0_api_key="")
    service = MemoryService(settings=settings)
    status = service.status()
    assert status.configured is False
    assert status.connected is False
    result = service.add_memory("customer_001", "Prefers Sony.")
    assert not result.ok
    assert "MEM0_API_KEY" in (result.error or "")


def test_authentication_failure_is_classified(settings: Settings) -> None:
    session = FakeMCPSession(raise_on_call=RuntimeError("401 Unauthorized"))
    client = Mem0MCPClient(settings=settings, client_factory=lambda: session)
    service = MemoryService(client=client, settings=settings)
    result = service.search_memories("customer_001", "Sony")
    assert not result.ok
    assert "authentication" in (result.error or "").casefold()


def test_missing_scope_argument_is_refused(settings: Settings) -> None:
    tools = [
        FakeTool(
            "get_memories",
            {"type": "object", "properties": {"page": {"type": "integer"}}},
        )
    ]
    session = FakeMCPSession(tools=tools)
    client = Mem0MCPClient(settings=settings, client_factory=lambda: session)
    service = MemoryService(client=client, settings=settings)
    result = service.list_memories("customer_001")
    assert not result.ok
    assert "customer scope" in (result.error or "")


def test_call_tool_rejects_unknown_tool(client: Mem0MCPClient) -> None:
    with pytest.raises(Mem0MCPError):
        client.call_tool("not_a_real_tool", {})


def test_not_configured_client_raises(settings: Settings) -> None:
    unconfigured = Settings(mem0_api_key="")
    client = Mem0MCPClient(settings=unconfigured)
    with pytest.raises(Mem0NotConfiguredError):
        client.list_tools()
    client.close()


def test_extract_raw_records_handles_wrapped_payloads() -> None:
    assert extract_raw_records({"results": [{"memory": "a"}]}) == [{"memory": "a"}]
    assert extract_raw_records({"result": {"results": [{"memory": "b"}]}}) == [{"memory": "b"}]
    assert extract_raw_records({"unexpected": True}) == []


# ---------------------------------------------------------------------------
# Memory manager rules (mock-only, deterministic)
# ---------------------------------------------------------------------------
def test_temporary_preferences_are_not_saved(service: MemoryService) -> None:
    manager = MemoryManager(service)
    report = manager.remember(
        "customer_001",
        [
            PreferenceCandidate("Budget of ₹3,000 for this purchase.", kind="temporary"),
            PreferenceCandidate("Wants JBL instead of Sony this time.", kind="temporary"),
        ],
        existing_memories=[],
    )
    assert report.saved == []
    assert len(report.skipped) == 2
    assert service.list_memories("customer_001").records == []


def test_temporary_wording_is_rejected_even_if_labelled_persistent(service: MemoryService) -> None:
    manager = MemoryManager(service)
    report = manager.remember(
        "customer_001",
        [PreferenceCandidate("Prefers JBL headphones this time.", kind="persistent", confidence=0.9)],
        existing_memories=[],
    )
    assert report.saved == []
    assert "temporary wording" in report.skipped[0]


def test_budget_only_candidate_needs_stable_wording(service: MemoryService) -> None:
    manager = MemoryManager(service)
    one_off = manager.remember(
        "customer_001",
        [PreferenceCandidate("Budget under ₹3,000.", kind="persistent", confidence=0.95)],
        existing_memories=[],
    )
    assert one_off.saved == []
    assert "one-off budget" in one_off.skipped[0]

    usual = manager.remember(
        "customer_001",
        [
            PreferenceCandidate(
                "Usually has a budget around ₹5,000.",
                kind="persistent",
                confidence=0.9,
            )
        ],
        existing_memories=[],
    )
    assert usual.saved == ["Usually has a budget around ₹5,000."]


def test_persistent_preference_is_saved(service: MemoryService, fake_session: FakeMCPSession) -> None:
    manager = MemoryManager(service)
    report = manager.remember(
        "customer_001",
        [PreferenceCandidate("Usually prefers Sony headphones with noise cancellation.", confidence=0.9)],
        existing_memories=[],
    )
    assert report.saved
    assert service.list_memories("customer_001").records[0].text.startswith("Usually prefers Sony")


def test_duplicate_preference_is_not_saved_twice(service: MemoryService) -> None:
    manager = MemoryManager(service)
    candidate = PreferenceCandidate("Usually prefers Sony headphones.", confidence=0.95)
    first = manager.remember("customer_001", [candidate], existing_memories=[])
    second = manager.remember(
        "customer_001", [candidate], existing_memories=["Usually prefers Sony headphones."]
    )
    assert first.saved
    assert second.saved == []
    assert "already remembered" in second.skipped[0]


def test_memory_write_failure_is_reported(settings: Settings) -> None:
    session = FakeMCPSession(raise_on_call=RuntimeError("MCP tool invocation failed"))
    client = Mem0MCPClient(settings=settings, client_factory=lambda: session)
    service = MemoryService(client=client, settings=settings)
    manager = MemoryManager(service)
    report = manager.remember(
        "customer_001",
        [PreferenceCandidate("Always prefers wireless headphones.", confidence=0.9)],
        existing_memories=[],
    )
    assert report.saved == []
    assert report.failures
    assert report.status == "error"


# ---------------------------------------------------------------------------
# Live integration tests (real Mem0 Cloud MCP)
# ---------------------------------------------------------------------------
@pytest.mark.live
@pytest.mark.skipif(
    not os.environ.get("MEM0_API_KEY"),
    reason="MEM0_API_KEY not set — live Mem0 MCP test skipped",
)
def test_live_mem0_add_search_isolation_and_delete() -> None:
    """Real add/search/isolation/delete round-trip against Mem0 Cloud."""
    client = Mem0MCPClient(settings=Settings())
    service = MemoryService(client=client, settings=client.settings)
    customer = f"poc_live_{uuid.uuid4().hex[:8]}"
    other = f"{customer}_other"
    text = "Usually prefers Sony wireless headphones with noise cancellation."
    probe_marker = "POC live test preference"

    try:
        tools = client.list_tools(refresh=True)
        assert tools, "Mem0 MCP returned no tools"

        added = service.add_memory(customer, f"{probe_marker}: {text}")
        assert added.ok, added.error

        # Mem0 extraction is asynchronous; allow a short moment before searching.
        import time

        time.sleep(3)
        found = service.search_memories(customer, "Sony headphones noise cancellation")
        assert found.ok, found.error
        assert any("Sony" in record.text for record in found.records)

        isolated = service.search_memories(other, "Sony headphones noise cancellation")
        assert isolated.ok, isolated.error
        assert not any("Sony" in record.text for record in isolated.records)

        for record in found.records:
            if record.memory_id:
                service.delete_memory(customer, record.memory_id)
    finally:
        client.close()
