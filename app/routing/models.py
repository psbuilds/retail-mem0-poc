"""Validated routing models.

The router's output is a single ``RouteDecision``.  Nothing downstream reads
raw Jev answers: every field is parsed, range-checked and typed here, so a
malformed or unexpected model response becomes a validation failure (and thus a
deterministic fallback) instead of an unsafe execution plan.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Intent = Literal[
    "greeting",
    "general_question",
    "product_search",
    "product_followup",
    "preference_statement",
    "memory_question",
    "memory_correction",
]

FollowupAction = Literal["none", "refine_search", "compare", "explain"]

RouteSource = Literal["jev", "rules", "jev+guards", "fallback"]

INTENTS: tuple[str, ...] = (
    "greeting",
    "general_question",
    "product_search",
    "product_followup",
    "preference_statement",
    "memory_question",
    "memory_correction",
)

FOLLOWUP_ACTIONS: tuple[str, ...] = ("none", "refine_search", "compare", "explain")


class RouteDecision(BaseModel):
    """What the current message needs.

    Flags are independent: several may be true at once (for example a message
    can state a preference *and* ask for products).
    """

    intent: Intent = "general_question"
    wants_products: bool = False
    refers_to_previous: bool = False
    states_preference: bool = False
    changes_memory: bool = False
    needs_personalisation: bool = False
    followup_action: FollowupAction = "none"

    # Diagnostics
    probabilities: dict[str, float] = Field(default_factory=dict)
    source: RouteSource = "rules"
    confident: bool = True
    model: str | None = None
    input_tokens: int = 0
    latency_ms: int = 0
    guards: list[str] = Field(default_factory=list)

    @field_validator("probabilities")
    @classmethod
    def _valid_probabilities(cls, value: dict[str, float]) -> dict[str, float]:
        """Reject probabilities outside 0..1 instead of trusting the model."""
        cleaned: dict[str, float] = {}
        for key, raw in value.items():
            try:
                number = float(raw)
            except (TypeError, ValueError):
                continue
            if 0.0 <= number <= 1.0:
                cleaned[str(key)] = number
        return cleaned

    @property
    def minimum_certainty(self) -> float:
        """Distance of the least certain flag from a coin flip (0.5 = certain)."""
        if not self.probabilities:
            return 1.0
        return min(abs(value - 0.5) * 2 for value in self.probabilities.values())
