"""Application-level memory operations over the Mem0 Cloud MCP client.

Other modules never see MCP tool names, schemas or payload shapes.  They call
simple functions such as ``search_memories(customer_id, query)`` and receive
``MemoryRecord`` objects plus an explicit success flag.

Customer isolation is enforced by always scoping requests with
``user_id = customer_id`` and by dropping any returned record that names a
different user.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from app.config.settings import Settings, get_settings
from app.memory.memory_client import (
    Mem0MCPClient,
    Mem0MCPError,
    Mem0NotConfiguredError,
    Mem0ToolNotFoundError,
    ToolInfo,
)


@dataclass(frozen=True)
class MemoryRecord:
    """One customer memory returned by Mem0."""

    memory_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float | None = None
    created_at: str | None = None
    user_id: str | None = None


@dataclass
class MemoryResult:
    """Outcome of a memory operation.

    ``ok`` is the only signal callers may use to claim success.  ``error`` is a
    human-readable message suitable for the UI.
    """

    ok: bool
    operation: str
    records: list[MemoryRecord] = field(default_factory=list)
    error: str | None = None
    tool: str | None = None
    raw: Any = None
    # Asynchronous writes: ``add_memory`` returns PENDING plus this event id.
    event_id: str | None = None
    event_status: str | None = None


@dataclass
class MemoryEventResult:
    """Outcome of polling one Mem0 memory operation event.

    Only ``status == "SUCCEEDED"`` (or ``COMPLETED``) means the write landed.
    ``ok`` is the single signal callers may use to claim success.
    """

    ok: bool
    event_id: str
    status: str = "UNKNOWN"
    memory_ids: list[str] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True)
class MemoryServiceStatus:
    """Connection state for status indicators."""

    configured: bool
    connected: bool
    message: str
    tools: list[str] = field(default_factory=list)


# Status values observed from Mem0's ``get_event_status`` tool.
_SUCCESS_EVENT_STATUSES = {"SUCCEEDED", "COMPLETED", "DONE"}
_FAILED_EVENT_STATUSES = {"FAILED", "ERROR", "CANCELLED", "CANCELED"}


def _user_filter(customer_id: str) -> dict[str, Any]:
    """Build the documented Mem0 filter for one user."""
    return {"AND": [{"user_id": customer_id}]}


def _parse_event_payload(event_id: str, payload: Any) -> MemoryEventResult:
    """Convert an event payload into a MemoryEventResult.

    Observed shape::

        {"id": ..., "status": "SUCCEEDED", "error": null,
         "results": [{"id": ..., "data": {"memory": ...}, "event": "ADD"}]}
    """
    data = payload if isinstance(payload, dict) else {}
    status = str(data.get("status") or "UNKNOWN").upper()
    memory_ids: list[str] = []
    texts: list[str] = []
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        if item.get("id"):
            memory_ids.append(str(item["id"]))
        body = item.get("data")
        text = body.get("memory") if isinstance(body, dict) else None
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
    error = data.get("error")
    ok = status in _SUCCESS_EVENT_STATUSES
    return MemoryEventResult(
        ok=ok,
        event_id=event_id,
        status=status,
        memory_ids=memory_ids,
        texts=texts,
        error=str(error) if error else None,
    )


def _first_present(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    """Return the first non-empty value among ``keys``."""
    for key in keys:
        value = payload.get(key)
        if value not in (None, "", []):
            return value
    return None


def extract_raw_records(payload: Any) -> list[dict[str, Any]]:
    """Find a list of memory dictionaries in an arbitrary MCP payload."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        candidates = _first_present(payload, ("results", "memories", "data", "items"))
        if isinstance(candidates, list):
            return [item for item in candidates if isinstance(item, dict)]
        # Some tools wrap the list deeper, e.g. {"result": {"results": [...]}}
        for key in ("result", "response"):
            nested = payload.get(key)
            if isinstance(nested, (dict, list)):
                found = extract_raw_records(nested)
                if found:
                    return found
    return []


