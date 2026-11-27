"""Routed workflow tests for the required scenarios.

Every test runs the real compiled LangGraph graph with deterministic fakes for
Ollama, Mem0 and the catalogue.  Assertions cover both the behaviour and the
execution path (``execution_path``), so a greeting regressing into the shopping
pipeline would fail here.
"""

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

PREVIOUS_PATH_PRODUCT = [
    "prepare_turn",
    "route_query",
    "understand_request",
    "search_products",
    "recommend_products",
    "generate_response",
    "finalize_turn",
]

SONY_MEMORIES = [
    "Usually prefers Sony headphones with noise cancellation.",
    "Always prefers wireless headphones.",
]


def run(graph: Any, message: str, customer: str = "customer_001", session: str = "s"):
    return process_message(customer, session, message, graph=graph)


def build(
    settings,
    catalogue,
    *,
    memories: list[str] | None = None,
    llm: FakeLLM | None = None,
    memory: FakeMemoryService | None = None,
    product_service: FakeProductService | None = None,
):
    memory = memory or FakeMemoryService(memories=memories)
    product_service = product_service or FakeProductService(catalogue)
    deps = make_deps(
        settings,
        catalogue,
        memory=memory,
        llm=llm or FakeLLM(),
        product_service=product_service,
    )
    return make_graph(deps), memory, product_service


# ---------------------------------------------------------------------------
# Workflow B: normal conversation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("message", ["Hi", "Hi, how are you?", "What is noise cancellation?"])
def test_normal_conversation_never_touches_products_or_memory(
    settings, catalogue, message: str
) -> None:
    graph, memory, products = build(settings, catalogue)

    result = run(graph, message, session="chat-normal")

    assert result.ok
    assert result.response_kind == "conversational"
    assert result.execution_path == [
        "prepare_turn",
        "route_query",
        "generate_response",
        "finalize_turn",
    ]
    assert result.recommendations == []
    assert products.fetches == []
    assert memory.search_calls == 0
    assert memory.added == []


# ---------------------------------------------------------------------------
# Workflow D: memory questions
# ---------------------------------------------------------------------------
def test_memory_question_lists_only_retrieved_records(settings, catalogue) -> None:
    graph, memory, products = build(settings, catalogue, memories=SONY_MEMORIES)

    result = run(graph, "What do you remember about me?", session="mem-q")

    assert result.response_kind == "memory"
    assert "retrieve_memory" in result.execution_path
    assert "search_products" not in result.execution_path
    assert products.fetches == []
    for memory_text in SONY_MEMORIES:
        assert memory_text in result.response


def test_memory_question_reports_retrieval_failure(settings, catalogue) -> None:
    memory = FakeMemoryService(fail_search=True)
    graph, _, _ = build(settings, catalogue, memory=memory)

    result = run(graph, "What do you remember about me?", session="mem-fail")

    assert result.response_kind == "memory"
    assert "couldn't read your saved preferences" in result.response
    assert any("Memory retrieval failed" in error for error in result.errors)


def test_customer_isolation_for_memory_questions(settings, catalogue) -> None:
    graph, _, _ = build(settings, catalogue, memories=SONY_MEMORIES)

    result = run(
        graph, "What do you remember about me?", customer="customer_002", session="iso"
    )

    assert "don't have any long-term preferences" in result.response
    assert all(memory not in result.response for memory in SONY_MEMORIES)


# ---------------------------------------------------------------------------
# Workflow A: store a preference
# ---------------------------------------------------------------------------
def test_preference_statement_is_saved_and_confirmed(settings, catalogue) -> None:
    graph, memory, products = build(settings, catalogue)
    message = "I usually prefer Sony headphones"

    result = run(graph, message, session="pref-save")

    assert result.response_kind == "confirmation"
    assert result.execution_path == [
        "prepare_turn",
        "route_query",
        "manage_memory",
        "generate_response",
        "finalize_turn",
    ]
    assert result.memory_saved == [message]
    assert result.memory_actions[0]["status"] == "saved"
    assert "Saved to your long-term preferences" in result.response
    assert products.fetches == []
    assert memory.added and memory.added[0][0] == "customer_001"


def test_pending_memory_write_is_not_reported_as_saved(settings, catalogue) -> None:
    memory = FakeMemoryService(event_status="PENDING")
    graph, _, _ = build(settings, catalogue, memory=memory)

    result = run(graph, "I usually prefer Sony headphones", session="pref-pending")

    assert result.memory_saved == []
    assert result.memory_pending
    assert "can't confirm" in result.response
    assert result.memory_actions[0]["status"] == "pending"


