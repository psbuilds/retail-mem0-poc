"""HTTP transport for the TypeSafe System One endpoint.

Verified request shape (docs.typesafe.ai/api/system-one)::

    POST https://api.typesafe.ai/v1/systemone
    Authorization: Bearer <TYPESAFE_API_KEY>
    {"model": "jev-latest", "state": "...", "questions": {...}}

Verified response shape::

    {"model": "...", "answers": {"<id>": {"type": "noul", "noul": 0.98}},
     "usage": {"input_tokens": 120, "output_tokens": 12}}

``httpx`` is already a project dependency, so one documented endpoint does not
justify a new SDK dependency.  Every failure is raised as ``JevError`` with a
category the classifier can fall back from.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config.settings import Settings, get_settings

DEFAULT_BASE_URL = "https://api.typesafe.ai/v1/systemone"


class JevError(RuntimeError):
    """Raised when Jev cannot produce a usable classification."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class JevResponse:
    """A parsed, still-unvalidated Jev response."""

    answers: dict[str, Any]
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    def noul(self, question_id: str) -> float | None:
        """Return one Noul probability, or None when absent/invalid."""
        answer = self.answers.get(question_id)
        if not isinstance(answer, dict):
            return None
        try:
            value = float(answer.get("noul"))
        except (TypeError, ValueError):
            return None
        return value if 0.0 <= value <= 1.0 else None

    def choice(self, question_id: str) -> str | None:
        """Return one Choice label, or None when absent."""
        answer = self.answers.get(question_id)
        if not isinstance(answer, dict):
            return None
        value = answer.get("choice")
        return str(value) if isinstance(value, str) else None

    def choice_confidence(self, question_id: str) -> float:
        """Return the Choice confidence, defaulting to 0 when missing."""
        answer = self.answers.get(question_id)
        if not isinstance(answer, dict):
            return 0.0
        try:
            return float(answer.get("confidence"))
        except (TypeError, ValueError):
            return 0.0


class JevClient:
    """Minimal synchronous Jev client used by the routing node."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._http = http_client

    @property
    def configured(self) -> bool:
        """Return True when a TypeSafe API key is present."""
        return self.settings.jev_configured

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST the request with a bounded timeout and one retry policy."""
        url = self.settings.jev_base_url or DEFAULT_BASE_URL
        headers = {
            "Authorization": f"Bearer {self.settings.typesafe_api_key.strip()}",
            "Content-Type": "application/json",
        }
        attempts = max(1, self.settings.jev_retry_attempts + 1)
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                client = self._http or httpx.Client(
                    timeout=self.settings.jev_timeout_seconds
                )
                try:
                    response = client.post(url, headers=headers, json=payload)
                finally:
                    if self._http is None:
                        client.close()
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(0.2)
                    continue
                raise JevError(f"Jev request failed: {exc}") from exc

            if response.status_code >= 500 or response.status_code == 429:
                last_error = JevError(
                    f"Jev returned HTTP {response.status_code}",
                    status_code=response.status_code,
                )
                if attempt + 1 < attempts:
                    time.sleep(0.2)
                    continue
                raise last_error

            if response.status_code == 401 or response.status_code == 403:
                raise JevError(
                    "Jev authentication failed (check TYPESAFE_API_KEY)",
                    status_code=response.status_code,
                )
            if response.status_code == 402:
                raise JevError(
                    "Jev credits exhausted (top up the TypeSafe account)",
                    status_code=402,
                )
            if response.status_code >= 400:
                raise JevError(
                    f"Jev rejected the request (HTTP {response.status_code}): "
                    f"{response.text[:200]}",
                    status_code=response.status_code,
                )
            try:
                body = response.json()
            except ValueError as exc:
                raise JevError("Jev returned a non-JSON response") from exc
            if not isinstance(body, dict):
                raise JevError("Jev returned an unexpected payload")
            return body

        # Defensive: the loop either returns or raises.
        raise JevError(f"Jev request failed: {last_error}")

    def classify(self, state: str, questions: dict[str, Any]) -> JevResponse:
        """Send one classification request.

        Raises:
            JevError: on timeout, authentication, payment, rate-limit, server,
                transport or malformed-response failures.
        """
        if not self.configured:
            raise JevError("TYPESAFE_API_KEY is not set")
        payload = {
            "model": self.settings.jev_model,
            "state": state,
            "questions": questions,
        }
        started = time.perf_counter()
        body = self._post(payload)
        latency_ms = int((time.perf_counter() - started) * 1000)
        answers = body.get("answers")
        if not isinstance(answers, dict) or not answers:
            raise JevError("Jev response contains no answers")
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        return JevResponse(
            answers=answers,
            model=str(body.get("model")) if body.get("model") else None,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            latency_ms=latency_ms,
            raw=body,
        )
