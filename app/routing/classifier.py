"""Router orchestration: shortcuts, Jev, guards, validation and step planning.

Precedence for classification:

1. Deterministic shortcuts (greeting-only, explicit correction/forget).
2. Jev, with deterministic recall guards applied afterwards.
3. Rule-based fallback when Jev is unavailable or returns unusable output.

The planner is pure Python and only emits node names from an allowlist.  Jev
never supplies an executable node name.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.config.settings import Settings, get_settings
from app.routing.jev_client import JevClient, JevError, JevResponse
from app.routing.models import INTENTS, RouteDecision
from app.routing.questions import NOUL_IDS, build_questions
from app.routing.rules import (
    apply_guards,
    explicit_memory_operation,
    heuristic_route,
    is_greeting_only,
)

# Every node the planner may schedule.  route_query and prepare_turn are fixed
# entry nodes and are never part of the plan.
PLANNED_NODES: tuple[str, ...] = (
    "manage_memory",
    "retrieve_memory",
    "understand_request",
    "search_products",
    "recommend_products",
    "generate_response",
    "finalize_turn",
)

# Steps that may run at most once per turn.
_SINGLETON_STEPS = ("generate_response", "finalize_turn")

_MAX_STATE_CHARS = 4000


def _jev_state(message: str, history: list[dict[str, Any]], turns: int) -> str:
    """Build the compact state sent to Jev: current message + recent turns.

    Saved memories and the customer id are deliberately never included.
    """
    prior: list[dict[str, Any]] = []
    if history and history[-1].get("role") == "user" and history[-1].get("content") == message:
        prior = history[:-1]
    else:
        prior = list(history)
    prior = prior[-max(0, turns * 2) :]

    lines = []
    for item in prior:
        role = item.get("role", "user")
        content = str(item.get("content", "")).strip()
        if content:
            lines.append(f"{role}: {content}")
    body = "\n".join(lines) if lines else "(new conversation)"
    return (
        "Recent conversation:\n"
        f"{body}\n"
        "Current customer message:\n"
        f"{message.strip()}"
    )[:_MAX_STATE_CHARS]


def _decision_from_jev(response: JevResponse, settings: Settings) -> RouteDecision:
    """Convert raw Jev answers into a validated ``RouteDecision``.

    Raises:
        JevError: when a required answer is missing or malformed.
    """
    probabilities: dict[str, float] = {}
    for question_id in NOUL_IDS:
        value = response.noul(question_id)
        if value is None:
            raise JevError(f"Jev answer '{question_id}' is missing or invalid")
        probabilities[question_id] = value

    intent = response.choice("intent")
    if intent not in INTENTS:
        raise JevError(f"Jev returned an unsupported intent: {intent!r}")

    threshold = settings.router_confidence_threshold
    certainty = min(abs(value - 0.5) * 2 for value in probabilities.values())
    confident = certainty >= 0.2 and response.choice_confidence("intent") >= threshold

    return RouteDecision(
        intent=intent,
        wants_products=probabilities["wants_products"] >= threshold,
        refers_to_previous=probabilities["refers_to_previous"] >= threshold,
        states_preference=probabilities["states_preference"] >= threshold,
        changes_memory=probabilities["changes_memory"] >= threshold,
        needs_personalisation=probabilities["needs_personalisation"] >= threshold,
        followup_action=_valid_followup(response.choice("followup_action")),
        probabilities=probabilities,
        source="jev",
        confident=confident,
        model=response.model,
        input_tokens=response.input_tokens,
        latency_ms=response.latency_ms,
    )


def _valid_followup(value: str | None) -> str:
    """Return a supported follow-up action, defaulting to ``none``."""
    if value in {"none", "refine_search", "compare", "explain"}:
        return value
    return "none"


def _reconcile(decision: RouteDecision, previous_recommendations: list[dict[str, Any]]) -> None:
    """Make flags, intent and follow-up action consistent.

    A follow-up that compares or explains earlier products must not trigger a
    new catalogue search when those products still exist in the checkpoint.
    """
    if decision.changes_memory:
        decision.intent = "memory_correction"
    elif (
        decision.refers_to_previous
        and decision.followup_action in {"compare", "explain"}
    ):
        decision.intent = "product_followup"
    elif decision.wants_products:
        decision.intent = (
            "product_followup"
            if decision.refers_to_previous or decision.followup_action != "none"
            else "product_search"
        )
    elif decision.intent in {"product_search", "product_followup"}:
        # The model asked for products but the flag (and the guards) disagree.
        decision.intent = "general_question"

    if decision.refers_to_previous and not previous_recommendations:
        if decision.followup_action in {"compare", "explain"}:
            # Nothing to compare yet: fall back to a fresh catalogue search.
            decision.wants_products = True
            decision.followup_action = "none"
        else:
            # A refinement with no earlier shortlist is a fresh request.
            decision.refers_to_previous = False


def build_step_plan(
    decision: RouteDecision,
    previous_recommendations: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Return the ordered node plan for one turn.

    Only allowlisted nodes are emitted, singleton steps appear once, and the
    plan always ends with ``generate_response`` then ``finalize_turn``.
    """
    previous = previous_recommendations or []
    steps: list[str] = []

    if decision.changes_memory or decision.states_preference:
        steps.append("manage_memory")

    if decision.needs_personalisation or decision.intent == "memory_question":
        steps.append("retrieve_memory")

    answer_from_previous = (
        decision.refers_to_previous
        and decision.followup_action in {"compare", "explain"}
        and bool(previous)
    )
    if decision.wants_products and not answer_from_previous:
        steps.extend(["understand_request", "search_products", "recommend_products"])

    steps.extend(["generate_response", "finalize_turn"])

    # Enforce allowlist, singleton steps and stable order without duplicates.
    cleaned: list[str] = []
    for step in steps:
        if step not in PLANNED_NODES:
            continue
        if step in _SINGLETON_STEPS and step in cleaned:
            continue
        if step in cleaned:
            continue
        cleaned.append(step)
    return cleaned