def test_failed_memory_write_is_reported(settings, catalogue) -> None:
    memory = FakeMemoryService(fail_add=True)
    graph, _, _ = build(settings, catalogue, memory=memory)

    result = run(graph, "I usually prefer Sony headphones", session="pref-fail")

    assert result.memory_saved == []
    assert "couldn't complete that memory change" in result.response
    assert result.memory_writes["status"] == "error"


def test_temporary_purchase_request_is_not_stored(settings, catalogue) -> None:
    graph, memory, _ = build(settings, catalogue)

    result = run(
        graph,
        "Show me JBL headphones under ₹3,000 this time.",
        session="temporary",
    )

    assert result.memory_actions == []
    assert result.memory_saved == []
    assert memory.added == []
    assert result.recommendations
    assert all(item["brand"] == "JBL" for item in result.recommendations)


# ---------------------------------------------------------------------------
# Workflow D: corrections
# ---------------------------------------------------------------------------
def test_forget_deletes_only_the_matching_preference(settings, catalogue) -> None:
    memory = FakeMemoryService(
        memories=["Usually prefers Sony headphones.", "Usually prefers JBL earbuds."]
    )
    graph, _, _ = build(settings, catalogue, memory=memory)

    result = run(graph, "Forget that I prefer Sony", session="forget")

    assert result.response_kind == "confirmation"
    assert "Removed the saved preference" in result.response
    assert memory.texts_for("customer_001") == ["Usually prefers JBL earbuds."]


def test_forget_and_new_preference_does_both(settings, catalogue) -> None:
    memory = FakeMemoryService(memories=["Usually prefers Sony headphones."])
    graph, _, _ = build(settings, catalogue, memory=memory)

    result = run(graph, "Forget Sony. I prefer JBL.", session="forget-and-prefer")

    statuses = {(action["kind"], action["status"]) for action in result.memory_actions}
    assert ("delete", "deleted") in statuses
    assert ("add", "saved") in statuses
    texts = memory.texts_for("customer_001")
    assert not any("Sony" in text for text in texts)
    assert any("JBL" in text for text in texts)
    assert "Removed the saved preference" in result.response
    assert "Saved to your long-term preferences" in result.response


def test_no_longer_prefer_deletes_the_old_preference(settings, catalogue) -> None:
    memory = FakeMemoryService(memories=["Usually prefers Sony headphones."])
    graph, _, _ = build(settings, catalogue, memory=memory)

    result = run(graph, "I no longer prefer Sony", session="no-longer")

    assert result.memory_actions[0]["status"] == "deleted"
    assert memory.texts_for("customer_001") == []


def test_forget_without_a_match_is_reported_honestly(settings, catalogue) -> None:
    memory = FakeMemoryService(memories=["Usually prefers Sony headphones."])
    graph, _, _ = build(settings, catalogue, memory=memory)

    result = run(graph, "Forget that I prefer Bose", session="forget-miss")

    assert result.memory_actions[0]["status"] == "not_found"
    assert "nothing was changed" in result.response
    assert memory.texts_for("customer_001") == ["Usually prefers Sony headphones."]


def test_negated_brand_is_excluded_not_required(settings, catalogue) -> None:
    memory = FakeMemoryService(memories=["Usually prefers JBL earbuds."])
    graph, _, products = build(settings, catalogue, memory=memory)

    result = run(
        graph,
        "I don't like JBL anymore; show me alternatives",
        session="negated",
    )

    assert result.recommendations
    assert all(item["brand"] != "JBL" for item in result.recommendations)
    assert result.requirements["excluded_brands"] == ["JBL"]
    assert memory.texts_for("customer_001") == []
    # Correction happens before any new catalogue search.
    assert products.fetches


def test_correction_replaces_old_preference_and_personalises(
    settings, catalogue
) -> None:
    memory = FakeMemoryService(memories=["User prefers Sony headphones."])
    graph, _, _ = build(settings, catalogue, memory=memory)

    result = run(
        graph,
        "I prefer JBL, not Sony. Recommend something based on my preferences.",
        session="replace",
    )

    path = result.execution_path
    assert path.index("manage_memory") < path.index("retrieve_memory")
    assert path.index("retrieve_memory") < path.index("search_products")
    assert result.recommendations
    assert all(item["brand"] == "JBL" for item in result.recommendations)
    assert any("JBL" in text for text in memory.texts_for("customer_001"))
    assert not any(
        "Sony" in text and "JBL" not in text for text in memory.texts_for("customer_001")
    )


