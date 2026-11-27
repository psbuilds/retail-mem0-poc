"""Shared fixtures and fake services for the test suite.

Unit tests never contact Mem0 Cloud, Ollama or the network.  The fakes implement
exactly the methods the workflow uses, so precedence and error-handling logic can
be tested deterministically.  Live service tests are marked ``live`` and skipped
unless credentials are present.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.agent.graph import build_graph
from app.agent.nodes import WorkflowDeps
from app.config.settings import Settings
from app.llm.ollama_client import OllamaUnavailableError
from app.memory.memory_manager import MemoryManager
from app.memory.memory_service import MemoryEventResult, MemoryRecord, MemoryResult

CATALOGUE_PATH = Path(__file__).resolve().parents[1] / "backend" / "data" / "products.json"


@pytest.fixture(scope="session")
def catalogue() -> list[dict[str, Any]]:
    """Return the real synthetic catalogue used by the product API."""
    with CATALOGUE_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)["products"]


class FakeLLM:
    """Scripted Ollama replacement.

    ``requirements_by_message`` maps an exact customer message to the JSON the
    model would return for requirement extraction.  Preference extraction and
    response generation use the other attributes.
    """

    def __init__(
        self,
        requirements: dict[str, Any] | None = None,
        preferences: list[dict[str, Any]] | None = None,
        response: str = "Here are some headphones that fit your request.",
        requirements_by_message: dict[str, dict[str, Any]] | None = None,
        fail_json: bool = False,
        fail_chat: bool = False,
    ) -> None:
        self.requirements = requirements or {"is_shopping_request": True}
        self.preferences = preferences or []
        self.response = response
        self.requirements_by_message = requirements_by_message or {}
        self.fail_json = fail_json
        self.fail_chat = fail_chat
        self.chat_json_calls = 0
        self.chat_calls = 0

    @staticmethod
    def _current_message(messages: list[dict[str, str]]) -> str:
        prompt = messages[-1]["content"]
        match = re.search(r"Current customer message: (['\"])(.*?)\1", prompt, re.S)
        if match:
            return match.group(2)
        match = re.search(r"Latest customer message: (['\"])(.*?)\1", prompt, re.S)
        return match.group(2) if match else ""

    def chat_json(
        self, messages: list[dict[str, str]], *, temperature: float = 0.1
    ) -> dict[str, Any]:
        self.chat_json_calls += 1
        if self.fail_json:
            raise OllamaUnavailableError("Ollama is not reachable at http://localhost:11434")
        system = messages[0]["content"]
        if "shopping requirements" in system:
            message = self._current_message(messages)
            if message in self.requirements_by_message:
                return self.requirements_by_message[message]
            return self.requirements
        return {"preferences": self.preferences}

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        json_mode: bool = False,
        temperature: float = 0.2,
    ) -> str:
        self.chat_calls += 1
        if self.fail_chat:
            raise OllamaUnavailableError("Ollama is not reachable at http://localhost:11434")
        return self.response


class FakeMemoryService:
    """In-process memory stub that mimics MemoryService's contract.

    Writes are asynchronous in the real service: ``add_memory`` returns an
    event id and only ``wait_for_event`` decides whether the memory lands.
    ``event_status`` lets tests exercise saved, pending and failed writes.
    """

    def __init__(
        self,
        memories: list[str] | None = None,
        fail_search: bool = False,
        fail_add: bool = False,
        event_status: str = "SUCCEEDED",
        fail_delete: bool = False,
        fail_update: bool = False,
    ) -> None:
        self._memories: dict[str, list[MemoryRecord]] = {
            "customer_001": [
                MemoryRecord(memory_id=f"mem_{index}", text=text, user_id="customer_001")
                for index, text in enumerate(memories or [])
            ]
        }
        self.fail_search = fail_search
        self.fail_add = fail_add
        self.event_status = event_status
        self.fail_delete = fail_delete
        self.fail_update = fail_update
        self.added: list[tuple[str, str, dict[str, Any] | None]] = []
        self.events: dict[str, tuple[str, dict[str, Any]]] = {}
        self.search_calls = 0
        self.deleted: list[str] = []
        self.updated: list[tuple[str, str]] = []

    def _for(self, customer_id: str) -> list[MemoryRecord]:
        return self._memories.setdefault(customer_id, [])

    def search_memories(
        self, customer_id: str, query: str, limit: int | None = None
    ) -> MemoryResult:
        self.search_calls += 1
        if self.fail_search:
            return MemoryResult(
                ok=False, operation="search", error="Mem0 MCP connection refused"
            )
        records = self._for(customer_id)[: (limit or 20)]
        return MemoryResult(ok=True, operation="search", records=records)

    def list_memories(self, customer_id: str, limit: int | None = None, page: int = 1):
        return MemoryResult(
            ok=True, operation="list", records=self._for(customer_id)[: (limit or 20)]
        )

    def add_memory(
        self,
        customer_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
        infer: bool = True,
    ) -> MemoryResult:
        if self.fail_add:
            return MemoryResult(ok=False, operation="add", error="Mem0 MCP write failed")
        self.added.append((customer_id, text, metadata))
        event_id = f"evt_{len(self.added)}"
        memory_id = f"mem_{len(self.added)}"
        record = MemoryRecord(
            memory_id=memory_id, text=text, user_id=customer_id, metadata=metadata or {}
        )
        self.events[event_id] = (customer_id, record)
        return MemoryResult(
            ok=True,
            operation="add",
            tool="add_memory",
            event_id=event_id,
            event_status="PENDING",
        )

    def wait_for_event(self, event_id: str, timeout: float | None = None, interval: float | None = None) -> MemoryEventResult:
        customer_id, record = self.events[event_id]
        if self.event_status != "SUCCEEDED":
            return MemoryEventResult(
                ok=False,
                event_id=event_id,
                status=self.event_status,
                error=("Mem0 event failed" if self.event_status == "FAILED" else None),
            )
        if record not in self._for(customer_id):
            self._for(customer_id).append(record)
        return MemoryEventResult(
            ok=True,
            event_id=event_id,
            status="SUCCEEDED",
            memory_ids=[record.memory_id],
            texts=[record.text],
        )

    def update_memory(self, customer_id: str, memory_id: str, text: str) -> MemoryResult:
        if self.fail_update:
            return MemoryResult(ok=False, operation="update", error="Mem0 update failed")
        for index, record in enumerate(self._for(customer_id)):
            if record.memory_id == memory_id:
                self._for(customer_id)[index] = MemoryRecord(
                    memory_id=record.memory_id,
                    text=text,
                    metadata=record.metadata,
                    user_id=customer_id,
                )
                self.updated.append((memory_id, text))
                return MemoryResult(ok=True, operation="update", records=[self._for(customer_id)[index]])
        return MemoryResult(
            ok=False, operation="update", error=f"Memory {memory_id} does not belong to {customer_id}"
        )

    def delete_memory(self, customer_id: str, memory_id: str) -> MemoryResult:
        if self.fail_delete:
            return MemoryResult(ok=False, operation="delete", error="Mem0 delete failed")
        records = self._for(customer_id)
        remaining = [record for record in records if record.memory_id != memory_id]
        if len(remaining) == len(records):
            return MemoryResult(
                ok=False,
                operation="delete",
                error=f"Memory {memory_id} does not belong to {customer_id}",
            )
        self._memories[customer_id] = remaining
        self.deleted.append(memory_id)
        return MemoryResult(ok=True, operation="delete")

    def texts_for(self, customer_id: str) -> list[str]:
        return [record.text for record in self._for(customer_id)]


class FakeProductService:
    """In-process product service backed by the real catalogue."""

    def __init__(self, products: list[dict[str, Any]], fail: bool = False) -> None:
        from app.products.product_client import ProductAPIError

        self.products = products
        self.fail = fail
        self.error = ProductAPIError("Product API is unavailable at http://localhost:8000")
        self.fetches: list[dict[str, Any]] = []

    def available_brands(self) -> list[str]:
        if self.fail:
            raise self.error
        return sorted({product["brand"] for product in self.products})

    def fetch_products(
        self,
        *,
        category: str | None = None,
        max_budget: float | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        if self.fail:
            raise self.error
        self.fetches.append(
            {"category": category, "max_budget": max_budget, "query": query}
        )
        results = []
        for product in self.products:
            if category and product["category"].casefold() != str(category).casefold():
                continue
            if max_budget is not None and product["price"] > max_budget:
                continue
            results.append(product)
        return results


@pytest.fixture
def settings() -> Settings:
    """Settings with a dummy key so tests never read a developer's .env."""
    return Settings(
        mem0_api_key="test-key",
        mem0_mcp_url="https://mcp.mem0.ai/mcp",
        ollama_model="llama3.1",
        max_recommendations=5,
        memory_top_k=20,
    )


def make_deps(
    settings: Settings,
    products: list[dict[str, Any]],
    memory: FakeMemoryService | None = None,
    llm: FakeLLM | None = None,
    product_service: FakeProductService | None = None,
) -> WorkflowDeps:
    """Assemble deterministic dependencies for graph tests."""
    memory = memory or FakeMemoryService()
    return WorkflowDeps(
        memory_service=memory,  # type: ignore[arg-type]
        product_service=product_service or FakeProductService(products),  # type: ignore[arg-type]
        llm_client=llm or FakeLLM(),  # type: ignore[arg-type]
        settings=settings,
        memory_manager=MemoryManager(memory),  # type: ignore[arg-type]
    )


def make_graph(deps: WorkflowDeps):
    """Build a graph with a fresh in-memory checkpointer."""
    return build_graph(deps=deps)
