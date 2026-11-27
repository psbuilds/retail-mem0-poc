"""Centralised application configuration.

All configuration is read from environment variables (and an optional ``.env``
file).  Nothing secret is ever hardcoded or logged.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the shopping assistant.

    Attributes are grouped by the external service they configure.  Defaults
    match a local development setup so the app can start without a ``.env``
    file; the Mem0 API key has no default because it is a secret.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Product catalogue API
    product_api_url: str = "http://localhost:8000"
    product_api_timeout_seconds: float = 10.0

    # Local LLM
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"
    ollama_timeout_seconds: float = 120.0

    # Mem0 Cloud via MCP
    mem0_mcp_url: str = "https://mcp.mem0.ai/mcp"
    mem0_api_key: str = Field(default="", repr=False)
    mem0_timeout_seconds: float = 30.0

    # Query router (Jev / TypeSafe System One)
    typesafe_api_key: str = Field(default="", repr=False)
    jev_base_url: str = "https://api.typesafe.ai/v1/systemone"
    jev_model: str = "jev-latest"
    jev_timeout_seconds: float = 2.0
    jev_retry_attempts: int = 1
    router_use_jev: bool = True
    router_confidence_threshold: float = 0.5
    router_send_history_turns: int = 2

    # Asynchronous Mem0 writes (add_memory returns an event id)
    memory_event_timeout_seconds: float = 15.0
    memory_event_poll_interval_seconds: float = 1.0

    # Application behaviour
    app_env: str = "development"
    max_recommendations: int = 5
    memory_top_k: int = 20

    @property
    def mem0_configured(self) -> bool:
        """Return True when a Mem0 API key is present (does not validate it)."""
        return bool(self.mem0_api_key.strip())

    @property
    def jev_configured(self) -> bool:
        """Return True when a TypeSafe API key is present (does not validate it)."""
        return bool(self.typesafe_api_key.strip())

    def startup_warnings(self) -> list[str]:
        """Return human-readable warnings about missing configuration.

        Used by the health check script and the Streamlit sidebar.  We never
        include the API key itself, only whether it is set.
        """
        warnings: list[str] = []
        if not self.mem0_configured:
            warnings.append(
                "MEM0_API_KEY is not set — persistent memory will be unavailable."
            )
        if not self.jev_configured:
            warnings.append(
                "TYPESAFE_API_KEY is not set — the query router runs in "
                "deterministic rules mode (no Jev classification)."
            )
        return warnings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so that Streamlit reruns do not re-read the environment on every
    interaction.
    """
    return Settings()
