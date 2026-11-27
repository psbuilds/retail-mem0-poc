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
from app.memory.memory_manager import MemoryManager
from app.memory.memory_service import (
    MemoryEventResult,
    MemoryRecord,
    MemoryResult,
    MemoryService,
    extract_raw_records,
)


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
        FakeTool(
            "update_memory",
            {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string"},
                    "text": {"type": "string"},
                    "metadata": {"type": "object"},
                },
                "required": ["memory_id"],
            },
        ),
        FakeTool(
            "get_event_status",
            {"type": "object", "properties": {"event_id": {"type": "string"}}, "required": ["event_id"]},
        ),
    ]


class FakeMCPSession:
    """Records calls and returns plausible Mem0 payloads."""

    def __init__(self, tools: list[FakeTool] | None = None, raise_on_call: Exception | None = None):
        self.tools = tools or default_tools()
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.raise_on_call = raise_on_call
        self.stored: dict[str, list[dict[str, Any]]] = {}
        self.events: dict[str, dict[str, Any]] = {}

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
            event_id = f"evt-{uuid.uuid4().hex[:8]}"
            self.events[event_id] = {
                "id": event_id,
                "event_type": "ADD",
                "status": "SUCCEEDED",
                "results": [
                    {"id": record["id"], "data": {"memory": content}, "event": "ADD"}
                ],
                "error": None,
            }
            return FakeResult({"event_id": event_id, "status": "PENDING"})

        if name == "get_event_status":
            event = self.events.get(arguments["event_id"], {"status": "UNKNOWN", "results": []})
            return FakeResult(event)

        if name == "update_memory":
            for records in self.stored.values():
                for record in records:
                    if record["id"] == arguments["memory_id"]:
                        record["memory"] = arguments.get("text", record["memory"])
                        return FakeResult({"message": "updated"})
            return FakeResult({"message": "not found"})

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
        "update_memory",
        "get_event_status",
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
    service.add_memory("customer_001", "Prefers Sony headphones.")
    memory_id = service.list_memories("customer_001").records[0].memory_id
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
    service.add_memory("customer_001", "Prefers wired headphones.")
    listed = service.list_memories("customer_001")
    memory_id = listed.records[0].memory_id
    result = service.update_memory("customer_001", memory_id, "Prefers wireless headphones.")
    assert result.ok, result.error
    assert "wireless" in service.list_memories("customer_001").records[0].text


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
class _StubEventService:
    """Minimal service stub for event-state tests (no MCP)."""

    def __init__(self, status: str) -> None:
        self.status = status

    def add_memory(self, customer_id: str, text: str, metadata=None, infer: bool = True):
        return MemoryResult(ok=True, operation="add", event_id="evt_stub")

    def wait_for_event(self, event_id: str, timeout=None, interval=None):
        return MemoryEventResult(
            ok=self.status == "SUCCEEDED",
            event_id=event_id,
            status=self.status,
            texts=["User prefers Sony"] if self.status == "SUCCEEDED" else [],
            error=None if self.status == "SUCCEEDED" else f"event is {self.status}",
        )


def test_temporary_preference_is_rejected_by_policy(service: MemoryService) -> None:
    manager = MemoryManager(service)
    allowed, reason = manager.should_store("Prefers JBL headphones this time.")
    assert not allowed
    assert "temporary" in reason
    report = manager.remember("customer_001", "Prefers JBL headphones this time.")
    assert report.saved == []
    assert service.list_memories("customer_001").records == []


def test_budget_only_preference_needs_stable_wording(service: MemoryService) -> None:
    manager = MemoryManager(service)
    assert not manager.should_store("Budget under ₹3,000.")[0]
    assert manager.should_store("I usually have a budget around ₹5,000.")[0]


def test_vague_statement_is_not_stored(service: MemoryService) -> None:
    manager = MemoryManager(service)
    allowed, reason = manager.should_store("Show me some options")
    assert not allowed
    assert "stable preference" in reason


def test_persistent_preference_is_saved_after_event_completes(
    service: MemoryService, fake_session: FakeMCPSession
) -> None:
    manager = MemoryManager(service)
    report = manager.remember(
        "customer_001", "I usually prefer Sony headphones with noise cancellation."
    )
    assert report.status == "success", report.failures
    assert report.saved
    assert service.list_memories("customer_001").records[0].text.startswith(
        "I usually prefer Sony"
    )


def test_pending_event_is_not_reported_as_saved(service: MemoryService) -> None:
    manager = MemoryManager(_StubEventService("PENDING"))  # type: ignore[arg-type]
    report = manager.remember("customer_001", "I usually prefer Sony headphones.")
    assert report.saved == []
    assert report.pending
    assert report.status == "pending"


def test_failed_event_is_reported_as_failure(service: MemoryService) -> None:
    manager = MemoryManager(_StubEventService("FAILED"))  # type: ignore[arg-type]
    report = manager.remember("customer_001", "I usually prefer Sony headphones.")
    assert report.saved == []
    assert report.failures
    assert report.status == "error"


