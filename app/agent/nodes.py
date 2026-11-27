"""LangGraph workflow nodes.

The router decides which of these nodes run for a message; nothing runs
unconditionally except the fixed entry pair ``prepare_turn``/``route_query``
and the closing ``generate_response``/``finalize_turn``.

Each processing node consumes exactly its own entry from ``pending_steps`` and
returns the updated queue.  ``next_step`` dispatches the head of that queue, so
the graph executes only the operations the message needs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.agent.prompts import (
    COMPARISON_SYSTEM_PROMPT,
    CONVERSATION_SYSTEM_PROMPT,
    REQUIREMENTS_SYSTEM_PROMPT,
    RESPONSE_SYSTEM_PROMPT,
    comparison_user_prompt,
    conversation_user_prompt,
    requirements_user_prompt,
    response_user_prompt,
)
from app.agent.state import AgentState
from app.config.settings import Settings
from app.llm.ollama_client import OllamaClient, OllamaError
from app.memory.memory_manager import MemoryAction, MemoryManager, MemoryWriteReport
from app.memory.memory_service import MemoryRecord, MemoryService
from app.products.product_client import ProductAPIError
from app.products.product_service import ProductService
from app.recommendations.recommendation_service import recommend
from app.routing.classifier import QueryRouter
from app.routing.rules import brand_polarity

MAX_HISTORY_MESSAGES = 12
MAX_VISITED_STEPS = 12

# Nodes the router may put in a plan (finalize_turn always closes it).
PLANNED_NODES: frozenset[str] = frozenset(
    {
        "manage_memory",
        "retrieve_memory",
        "understand_request",
        "search_products",
        "recommend_products",
        "generate_response",
        "finalize_turn",
    }
)


# ---------------------------------------------------------------------------
# Pydantic validation for LLM output
# ---------------------------------------------------------------------------
class RequirementExtraction(BaseModel):
    """Validated requirements extracted from the current message."""

    is_shopping_request: bool = True
    refines_previous_request: bool = False
    category: str | None = None
    brand: str | None = None
    excluded_brands: list[str] = Field(default_factory=list)
    max_budget: float | None = None
    wireless: bool | None = None
    noise_cancellation: bool | None = None
    required_features: list[str] = Field(default_factory=list)
    excluded_features: list[str] = Field(default_factory=list)

    @field_validator("category")
    @classmethod
    def normalise_category(cls, value: str | None) -> str | None:
        """Map common synonyms onto the catalogue's two categories."""
        if value is None:
            return None
        cleaned = value.strip().casefold()
        if cleaned in {"headphone", "headphones", "over-ear", "on-ear"}:
            return "headphones"
        if cleaned in {"earbud", "earbuds", "earphone", "earphones", "tws", "in-ear"}:
            return "earbuds"
        return None

    @field_validator("brand")
    @classmethod
    def clean_brand(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("excluded_brands")
    @classmethod
    def clean_excluded_brands(cls, value: list[str]) -> list[str]:
        return [str(item).strip() for item in value if str(item).strip()][:5]

    @field_validator("max_budget")
    @classmethod
    def sane_budget(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value <= 0:
            return None
        return float(value)

    @field_validator("required_features", "excluded_features")
    @classmethod
    def clean_features(cls, value: list[str]) -> list[str]:
        return [str(item).strip() for item in value if str(item).strip()][:6]


@dataclass
class WorkflowDeps:
    """Services injected into the graph (swappable in tests)."""

    memory_service: MemoryService
    product_service: ProductService
    llm_client: OllamaClient
    settings: Settings
    memory_manager: MemoryManager | None = None
    router: QueryRouter | None = None

    def __post_init__(self) -> None:
        if self.memory_manager is None:
            self.memory_manager = MemoryManager(self.memory_service)
        if self.router is None:
            self.router = QueryRouter(self.settings)


# ---------------------------------------------------------------------------
# Deterministic helpers
# ---------------------------------------------------------------------------
_MONEY_PATTERN = re.compile(
    r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)\s*(k|thousand)?"
    r"|(?:under|below|less than|within|up to|upto|max(?:imum)?|budget(?: of| around)?)"
    r"\s*₹?\s*([\d,]+(?:\.\d+)?)\s*(k|thousand)?",
    re.IGNORECASE,
)

_FEATURE_KEYWORDS: dict[str, str] = {
    "lightweight": "Lightweight",
    "long battery": "Long Battery Life",
    "extra bass": "Extra Bass",
    "foldable": "Foldable",
    "multipoint": "Multipoint",
    "hi-res": "Hi-Res Audio",
    "detachable cable": "Detachable Cable",
    "sweat resistant": "Sweat Resistant",
}

_SHOPPING_VERBS = (
    "show",
    "suggest",
    "recommend",
    "looking for",
    "want",
    "need",
    "buy",
    "find",
    "prefer",
)

_CHEAPER_PATTERN = re.compile(
    r"\b(cheaper|less expensive|lower price|cheapest)\b", re.IGNORECASE
)

_ORDINAL_PATTERN = re.compile(
    r"\b(first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th)\b", re.IGNORECASE
)

_ORDINALS = {
    "first": 1,
    "1st": 1,
    "second": 2,
    "2nd": 2,
    "third": 3,
    "3rd": 3,
    "fourth": 4,
    "4th": 4,
    "fifth": 5,
    "5th": 5,
}


def parse_budget(text: str) -> float | None:
    """Extract the first rupee budget mentioned in a message."""
    match = _MONEY_PATTERN.search(text)
    if not match:
        return None
    raw = match.group(1) or match.group(3)
    multiplier = match.group(2) or match.group(4)
    if raw is None:
        return None
    try:
        value = float(raw.replace(",", ""))
    except ValueError:
        return None
    if multiplier:
        value *= 1000
    return value if value > 0 else None


def extract_features(message: str) -> list[str]:
    """Map simple feature keywords onto catalogue feature names."""
    lowered = message.casefold()
    return [
        feature
        for keyword, feature in _FEATURE_KEYWORDS.items()
        if keyword in lowered
    ]


def match_brand(message: str, known_brands: list[str]) -> str | None:
    """Return the first positively mentioned catalogue brand."""
    brand, _ = brand_polarity(message, known_brands or None)
    return brand


def parse_memory_preferences(
    memories: list[str], known_brands: list[str]
) -> dict[str, Any]:
    """Derive structured preference hints from retrieved memory sentences.

    These are *hints* used only to fill fields the customer did not specify.
    Ambiguous memories (for example listing two brands) are skipped rather than
    guessed.
    """
    if not memories:
        return {}
    joined = " ".join(memories).casefold()
    preferences: dict[str, Any] = {}

    brand_mentions: dict[str, int] = {}
    for brand in known_brands:
        count = len(re.findall(rf"\b{re.escape(brand.casefold())}\b", joined))
        if count:
            brand_mentions[brand] = count
    if brand_mentions:
        top = max(brand_mentions.values())
        winners = [brand for brand, count in brand_mentions.items() if count == top]
        if len(winners) == 1:
            preferences["brand"] = winners[0]

    if "noise cancellation" in joined or "noise cancelling" in joined or re.search(r"\banc\b", joined):
        preferences["noise_cancellation"] = True
    if "wireless" in joined or "bluetooth" in joined:
        preferences["wireless"] = True
    elif re.search(r"\bwired\b", joined):
        preferences["wireless"] = False

    budget = parse_budget(joined)
    if budget is not None:
        preferences["max_budget"] = budget

    if "earbud" in joined or "earphone" in joined or "tws" in joined:
        preferences["category"] = "earbuds"
    elif "headphone" in joined:
        preferences["category"] = "headphones"

    return preferences


def heuristic_requirements(
    message: str, known_brands: list[str]
) -> RequirementExtraction:
    """Safe deterministic fallback when Ollama cannot extract requirements."""
    lowered = message.casefold()
    is_shopping = any(keyword in lowered for keyword in _SHOPPING_VERBS) or bool(
        parse_budget(message)
    )
    category: str | None = None
    if any(word in lowered for word in ("earbud", "earphone", "tws", "in-ear")):
        category = "earbuds"
    elif any(word in lowered for word in ("headphone", "over-ear", "on-ear")):
        category = "headphones"

    wireless: bool | None = None
    if "wireless" in lowered or "bluetooth" in lowered:
        wireless = True
    elif re.search(r"\bwired\b", lowered):
        wireless = False

    noise_cancellation: bool | None = None
    if "noise cancellation" in lowered or "noise cancelling" in lowered or re.search(
        r"\banc\b", lowered
    ):
        noise_cancellation = True
        if re.search(r"(without|no)\s+noise cancell", lowered):
            noise_cancellation = False

    excluded_features: list[str] = []
    if re.search(r"(without|no)\s+noise cancell", lowered):
        excluded_features.append("Noise Cancellation")

    brand, excluded_brands = brand_polarity(message, known_brands or None)

    # A short message with a constraint but no category usually refines the
    # previous request ("only Sony", "under 5000").
    refines = category is None and len(lowered.split()) <= 8 and (
        "only" in lowered or parse_budget(message) is not None or wireless is not None
    )

    return RequirementExtraction(
        is_shopping_request=is_shopping,
        refines_previous_request=refines,
        category=category,
        brand=brand,
        excluded_brands=excluded_brands,
        max_budget=parse_budget(message),
        wireless=wireless,
        noise_cancellation=noise_cancellation,
        required_features=extract_features(message),
        excluded_features=excluded_features,
    )


def ground_requirements(
    extraction: RequirementExtraction,
    message: str,
    known_brands: list[str],
) -> RequirementExtraction:
    """Verify LLM fields against the literal message text.

    The model interprets natural language, but every structured field must be
    supported by the current message.  This prevents a previous request or a
    remembered preference from leaking into the "current" extraction.  Brand
    polarity is resolved deterministically so a negated brand becomes an
    exclusion, never a requirement.
    """
    lowered = message.casefold()
    grounded = extraction.model_copy(deep=True)

    explicit_category: str | None = None
    if any(word in lowered for word in ("earbud", "earphone", "tws", "in-ear")):
        explicit_category = "earbuds"
    elif any(word in lowered for word in ("headphone", "over-ear", "on-ear")):
        explicit_category = "headphones"
    grounded.category = explicit_category

    brand, excluded_brands = brand_polarity(message, known_brands or None)
    grounded.brand = brand
    grounded.excluded_brands = excluded_brands

    if not re.search(r"\d", message):
        grounded.max_budget = None
    else:
        grounded.max_budget = parse_budget(message) or extraction.max_budget

    if "wireless" in lowered or "bluetooth" in lowered:
        grounded.wireless = True
    elif re.search(r"\bwired\b", lowered):
        grounded.wireless = False
    else:
        grounded.wireless = None

    if "noise cancellation" in lowered or "noise cancelling" in lowered or re.search(
        r"\banc\b", lowered
    ):
        grounded.noise_cancellation = not bool(
            re.search(r"(without|no)\s+noise cancell", lowered)
        )
    else:
        grounded.noise_cancellation = None

    grounded.required_features = extract_features(message)
    grounded.excluded_features = (
        ["Noise Cancellation"]
        if re.search(r"(without|no)\s+noise cancell", lowered)
        else []
    )
    return grounded


def resolve_requirements(
    current: RequirementExtraction,
    previous: dict[str, Any],
    memory_preferences: dict[str, Any],
    default_category: str = "headphones",
) -> dict[str, Any]:
    """Apply precedence: current > conversation > memory > defaults."""
    use_previous = bool(current.refines_previous_request)
    if (
        use_previous
        and current.category
        and previous.get("category")
        and current.category != previous.get("category")
    ):
        # A different category starts a new product search.
        use_previous = False

    resolved: dict[str, Any] = {}
    for field in ("category", "brand", "max_budget", "wireless", "noise_cancellation"):
        value = getattr(current, field)
        if value is None and use_previous:
            value = previous.get(field)
        if value is None:
            value = memory_preferences.get(field)
        resolved[field] = value

    if resolved["category"] is None:
        resolved["category"] = default_category

    if current.required_features:
        resolved["required_features"] = current.required_features
    elif use_previous and previous.get("required_features"):
        resolved["required_features"] = previous["required_features"]
    else:
        resolved["required_features"] = []

    resolved["excluded_features"] = current.excluded_features or (
        previous.get("excluded_features", []) if use_previous else []
    )
    excluded_brands = list(current.excluded_brands)
    if use_previous:
        for brand in previous.get("excluded_brands", []):
            if brand not in excluded_brands:
                excluded_brands.append(brand)
    resolved["excluded_brands"] = excluded_brands
    resolved["excluded_product_ids"] = []
    return resolved


def describe_requirements(requirements: dict[str, Any]) -> str:
    """Create a short human-readable requirement summary for fallback replies."""
    parts: list[str] = []
    brand = requirements.get("brand")
    category = requirements.get("category") or "products"
    parts.append(f"{brand} {category}" if brand else str(category))
    if requirements.get("max_budget"):
        parts.append(f"under ₹{int(requirements['max_budget']):,}")
    if requirements.get("wireless") is True:
        parts.append("wireless")
    if requirements.get("noise_cancellation") is True:
        parts.append("with noise cancellation")
    features = requirements.get("required_features") or []
    if features:
        parts.append("with " + ", ".join(features))
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Step-queue plumbing
# ---------------------------------------------------------------------------
def _consume(state: AgentState, name: str) -> dict[str, Any]:
    """Pop this node's step, record the visit and defend against bad plans."""
    steps = list(state.get("pending_steps") or [])
    visited = list(state.get("visited_steps") or [])
    errors = list(state.get("errors") or [])

    if steps and steps[0] == name:
        steps.pop(0)
    elif name in steps:
        errors.append(
            f"Routing plan order mismatch: expected '{steps[0]}' but ran '{name}'."
        )
        steps.remove(name)
    else:
        errors.append(f"Node '{name}' ran without being scheduled.")
    visited.append(name)

    # Hard bound: never let a malformed plan loop.
    if len(visited) >= MAX_VISITED_STEPS and steps:
        steps = ["finalize_turn"]

    return {"pending_steps": steps, "visited_steps": visited, "errors": errors}


def next_step(state: AgentState) -> str:
    """Return the next scheduled node, or END when the plan is exhausted."""
    from langgraph.graph import END

    steps = state.get("pending_steps") or []
    if not steps:
        return END
    candidate = steps[0]
    if candidate not in PLANNED_NODES:
        return "finalize_turn"
    visited = state.get("visited_steps") or []
    if candidate == "finalize_turn" and "finalize_turn" in visited:
        return END
    return candidate


def _with_errors(updates: dict[str, Any], extra: list[str]) -> dict[str, Any]:
    """Return ``updates`` with ``extra`` appended to the error list."""
    return {
        **updates,
        "errors": list(updates.get("errors") or []) + list(extra),
    }


# ---------------------------------------------------------------------------
# Fixed entry nodes
# ---------------------------------------------------------------------------
def prepare_turn(state: AgentState, deps: WorkflowDeps) -> dict[str, Any]:
    """Append the user message and reset per-turn fields.

    Persistent fields (conversation history, last resolved requirements and the
    previous shortlist) are preserved so follow-ups can reuse them.
    """
    history = list(state.get("conversation_history") or [])
    history.append({"role": "user", "content": state["user_message"]})
    return {
        "conversation_history": history[-MAX_HISTORY_MESSAGES:],
        "previous_recommendations": list(state.get("recommendations") or []),
        "customer_memories": [],
        "memory_status": "unknown",
        "memory_error": None,
        "requirements": {},
        "products": [],
        "product_status": "unknown",
        "recommendations": [],
        "response": "",
        "response_kind": "",
        "llm_error": None,
        "memory_applied": {},
        "relaxed_preferences": [],
        "memory_candidates": [],
        "memory_actions": [],
        "memory_events": [],
        "memory_writes": {},
        "route": {},
        "pending_steps": [],
        "visited_steps": ["prepare_turn"],
        "router_usage": {},
        "route_guards": [],
        "errors": [],
    }


def route_query(state: AgentState, deps: WorkflowDeps) -> dict[str, Any]:
    """Classify the message and build the ordered step plan."""
    router = deps.router or QueryRouter(deps.settings)
    try:
        decision, plan = router.route(
            state["user_message"],
            state.get("conversation_history") or [],
            state.get("previous_recommendations") or [],
        )
    except Exception as exc:  # pragma: no cover - router already falls back
        errors = list(state.get("errors") or []) + [
            f"Query routing failed; using a safe conversational plan ({exc})"
        ]
        return {
            "route": {"intent": "general_question", "source": "fallback"},
            "pending_steps": ["generate_response", "finalize_turn"],
            "visited_steps": ["prepare_turn", "route_query"],
            "router_usage": {},
            "route_guards": [],
            "errors": errors,
        }

    return {
        "route": decision.model_dump(),
        "pending_steps": plan,
        "visited_steps": ["prepare_turn", "route_query"],
        "router_usage": {
            "source": decision.source,
            "model": decision.model,
            "input_tokens": decision.input_tokens,
            "latency_ms": decision.latency_ms,
            "confident": decision.confident,
        },
        "route_guards": decision.guards,
    }


# ---------------------------------------------------------------------------
# Processing nodes
# ---------------------------------------------------------------------------
def retrieve_memory(state: AgentState, deps: WorkflowDeps) -> dict[str, Any]:
    """Retrieve relevant long-term preferences for this customer."""
    updates = _consume(state, "retrieve_memory")
    result = deps.memory_service.search_memories(
        state["customer_id"],
        state["user_message"],
        limit=deps.settings.memory_top_k,
    )
    if not result.ok:
        status = (
            "not_configured"
            if "not configured" in (result.error or "").casefold()
            or "MEM0_API_KEY" in (result.error or "")
            else "error"
        )
        return _with_errors(
            updates,
            [f"Memory retrieval failed: {result.error}"],
        ) | {
            "customer_memories": [],
            "memory_status": status,
            "memory_error": result.error,
        }
    memories = [record.text for record in result.records]
    return updates | {
        "customer_memories": memories,
        "memory_status": "success" if memories else "empty",
        "memory_error": None,
    }


def _apply_followup(
    extraction: RequirementExtraction,
    message: str,
    route: dict[str, Any],
    previous_recommendations: list[dict[str, Any]],
) -> RequirementExtraction:
    """Turn a refinement follow-up into concrete requirements.

    "Show me cheaper ones" becomes a budget just below the cheapest product
    already recommended, and explicitly refines the previous request.
    """
    if route.get("followup_action") != "refine_search":
        return extraction

    refined = extraction.model_copy(deep=True)
    refined.refines_previous_request = True
    if _CHEAPER_PATTERN.search(message) and previous_recommendations:
        prices = [
            float(item["price"])
            for item in previous_recommendations
            if isinstance(item.get("price"), (int, float))
        ]
        if prices:
            refined.max_budget = max(min(prices) - 1, 1.0)
    return refined


def understand_request(state: AgentState, deps: WorkflowDeps) -> dict[str, Any]:
    """Extract current requirements with Ollama, then resolve precedence."""
    updates = _consume(state, "understand_request")
    errors = list(updates["errors"])
    try:
        known_brands = deps.product_service.available_brands()
    except ProductAPIError:
        known_brands = []

    llm_status = "success"
    llm_error: str | None = None
    previous = dict(state.get("resolved_requirements") or {})
    route = state.get("route") or {}

    try:
        raw = deps.llm_client.chat_json(
            [
                {"role": "system", "content": REQUIREMENTS_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": requirements_user_prompt(
                        state["user_message"],
                        previous,
                        state.get("conversation_history") or [],
                        state.get("customer_memories") or [],
                    ),
                },
            ]
        )
        current = ground_requirements(
            RequirementExtraction.model_validate(raw),
            state["user_message"],
            known_brands,
        )
    except (OllamaError, ValidationError, ValueError) as exc:
        llm_status = "degraded"
        llm_error = str(exc)
        current = heuristic_requirements(state["user_message"], known_brands)
        errors.append(
            "Ollama request understanding failed; used a deterministic fallback "
            f"({exc})"
        )

    current = _apply_followup(
        current,
        state["user_message"],
        route,
        state.get("previous_recommendations") or [],
    )

    memory_preferences = parse_memory_preferences(
        state.get("customer_memories") or [], known_brands
    )
    resolved = resolve_requirements(current, previous, memory_preferences)

    # Record which fields came only from memory, so they can be relaxed if they
    # would otherwise make an explicit current request impossible to satisfy.
    use_previous = bool(current.refines_previous_request)
    memory_applied: dict[str, Any] = {}
    for field, value in memory_preferences.items():
        if getattr(current, field, None) is not None:
            continue
        if use_previous and previous.get(field) is not None:
            continue
        if resolved.get(field) == value:
            memory_applied[field] = value

    return {
        **updates,
        "requirements": current.model_dump(),
        "previous_requirements": previous,
        "resolved_requirements": resolved,
        "memory_applied": memory_applied,
        "llm_status": llm_status,
        "llm_error": llm_error,
        "errors": errors,
    }


