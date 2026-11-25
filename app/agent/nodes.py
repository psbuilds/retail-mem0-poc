"""LangGraph workflow nodes.

Flow: prepare_turn -> retrieve_memory -> understand_request -> search_products
      -> recommend_products -> generate_response -> update_memory

Each node is a small pure-ish function that receives state and injected
services, performs one job, and returns only the state fields it owns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.agent.prompts import (
    PREFERENCE_EXTRACTION_SYSTEM_PROMPT,
    REQUIREMENTS_SYSTEM_PROMPT,
    RESPONSE_SYSTEM_PROMPT,
    preference_user_prompt,
    requirements_user_prompt,
    response_user_prompt,
)
from app.agent.state import AgentState
from app.config.settings import Settings
from app.llm.ollama_client import OllamaClient, OllamaError
from app.memory.memory_manager import MemoryManager, PreferenceCandidate
from app.memory.memory_service import MemoryService
from app.products.product_client import ProductAPIError
from app.products.product_service import ProductService
from app.recommendations.recommendation_service import recommend

MAX_HISTORY_MESSAGES = 12


# ---------------------------------------------------------------------------
# Pydantic validation for LLM output
# ---------------------------------------------------------------------------
class RequirementExtraction(BaseModel):
    """Validated requirements extracted from the current message."""

    is_shopping_request: bool = True
    refines_previous_request: bool = False
    category: str | None = None
    brand: str | None = None
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

    def __post_init__(self) -> None:
        if self.memory_manager is None:
            self.memory_manager = MemoryManager(self.memory_service)


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
    """Return the first catalogue brand mentioned in the message."""
    lowered = message.casefold()
    for brand in known_brands:
        if re.search(rf"\b{re.escape(brand.casefold())}\b", lowered):
            return brand
    return None


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

    # A short message with a constraint but no category usually refines the
    # previous request ("only Sony", "under 5000").
    refines = category is None and len(lowered.split()) <= 8 and (
        "only" in lowered or parse_budget(message) is not None or wireless is not None
    )

    return RequirementExtraction(
        is_shopping_request=is_shopping,
        refines_previous_request=refines,
        category=category,
        brand=match_brand(message, known_brands),
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
    remembered preference from leaking into the "current" extraction.  Fields
    the message does not mention become ``None`` and are filled later by the
    precedence rules.
    """
    lowered = message.casefold()
    grounded = extraction.model_copy(deep=True)

    explicit_category: str | None = None
    if any(word in lowered for word in ("earbud", "earphone", "tws", "in-ear")):
        explicit_category = "earbuds"
    elif any(word in lowered for word in ("headphone", "over-ear", "on-ear")):
        explicit_category = "headphones"
    grounded.category = explicit_category

    grounded.brand = match_brand(message, known_brands)

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
    resolved["excluded_brands"] = []
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
# Nodes
# ---------------------------------------------------------------------------
def prepare_turn(state: AgentState, deps: WorkflowDeps) -> dict[str, Any]:
    """Append the user message and reset per-turn fields.

    Persistent fields (conversation history, last resolved requirements) are
    deliberately preserved for follow-up requests.
    """
    history = list(state.get("conversation_history") or [])
    history.append({"role": "user", "content": state["user_message"]})
    return {
        "conversation_history": history[-MAX_HISTORY_MESSAGES:],
        "customer_memories": [],
        "memory_status": "unknown",
        "memory_error": None,
        "requirements": {},
        "products": [],
        "product_status": "unknown",
        "recommendations": [],
        "response": "",
        "llm_error": None,
        "memory_applied": {},
        "relaxed_preferences": [],
        "memory_candidates": [],
        "memory_writes": {},
        "errors": [],
    }


def retrieve_memory(state: AgentState, deps: WorkflowDeps) -> dict[str, Any]:
    """Retrieve relevant long-term preferences for this customer."""
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
        return {
            "customer_memories": [],
            "memory_status": status,
            "memory_error": result.error,
            "errors": [f"Memory retrieval failed: {result.error}"],
        }
    memories = [record.text for record in result.records]
    return {
        "customer_memories": memories,
        "memory_status": "success" if memories else "empty",
        "memory_error": None,
        "errors": [],
    }


def understand_request(state: AgentState, deps: WorkflowDeps) -> dict[str, Any]:
    """Extract current requirements with Ollama, then resolve precedence."""
    errors = list(state.get("errors") or [])
    try:
        known_brands = deps.product_service.available_brands()
    except ProductAPIError:
        known_brands = []

    llm_status = "success"
    llm_error: str | None = None
    previous = dict(state.get("resolved_requirements") or {})

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
    requirements = state.get("resolved_requirements") or {}
    try:
        products = deps.product_service.fetch_products(
            category=requirements.get("category"),
            max_budget=requirements.get("max_budget"),
        )
    except ProductAPIError as exc:
        return {
            "products": [],
            "product_status": "error",
            "errors": list(state.get("errors") or []) + [f"Product API error: {exc}"],
        }
    return {
        "products": products,
        "product_status": "success" if products else "empty",
    }