def to_memory_record(raw: dict[str, Any]) -> MemoryRecord | None:
    """Convert a raw Mem0 record into a MemoryRecord, skipping empty entries."""
    text = _first_present(raw, ("memory", "text", "content", "data"))
    if not isinstance(text, str) or not text.strip():
        return None
    memory_id = _first_present(raw, ("id", "memory_id", "uuid"))
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    try:
        score = float(raw["score"]) if raw.get("score") is not None else None
    except (TypeError, ValueError):
        score = None
    return MemoryRecord(
        memory_id=str(memory_id or ""),
        text=text.strip(),
        metadata=metadata,
        score=score,
        created_at=str(raw.get("created_at")) if raw.get("created_at") else None,
        user_id=str(raw["user_id"]) if raw.get("user_id") else None,
    )


class MemoryService:
    """Scoped memory operations for a single Mem0 account."""

    def __init__(
        self,
        client: Mem0MCPClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.client = client or Mem0MCPClient(settings=self.settings)

    # ---------------------------------------------------------------- status
    def status(self) -> MemoryServiceStatus:
        """Return configuration and connection status without raising."""
        if not self.settings.mem0_configured:
            return MemoryServiceStatus(
                configured=False,
                connected=False,
                message="Mem0 MCP not configured — set MEM0_API_KEY in .env",
            )
        try:
            tools = self.client.list_tools(refresh=False)
        except Mem0MCPError as exc:
            return MemoryServiceStatus(
                configured=True, connected=False, message=str(exc)
            )
        return MemoryServiceStatus(
            configured=True,
            connected=True,
            message="Mem0 MCP connected",
            tools=[tool.name for tool in tools],
        )

    # ------------------------------------------------------------- internals
    def _scoped_arguments(
        self, tool: ToolInfo, candidates: dict[str, Any], customer_id: str
    ) -> dict[str, Any]:
        """Build tool arguments and guarantee a customer scope is present."""
        arguments = self.client.build_arguments(tool, candidates)
        filters = arguments.get("filters")
        scoped = (isinstance(filters, dict) and customer_id in json.dumps(filters)) or (
            arguments.get("user_id") == customer_id
        )
        if not scoped:
            raise Mem0MCPError(
                f"Refusing to call '{tool.name}' without a customer scope; "
                "the server schema did not accept user_id or filters."
            )
        return arguments

    @staticmethod
    def _records_from_result(
        result: Any, customer_id: str
    ) -> list[MemoryRecord]:
        """Parse records and enforce customer isolation on returned data."""
        records: list[MemoryRecord] = []
        for raw in extract_raw_records(result.payload):
            record = to_memory_record(raw)
            if record is None:
                continue
            if record.user_id is not None and record.user_id != customer_id:
                # Defensive: never surface another customer's memory.
                continue
            records.append(record)
        return records

    # -------------------------------------------------------------- operations
    def add_memory(
        self,
        customer_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
        infer: bool = True,
    ) -> MemoryResult:
        """Store one preference sentence for a customer.

        Both ``text`` and ``messages`` are offered as candidates; the tool's
        actual input schema decides which is sent.
        """
        if not customer_id or not text.strip():
            return MemoryResult(
                ok=False,
                operation="add",
                error="customer_id and memory text are required",
            )
        candidates = {
            "text": text.strip(),
            "messages": [{"role": "user", "content": text.strip()}],
            "user_id": customer_id,
            "metadata": metadata or {"source": "retail-memory-assistant"},
            "infer": infer,
        }
        try:
            tool = self.client.get_tool("add_memory")
            arguments = self._scoped_arguments(tool, candidates, customer_id)
            result = self.client.call_tool("add_memory", arguments)
        except (Mem0MCPError, Mem0NotConfiguredError) as exc:
            return MemoryResult(ok=False, operation="add", error=str(exc), tool="add_memory")

        records = self._records_from_result(result, customer_id)
        payload = result.payload if isinstance(result.payload, dict) else {}
        event_id = _first_present(payload, ("event_id", "id"))
        event_status = _first_present(payload, ("status",))
        return MemoryResult(
            ok=True,
            operation="add",
            records=records,
            tool=result.tool,
            raw=result.payload,
            event_id=str(event_id) if event_id else None,
            event_status=str(event_status) if event_status else None,
        )

    def get_event_status(self, event_id: str) -> MemoryEventResult:
        """Read the current state of a Mem0 memory operation event."""
        if not event_id:
            return MemoryEventResult(ok=False, event_id="", error="event_id is required")
        try:
            result = self.client.call_tool("get_event_status", {"event_id": event_id})
        except Mem0MCPError as exc:
            return MemoryEventResult(ok=False, event_id=event_id, error=str(exc))
        return _parse_event_payload(event_id, result.payload)

    def wait_for_event(
        self,
        event_id: str,
        timeout: float | None = None,
        interval: float | None = None,
    ) -> MemoryEventResult:
        """Poll ``get_event_status`` until the event finishes or times out.

        A timeout is reported as ``PENDING`` - never as success.  Mem0's ADD
        events complete asynchronously (observed latency is a few seconds).
        """
        if not event_id:
            return MemoryEventResult(ok=False, event_id="", error="event_id is required")
        deadline = time.monotonic() + (
            timeout if timeout is not None else self.settings.memory_event_timeout_seconds
        )
        wait = (
            interval
            if interval is not None
            else self.settings.memory_event_poll_interval_seconds
        )
        last = MemoryEventResult(ok=False, event_id=event_id, status="PENDING")
        while True:
            last = self.get_event_status(event_id)
            if last.ok or last.status in _FAILED_EVENT_STATUSES:
                return last
            if last.status == "UNKNOWN" and last.error:
                # The status tool is unavailable or the payload is unusable;
                # polling again cannot help, so report it instead of stalling.
                return last
            if time.monotonic() >= deadline:
                last.ok = False
                if not last.error:
                    last.error = (
                        f"Mem0 memory event {event_id} is still {last.status} after "
                        f"the polling timeout"
                    )
                return last
            time.sleep(max(0.0, wait))

    def search_memories(
        self,
        customer_id: str,
        query: str,
        limit: int | None = None,
    ) -> MemoryResult:
        """Semantic search restricted to one customer."""
        if not customer_id or not query.strip():
            return MemoryResult(
                ok=False, operation="search", error="customer_id and query are required"
            )
        top_k = limit or self.settings.memory_top_k
        candidates = {
            "query": query.strip(),
            "filters": _user_filter(customer_id),
            "user_id": customer_id,
            "limit": top_k,
            "top_k": top_k,
        }
        try:
            tool = self.client.get_tool("search_memories")
            arguments = self._scoped_arguments(tool, candidates, customer_id)
            result = self.client.call_tool("search_memories", arguments)
        except Mem0MCPError as exc:
            return MemoryResult(
                ok=False, operation="search", error=str(exc), tool="search_memories"
            )
        records = self._records_from_result(result, customer_id)
        return MemoryResult(
            ok=True,
            operation="search",
            records=records,
            tool=result.tool,
            raw=result.payload,
        )

    def list_memories(
        self,
        customer_id: str,
        limit: int | None = None,
        page: int = 1,
    ) -> MemoryResult:
        """List a customer's memories with pagination."""
        page_size = limit or self.settings.memory_top_k
        candidates = {
            "filters": _user_filter(customer_id),
            "user_id": customer_id,
            "page": page,
            "page_size": page_size,
            "limit": page_size,
            "top_k": page_size,
        }
        try:
            tool = self.client.get_tool("get_memories")
            arguments = self._scoped_arguments(tool, candidates, customer_id)
            result = self.client.call_tool("get_memories", arguments)
        except Mem0MCPError as exc:
            return MemoryResult(
                ok=False, operation="list", error=str(exc), tool="get_memories"
            )
        records = self._records_from_result(result, customer_id)
        return MemoryResult(
            ok=True,
            operation="list",
            records=records,
            tool=result.tool,
            raw=result.payload,
        )

    def _owns_memory(self, customer_id: str, memory_id: str) -> bool:
        """Return True only when the memory is confirmed in the customer's scope."""
        listing = self.list_memories(customer_id, limit=100)
        if not listing.ok:
            raise Mem0MCPError(
                f"Could not verify memory ownership before modifying it: {listing.error}"
            )
        return any(record.memory_id == memory_id for record in listing.records)

    def update_memory(
        self,
        customer_id: str,
        memory_id: str,
        text: str,
    ) -> MemoryResult:
        """Overwrite a memory's text after verifying customer ownership.

        The live tool takes ``memory_id`` plus optional ``text``/``metadata``
        (verified against the MCP input schema); no customer scope is accepted
        by the tool, so ownership is checked first through a scoped list.
        """
        if not text.strip():
            return MemoryResult(ok=False, operation="update", error="New text is required")
        try:
            if not self._owns_memory(customer_id, memory_id):
                return MemoryResult(
                    ok=False,
                    operation="update",
                    error=f"Memory {memory_id} does not belong to {customer_id}",
                )
            tool = self.client.get_tool("update_memory")
            arguments = self.client.build_arguments(
                tool,
                {
                    "memory_id": memory_id,
                    "text": text.strip(),
                    "memory": text.strip(),
                    "user_id": customer_id,
                    "filters": _user_filter(customer_id),
                },
            )
            result = self.client.call_tool("update_memory", arguments)
        except (Mem0MCPError, Mem0ToolNotFoundError) as exc:
            return MemoryResult(
                ok=False, operation="update", error=str(exc), tool="update_memory"
            )
        return MemoryResult(
            ok=True, operation="update", tool=result.tool, raw=result.payload
        )

    def delete_memory(self, customer_id: str, memory_id: str) -> MemoryResult:
        """Delete one memory after verifying customer ownership."""
        try:
            if not self._owns_memory(customer_id, memory_id):
                return MemoryResult(
                    ok=False,
                    operation="delete",
                    error=f"Memory {memory_id} does not belong to {customer_id}",
                )
            tool = self.client.get_tool("delete_memory")
            arguments = self.client.build_arguments(
                tool,
                {
                    "memory_id": memory_id,
                    "id": memory_id,
                    "user_id": customer_id,
                    "filters": _user_filter(customer_id),
                },
            )
            result = self.client.call_tool("delete_memory", arguments)
        except (Mem0MCPError, Mem0ToolNotFoundError) as exc:
            return MemoryResult(
                ok=False, operation="delete", error=str(exc), tool="delete_memory"
            )
        return MemoryResult(
            ok=True, operation="delete", tool=result.tool, raw=result.payload
        )

    def delete_all_memories(self, customer_id: str) -> MemoryResult:
        """Delete every memory in one customer's scope.

        The hosted tool may require a confirmation flag; candidates are adapted
        to the schema the server exposes.  The scope always includes the
        customer filter, never a global delete.
        """
        candidates = {
            "user_id": customer_id,
            "filters": _user_filter(customer_id),
            "confirm": True,
        }
        try:
            tool = self.client.get_tool("delete_all_memories")
            arguments = self._scoped_arguments(tool, candidates, customer_id)
            result = self.client.call_tool("delete_all_memories", arguments)
        except (Mem0MCPError, Mem0ToolNotFoundError) as exc:
            return MemoryResult(
                ok=False, operation="delete_all", error=str(exc), tool="delete_all_memories"
            )
        return MemoryResult(
            ok=True, operation="delete_all", tool=result.tool, raw=result.payload
        )


@lru_cache(maxsize=1)
def get_memory_service() -> MemoryService:
    """Return the process-wide memory service (one MCP session per process)."""
    return MemoryService()
