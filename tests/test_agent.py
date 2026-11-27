"""LangGraph workflow tests with deterministic fakes (no network)."""

from __future__ import annotations

from typing import Any

import pytest

from app.services.chatbot_service import process_message
from tests.conftest import (
    FakeLLM,
    FakeMemoryService,
    FakeProductService,
    make_deps,
    make_graph,
)


def _sony_memories() -> FakeMemoryService:
    return FakeMemoryService(
        memories=[
            "Usually prefers Sony headphones with noise cancellation.",
            "Always prefers wireless headphones.",
        ]
    )


def test_new_customer_without_memories(
    settings, catalogue: list[dict[str, Any]]
) -> None:
    memory = FakeMemoryService()
    llm = FakeLLM(requirements={"is_shopping_request": True, "category": "headphones"})
    graph = make_graph(make_deps(settings, catalogue, memory=memory, llm=llm))

    result = process_message("customer_002", "session-a", "Suggest headphones for me", graph=graph)

    assert result.ok
    assert result.memory_status == "empty"
    assert result.recommendations
    assert all(item["category"] == "headphones" for item in result.recommendations)


def test_existing_customer_preferences_are_applied(
    settings, catalogue: list[dict[str, Any]]
) -> None:
    memory = _sony_memories()
    llm = FakeLLM(requirements={"is_shopping_request": True})
    graph = make_graph(make_deps(settings, catalogue, memory=memory, llm=llm))

    result = process_message("customer_001", "session-b", "Suggest headphones for me", graph=graph)

    assert result.ok
    assert result.memory_status == "success"
    assert result.recommendations
    assert all(item["brand"] == "Sony" for item in result.recommendations)
    assert all(item["noise_cancellation"] for item in result.recommendations)
    assert result.requirements["brand"] == "Sony"


def test_current_request_overrides_persistent_memory(
    settings, catalogue: list[dict[str, Any]]
) -> None:
    memory = _sony_memories()
    llm = FakeLLM(
        requirements_by_message={
            "Show me JBL headphones under ₹3,000 this time.": {
                "is_shopping_request": True,
                "category": "headphones",
                "brand": "JBL",
                "max_budget": 3000,
            }
        },
    )
    graph = make_graph(make_deps(settings, catalogue, memory=memory, llm=llm))

    result = process_message(
        "customer_001", "session-c", "Show me JBL headphones under ₹3,000 this time.", graph=graph
    )

    assert result.ok
    assert result.requirements["brand"] == "JBL"
    assert result.requirements["max_budget"] == 3000
    assert result.recommendations
    assert all(item["brand"] == "JBL" for item in result.recommendations)
    assert all(item["price"] <= 3000 for item in result.recommendations)
    # A temporary, purchase-scoped request must not be persisted.
    assert result.memory_saved == []
    assert result.memory_actions == []
    assert memory.added == []


def test_persistent_preference_is_saved(settings, catalogue: list[dict[str, Any]]) -> None:
    memory = FakeMemoryService()
    llm = FakeLLM(
        requirements={"is_shopping_request": True, "refines_previous_request": False}
    )
    graph = make_graph(make_deps(settings, catalogue, memory=memory, llm=llm))
    message = "I usually prefer Sony headphones with noise cancellation."

    result = process_message("customer_002", "session-d", message, graph=graph)

    assert result.ok
    # Only a verified Mem0 event counts as saved.
    assert result.memory_saved == [message]
    assert result.memory_actions[0]["status"] == "saved"
    assert result.execution_path == [
        "prepare_turn",
        "route_query",
        "manage_memory",
        "generate_response",
        "finalize_turn",
    ]
    assert memory.added and memory.added[0][0] == "customer_002"


def test_follow_up_conversation_context(
    settings, catalogue: list[dict[str, Any]]
) -> None:
    memory = FakeMemoryService()
    llm = FakeLLM(
        requirements_by_message={
            "Show me wireless headphones.": {
                "is_shopping_request": True,
                "category": "headphones",
                "wireless": True,
            },
            "Only Sony.": {
                "is_shopping_request": True,
                "brand": "Sony",
                "refines_previous_request": True,
            },
            "Under ₹5,000.": {
                "is_shopping_request": True,
                "max_budget": 5000,
                "refines_previous_request": True,
            },
        }
    )
    graph = make_graph(make_deps(settings, catalogue, memory=memory, llm=llm))

    first = process_message("customer_002", "session-e", "Show me wireless headphones.", graph=graph)
    assert first.requirements["wireless"] is True

    second = process_message("customer_002", "session-e", "Only Sony.", graph=graph)
    assert second.requirements["brand"] == "Sony"
    assert second.requirements["wireless"] is True

    third = process_message("customer_002", "session-e", "Under ₹5,000.", graph=graph)
    assert third.requirements["brand"] == "Sony"
    assert third.requirements["max_budget"] == 5000
    assert third.requirements["wireless"] is True
    assert third.recommendations
    assert all(item["brand"] == "Sony" for item in third.recommendations)
    assert all(item["price"] <= 5000 for item in third.recommendations)
    assert all(item["wireless"] for item in third.recommendations)


