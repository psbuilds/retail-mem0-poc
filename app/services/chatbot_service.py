"""Reusable chatbot entry point.

This is the boundary between any frontend (Streamlit today, FastAPI later) and
the LangGraph workflow.  It imports no UI code and returns structured Python
data, so a future ``POST /chat`` endpoint can call ``process_message`` unchanged.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from app.agent.graph import get_graph
from app.config.settings import get_settings
from app.llm.ollama_client import ollama_status
from app.memory.memory_service import (
    MemoryResult,
    MemoryServiceStatus,
    get_memory_service,
)
from app.products.product_service import product_api_status

ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class ChatResponse(BaseModel):
    """Structured result of one chatbot turn."""

    ok: bool = True
    customer_id: str = ""
    session_id: str = ""
    response: str = ""
    recommendations: list[dict[str, Any]] = Field(default_factory=list)
    memory_status: str = "unknown"
    memory_saved: list[str] = Field(default_factory=list)
    memory_pending: list[str] = Field(default_factory=list)
    memory_writes: dict[str, Any] = Field(default_factory=dict)
    memory_actions: list[dict[str, Any]] = Field(default_factory=list)
    memory_events: list[dict[str, Any]] = Field(default_factory=list)
    requirements: dict[str, Any] = Field(default_factory=dict)
    relaxed_preferences: list[str] = Field(default_factory=list)
    product_status: str = "unknown"
    llm_status: str = "unknown"
    # Routing diagnostics
    route: dict[str, Any] = Field(default_factory=dict)
    route_guards: list[str] = Field(default_factory=list)
    router_usage: dict[str, Any] = Field(default_factory=dict)
    response_kind: str = ""
    execution_path: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


def _id_error(field_name: str, value: str) -> ChatResponse | None:
    """Return an error response when an identifier is missing or malformed."""
    if not value:
        return ChatResponse(
            ok=False,
            response=f"{field_name} is required.",
            memory_status="error",
            errors=[f"Missing {field_name}"],
        )
    if not ID_PATTERN.match(value):
        return ChatResponse(
            ok=False,
            response=(
                f"{field_name} must be 1–64 characters using letters, digits, "
                "underscores or hyphens."
            ),
            memory_status="error",
            errors=[f"Invalid {field_name}"],
        )
    return None


def process_message(
    customer_id: str,
    session_id: str,
    message: str,
    graph: Any | None = None,
) -> ChatResponse:
    """Handle one customer message through the LangGraph workflow.

    Parameters:
        customer_id: Mem0 ``user_id`` used to scope long-term memory.
        session_id: Conversation thread id for LangGraph checkpointing.
        message: The customer's natural-language message.
        graph: Optional pre-built graph (used by tests).

    Returns:
        ``ChatResponse`` with recommendations, memory status and diagnostics.
        Failures are reported in the response rather than raised, except for
        truly unexpected programming errors which are caught and surfaced too.
    """
    customer_id = (customer_id or "").strip()
    session_id = (session_id or "").strip()
    message = (message or "").strip()

    for field_name, value in (("customer_id", customer_id), ("session_id", session_id)):
        error = _id_error(field_name, value)
        if error is not None:
            error.session_id = session_id
            return error

    if not message:
        return ChatResponse(
            ok=False,
            customer_id=customer_id,
            session_id=session_id,
            response="Please enter a shopping request.",
            memory_status="unknown",
            errors=["Empty user message"],
        )

    workflow = graph or get_graph()
    try:
        state = workflow.invoke(
            {
                "customer_id": customer_id,
                "session_id": session_id,
                "user_message": message,
            },
            config={"configurable": {"thread_id": session_id}},
        )
    except Exception as exc:  # pragma: no cover - defensive boundary
        return ChatResponse(
            ok=False,
            customer_id=customer_id,
            session_id=session_id,
            response=f"The assistant hit an unexpected error: {exc}",
            memory_status="error",
            errors=[str(exc)],
        )

    memory_writes = state.get("memory_writes") or {}
    return ChatResponse(
        ok=True,
        customer_id=customer_id,
        session_id=session_id,
        response=state.get("response", ""),
        recommendations=state.get("recommendations") or [],
        memory_status=state.get("memory_status", "unknown"),
        memory_saved=memory_writes.get("saved", []) or [],
        memory_pending=memory_writes.get("pending", []) or [],
        memory_writes=memory_writes,
        memory_actions=state.get("memory_actions") or [],
        memory_events=state.get("memory_events") or [],
        requirements=state.get("resolved_requirements") or {},
        relaxed_preferences=state.get("relaxed_preferences") or [],
        product_status=state.get("product_status", "unknown"),
        llm_status=state.get("llm_status", "unknown"),
        route=state.get("route") or {},
        route_guards=state.get("route_guards") or [],
        router_usage=state.get("router_usage") or {},
        response_kind=state.get("response_kind", ""),
        execution_path=state.get("visited_steps") or [],
        errors=state.get("errors") or [],
    )


# ---------------------------------------------------------------------------
# Status and memory management helpers used by the UI
# ---------------------------------------------------------------------------
def check_services() -> dict[str, Any]:
    """Check Mem0 MCP, Ollama and the product API for status displays."""
    memory: MemoryServiceStatus = get_memory_service().status()
    ollama_ok, ollama_message = ollama_status()
    product_ok, product_message = product_api_status()
    settings = get_settings()
    router_enabled = bool(settings.jev_configured and settings.router_use_jev)
    return {
        "memory": {
            "ok": memory.connected,
            "configured": memory.configured,
            "message": memory.message,
            "tools": memory.tools,
        },
        "ollama": {"ok": ollama_ok, "message": ollama_message},
        "product_api": {"ok": product_ok, "message": product_message},
        "router": {
            "ok": router_enabled,
            "configured": settings.jev_configured,
            "model": settings.jev_model,
            "message": (
                f"Jev router ready ({settings.jev_model})"
                if router_enabled
                else "Deterministic rules mode (TYPESAFE_API_KEY not set "
                "or Jev disabled)"
            ),
        },
    }


def list_customer_memories(customer_id: str) -> MemoryResult:
    """Return the stored memories for one customer."""
    return get_memory_service().list_memories(customer_id)


def delete_customer_memory(customer_id: str, memory_id: str) -> MemoryResult:
    """Delete one customer memory after ownership verification."""
    return get_memory_service().delete_memory(customer_id, memory_id)


def clear_customer_memories(customer_id: str) -> MemoryResult:
    """Delete all memories for one customer."""
    return get_memory_service().delete_all_memories(customer_id)
