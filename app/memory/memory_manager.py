"""Memory policy and truthful operation reporting.

Mem0 performs the actual extraction, deduplication, merging and superseding
(``add_memory`` runs with ``infer=true`` by default).  This module therefore no
longer runs a duplicate Ollama extraction pipeline or fuzzy duplicate matching.
It keeps only the application policy Mem0 cannot know:

- which statements are durable preferences versus temporary requirements;
- which memory operation a correction/forget request means;
- that a write is only "saved" once Mem0's asynchronous event completes.

Corrections operate exclusively on memory ids returned by a scoped search, so a
model or a caller can never invent an id that reaches ``delete_memory``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.memory.memory_service import MemoryRecord, MemoryResult, MemoryService
from app.routing.rules import CATALOGUE_BRAND_HINTS, brand_polarity

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
_WITHDRAWAL_PATTERN = re.compile(r"\b(forget|stop remembering)\b", re.IGNORECASE)


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


@dataclass
class MemoryAction:
    """One attempted memory operation and its verified outcome."""

    kind: str  # add | update | delete
    status: str  # saved | deleted | pending | failed | not_found | skipped
    text: str = ""
    memory_id: str | None = None
    event_id: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        """True only when the operation definitely landed."""
        return self.status in {"saved", "deleted"}

    def as_dict(self) -> dict[str, Any]:
        """Serialise for graph state and the UI."""
        return {
            "kind": self.kind,
            "status": self.status,
            "text": self.text,
            "memory_id": self.memory_id,
            "event_id": self.event_id,
            "error": self.error,
        }


@dataclass
class MemoryWriteReport:
    """Aggregate outcome of a turn's memory work."""

    actions: list[MemoryAction] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def saved(self) -> list[str]:
        """Texts whose write definitely completed."""
        return [action.text for action in self.actions if action.status == "saved"]

    @property
    def pending(self) -> list[str]:
        """Texts Mem0 accepted but has not finished processing."""
        return [action.text for action in self.actions if action.status == "pending"]

    @property
    def failures(self) -> list[str]:
        """Human-readable failures, including corrections that found nothing."""
        return [
            f"{action.kind}: {action.error or action.text}"
            for action in self.actions
            if action.status in {"failed", "not_found"}
        ]

    @property
    def attempted(self) -> list[str]:
        """Every text an operation was attempted for."""
        return [action.text for action in self.actions if action.status != "skipped"]

    @property
    def ok(self) -> bool:
        """True when nothing failed and every attempted write completed."""
        return not self.failures and not self.pending

    @property
    def status(self) -> str:
        """Short status string for the UI/graph state."""
        if self.failures and not (self.saved or self.pending):
            return "error"
        if self.failures:
            return "partial"
        if self.pending:
            return "pending"
        if self.saved:
            return "success"
        return "no_new_memories"

    def as_dict(self) -> dict[str, Any]:
        """Serialise for ``memory_writes`` in the graph state."""
        return {
            "status": self.status,
            "saved": self.saved,
            "pending": self.pending,
            "skipped": self.skipped,
            "failures": self.failures,
            "attempted": self.attempted,
            "actions": [action.as_dict() for action in self.actions],
        }


@dataclass
class CorrectionPlan:
    """A concrete, validated correction derived from the customer message."""

    mode: str  # delete | replace
    old_brand: str | None
    new_brand: str | None
    targets: list[MemoryRecord] = field(default_factory=list)