# ---------------------------------------------------------------------------
# Workflow C: product search
# ---------------------------------------------------------------------------
def test_product_search_does_not_call_memory(settings, catalogue) -> None:
    graph, memory, products = build(settings, catalogue)

    result = run(graph, "Show me earbuds under ₹5,000", session="search")

    assert result.execution_path == PREVIOUS_PATH_PRODUCT
    assert result.recommendations
    assert all(item["category"] == "earbuds" for item in result.recommendations)
    assert all(item["price"] <= 5000 for item in result.recommendations)
    assert memory.search_calls == 0
    assert memory.added == []
    assert products.fetches


def test_personalised_recommendation_retrieves_memory(settings, catalogue) -> None:
    graph, memory, _ = build(settings, catalogue, memories=SONY_MEMORIES)

    result = run(
        graph, "Recommend headphones based on my preferences", session="personal"
    )

    assert "retrieve_memory" in result.execution_path
    assert memory.search_calls == 1
    assert result.recommendations
    assert all(item["brand"] == "Sony" for item in result.recommendations)
    assert all(item["noise_cancellation"] for item in result.recommendations)


def test_combined_preference_and_search_does_both(settings, catalogue) -> None:
    graph, memory, _ = build(settings, catalogue)

    result = run(
        graph, "Remember I like ANC and show me options", session="combined"
    )

    assert "manage_memory" in result.execution_path
    assert "search_products" in result.execution_path
    assert result.memory_saved
    assert result.recommendations
    assert all(item["noise_cancellation"] for item in result.recommendations)
    # One response, with the real memory outcome appended.
    assert result.response.count("Saved to your long-term preferences") == 1


def test_cheaper_followup_refines_previous_requirements(settings, catalogue) -> None:
    graph, _, products = build(settings, catalogue)

    first = run(graph, "Show me headphones", session="cheaper")
    assert first.recommendations
    previous_min = min(item["price"] for item in first.recommendations)
    fetches_before = len(products.fetches)

    second = run(graph, "Show me cheaper ones", session="cheaper")

    assert len(products.fetches) == fetches_before + 1
    assert second.requirements["max_budget"] < previous_min
    assert second.recommendations
    assert all(item["price"] <= second.requirements["max_budget"] for item in second.recommendations)


def test_compare_followup_does_not_search_again(settings, catalogue) -> None:
    graph, _, products = build(settings, catalogue)

    first = run(graph, "Show me headphones", session="compare")
    assert first.recommendations
    fetches_before = len(products.fetches)

    second = run(graph, "Which is better?", session="compare")

    assert second.response_kind == "comparison"
    assert len(products.fetches) == fetches_before
    assert "search_products" not in second.execution_path


def test_explain_followup_does_not_search_again(settings, catalogue) -> None:
    graph, _, products = build(settings, catalogue)

    run(graph, "Show me headphones", session="explain")
    fetches_before = len(products.fetches)

    result = run(graph, "Tell me more about the second one", session="explain")

    assert result.response_kind == "explanation"
    assert len(products.fetches) == fetches_before


# ---------------------------------------------------------------------------
# Plan invariants
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "message",
    [
        "Hi",
        "What do you remember about me?",
        "I usually prefer Sony headphones",
        "Show me earbuds under ₹5,000",
        "Which is better?",
    ],
)
def test_every_turn_has_one_response_and_reaches_finalize(
    settings, catalogue, message: str
) -> None:
    graph, _, _ = build(settings, catalogue, memories=SONY_MEMORIES)

    run(graph, "Show me headphones", session="inv")
    result = run(graph, message, session="inv")

    assert result.execution_path[-1] == "finalize_turn"
    assert result.execution_path.count("generate_response") == 1
    assert result.response_kind
    assert result.response
    # The conversation always ends with exactly one assistant turn per user turn.
    state = graph.get_state({"configurable": {"thread_id": "inv"}}).values
    history = state["conversation_history"]
    assert history[-1]["role"] == "assistant"
    assert [item["role"] for item in history].count("assistant") == 2