def test_new_session_does_not_reuse_conversation_but_keeps_memories(
    settings, catalogue: list[dict[str, Any]]
) -> None:
    memory = _sony_memories()
    llm = FakeLLM(
        requirements_by_message={
            "Show me wireless headphones.": {
                "is_shopping_request": True,
                "category": "headphones",
                "wireless": True,
            },
            "Suggest headphones for me.": {"is_shopping_request": True},
        }
    )
    graph = make_graph(make_deps(settings, catalogue, memory=memory, llm=llm))

    process_message("customer_001", "session-f1", "Show me wireless headphones.", graph=graph)
    fresh = process_message(
        "customer_001", "session-f2", "Suggest headphones for me.", graph=graph
    )

    # Sony preference comes from long-term memory, not from the previous session.
    assert fresh.requirements["brand"] == "Sony"
    assert fresh.memory_status == "success"


def test_memory_retrieval_failure_continues(
    settings, catalogue: list[dict[str, Any]]
) -> None:
    memory = FakeMemoryService(fail_search=True)
    llm = FakeLLM(requirements={"is_shopping_request": True, "category": "headphones"})
    graph = make_graph(make_deps(settings, catalogue, memory=memory, llm=llm))

    result = process_message(
        "customer_001", "session-g", "Suggest headphones for me", graph=graph
    )

    assert result.ok
    assert result.memory_status == "error"
    assert result.recommendations
    assert result.memory_saved == []
    assert any("Memory retrieval failed" in error for error in result.errors)


def test_product_api_failure_does_not_fabricate_products(
    settings, catalogue: list[dict[str, Any]]
) -> None:
    product_service = FakeProductService(catalogue, fail=True)
    llm = FakeLLM(requirements={"is_shopping_request": True, "category": "headphones"})
    graph = make_graph(
        make_deps(
            settings,
            catalogue,
            memory=FakeMemoryService(),
            llm=llm,
            product_service=product_service,
        )
    )

    result = process_message("customer_001", "session-h", "Suggest headphones", graph=graph)

    assert result.ok
    assert result.product_status == "error"
    assert result.recommendations == []
    assert "couldn't reach the product catalogue" in result.response
    assert any("Product API error" in error for error in result.errors)


def test_ollama_failure_is_reported_and_fallback_used(
    settings, catalogue: list[dict[str, Any]]
) -> None:
    llm = FakeLLM(fail_json=True, fail_chat=True)
    graph = make_graph(make_deps(settings, catalogue, memory=FakeMemoryService(), llm=llm))

    result = process_message(
        "customer_001", "session-i", "Show me wireless Sony headphones under ₹5,000", graph=graph
    )

    assert result.ok
    assert result.llm_status == "error"
    assert result.recommendations  # deterministic fallback still searched
    assert all(item["brand"] == "Sony" for item in result.recommendations)
    assert all(item["wireless"] for item in result.recommendations)
    assert "local LLM (Ollama) is unavailable" in result.response
    assert any("Ollama" in error for error in result.errors)


def test_empty_message_is_rejected(settings, catalogue: list[dict[str, Any]]) -> None:
    graph = make_graph(make_deps(settings, catalogue))
    result = process_message("customer_001", "session-j", "   ", graph=graph)
    assert not result.ok
    assert "shopping request" in result.response


def test_invalid_identifiers_are_rejected(settings, catalogue: list[dict[str, Any]]) -> None:
    graph = make_graph(make_deps(settings, catalogue))
    assert not process_message("", "session-k", "hello", graph=graph).ok
    assert not process_message("customer_001", "", "hello", graph=graph).ok
    assert not process_message("bad id!", "session-k", "hello", graph=graph).ok


@pytest.mark.parametrize(
    ("message", "expected_category", "expected_budget"),
    [
        ("Show me wireless headphones under 3k", "headphones", 3000),
        ("Need earbuds below ₹2,299", "earbuds", 2299),
        ("Recommend noise cancelling headphones", "headphones", None),
    ],
)
def test_heuristic_requirement_parser(
    message: str, expected_category: str, expected_budget: float | None
) -> None:
    from app.agent.nodes import heuristic_requirements

    extraction = heuristic_requirements(message, ["Sony", "JBL"])
    assert extraction.category == expected_category
    assert extraction.max_budget == expected_budget