class MemoryManager:
    """Apply memory policy and report the real Mem0 outcome."""

    def __init__(self, memory_service: MemoryService | None = None) -> None:
        self.memory_service = memory_service or MemoryService()

    # --------------------------------------------------------------- policy
    def should_store(self, message: str) -> tuple[bool, str]:
        """Return (should_store, reason) for a message the router flagged.

        Temporary requirements, one-off budgets, withdrawal instructions and
        vague statements are never stored.  Mem0 still owns extraction: we pass
        the customer's sentence and let its inference produce the memory.
        """
        text = (message or "").strip()
        if len(normalise_text(text)) < _MIN_CANDIDATE_LENGTH:
            return False, "statement is too short to be a durable preference"
        if is_temporary(text):
            return False, "temporary requirement for this purchase"
        if _WITHDRAWAL_PATTERN.search(text):
            return False, "withdrawal instruction, not a new preference"
        if (
            _BUDGET_PATTERN.search(text)
            and not _PRODUCT_TOKEN_PATTERN.search(text)
            and not _STABLE_BUDGET_PATTERN.search(text)
        ):
            return False, "one-off budget without a stable preference"
        if not has_persistent_marker(text):
            return False, "not clearly a stable preference"
        return True, ""

    def preference_text(self, message: str) -> str:
        """Return the part of a message that states a preference.

        Withdrawal clauses are dropped so "Forget Sony. I prefer JBL." stores
        only "I prefer JBL." rather than asking Mem0 to infer from both.
        """
        parts = re.split(r"(?<=[.!?;])\s+", (message or "").strip())
        kept = [part for part in parts if not _WITHDRAWAL_PATTERN.search(part)]
        return " ".join(part.strip() for part in kept if part.strip()) or ""

    # --------------------------------------------------------------- writes
    def store(
        self,
        customer_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryAction:
        """Add one preference and wait for the real Mem0 event result."""
        result: MemoryResult = self.memory_service.add_memory(
            customer_id=customer_id,
            text=text,
            metadata=metadata
            or {"source": "retail-memory-assistant", "kind": "persistent_preference"},
        )
        if not result.ok:
            return MemoryAction(
                kind="add", status="failed", text=text, error=result.error
            )
        if not result.event_id:
            # Accepted but unverifiable: never claim success.
            return MemoryAction(
                kind="add",
                status="pending",
                text=text,
                error=(
                    "Mem0 accepted the write but returned no event id, so "
                    "completion could not be verified."
                ),
            )
        event = self.memory_service.wait_for_event(result.event_id)
        if event.ok:
            return MemoryAction(
                kind="add",
                status="saved",
                text=event.texts[0] if event.texts else text,
                event_id=result.event_id,
            )
        if event.status in {"FAILED", "ERROR", "CANCELLED", "CANCELED"}:
            return MemoryAction(
                kind="add",
                status="failed",
                text=text,
                event_id=result.event_id,
                error=event.error or f"Mem0 event {result.event_id} failed",
            )
        return MemoryAction(
            kind="add",
            status="pending",
            text=text,
            event_id=result.event_id,
            error=event.error or f"Mem0 event {result.event_id} is still {event.status}",
        )

    def remember(
        self,
        customer_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryWriteReport:
        """Store one message's preference, applying the temporary policy."""
        report = MemoryWriteReport()
        allowed, reason = self.should_store(text)
        if not allowed:
            report.skipped.append(f"{text} ({reason})")
            return report
        report.actions.append(self.store(customer_id, text, metadata))
        return report

    # ---------------------------------------------------------- corrections
    def correction_plan(
        self,
        message: str,
        candidates: list[MemoryRecord],
    ) -> CorrectionPlan:
        """Resolve which retrieved records a correction refers to.

        Only ids present in ``candidates`` are ever returned, and a brand is
        only treated as the *old* preference when the wording negates it or
        when another positive brand replaces it.
        """
        # Include catalogue hints so a *new* brand ("I prefer JBL, not Sony")
        # is recognised even before it has ever been stored.
        candidate_brands = sorted(
            {
                brand
                for record in candidates
                for brand in _brands_in(record.text)
            }
            | set(CATALOGUE_BRAND_HINTS)
        )
        preferred, excluded = brand_polarity(message, candidate_brands)
        operation = _correction_operation(message)

        if operation == "delete":
            mode = "delete"
            # The target is the brand named in the withdrawal clause, not a
            # brand introduced by a following new-preference sentence.
            withdrawal = " ".join(
                part
                for part in re.split(r"(?<=[.!?;])\s+", message)
                if _WITHDRAWAL_PATTERN.search(part)
            )
            w_preferred, w_excluded = brand_polarity(
                withdrawal or message, candidate_brands
            )
            ordered = w_excluded + ([w_preferred] if w_preferred else [])
            ordered += excluded + ([preferred] if preferred else [])
            old_brand = ordered[0] if ordered else None
            new_brand = None
        else:
            new_brand = preferred
            old_brand = excluded[0] if excluded else None
            if old_brand is None and new_brand:
                # "I prefer JBL now" - the old brand is whatever the stored
                # preference mentions that is not the new brand.
                for record in candidates:
                    others = [b for b in _brands_in(record.text) if b != new_brand]
                    if others:
                        old_brand = others[0]
                        break
            mode = "replace" if (old_brand and new_brand) else "delete"

        target_tokens = [token for token in (old_brand, new_brand) if token]
        # Without an identifiable target, never guess: touching every record
        # would be a destructive correction nobody asked for.
        targets = [
            record
            for record in candidates
            if record.memory_id
            and any(
                token.casefold() in record.text.casefold()
                for token in target_tokens
            )
        ][:5]

        return CorrectionPlan(
            mode=mode,
            old_brand=old_brand,
            new_brand=new_brand,
            targets=targets,
        )

    def apply_correction(
        self,
        customer_id: str,
        plan: CorrectionPlan,
    ) -> list[MemoryAction]:
        """Delete or update the plan's validated records."""
        if not plan.targets:
            detail = " and ".join(
                item for item in (plan.old_brand, plan.new_brand) if item
            ) or "that preference"
            return [
                MemoryAction(
                    kind="delete" if plan.mode == "delete" else "update",
                    status="not_found",
                    text=detail,
                    error=(
                        f"No saved memory matching {detail} was found, so "
                        "nothing was changed."
                    ),
                )
            ]

        actions: list[MemoryAction] = []
        for record in plan.targets:
            if plan.mode == "replace" and plan.old_brand and plan.new_brand:
                new_text = re.sub(
                    re.escape(plan.old_brand),
                    plan.new_brand,
                    record.text,
                    flags=re.IGNORECASE,
                )
                result = self.memory_service.update_memory(
                    customer_id, record.memory_id, new_text
                )
                actions.append(
                    MemoryAction(
                        kind="update",
                        status="saved" if result.ok else "failed",
                        text=new_text,
                        memory_id=record.memory_id,
                        error=result.error,
                    )
                )
            else:
                result = self.memory_service.delete_memory(
                    customer_id, record.memory_id
                )
                actions.append(
                    MemoryAction(
                        kind="delete",
                        status="deleted" if result.ok else "failed",
                        text=record.text,
                        memory_id=record.memory_id,
                        error=result.error,
                    )
                )
        return actions


def _correction_operation(message: str) -> str:
    """Return delete/correct/none for a correction message."""
    from app.routing.rules import explicit_memory_operation

    return explicit_memory_operation(message) or "correct"


def _brands_in(text: str) -> list[str]:
    """Return catalogue brand names mentioned in a memory sentence."""
    from app.routing.rules import CATALOGUE_BRAND_HINTS

    lowered = text.casefold()
    return [
        brand
        for brand in CATALOGUE_BRAND_HINTS
        if re.search(rf"\b{re.escape(brand.casefold())}\b", lowered)
    ]