def search_products(state: AgentState, deps: WorkflowDeps) -> dict[str, Any]:
    """Fetch candidate products from the FastAPI catalogue."""
    updates = _consume(state, "search_products")
    requirements = state.get("resolved_requirements") or {}
    try:
        products = deps.product_service.fetch_products(
            category=requirements.get("category"),
            max_budget=requirements.get("max_budget"),
        )
    except ProductAPIError as exc:
        return _with_errors(
            updates, [f"Product API error: {exc}"]
        ) | {"products": [], "product_status": "error"}
    return updates | {
        "products": products,
        "product_status": "success" if products else "empty",
    }


def recommend_products(state: AgentState, deps: WorkflowDeps) -> dict[str, Any]:
    """Apply deterministic filtering and ranking.

    Remembered feature preferences are soft constraints: if they would make an
    otherwise valid explicit request return nothing, they are relaxed and the
    relaxation is reported.  Explicit current instructions are never relaxed.
    """
    updates = _consume(state, "recommend_products")
    requirements = dict(state.get("resolved_requirements") or {})
    products = state.get("products") or []
    recommendations = recommend(
        products, requirements, limit=deps.settings.max_recommendations
    )

    relaxed: list[str] = []
    memory_applied = state.get("memory_applied") or {}
    if not recommendations and memory_applied:
        relaxed_requirements = dict(requirements)
        for field in ("noise_cancellation", "wireless"):
            if field in memory_applied and relaxed_requirements.get(field) is not None:
                relaxed_requirements[field] = None
                relaxed.append(field)
        if relaxed:
            recommendations = recommend(
                products,
                relaxed_requirements,
                limit=deps.settings.max_recommendations,
            )
            if recommendations:
                requirements = relaxed_requirements

    return updates | {
        "recommendations": recommendations,
        "resolved_requirements": requirements,
        "relaxed_preferences": relaxed if recommendations else [],
    }