class QueryRouter:
    """Classify one message and plan the turn."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        jev_client: JevClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.jev = jev_client or JevClient(self.settings)

    def route(
        self,
        message: str,
        history: list[dict[str, Any]] | None = None,
        previous_recommendations: list[dict[str, Any]] | None = None,
    ) -> tuple[RouteDecision, list[str]]:
        """Return ``(decision, plan)`` for the current message.

        Never raises for model/transport failures: it falls back to rules so a
        customer turn cannot fail because Jev is unavailable.
        """
        history = list(history or [])
        previous = list(previous_recommendations or [])

        has_context = bool(previous)

        # Layer 1: deterministic shortcuts.
        if is_greeting_only(message) or explicit_memory_operation(message):
            decision = heuristic_route(message, has_context)
            decision.source = "rules"
            decision.confident = True
            _reconcile(decision, previous)
            return decision, build_step_plan(decision, previous)

        # Layer 2: Jev + guards, or Layer 3: rules fallback.
        if self.settings.router_use_jev and self.settings.jev_configured:
            try:
                response = self.jev.classify(
                    _jev_state(message, history, self.settings.router_send_history_turns),
                    build_questions(),
                )
                decision = _decision_from_jev(response, self.settings)
                decision = apply_guards(decision, message, has_context)
            except (JevError, ValidationError, ValueError) as exc:
                decision = heuristic_route(message, has_context)
                decision.source = "fallback"
                decision.confident = False
                decision.guards = [f"jev_unavailable: {type(exc).__name__}"]
        else:
            decision = heuristic_route(message, has_context)
            decision.source = "rules"
            decision.guards = list(decision.guards) + ["jev_not_configured"]

        _reconcile(decision, previous)
        return decision, build_step_plan(decision, previous)
