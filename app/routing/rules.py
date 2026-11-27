"""Deterministic routing rules.

Three jobs:

1. **Shortcuts** - obvious greetings and explicit correction/forget requests are
   decided in Python.  Corrections are irreversible, so they never depend on a
   model probability.
2. **Recall guards** - high-precision signals that supplement Jev (a catalogue
   brand/category plus a shopping verb, durable-preference phrases, temporary
   purchase scope).  Every applied guard is named in ``RouteDecision.guards``.
3. **Fallback** - a complete rule-based classifier used when Jev is unavailable
   or returns something invalid.

Brand polarity is also resolved here and reused by request understanding, so
"I don't like JBL anymore" never becomes a request *for* JBL.
"""

from __future__ import annotations

import re
from typing import Iterable

from app.routing.models import FOLLOWUP_ACTIONS, RouteDecision

# Routing hints only: the catalogue itself remains owned by the product API.
# Used to recognise a brand mention in a message without calling ProductService.
CATALOGUE_BRAND_HINTS: tuple[str, ...] = (
    "Anker",
    "JBL",
    "Realme",
    "Sennheiser",
    "Sony",
    "boAt",
)

CATEGORY_WORDS: tuple[str, ...] = (
    "headphone",
    "headphones",
    "earbud",
    "earbuds",
    "earphone",
    "earphones",
    "tws",
    "in-ear",
    "over-ear",
    "on-ear",
)

SHOPPING_VERBS: tuple[str, ...] = (
    "show",
    "suggest",
    "recommend",
    "looking for",
    "want",
    "need",
    "buy",
    "find",
    "options",
    "alternatives",
    "shortlist",
    "browse",
)

DURABLE_MARKERS: tuple[str, ...] = (
    "usually",
    "always",
    "generally",
    "normally",
    "i prefer",
    "i like",
    "i love",
    "i tend to",
    "my favourite",
    "my favorite",
    "i'm a fan",
    "i am a fan",
    "not a fan",
)

TEMPORARY_MARKERS: tuple[str, ...] = (
    "this time",
    "this purchase",
    "this order",
    "for now",
    "right now",
    "today",
    "tonight",
    "this week",
    "this month",
    "for a trip",
    "for my trip",
    "as a gift",
    "for a gift",
    "one-time",
    "one time",
    "temporarily",
    "just for",
    "only for",
)

PERSONALISATION_PHRASES: tuple[str, ...] = (
    "based on my preferences",
    "based on what you know",
    "my preferences",
    "what you know about me",
    "what do you remember",
    "remember about me",
    "my usual",
    "personalised",
    "personalized",
    "my taste",
    "for me",
)

GREETING_PATTERN = re.compile(
    r"^(?:hi|hello|hey|yo|hiya|namaste|good\s+(?:morning|afternoon|evening))"
    r"(?:[\s,!.]+(?:there|bot|again|friend))?[\s,!.?]*$",
    re.IGNORECASE,
)

_MONEY_HINT = re.compile(
    r"(?:₹|rs\.?|\binr\b|\bunder\b|\bbelow\b|\bbudget\b|\bmax(?:imum)?\b|\d{3,})",
    re.IGNORECASE,
)

_DELETE_PATTERN = re.compile(
    r"\b(?:forget|stop\s+remembering|don'?t\s+remember\s+(?:that|this|it)|"
    r"remove\s+(?:that|this)\s+memory)\b",
    re.IGNORECASE,
)

_CORRECTION_PATTERN = re.compile(
    r"\b(?:no\s+longer\s+(?:prefer|like|want)|"
    r"don'?t\s+(?:like|prefer|want)\b[^.;!?]*\banymore|"
    r"do\s+not\s+(?:like|prefer|want)\b[^.;!?]*\banymore|"
    r"changed\s+my\s+mind|switched\s+(?:to|from))\b",
    re.IGNORECASE,
)

_COMPARE_PATTERN = re.compile(
    r"\b(?:which\s+(?:one\s+)?is\s+better|which\s+one|compare|"
    r"difference\s+between\s+(?:them|these|the))\b",
    re.IGNORECASE,
)

_EXPLAIN_PATTERN = re.compile(
    r"\b(?:tell\s+me\s+more|more\s+about|details?\s+(?:on|about)|"
    r"explain|elaborate|why\s+(?:is|did|does))\b",
    re.IGNORECASE,
)

_REFINE_PATTERN = re.compile(
    r"\b(?:cheaper|less\s+expensive|lower\s+price|cheapest|"
    r"other\s+options|more\s+options|alternatives?|instead)\b",
    re.IGNORECASE,
)

_NEGATION_WINDOW = 28

_NEGATORS = (
    "not",
    "no longer",
    "never",
    "without",
    "except",
    "instead of",
    "rather than",
    "don't like",
    "do not like",
    "don't prefer",
    "do not prefer",
    "don't want",
    "do not want",
    "not a fan of",
    "tired of",
    "avoid",
    "anything but",
)


