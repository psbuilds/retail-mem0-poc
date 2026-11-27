"""Shared LangGraph state for the shopping assistant.

The state is a plain ``TypedDict``.  LangGraph checkpoints it per
``thread_id`` (= session id), which is how follow-up messages such as
"only Sony" reuse the previous shopping request.

Conversation state is intentionally separate from long-term memory:
clearing a chat starts a new session id, while Mem0 preferences remain.

Routing fields make the turn's plan explicit.  ``pending_steps`` is consumed by
each node as it runs and ``visited_steps`` is the audit trail; both are reset
by ``prepare_turn``.
"""

from __future__ import annotations

from typing import Any, TypedDict


class ChatMessage(TypedDict):
    """One chat turn kept for context."""

    role: str  # "user" | "assistant"
    content: str


class AgentState(TypedDict, total=False):
    """State passed between workflow nodes.

    Fields are grouped by the node that produces them.  Per-turn fields are
    reset by ``prepare_turn`` so checkpoints do not leak stale results into the
    next message.
    """

    # Request identity
    customer_id: str
    session_id: str
    user_message: str

    # Conversation context (checkpointed per session)
    conversation_history: list[ChatMessage]

    # Routing node
    route: dict[str, Any]  # serialised RouteDecision
    pending_steps: list[str]  # ordered node allowlist for this turn
    visited_steps: list[str]  # nodes actually executed
    router_usage: dict[str, Any]  # model, input tokens, latency
    route_guards: list[str]  # deterministic guards applied after Jev

    # Memory retrieval node
    customer_memories: list[str]
    memory_status: str  # success | empty | error | unavailable | not_configured
    memory_error: str | None

    # Understanding node
    requirements: dict[str, Any]  # explicitly extracted from this message
    previous_requirements: dict[str, Any]
    resolved_requirements: dict[str, Any]  # after precedence rules
    memory_applied: dict[str, Any]  # fields filled from long-term memory
    relaxed_preferences: list[str]  # memory filters relaxed to avoid empty results
    llm_status: str  # success | degraded | error
    llm_error: str | None

    # Product node
    products: list[dict[str, Any]]
    previous_recommendations: list[dict[str, Any]]  # last turn's shortlist
    product_status: str  # success | empty | error

    # Recommendation node
    recommendations: list[dict[str, Any]]

    # Response node
    response: str
    response_kind: str  # conversational | product | comparison | explanation |
    #                     memory | confirmation | error

    # Memory management node
    memory_candidates: list[dict[str, Any]]  # retrieved records considered
    memory_actions: list[dict[str, Any]]  # verified add/update/delete outcomes
    memory_events: list[dict[str, Any]]  # event ids and their final status
    memory_writes: dict[str, Any]  # aggregate report (kept for the public API)

    # Diagnostics
    errors: list[str]