def manage_memory(state: AgentState, deps: WorkflowDeps) -> dict[str, Any]:
    """Add, correct, update or delete memories through Mem0 MCP.

    Corrections run first (the planner puts this node before retrieval) and use
    only ids returned by a scoped search.  Preference additions are attempted
    only after application policy accepts them, and every write is reported as
    saved/pending/failed based on the real Mem0 event.
    """
    updates = _consume(state, "manage_memory")
    errors = list(updates["errors"])
    route = state.get("route") or {}
    customer_id = state["customer_id"]
    message = state["user_message"]
    assert deps.memory_manager is not None
    manager = deps.memory_manager

    actions: list[MemoryAction] = []
    skipped: list[str] = []
    correction_candidates: list[MemoryRecord] = []
    corrected_with_new = False

    # 1. Corrections and withdrawals (always before new preferences).
    if route.get("changes_memory"):
        search = deps.memory_service.search_memories(
            customer_id, message, limit=deps.settings.memory_top_k
        )
        if not search.ok:
            errors.append(f"Memory search for correction failed: {search.error}")
            actions.append(
                MemoryAction(
                    kind="update",
                    status="failed",
                    text=message,
                    error=search.error,
                )
            )
        else:
            correction_candidates = list(search.records)
            plan = manager.correction_plan(message, correction_candidates)
            plan_actions = manager.apply_correction(customer_id, plan)
            actions.extend(plan_actions)
            for action in plan_actions:
                if action.status == "failed":
                    errors.append(
                        f"Memory {action.kind} failed: {action.error or action.text}"
                    )
                if action.ok and action.kind == "update":
                    corrected_with_new = True
            if plan.new_brand and any(action.ok for action in plan_actions):
                corrected_with_new = True

    # 2. A durable preference stated in this message (withdrawal clauses are
    # stripped so "Forget Sony. I prefer JBL." stores only the new preference).
    if route.get("states_preference") and not corrected_with_new:
        preference_text = manager.preference_text(message)
        allowed, reason = manager.should_store(preference_text)
        if allowed:
            action = manager.store(
                customer_id,
                preference_text,
                {
                    "source": "retail-memory-assistant",
                    "kind": "persistent_preference",
                },
            )
            actions.append(action)
            if action.status == "failed":
                errors.append(f"Memory write failed: {action.error or action.text}")
        else:
            skipped.append(f"{preference_text or message} ({reason})")

    report = MemoryWriteReport(actions=actions, skipped=skipped)
    events = [
        {"kind": action.kind, "event_id": action.event_id, "status": action.status}
        for action in actions
        if action.event_id
    ]
    return {
        **updates,
        "memory_candidates": [
            {"memory_id": record.memory_id, "text": record.text}
            for record in correction_candidates
        ],
        "memory_actions": [action.as_dict() for action in actions],
        "memory_events": events,
        "memory_writes": report.as_dict(),
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Response generation
# ---------------------------------------------------------------------------
def _response_kind(state: AgentState) -> str:
    """Choose the response mode from the route and what actually ran."""
    route = state.get("route") or {}
    actions = state.get("memory_actions") or []
    followup = route.get("followup_action")
    has_previous = bool(state.get("previous_recommendations"))

    if state.get("product_status") == "error":
        return "error"
    # Comparing or explaining earlier recommendations never needs a new search.
    if followup == "compare" and has_previous:
        return "comparison"
    if followup == "explain" and has_previous:
        return "explanation"
    if route.get("wants_products"):
        return "product"
    if route.get("intent") == "memory_question" or (
        route.get("needs_personalisation") and not actions
    ):
        return "memory"
    if actions:
        return "confirmation"
    return "conversational"


def _product_fallback(recommendations: list[dict[str, Any]]) -> str:
    """Deterministic product summary used when Ollama is unavailable."""
    lines = [f"I found {len(recommendations)} matching product(s):"]
    for product in recommendations:
        lines.append(
            f"- {product.get('name')} — ₹{product.get('price')} "
            f"(⭐ {product.get('rating')})"
        )
    return "\n".join(lines)


def _memory_answer(state: AgentState) -> str:
    """Answer a memory question using only retrieved records."""
    status = state.get("memory_status")
    memories = state.get("customer_memories") or []
    if status in {"error", "not_configured", "unavailable", "unknown"}:
        detail = state.get("memory_error") or "the memory service is unavailable"
        return (
            "I couldn't read your saved preferences just now, so I can't tell "
            f"you what I remember. Details: {detail}"
        )
    if not memories:
        return (
            "I don't have any long-term preferences saved for you yet. Tell me "
            "things like your preferred brand or features and I can remember them."
        )
    lines = ["Here is what I have saved for you:"]
    lines.extend(f"- {memory}" for memory in memories)
    return "\n".join(lines)


def _confirmation_answer(state: AgentState) -> str:
    """Report memory outcomes truthfully, without an LLM."""
    actions = state.get("memory_actions") or []
    if not actions:
        skipped = (state.get("memory_writes") or {}).get("skipped") or []
        reason = skipped[0] if skipped else "there was nothing durable to save"
        return f"I didn't save anything from that message — {reason}."

    saved_lines: list[str] = []
    pending_lines: list[str] = []
    failed_lines: list[str] = []
    for action in actions:
        kind = action.get("kind")
        status = action.get("status")
        text = action.get("text") or ""
        if status == "saved" and kind == "add":
            saved_lines.append(f"Saved to your long-term preferences: {text}")
        elif status == "saved" and kind == "update":
            saved_lines.append(f"Updated your saved preference to: {text}")
        elif status == "deleted":
            saved_lines.append(f"Removed the saved preference: {text}")
        elif status == "pending":
            pending_lines.append(
                "Mem0 is still processing this preference, so I can't confirm "
                f"it is saved yet: {text}"
            )
        elif status == "not_found":
            failed_lines.append(
                action.get("error")
                or "I couldn't find a saved memory matching that request."
            )
        elif status == "failed":
            failed_lines.append(
                f"I couldn't complete that memory change: {action.get('error') or text}"
            )

    parts = saved_lines + pending_lines + failed_lines
    return "\n".join(parts) if parts else "No memory changes were made."


def _comparison_answer(state: AgentState, kind: str, deps: WorkflowDeps) -> str:
    """Compare or explain earlier recommendations using only those products."""
    recommendations = state.get("previous_recommendations") or []
    errors = list(state.get("errors") or [])
    if not recommendations:
        return "I don't have earlier recommendations in this conversation to compare."

    # "Tell me more about the second one" -> explain just that product.
    focus = recommendations
    if kind == "explanation":
        match = _ORDINAL_PATTERN.search(state["user_message"])
        if match:
            index = _ORDINALS.get(match.group(1).casefold())
            if index and index <= len(recommendations):
                focus = [recommendations[index - 1]]

    try:
        response = deps.llm_client.chat(
            [
                {"role": "system", "content": COMPARISON_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": comparison_user_prompt(
                        state["user_message"],
                        focus,
                        state.get("resolved_requirements") or {},
                        "explain" if kind == "explanation" else "compare",
                    ),
                },
            ],
            temperature=0.3,
        )
    except OllamaError as exc:
        errors.append(f"Ollama comparison failed: {exc}")
        response = _product_fallback(focus)

    return response