def recommend_products(state: AgentState, deps: WorkflowDeps) -> dict[str, Any]:
    """Apply deterministic filtering and ranking.

    Remembered feature preferences are soft constraints: if they would make an
    otherwise valid explicit request return nothing, they are relaxed and the
    relaxation is reported.  Explicit current instructions are never relaxed.
    """
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

    return {
        "recommendations": recommendations,
        "resolved_requirements": requirements,
        "relaxed_preferences": relaxed if recommendations else [],
    }


def generate_response(state: AgentState, deps: WorkflowDeps) -> dict[str, Any]:
    """Explain the deterministic recommendations with Ollama.

    If the catalogue is unreachable, no products are invented.  If Ollama is
    down, the response says so instead of pretending an explanation succeeded.
    """
    errors = list(state.get("errors") or [])
    requirements = state.get("resolved_requirements") or {}
    recommendations = state.get("recommendations") or []

    if state.get("product_status") == "error":
        return {
            "response": (
                "I couldn't reach the product catalogue, so I can't recommend "
                "anything right now. Please make sure the product API is running "
                "and try again."
            ),
            "errors": errors,
        }

    if not recommendations:
        return {
            "response": (
                "I couldn't find any products in the catalogue matching "
                f"{describe_requirements(requirements)}. Try raising the budget or "
                "removing a requirement."
            ),
            "errors": errors,
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
            "response": (
                f"I found {len(recommendations)} matching product(s), shown below, "
                "but the local LLM (Ollama) is unavailable so I can't generate an "
                f"explanation. Details: {exc}"
            ),
            "llm_status": "error",
            "llm_error": str(exc),
            "errors": errors,
        }

    return {"response": response, "llm_status": "success"}


def update_memory(state: AgentState, deps: WorkflowDeps) -> dict[str, Any]:
    """Extract stable preferences with Ollama and save them through Mem0 MCP."""
    errors = list(state.get("errors") or [])
    history = list(state.get("conversation_history") or [])
    history.append({"role": "assistant", "content": state.get("response", "")})

    memory_status = state.get("memory_status")
    if memory_status not in {"success", "empty"}:
        return {
            "conversation_history": history[-MAX_HISTORY_MESSAGES:],
            "memory_writes": {
                "status": "skipped",
                "reason": "Memory retrieval unavailable; not saving to avoid duplicates.",
                "saved": [],
                "skipped": [],
                "failures": [],
                "attempted": [],
            },
        }

    try:
        raw = deps.llm_client.chat_json(
            [
                {"role": "system", "content": PREFERENCE_EXTRACTION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": preference_user_prompt(
                        state["user_message"], state.get("conversation_history") or []
                    ),
                },
            ]
        )
        raw_preferences = raw.get("preferences", [])
        if not isinstance(raw_preferences, list):
            raise ValueError("preferences must be a list")
        candidates: list[PreferenceCandidate] = []
        for item in raw_preferences[:5]:
            if not isinstance(item, dict) or not item.get("text"):
                continue
            try:
                confidence = float(item.get("confidence", 0) or 0)
            except (TypeError, ValueError):
                confidence = 0.0
            candidates.append(
                PreferenceCandidate(
                    text=str(item["text"]),
                    kind=str(item.get("kind", "temporary")),
                    confidence=confidence,
                    reason=str(item.get("reason", "")),
                )
            )
    except (OllamaError, ValueError, ValidationError) as exc:
        errors.append(f"Preference extraction failed; nothing saved ({exc})")
        return {
            "conversation_history": history[-MAX_HISTORY_MESSAGES:],
            "memory_candidates": [],
            "memory_writes": {
                "status": "error",
                "reason": f"Preference extraction failed: {exc}",
                "saved": [],
                "skipped": [],
                "failures": [str(exc)],
                "attempted": [],
            },
            "errors": errors,
        }

    assert deps.memory_manager is not None
    report = deps.memory_manager.remember(
        customer_id=state["customer_id"],
        candidates=candidates,
        existing_memories=state.get("customer_memories") or [],
    )
    if report.failures:
        errors.append("Some preferences could not be saved: " + "; ".join(report.failures))

    return {
        "conversation_history": history[-MAX_HISTORY_MESSAGES:],
        "memory_candidates": [candidate.__dict__ for candidate in candidates],
        "memory_writes": {
            "status": report.status,
            "saved": report.saved,
            "skipped": report.skipped,
            "failures": report.failures,
            "attempted": report.attempted,
        },
        "errors": errors,
    }