def test_memory_write_failure_is_reported(settings: Settings) -> None:
    session = FakeMCPSession(raise_on_call=RuntimeError("MCP tool invocation failed"))
    client = Mem0MCPClient(settings=settings, client_factory=lambda: session)
    service = MemoryService(client=client, settings=settings)
    manager = MemoryManager(service)
    report = manager.remember("customer_001", "I always prefer wireless headphones.")
    assert report.saved == []
    assert report.failures
    assert report.status == "error"


# ---------------------------------------------------------------------------
# Async event polling (real service + fake MCP transport)
# ---------------------------------------------------------------------------
def test_add_memory_returns_event_and_wait_succeeds(service: MemoryService) -> None:
    added = service.add_memory("customer_001", "Usually prefers Sony headphones.")
    assert added.ok
    assert added.event_id
    assert added.event_status == "PENDING"
    event = service.wait_for_event(added.event_id)
    assert event.ok
    assert event.status == "SUCCEEDED"
    assert event.texts


def test_event_timeout_is_pending_not_success(service: MemoryService, fake_session: FakeMCPSession) -> None:
    added = service.add_memory("customer_001", "Usually prefers Sony headphones.")
    fake_session.events[added.event_id]["status"] = "PENDING"
    event = service.wait_for_event(added.event_id, timeout=0, interval=0)
    assert not event.ok
    assert event.status == "PENDING"
    assert "timeout" in (event.error or "")


def test_missing_event_status_tool_is_reported(settings: Settings) -> None:
    tools = [tool for tool in default_tools() if tool.name != "get_event_status"]
    session = FakeMCPSession(tools=tools)
    client = Mem0MCPClient(settings=settings, client_factory=lambda: session)
    service = MemoryService(client=client, settings=settings)

    added = service.add_memory("customer_001", "Usually prefers Sony headphones.")
    assert added.event_id
    event = service.wait_for_event(added.event_id, timeout=5, interval=0)
    assert not event.ok
    assert event.status == "UNKNOWN"
    assert "does not expose" in (event.error or "")


def test_failed_event_surfaces_its_error(service: MemoryService, fake_session: FakeMCPSession) -> None:
    added = service.add_memory("customer_001", "Usually prefers Sony headphones.")
    fake_session.events[added.event_id].update(
        {"status": "FAILED", "error": "extraction failed"}
    )
    event = service.wait_for_event(added.event_id, timeout=0, interval=0)
    assert not event.ok
    assert event.status == "FAILED"
    assert event.error == "extraction failed"


# ---------------------------------------------------------------------------
# Corrections operate only on retrieved ids
# ---------------------------------------------------------------------------
def test_delete_correction_targets_only_matching_records(
    service: MemoryService, fake_session: FakeMCPSession
) -> None:
    service.add_memory("customer_001", "Usually prefers Sony headphones.")
    service.add_memory("customer_001", "Usually prefers JBL earbuds.")
    candidates = service.list_memories("customer_001").records
    manager = MemoryManager(service)

    plan = manager.correction_plan("Forget that I prefer Sony.", candidates)
    assert plan.mode == "delete"
    assert plan.old_brand == "Sony"
    assert [record.memory_id for record in plan.targets] == [candidates[0].memory_id]

    actions = manager.apply_correction("customer_001", plan)
    assert actions[0].status == "deleted"
    assert service.list_memories("customer_001").records[0].text.startswith("Usually prefers JBL")


def test_replace_correction_rewrites_the_stored_preference(
    service: MemoryService, fake_session: FakeMCPSession
) -> None:
    service.add_memory("customer_001", "User prefers Sony headphones.")
    candidates = service.list_memories("customer_001").records
    manager = MemoryManager(service)

    plan = manager.correction_plan("I prefer JBL, not Sony.", candidates)
    assert plan.mode == "replace"
    assert plan.old_brand == "Sony"
    assert plan.new_brand == "JBL"

    actions = manager.apply_correction("customer_001", plan)
    assert actions[0].status == "saved"
    assert "JBL" in service.list_memories("customer_001").records[0].text


def test_correction_without_a_matching_record_changes_nothing(
    service: MemoryService, fake_session: FakeMCPSession
) -> None:
    service.add_memory("customer_001", "Usually prefers Sony headphones.")
    candidates = service.list_memories("customer_001").records
    manager = MemoryManager(service)

    plan = manager.correction_plan("Forget that I prefer Bose.", candidates)
    assert plan.targets == []
    actions = manager.apply_correction("customer_001", plan)
    assert actions[0].status == "not_found"
    assert len(service.list_memories("customer_001").records) == 1


def test_invented_memory_id_cannot_be_deleted(
    service: MemoryService, fake_session: FakeMCPSession
) -> None:
    service.add_memory("customer_001", "Usually prefers Sony headphones.")
    result = service.delete_memory("customer_001", "invented-id")
    assert not result.ok
    assert "does not belong" in (result.error or "")
    assert service.list_memories("customer_001").records


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
        assert added.event_id, "add_memory must return an event id"

        # Mem0 extraction is asynchronous: wait for the real event instead of
        # sleeping and hoping the memory exists.
        event = service.wait_for_event(added.event_id)
        assert event.ok, f"{event.status}: {event.error}"

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
