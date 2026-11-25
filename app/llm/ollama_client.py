"""Reusable client for a locally running Ollama server.

Only this module knows Ollama's HTTP API.  Callers receive text or parsed JSON
and never deal with HTTP details.  Failures raise explicit exceptions so the
workflow can report them instead of pretending a response was generated.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.config.settings import Settings, get_settings


class OllamaError(RuntimeError):
    """Base class for Ollama failures."""


class OllamaUnavailableError(OllamaError):
    """Raised when the Ollama server cannot be reached."""


class OllamaInvalidJSONError(OllamaError):
    """Raised when Ollama output cannot be parsed as JSON."""


def extract_json_object(text: str) -> dict[str, Any]:
    """Extract the first JSON object from an LLM response.

    Handles plain JSON, fenced code blocks, and short prose around an object.

    Raises:
        OllamaInvalidJSONError: If no parseable JSON object is present.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end <= start:
            raise OllamaInvalidJSONError("LLM response did not contain a JSON object")
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise OllamaInvalidJSONError(f"LLM returned invalid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise OllamaInvalidJSONError("LLM JSON response was not an object")
    return parsed


class OllamaClient:
    """Thin synchronous client for Ollama's ``/api/chat`` endpoint."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        http_client: httpx.Client | None = None,
        settings: Settings | None = None,
    ) -> None:
        settings = settings or get_settings()
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.ollama_model
        self.timeout = timeout or settings.ollama_timeout_seconds
        self._client = http_client

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST JSON to Ollama and return the decoded response.

        Raises:
            OllamaUnavailableError: On connection errors or timeouts.
            OllamaError: On non-2xx responses or invalid JSON.
        """
        url = f"{self.base_url}{path}"
        try:
            if self._client is not None:
                response = self._client.post(url, json=payload, timeout=self.timeout)
            else:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.post(url, json=payload)
        except httpx.HTTPError as exc:
            raise OllamaUnavailableError(
                f"Ollama is not reachable at {self.base_url}: {exc}"
            ) from exc

        if response.status_code == 404:
            raise OllamaError(
                f"Ollama model '{self.model}' is not installed. "
                f"Run: ollama pull {self.model}"
            )
        if response.status_code >= 400:
            raise OllamaError(
                f"Ollama returned HTTP {response.status_code}: {response.text[:300]}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise OllamaError("Ollama returned a non-JSON response") from exc
        if not isinstance(data, dict):
            raise OllamaError("Ollama returned an unexpected response shape")
        return data

    def is_available(self) -> bool:
        """Return True when the Ollama server responds and the model is installed."""
        try:
            if self._client is not None:
                response = self._client.get(f"{self.base_url}/api/tags", timeout=5)
            else:
                with httpx.Client(timeout=5) as client:
                    response = client.get(f"{self.base_url}/api/tags")
            if response.status_code >= 400:
                return False
            models = response.json().get("models", [])
        except (httpx.HTTPError, ValueError):
            return False

        target = self.model.casefold()
        return any(
            str(entry.get("name", "")).casefold() == target
            or str(entry.get("name", "")).casefold().startswith(f"{target}:")
            or str(entry.get("model", "")).casefold() == target
            for entry in models
        )

    def list_models(self) -> list[str]:
        """Return installed model names, or an empty list when unreachable."""
        try:
            if self._client is not None:
                response = self._client.get(f"{self.base_url}/api/tags", timeout=5)
            else:
                with httpx.Client(timeout=5) as client:
                    response = client.get(f"{self.base_url}/api/tags")
            return [entry.get("name", "") for entry in response.json().get("models", [])]
        except (httpx.HTTPError, ValueError):
            return []

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        json_mode: bool = False,
        temperature: float = 0.2,
    ) -> str:
        """Send a chat conversation to Ollama and return the assistant text.

        Parameters:
            messages: Chat messages with ``role`` and ``content`` keys.
            json_mode: Ask Ollama to constrain output to JSON.
            temperature: Sampling temperature (low by default for extraction).

        Raises:
            OllamaUnavailableError: If the server is down.
            OllamaError: For HTTP errors, missing model, or an empty reply.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if json_mode:
            payload["format"] = "json"

        data = self._post("/api/chat", payload)
        message = data.get("message") or {}
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise OllamaError("Ollama returned an empty response")
        return content.strip()

    def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        """Request JSON from Ollama and parse it safely."""
        raw = self.chat(messages, json_mode=True, temperature=temperature)
        return extract_json_object(raw)


def ollama_status(model: str | None = None) -> tuple[bool, str]:
    """Check Ollama availability and model presence for status displays."""
    client = OllamaClient()
    target = model or client.model
    if client.is_available():
        return True, f"Ollama online ({target})"
    models = client.list_models()
    if models:
        return False, f"Ollama online, but model '{target}' is not installed"
    return False, f"Ollama is not reachable at {client.base_url}"