def _lower(text: str) -> str:
    return (text or "").casefold()


def mentions_product(text: str) -> bool:
    """Return True when the message names a category, catalogue brand or price."""
    lowered = _lower(text)
    if any(word in lowered for word in CATEGORY_WORDS):
        return True
    if any(re.search(rf"\b{re.escape(brand.casefold())}\b", lowered) for brand in CATALOGUE_BRAND_HINTS):
        return True
    return bool(_MONEY_HINT.search(text))


def is_greeting_only(message: str) -> bool:
    """Return True for a bare greeting with no product or request content."""
    stripped = (message or "").strip()
    if not stripped or not GREETING_PATTERN.match(stripped):
        return False
    return not mentions_product(stripped)


def has_temporary_scope(message: str) -> bool:
    """Return True when the message scopes itself to one purchase."""
    lowered = _lower(message)
    return any(marker in lowered for marker in TEMPORARY_MARKERS)


def has_durable_marker(message: str) -> bool:
    """Return True when the message uses stable-preference language."""
    lowered = _lower(message)
    return any(marker in lowered for marker in DURABLE_MARKERS)


def new_preference_after_withdrawal(message: str) -> bool:
    """True when a message both withdraws a preference and states a new one.

    "Forget Sony. I prefer JBL." -> True
    "Forget that I prefer Sony." -> False
    """
    parts = re.split(r"[,.;!?]", message)
    remainder = " ".join(part for part in parts if not _DELETE_PATTERN.search(part))
    return bool(remainder.strip()) and has_durable_marker(remainder)


def explicit_memory_operation(message: str) -> str | None:
    """Classify an explicit memory instruction as ``delete`` or ``correct``.

    Returns None for ordinary messages.  A *new negative preference* (for
    example "I don't like in-ear models") is intentionally not a correction:
    only statements about no-longer-holding preferences are.
    """
    lowered = _lower(message)
    if _DELETE_PATTERN.search(lowered):
        return "delete"
    if _CORRECTION_PATTERN.search(lowered):
        return "correct"
    return None


def detect_followup_action(message: str) -> str:
    """Return ``none``/``refine_search``/``compare``/``explain`` from wording."""
    lowered = _lower(message)
    if _COMPARE_PATTERN.search(lowered):
        return "compare"
    if _EXPLAIN_PATTERN.search(lowered):
        return "explain"
    if _REFINE_PATTERN.search(lowered):
        return "refine_search"
    return "none"


def brand_polarity(
    message: str,
    known_brands: Iterable[str] | None = None,
) -> tuple[str | None, list[str]]:
    """Return (preferred brand, excluded brands) using explicit negation.

    "I prefer JBL, not Sony" -> ("JBL", ["Sony"])
    "I don't like JBL anymore" -> (None, ["JBL"])
    """
    brands = list(known_brands or CATALOGUE_BRAND_HINTS)
    lowered = _lower(message)
    preferred: str | None = None
    excluded: list[str] = []

    for brand in brands:
        for match in re.finditer(rf"\b{re.escape(brand.casefold())}\b", lowered):
            before = lowered[max(0, match.start() - _NEGATION_WINDOW): match.start()]
            # "JBL, not Sony": the negation sits immediately before the brand.
            negated = any(negator in before[-len(negator) - 6:] for negator in _NEGATORS)
            if negated:
                if brand not in excluded:
                    excluded.append(brand)
            elif preferred is None:
                preferred = brand

    if preferred in excluded:
        excluded.remove(preferred)
    return preferred, excluded


def _shopping_signal(message: str) -> bool:
    """Return True when the message asks for catalogue products."""
    lowered = _lower(message)
    # "Sony headphones under 500" is a request even without an explicit verb.
    if mentions_product(message) and parse_money_hint(message):
        return True
    verb = any(verb in lowered for verb in SHOPPING_VERBS)
    if not verb:
        return False
    if mentions_product(message):
        return True
    # "show me something / other options / alternatives"
    return any(word in lowered for word in ("something", "options", "alternatives", "product", "one"))


def _refinement_signal(message: str) -> bool:
    """Return True for a short constraint that adjusts an earlier request.

    "Only Sony." / "Under ₹5,000." / "with ANC" carry no shopping verb; they
    only make sense against the previous shortlist.
    """
    lowered = _lower(message)
    if len(lowered.split()) > 8:
        return False
    # A durable preference or an explanatory question is not a refinement.
    if has_durable_marker(message):
        return False
    if re.match(r"^(?:what|what's|how|why|who|when|where|explain|does|is)\b", lowered):
        return False
    if parse_money_hint(message):
        return True
    if any(word in lowered for word in CATEGORY_WORDS):
        return True
    if any(
        re.search(rf"\b{re.escape(brand.casefold())}\b", lowered)
        for brand in CATALOGUE_BRAND_HINTS
    ):
        return True
    return any(
        word in lowered
        for word in ("wireless", "wired", "bluetooth", "anc", "noise", "only", "lightweight")
    )


