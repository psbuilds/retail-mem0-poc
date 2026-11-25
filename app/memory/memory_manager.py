"""Rules that decide what is worth remembering.

Persistent preferences (brands, product types, feature likes) may be stored.
Temporary requirements (a budget for this purchase, a gift, a one-off brand)
must not become permanent memories.

Extraction is performed by the agent node using Ollama; this module applies the
deterministic rules and performs de-duplicated writes through MemoryService.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from app.memory.memory_service import MemoryResult, MemoryService

# Phrases that mark a request as temporary.  A candidate containing one of
# these is never stored unless it also clearly states a stable preference,
# e.g. "I usually prefer Sony, but for this trip I want JBL".
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
    "upcoming trip",
    "as a gift",
    "for a gift",
    "gift for",
    "one-time",
    "one time",
    "temporarily",
    "just for",
    "only for",
)

PERSISTENT_MARKERS: tuple[str, ...] = (
    "usually",
    "always",
    "generally",
    "normally",
    "prefer",
    "i like",
    "i love",
    "i tend to",
    "my favourite",
    "my favorite",
    "i'm a fan of",
    "i am a fan of",
)

_MIN_CANDIDATE_LENGTH = 8
_SIMILARITY_THRESHOLD = 0.82

_BUDGET_PATTERN = re.compile(
    r"(₹|\brs\.?\b|\binr\b|\bbudget\b|\bunder\b|\bbelow\b|\bmax\b)", re.IGNORECASE
)
_PRODUCT_TOKEN_PATTERN = re.compile(
    r"(headphone|earbud|earphone|wireless|bluetooth|noise|lightweight|"
    r"battery|bass|foldable|\banc\b|over-ear|on-ear|in-ear)",
    re.IGNORECASE,
)
_STABLE_BUDGET_PATTERN = re.compile(
    r"(usually|normally|generally|always)", re.IGNORECASE
)


@dataclass(frozen=True)
class PreferenceCandidate:
    """A potential long-term preference produced by the extraction LLM."""

    text: str
    kind: str = "persistent"
    confidence: float = 0.0
    reason: str = ""


@dataclass
class MemoryWriteReport:
    """Result of attempting to persist preferences."""

    saved: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    attempted: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when every attempted write succeeded (and none failed)."""
        return not self.failures

    @property
    def status(self) -> str:
        """Short status string for the UI/graph state."""
        if self.failures and not self.saved:
            return "error"
        if self.failures:
            return "partial"
        if self.saved:
            return "success"
        return "no_new_memories"


def normalise_text(text: str) -> str:
    """Lowercase and collapse whitespace/punctuation for comparisons."""
    cleaned = re.sub(r"[^a-z0-9\s]", " ", text.casefold())
    return re.sub(r"\s+", " ", cleaned).strip()


def is_temporary(text: str) -> bool:
    """Return True when the sentence contains a temporary-request marker."""
    lowered = text.casefold()
    return any(marker in lowered for marker in TEMPORARY_MARKERS)


def has_persistent_marker(text: str) -> bool:
    """Return True when the sentence uses stable-preference language."""
    lowered = text.casefold()
    return any(marker in lowered for marker in PERSISTENT_MARKERS)


def is_duplicate(candidate: str, existing: list[str]) -> bool:
    """Return True when a candidate is near-identical to an existing memory."""
    normalised = normalise_text(candidate)
    for memory in existing:
        other = normalise_text(memory)
        if not other:
            continue
        if normalised == other:
            return True
        if SequenceMatcher(None, normalised, other).ratio() >= _SIMILARITY_THRESHOLD:
            return True
        # Short candidates such as "Prefers Sony headphones" should also match
        # longer memories that contain the same core tokens.
        candidate_tokens = set(normalised.split())
        other_tokens = set(other.split())
        if candidate_tokens and len(candidate_tokens & other_tokens) / len(
            candidate_tokens
        ) >= 0.9:
            return True
    return False


class MemoryManager:
    """Apply memory rules and persist preferences with de-duplication."""

    def __init__(self, memory_service: MemoryService | None = None) -> None:
        self.memory_service = memory_service or MemoryService()

    def select_persistent(
        self,
        candidates: list[PreferenceCandidate] | list[dict],
        existing_memories: list[str],
    ) -> tuple[list[PreferenceCandidate], list[str]]:
        """Return (accepted, skipped_reasons) after applying memory rules.

        Rules:
        - candidate must be classified ``persistent``;
        - must not contain temporary markers;
        - must be a meaningful sentence;
        - must not duplicate an existing or already-accepted memory.
        """
        accepted: list[PreferenceCandidate] = []
        skipped: list[str] = []
        seen = list(existing_memories)

        for item in candidates:
            candidate = (
                item if isinstance(item, PreferenceCandidate) else PreferenceCandidate(**item)
            )
            text = candidate.text.strip()
            if not text:
                continue
            if candidate.kind.casefold() != "persistent":
                skipped.append(f"{text} (temporary requirement)")
                continue
            if is_temporary(text):
                skipped.append(f"{text} (temporary wording)")
                continue
            if len(normalise_text(text)) < _MIN_CANDIDATE_LENGTH:
                skipped.append(f"{text} (too short)")
                continue
            # A budget with no product/feature context is a one-off constraint
            # unless the customer phrased it as a stable, usual budget.
            if (
                _BUDGET_PATTERN.search(text)
                and not _PRODUCT_TOKEN_PATTERN.search(text)
                and not _STABLE_BUDGET_PATTERN.search(text)
            ):
                skipped.append(f"{text} (one-off budget)")
                continue
            if not has_persistent_marker(text) and candidate.confidence < 0.7:
                skipped.append(f"{text} (not clearly a stable preference)")
                continue
            if is_duplicate(text, seen):
                skipped.append(f"{text} (already remembered)")
                continue
            accepted.append(candidate)
            seen.append(text)

        return accepted, skipped

    def remember(
        self,
        customer_id: str,
        candidates: list[PreferenceCandidate] | list[dict],
        existing_memories: list[str],
    ) -> MemoryWriteReport:
        """Persist accepted preferences through Mem0 MCP and report real outcomes."""
        report = MemoryWriteReport()
        accepted, skipped = self.select_persistent(candidates, existing_memories)
        report.skipped.extend(skipped)

        for candidate in accepted:
            report.attempted.append(candidate.text)
            result: MemoryResult = self.memory_service.add_memory(
                customer_id=customer_id,
                text=candidate.text,
                metadata={
                    "source": "retail-memory-assistant",
                    "kind": "persistent_preference",
                },
            )
            if result.ok:
                report.saved.append(candidate.text)
            else:
                report.failures.append(f"{candidate.text}: {result.error}")
        return report
