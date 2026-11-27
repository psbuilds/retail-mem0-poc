"""Router tests: shortcuts, Jev parsing, guards, fallback and step planning.

No network is used.  ``FakeJev`` returns scripted answers with the verified
response shape, so every failure mode can be exercised deterministically.
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

import httpx

from app.config.settings import Settings
from app.routing.classifier import PLANNED_NODES, QueryRouter, build_step_plan
from app.routing.jev_client import JevClient, JevError, JevResponse
from app.routing.models import RouteDecision
from app.routing.questions import NOUL_IDS, build_questions
from app.routing.rules import (
    apply_guards,
    brand_polarity,
    detect_followup_action,
    explicit_memory_operation,
    heuristic_route,
    is_greeting_only,
)

PREVIOUS = [
    {"product_id": "p1", "name": "Cheap One", "price": 2999},
    {"product_id": "p2", "name": "Nice One", "price": 4999},
]


def jev_answers(
    *,
    nouls: dict[str, float] | None = None,
    intent: str = "general_question",
    followup: str = "none",
    intent_confidence: float = 0.95,
) -> dict[str, Any]:
    """Build a response map in the verified Jev answer shape."""
    values = {question_id: 0.02 for question_id in NOUL_IDS}
    values.update(nouls or {})
    answers: dict[str, Any] = {
        key: {"type": "noul", "noul": value} for key, value in values.items()
    }
    answers["intent"] = {
        "type": "choice",
        "choice": intent,
        "confidence": intent_confidence,
        "probabilities": {intent: intent_confidence},
    }
    answers["followup_action"] = {
        "type": "choice",
        "choice": followup,
        "confidence": 0.9,
        "probabilities": {followup: 0.9},
    }
    return answers


class FakeJev:
    """Scripted Jev client."""

    def __init__(self, answers: dict[str, Any] | None = None, error: Exception | None = None):
        self.answers = answers or jev_answers()
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def classify(self, state: str, questions: dict[str, Any]) -> JevResponse:
        self.calls.append((state, questions))
        if self.error is not None:
            raise self.error
        return JevResponse(
            answers=self.answers,
            model="jev-test",
            input_tokens=321,
            output_tokens=12,
            latency_ms=7,
        )


def router_with(jev: FakeJev | None = None, **overrides: Any) -> QueryRouter:
    # Without an explicit fake, use deterministic rules mode (no Jev key), so
    # scenario tests do not depend on scripted model answers.
    key = "test-key" if jev is not None else ""
    settings = Settings(typesafe_api_key=key, **overrides)
    return QueryRouter(settings, jev_client=jev)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Jev contract
# ---------------------------------------------------------------------------
def test_jev_probabilities_become_flags() -> None:
    jev = FakeJev(
        jev_answers(
            nouls={"wants_products": 0.94, "states_preference": 0.88},
            intent="product_search",
        )
    )
    decision, plan = router_with(jev).route("Show me wireless headphones under 5000")

    assert decision.wants_products is True
    assert decision.states_preference is True
    assert decision.changes_memory is False
    assert decision.source in {"jev", "jev+guards"}
    assert decision.model == "jev-test"
    assert decision.input_tokens == 321
    assert "manage_memory" in plan
    assert plan == [
        "manage_memory",
        "understand_request",
        "search_products",
        "recommend_products",
        "generate_response",
        "finalize_turn",
    ]


def test_jev_answer_ids_are_echoed_and_questions_are_typed() -> None:
    jev = FakeJev()
    router_with(jev).route("Hello there, what can you do?")
    _, questions = jev.calls[0]
    assert set(questions) == set(NOUL_IDS) | {"intent", "followup_action"}
    assert questions["wants_products"]["type"] == "noul"
    assert questions["intent"]["type"] == "choice"


def test_jev_state_has_message_and_recent_turns_but_no_customer_id() -> None:
    jev = FakeJev()
    history = [
        {"role": "user", "content": "first message"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "current message"},
    ]
    router_with(jev).route("current message", history)
    state, _ = jev.calls[0]
    assert "current message" in state
    assert "first message" in state
    assert "customer_001" not in state


@pytest.mark.parametrize(
    "error",
    [
        JevError("Jev request failed: timed out"),
        JevError("Jev authentication failed", status_code=401),
        JevError("Jev credits exhausted", status_code=402),
        JevError("Jev returned HTTP 429", status_code=429),
        JevError("Jev returned HTTP 500", status_code=500),
    ],
)
def test_jev_failures_fall_back_to_rules(error: JevError) -> None:
    decision, plan = router_with(FakeJev(error=error)).route(
        "Show me earbuds under ₹5,000"
    )
    assert decision.source == "fallback"
    assert decision.confident is False
    assert decision.wants_products is True
    assert "understand_request" in plan
    assert any(guard.startswith("jev_unavailable") for guard in decision.guards)


@pytest.mark.parametrize(
    "answers",
    [
        {"intent": {"type": "choice", "choice": "product_search"}},
        jev_answers(intent="teleportation"),
    ],
)
def test_malformed_jev_answers_fall_back(answers: dict[str, Any]) -> None:
    decision, _ = router_with(FakeJev(answers)).route("Show me headphones")
    assert decision.source == "fallback"
    assert decision.confident is False


def test_low_confidence_is_marked_but_still_used() -> None:
    jev = FakeJev(
        jev_answers(
            nouls={"wants_products": 0.55, "needs_personalisation": 0.45},
            intent="product_search",
            intent_confidence=0.51,
        )
    )
    decision, _ = router_with(jev).route("Show me headphones")
    assert decision.source.startswith("jev")
    assert decision.confident is False
    assert decision.probabilities["wants_products"] == pytest.approx(0.55)


def test_router_without_key_never_calls_jev() -> None:
    jev = FakeJev(error=AssertionError("should not be called"))
    router = QueryRouter(Settings(typesafe_api_key=""), jev_client=jev)  # type: ignore[arg-type]
    decision, _ = router.route("Show me headphones")
    assert jev.calls == []
    assert decision.source == "rules"
    assert "jev_not_configured" in decision.guards


# ---------------------------------------------------------------------------
# Deterministic shortcuts and guards
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Forget that I prefer Sony.", False),
        ("Forget Sony. I prefer JBL.", True),
        ("Forget Sony, I prefer JBL now", True),
    ],
)
def test_withdrawal_does_not_hide_a_new_preference(
    message: str, expected: bool
) -> None:
    decision, _ = router_with().route(message)
    assert decision.changes_memory is True
    assert decision.states_preference is expected


def test_greeting_shortcut_skips_jev() -> None:
    jev = FakeJev(error=AssertionError("should not be called"))
    decision, plan = router_with(jev).route("Hi!")
    assert jev.calls == []
    assert decision.source == "rules"
    assert decision.intent == "greeting"
    assert plan == ["generate_response", "finalize_turn"]


@pytest.mark.parametrize(
    "message",
    ["Forget that I prefer Sony.", "Stop remembering that I like Bose.", "I no longer prefer Sony."],
)
def test_explicit_memory_shortcuts_skip_jev(message: str) -> None:
    jev = FakeJev(error=AssertionError("should not be called"))
    decision, plan = router_with(jev).route(message)
    assert jev.calls == []
    assert decision.changes_memory is True
    assert decision.intent == "memory_correction"
    assert plan[:1] == ["manage_memory"]
    assert plan[-2:] == ["generate_response", "finalize_turn"]


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Forget that I prefer Sony.", "delete"),
        ("I no longer prefer Sony.", "correct"),
        ("I don't like JBL anymore.", "correct"),
        ("I prefer Sony headphones.", None),
        ("Show me JBL headphones.", None),
        ("I don't like in-ear models.", None),
    ],
)
def test_explicit_memory_operation_distinguishes_meaning(
    message: str, expected: str | None
) -> None:
    assert explicit_memory_operation(message) == expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("I prefer JBL, not Sony.", ("JBL", ["Sony"])),
        ("I don't like JBL anymore.", (None, ["JBL"])),
        ("Show me Sony headphones.", ("Sony", [])),
        ("Anything but boAt.", (None, ["boAt"])),
    ],
)
def test_brand_polarity(message: str, expected: tuple[str | None, list[str]]) -> None:
    assert brand_polarity(message) == expected


def test_guard_adds_product_search_when_jev_misses_it() -> None:
    decision = RouteDecision(intent="greeting", source="jev")
    guarded = apply_guards(decision, "Hi, show me Sony headphones")
    assert guarded.wants_products is True
    assert guarded.source == "jev+guards"
    assert "shopping_verb_with_product" in guarded.guards


def test_guard_detects_durable_preference_language() -> None:
    decision = RouteDecision(intent="general_question", source="jev")
    guarded = apply_guards(decision, "I always want wireless headphones")
    assert guarded.states_preference is True
    assert "durable_preference_marker" in guarded.guards


def test_guard_suppresses_temporary_scope() -> None:
    decision = RouteDecision(intent="preference_statement", states_preference=True, source="jev")
    guarded = apply_guards(decision, "Show me something under 5000 as a gift")
    assert guarded.states_preference is False
    assert "temporary_scope_suppressed" in guarded.guards


def test_refinement_guard_uses_previous_context() -> None:
    decision = RouteDecision(intent="general_question", source="jev")
    guarded = apply_guards(decision, "Only Sony.", has_previous_context=True)
    assert guarded.wants_products is True
    assert guarded.followup_action == "refine_search"
    assert "refinement_of_previous_request" in guarded.guards


def test_greeting_detection_is_narrow() -> None:
    assert is_greeting_only("Hi")
    assert is_greeting_only("Hello there!")
    assert not is_greeting_only("Hi, show me Sony headphones")
    assert not is_greeting_only("What is noise cancellation?")


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Which is better?", "compare"),
        ("Show me cheaper ones.", "refine_search"),
        ("Tell me more about the second one.", "explain"),
        ("Show me headphones.", "none"),
    ],
)
def test_followup_action_detection(message: str, expected: str) -> None:
    assert detect_followup_action(message) == expected


# ---------------------------------------------------------------------------
# Required scenarios -> route + plan
# ---------------------------------------------------------------------------
SCENARIOS = [
    ("Hi", "greeting", set()),
    ("What is noise cancellation?", "general_question", set()),
    ("What do you remember about me?", "memory_question", {"retrieve_memory"}),
    ("I usually prefer Sony headphones", "preference_statement", {"manage_memory"}),
    ("Forget that I prefer Sony", "memory_correction", {"manage_memory"}),
    ("I no longer prefer Sony", "memory_correction", {"manage_memory"}),
    (
        "Show me earbuds under ₹5,000",
        "product_search",
        {"understand_request", "search_products", "recommend_products"},
    ),
    (
        "I prefer Sony; show me something under ₹5,000",
        "product_search",
        {"manage_memory", "understand_request", "search_products", "recommend_products"},
    ),
    (
        "Recommend headphones based on my preferences",
        "product_search",
        {"retrieve_memory", "understand_request", "search_products", "recommend_products"},
    ),
    (
        "I don't like JBL anymore; show me alternatives",
        "memory_correction",
        {"manage_memory", "understand_request", "search_products", "recommend_products"},
    ),
    (
        "Remember I like ANC and show me options",
        "product_search",
        {"manage_memory", "understand_request", "search_products", "recommend_products"},
    ),
    (
        "Show me cheaper ones",
        "product_followup",
        {"understand_request", "search_products", "recommend_products"},
    ),
    ("Which is better?", "product_followup", {"generate_response"}),
    ("Tell me more about the second one", "product_followup", {"generate_response"}),
    (
        "I prefer JBL, not Sony. Recommend something based on my preferences.",
        "memory_correction",
        {"manage_memory", "retrieve_memory", "understand_request", "search_products", "recommend_products"},
    ),
]


@pytest.mark.parametrize(("message", "intent", "expected_steps"), SCENARIOS)
def test_scenario_routes_and_plans(
    message: str, intent: str, expected_steps: set[str]
) -> None:
    decision, plan = router_with().route(message, [], PREVIOUS)
    assert decision.intent == intent
    assert expected_steps.issubset(set(plan))
    # Invariants every plan must satisfy.
    assert plan[-1] == "finalize_turn"
    assert plan.count("generate_response") == 1
    assert plan.count("finalize_turn") == 1
    assert len(plan) == len(set(plan))
    assert set(plan) <= set(PLANNED_NODES)
    if "manage_memory" in plan:
        assert plan[0] == "manage_memory"


def test_greeting_never_schedules_shopping_or_memory() -> None:
    decision, plan = router_with().route("Hi", [], PREVIOUS)
    assert decision.wants_products is False
    assert decision.needs_personalisation is False
    assert decision.refers_to_previous is False
    assert plan == ["generate_response", "finalize_turn"]


def test_comparison_reuses_previous_products_without_search() -> None:
    decision, plan = router_with().route("Which is better?", [], PREVIOUS)
    assert decision.wants_products is False
    assert decision.refers_to_previous is True
    assert "search_products" not in plan
    assert "understand_request" not in plan


def test_comparison_without_previous_products_searches() -> None:
    decision, plan = router_with().route("Which is better?", [], [])
    assert decision.wants_products is True
    assert "search_products" in plan


def test_correction_runs_before_retrieval() -> None:
    decision, plan = router_with().route(
        "I don't like JBL anymore; show me alternatives", [], PREVIOUS
    )
    assert plan.index("manage_memory") < plan.index("understand_request")


def test_multi_flag_message_keeps_all_capabilities() -> None:
    decision, plan = router_with().route(
        "I prefer JBL, not Sony. Recommend something based on my preferences."
    )
    assert decision.changes_memory and decision.states_preference
    assert decision.wants_products and decision.needs_personalisation
    assert plan[:2] == ["manage_memory", "retrieve_memory"]


# ---------------------------------------------------------------------------
# Planner hardening
# ---------------------------------------------------------------------------
def test_plan_rejects_unknown_and_duplicate_steps() -> None:
    decision = RouteDecision(wants_products=True, states_preference=True)
    plan = build_step_plan(decision, PREVIOUS)
    assert "route_query" not in plan
    assert plan.count("manage_memory") == 1


def test_next_step_forces_finalize_on_unknown_step() -> None:
    from app.agent.nodes import next_step

    assert next_step({"pending_steps": ["delete_everything"], "visited_steps": []}) == (
        "finalize_turn"
    )
    assert next_step({"pending_steps": [], "visited_steps": []}) == "__end__"


def test_next_step_stops_after_finalize() -> None:
    from app.agent.nodes import next_step

    assert (
        next_step(
            {"pending_steps": ["finalize_turn"], "visited_steps": ["finalize_turn"]}
        )
        == "__end__"
    )


def test_visit_budget_forces_finalization() -> None:
    from app.agent.nodes import MAX_VISITED_STEPS, _consume

    state = {
        "pending_steps": ["retrieve_memory", "generate_response", "finalize_turn"],
        "visited_steps": ["x"] * (MAX_VISITED_STEPS - 1),
        "errors": [],
    }
    updates = _consume(state, "retrieve_memory")
    assert updates["pending_steps"] == ["finalize_turn"]


def test_heuristic_route_never_sends_greeting_to_search() -> None:
    decision = heuristic_route("Hello!")
    assert decision.intent == "greeting"
    assert decision.wants_products is False


# ---------------------------------------------------------------------------
# HTTP client error mapping (httpx mock transport, no network)
# ---------------------------------------------------------------------------
def jev_http_client(handler, **overrides: Any) -> JevClient:
    options: dict[str, Any] = {"typesafe_api_key": "test-key", "jev_retry_attempts": 0}
    options.update(overrides)
    settings = Settings(**options)
    return JevClient(
        settings,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_jev_client_parses_verified_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload["model"] == "jev-latest"
        assert "questions" in payload
        return httpx.Response(
            200,
            json={
                "model": "jev-latest",
                "answers": {
                    "wants_products": {"type": "noul", "noul": 0.97},
                    "intent": {
                        "type": "choice",
                        "choice": "product_search",
                        "confidence": 0.98,
                    },
                },
                "usage": {"input_tokens": 428, "output_tokens": 12},
            },
        )

    response = jev_http_client(handler).classify("Show me headphones", build_questions())
    assert response.noul("wants_products") == pytest.approx(0.97)
    assert response.choice("intent") == "product_search"
    assert response.input_tokens == 428


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "authentication"),
        (403, "authentication"),
        (402, "credits"),
        (400, "rejected"),
    ],
)
def test_jev_client_maps_http_errors(status: int, expected: str) -> None:
    client = jev_http_client(lambda request: httpx.Response(status, text="nope"))
    with pytest.raises(JevError, match=expected):
        client.classify("hello", build_questions())


@pytest.mark.parametrize("status", [429, 500, 503])
def test_jev_client_retries_then_reports(status: int) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(status, text="unavailable")

    client = jev_http_client(handler, jev_retry_attempts=1)
    with pytest.raises(JevError, match=str(status)):
        client.classify("hello", build_questions())
    assert len(calls) == 2


def test_jev_client_reports_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    with pytest.raises(JevError, match="Jev request failed"):
        jev_http_client(handler).classify("hello", build_questions())


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"answers": {}}),
        httpx.Response(200, json={"unexpected": True}),
    ],
)
def test_jev_client_rejects_malformed_payloads(response: httpx.Response) -> None:
    client = jev_http_client(lambda request: response)
    with pytest.raises(JevError):
        client.classify("hello", build_questions())


# ---------------------------------------------------------------------------
# Live Jev (skipped without a key)
# ---------------------------------------------------------------------------
@pytest.mark.live
@pytest.mark.skipif(
    not os.environ.get("TYPESAFE_API_KEY"),
    reason="TYPESAFE_API_KEY not set — live Jev test skipped",
)
def test_live_jev_returns_typed_answers() -> None:
    from app.routing.jev_client import JevClient

    settings = Settings()
    client = JevClient(settings)
    response = client.classify(
        "Hi, show me Sony headphones under 5000.",
        build_questions(),
    )
    assert response.input_tokens > 0
    assert response.noul("wants_products") is not None
    assert response.choice("intent") is not None