def generate_response(state: AgentState, deps: WorkflowDeps) -> dict[str, Any]:
    """Generate one response appropriate to the turn.

    Product and comparison text is grounded in deterministic results; memory
    confirmations and memory answers are built from real outcomes, never
    invented by a model.
    """
    updates = _consume(state, "generate_response")
    errors = list(updates["errors"])
    kind = _response_kind(state)
    requirements = state.get("resolved_requirements") or {}
    recommendations = state.get("recommendations") or []

    if kind == "error":
        return {
            **updates,
            "response_kind": kind,
            "response": (
                "I couldn't reach the product catalogue, so I can't recommend "
                "anything right now. Please make sure the product API is running "
                "and try again."
            ),
        }

    if kind == "memory":
        return {
            **updates,
            "response_kind": kind,
            "response": _memory_answer(state),
        }

    if kind == "confirmation":
        return {
            **updates,
            "response_kind": kind,
            "response": _confirmation_answer(state),
        }

    if kind in {"comparison", "explanation"}:
        response = _comparison_answer(state, kind, deps)
        return {
            **updates,
            "response_kind": kind,
            "response": response,
            "errors": errors,
        }

    if kind == "conversational":
        try:
            response = deps.llm_client.chat(
                [
                    {"role": "system", "content": CONVERSATION_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": conversation_user_prompt(
                            state["user_message"],
                            state.get("conversation_history") or [],
                        ),
                    },
                ],
                temperature=0.4,
            )
        except OllamaError as exc:
            errors.append(f"Ollama conversation failed: {exc}")
            return {
                **updates,
                "response_kind": kind,
                "response": (
                    "I can help with headphones and earbuds, but the local LLM "
                    f"(Ollama) is unavailable right now. Details: {exc}"
                ),
                "llm_status": "error",
                "llm_error": str(exc),
                "errors": errors,
            }
        return {
            **updates,
            "response_kind": kind,
            "response": response,
            "llm_status": "success",
        }

    # Product recommendations.
    if not recommendations:
        return {
            **updates,
            "response_kind": "product",
            "response": (
                "I couldn't find any products in the catalogue matching "
                f"{describe_requirements(requirements)}. Try raising the budget or "
                "removing a requirement."
            ),
        }

    relaxed = state.get("relaxed_preferences") or []
    try:
        response = deps.llm_client.chat(
            [
                {"role": "system", "content": RESPONSE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": response_user_prompt(
                        state["user_message"],
                        requirements,
                        state.get("customer_memories") or [],
                        recommendations,
                        state.get("memory_status", "unknown"),
                        relaxed,
                    ),
                },
            ],
            temperature=0.3,
        )
    except OllamaError as exc:
        errors.append(f"Ollama response generation failed: {exc}")
        return {
            **updates,
            "response_kind": "product",
            "response": (
                f"I found {len(recommendations)} matching product(s), shown below, "
                "but the local LLM (Ollama) is unavailable so I can't generate an "
                f"explanation. Details: {exc}"
            ),
            "llm_status": "error",
            "llm_error": str(exc),
            "errors": errors,
        }

    # A combined turn (product request + preference) still reports the real
    # memory outcome, in the same single response.
    if state.get("memory_actions"):
        response = f"{response}\n\n{_confirmation_answer(state)}"

    return {
        **updates,
        "response_kind": "product",
        "response": response,
        "llm_status": "success",
    }


def finalize_turn(state: AgentState, deps: WorkflowDeps) -> dict[str, Any]:
    """Record the assistant reply and close the step queue."""
    updates = _consume(state, "finalize_turn")
    history = list(state.get("conversation_history") or [])
    response = state.get("response") or ""
    if response:
        history.append({"role": "assistant", "content": response})
    return {
        **updates,
        "conversation_history": history[-MAX_HISTORY_MESSAGES:],
    }