def parse_money_hint(message: str) -> bool:
    """Return True when the message contains a numeric budget hint."""
    return bool(_MONEY_HINT.search(message))


def apply_guards(
    decision: RouteDecision,
    message: str,
    has_previous_context: bool = False,
) -> RouteDecision:
    """Supplement a Jev decision with high-precision deterministic signals.

    Guards only *add* capabilities or record scope.  They never contradict an
    explicit instruction except for the documented temporary-scope rule below.
    """
    guards: list[str] = list(decision.guards)
    lowered = _lower(message)

    if not decision.wants_products and _shopping_signal(message):
        decision.wants_products = True
        guards.append("shopping_verb_with_product")

    if (
        has_previous_context
        and not decision.wants_products
        and not explicit_memory_operation(message)
        and _refinement_signal(message)
    ):
        decision.wants_products = True
        decision.refers_to_previous = True
        if decision.followup_action == "none":
            decision.followup_action = "refine_search"
        guards.append("refinement_of_previous_request")

    if not decision.states_preference and has_durable_marker(message):
        decision.states_preference = True
        guards.append("durable_preference_marker")

    if decision.states_preference and has_temporary_scope(message) and not has_durable_marker(message):
        decision.states_preference = False
        guards.append("temporary_scope_suppressed")

    operation = explicit_memory_operation(message)
    if operation and not decision.changes_memory:
        decision.changes_memory = True
        guards.append(f"explicit_memory_{operation}")
    if (
        operation == "delete"
        and decision.states_preference
        and not new_preference_after_withdrawal(message)
    ):
        decision.states_preference = False
        guards.append("withdrawal_is_not_a_preference")

    preferred, excluded = brand_polarity(message)
    if excluded and not decision.changes_memory and (decision.states_preference or preferred):
        decision.changes_memory = True
        guards.append("negated_existing_brand")

    if decision.changes_memory and not decision.needs_personalisation:
        # A correction changes what a later recommendation should apply.
        decision.needs_personalisation = True
        guards.append("correction_requires_updated_memory")

    if not decision.needs_personalisation and any(
        phrase in lowered for phrase in PERSONALISATION_PHRASES
    ):
        decision.needs_personalisation = True
        guards.append("personalisation_phrase")

    decision.guards = guards
    if guards:
        decision.source = "jev+guards"
    return decision


def heuristic_route(
    message: str,
    has_previous_context: bool = False,
) -> RouteDecision:
    """Deterministic routing when Jev is unavailable or unusable.

    This is deliberately conservative: it prefers a plain conversational reply
    over sending a greeting through the shopping pipeline, and never invents a
    correction that the wording does not support.
    """
    lowered = _lower(message)
    operation = explicit_memory_operation(message)
    preferred, excluded = brand_polarity(message)
    followup = detect_followup_action(message)
    wants_products = _shopping_signal(message)
    if (
        has_previous_context
        and not wants_products
        and not operation
        and _refinement_signal(message)
    ):
        wants_products = True
        refers_to_previous = True
        if followup == "none":
            followup = "refine_search"
    states_preference = has_durable_marker(message)
    # "Forget that I prefer Sony" mentions a preference only to withdraw it,
    # but "Forget Sony. I prefer JBL." also states a new durable preference.
    if operation == "delete":
        states_preference = new_preference_after_withdrawal(message)
    elif operation == "correct" and not preferred:
        states_preference = False
    needs_personalisation = any(
        phrase in lowered for phrase in PERSONALISATION_PHRASES
    )
    refers_to_previous = followup != "none"
    changes_memory = bool(operation) or bool(
        states_preference and excluded and preferred
    )

    if followup == "refine_search":
        wants_products = True
    if followup in {"compare", "explain"}:
        wants_products = False
    # A pure refinement ("cheaper ones") only refers backwards when there is
    # something to refine; "alternatives" is about the current request.
    if followup == "refine_search" and "alternatives" in lowered:
        refers_to_previous = False

    greeting = is_greeting_only(message)

    if operation:
        intent = "memory_correction"
    elif "remember" in lowered and lower_question(lowered):
        intent = "memory_question"
    elif wants_products and followup != "none":
        intent = "product_followup"
    elif wants_products:
        intent = "product_search"
    elif needs_personalisation and lower_question(lowered):
        intent = "memory_question"
    elif states_preference:
        intent = "preference_statement"
    elif greeting:
        intent = "greeting"
    else:
        intent = "general_question"

    if followup not in FOLLOWUP_ACTIONS:
        followup = "none"

    return RouteDecision(
        intent=intent,
        wants_products=wants_products,
        refers_to_previous=refers_to_previous,
        states_preference=states_preference,
        changes_memory=changes_memory,
        needs_personalisation=needs_personalisation,
        followup_action=followup,
        source="fallback",
        confident=True,
    )


def lower_question(lowered: str) -> bool:
    """Return True when the message looks like a question."""
    return "?" in lowered or any(
        phrase in lowered for phrase in ("what do you", "what have you", "do you")
    )
