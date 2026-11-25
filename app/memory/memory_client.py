"""Mem0 Cloud MCP transport and tool invocation.

Responsibilities:
- Maintain one authenticated Streamable HTTP session with the official hosted
  Mem0 MCP server (``https://mcp.mem0.ai/mcp``).
- Discover the tools the server actually exposes and cache their input schemas.
- Invoke tools and parse results safely.
- Report connection / authentication failures explicitly.

The session is owned by a single background asyncio task.  All requests are
queued to that task, which keeps anyio's task groups in one task (required by
the MCP transport) and lets the Streamlit UI reuse the connection across reruns
instead of creating a new session per interaction.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import threading
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from app.config.settings import Settings, get_settings

AUTH_HEADER_PREFIX = "Token"


class Mem0MCPError(RuntimeError):
    """Base class for Mem0 MCP failures."""


class Mem0NotConfiguredError(Mem0MCPError):
    """Raised when MEM0_API_KEY is missing."""


class Mem0AuthenticationError(Mem0MCPError):
    """Raised when Mem0 rejects the API key."""


class Mem0ToolNotFoundError(Mem0MCPError):
    """Raised when the server does not expose an expected tool."""


@dataclass(frozen=True)
class ToolInfo:
    """A tool advertised by the MCP server."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)

    @property
    def properties(self) -> dict[str, Any]:
        """JSON-schema properties for the tool input."""
        return dict(self.input_schema.get("properties") or {})

    @property
    def required(self) -> list[str]:
        """Required argument names for the tool input."""
        return list(self.input_schema.get("required") or [])


@dataclass(frozen=True)
class ToolCallResult:
    """Normalised result of an MCP tool call."""

    tool: str
    payload: Any
    is_error: bool = False


def _stringify_exception(exc: BaseException) -> str:
    """Collect an exception and its causes into one readable string."""
    parts: list[str] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts.append(f"{type(current).__name__}: {current}")
        current = current.__cause__ or current.__context__
    return " | ".join(parts)


class Mem0MCPClient:
    """Synchronous facade over the official Mem0 Cloud MCP server.

    Example::

        client = Mem0MCPClient()
        tools = client.list_tools()
        result = client.call_tool("search_memories", {"query": "Sony", "limit": 5})
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.url = self.settings.mem0_mcp_url
        self.timeout = self.settings.mem0_timeout_seconds

        # Test seam: an object exposing list_tools/call_tool coroutines.
        # Production uses the real MCP ClientSession created in _runner.
        self._client_factory = client_factory

        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._queue: asyncio.Queue[Any] | None = None
        self._runner_future: concurrent.futures.Future[Any] | None = None
        self._runner_lock = threading.Lock()
        self._connected = False
        self._session: Any = None
        self._tools: dict[str, ToolInfo] | None = None
        self._last_error: str | None = None

    # ------------------------------------------------------------------ setup
    @property
    def api_key(self) -> str:
        """The configured API key, used only to build the auth header."""
        return self.settings.mem0_api_key.strip()

    def _auth_headers(self) -> dict[str, str]:
        """Build the documented auth header.

        Mem0's hosted MCP server documents API-key access as
        ``Authorization: Token <MEM0_API_KEY>``.  The key is never logged.
        """
        if not self.api_key:
            raise Mem0NotConfiguredError(
                "MEM0_API_KEY is not set. Add it to .env to enable persistent memory."
            )
        return {
            "Authorization": f"{AUTH_HEADER_PREFIX} {self.api_key}",
            "Accept": "application/json, text/event-stream",
        }

    def _ensure_loop(self) -> None:
        """Start the dedicated event loop thread once."""
        if self._loop is not None and not self._loop.is_closed():
            return
        with self._runner_lock:
            if self._loop is not None and not self._loop.is_closed():
                return
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=self._run_loop, args=(loop,), name="mem0-mcp-loop", daemon=True
            )
            thread.start()
            self._loop = loop
            self._loop_thread = thread

    @staticmethod
    def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
        """Thread body: run the event loop forever."""
        asyncio.set_event_loop(loop)
        loop.run_forever()

    def _ensure_runner(self) -> None:
        """Start (or restart) the single session-owning task."""
        self._ensure_loop()
        assert self._loop is not None
        with self._runner_lock:
            if (
                self._connected
                and self._runner_future is not None
                and not self._runner_future.done()
            ):
                return
            if self._runner_future is not None and not self._runner_future.done():
                return

            self._connected = False
            self._tools = None
            self._queue = asyncio.Queue()
            ready: concurrent.futures.Future[bool] = concurrent.futures.Future()
            self._runner_future = asyncio.run_coroutine_threadsafe(
                self._runner(ready), self._loop
            )
            self._runner_future.add_done_callback(self._consume_runner_exception)

        try:
            ready.result(timeout=self.timeout)
        except concurrent.futures.TimeoutError as exc:
            raise Mem0MCPError(
                f"Timed out connecting to the Mem0 MCP server at {self.url}"
            ) from exc

    @staticmethod
    def _consume_runner_exception(future: concurrent.futures.Future[Any]) -> None:
        """Prevent 'exception was never retrieved' warnings."""
        if future.cancelled():
            return
        try:
            future.exception()
        except Exception:  # pragma: no cover - defensive
            pass

    async def _runner(self, ready: concurrent.futures.Future[bool]) -> None:
        """Own the MCP session for the process lifetime and serve queued calls."""
        stack = AsyncExitStack()
        try:
            if self._client_factory is not None:
                session = self._client_factory()
            else:
                session = await self._open_session(stack)

            self._session = session
            self._connected = True
            self._last_error = None
            if not ready.done():
                ready.set_result(True)

            while True:
                assert self._queue is not None
                item = await self._queue.get()
                if item is None:
                    break
                operation, future = item
                try:
                    future.set_result(await operation(self._session))
                except Exception as exc:  # delivered to the waiting thread
                    future.set_exception(exc)
        except BaseException as exc:  # noqa: BLE001 - reported to caller
            self._connected = False
            self._last_error = _stringify_exception(exc)
            if not ready.done():
                ready.set_exception(self._classify_error(exc))
            if not isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            self._connected = False
            self._session = None
            try:
                await stack.aclose()
            except Exception:  # pragma: no cover - best effort cleanup
                pass

    async def _open_session(self, stack: AsyncExitStack) -> Any:
        """Open the Streamable HTTP transport and initialise an MCP ClientSession."""
        from mcp import ClientSession
        from mcp.client.streamable_http import (
            create_mcp_http_client,
            streamable_http_client,
        )

        headers = self._auth_headers()
        http_client = create_mcp_http_client(headers=headers)
        await stack.enter_async_context(http_client)
        streams = await stack.enter_async_context(
            streamable_http_client(self.url, http_client=http_client)
        )
        read_stream, write_stream = streams
        session = await stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await session.initialize()
        return session

    # ------------------------------------------------------------- invocation
    def _submit(
        self,
        operation: Callable[[Any], Coroutine[Any, Any, Any]],
        timeout: float | None = None,
    ) -> Any:
        """Queue an operation on the session task and wait for its result."""
        self._ensure_runner()
        assert self._loop is not None and self._queue is not None
        future: concurrent.futures.Future[Any] = concurrent.futures.Future()
        self._loop.call_soon_threadsafe(self._queue.put_nowait, (operation, future))
        try:
            return future.result(timeout=timeout or self.timeout)
        except concurrent.futures.TimeoutError as exc:
            raise Mem0MCPError(
                f"Mem0 MCP operation timed out after {timeout or self.timeout}s"
            ) from exc
        except Exception as exc:
            raise self._classify_error(exc) from exc

    @staticmethod
    def _classify_error(exc: BaseException) -> Mem0MCPError:
        """Map transport errors onto actionable exception types."""
        if isinstance(exc, Mem0MCPError):
            return exc
        text = _stringify_exception(exc)
        lowered = text.casefold()
        if "401" in lowered or "unauthor" in lowered or "authentication required" in lowered:
            return Mem0AuthenticationError(
                f"Mem0 authentication failed (check MEM0_API_KEY): {text}"
            )
        if "403" in lowered or "forbidden" in lowered:
            return Mem0AuthenticationError(
                f"Mem0 rejected the request (403): {text}"
            )
        return Mem0MCPError(f"Mem0 MCP request failed: {text}")

    def list_tools(self, refresh: bool = False) -> list[ToolInfo]:
        """Discover and cache the tools exposed by the Mem0 MCP server."""
        if self._tools is not None and not refresh:
            return list(self._tools.values())

        async def operation(client: Any) -> list[ToolInfo]:
            result = await client.list_tools()
            tools = []
            for tool in getattr(result, "tools", []):
                tools.append(
                    ToolInfo(
                        name=tool.name,
                        description=getattr(tool, "description", "") or "",
                        input_schema=getattr(tool, "input_schema", None)
                        or getattr(tool, "inputSchema", None)
                        or {},
                    )
                )
            return tools

        tools = self._submit(operation)
        self._tools = {tool.name: tool for tool in tools}
        return list(tools)

    def get_tool(self, name: str) -> ToolInfo:
        """Return a discovered tool by name or raise Mem0ToolNotFoundError."""
        tools = {tool.name: tool for tool in self.list_tools()}
        if name not in tools:
            available = ", ".join(sorted(tools)) or "none"
            raise Mem0ToolNotFoundError(
                f"Mem0 MCP server does not expose '{name}'. Available tools: {available}"
            )
        return tools[name]

    def build_arguments(
        self, tool: ToolInfo, candidates: dict[str, Any]
    ) -> dict[str, Any]:
        """Adapt candidate arguments to the tool's real input schema.

        Unknown keys are dropped and ``None`` values are omitted.  This allows
        the application to offer e.g. both ``text`` and ``messages`` for
        ``add_memory`` and let the server's schema decide which is accepted.
        """
        properties = tool.properties
        if not properties:
            return {key: value for key, value in candidates.items() if value is not None}

        arguments = {
            key: value
            for key, value in candidates.items()
            if key in properties and value is not None
        }
        missing = [
            name
            for name in tool.required
            if name not in arguments or arguments[name] in (None, "")
        ]
        if missing:
            raise Mem0MCPError(
                f"Tool '{tool.name}' requires {missing}; the server schema is "
                f"{json.dumps(tool.input_schema, default=str)[:400]}"
            )
        return arguments

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> ToolCallResult:
        """Invoke an MCP tool and normalise its result.

        Raises:
            Mem0NotConfiguredError, Mem0AuthenticationError, Mem0ToolNotFoundError,
            or Mem0MCPError.
        """
        tool = self.get_tool(name)
        payload = self.build_arguments(tool, arguments or {})

        async def operation(client: Any) -> Any:
            return await client.call_tool(name, payload)

        result = self._submit(operation, timeout=timeout)
        return self._normalise_result(name, result)

    @staticmethod
    def _normalise_result(name: str, result: Any) -> ToolCallResult:
        """Convert an MCP CallToolResult into a plain Python payload."""
        is_error = bool(getattr(result, "is_error", False) or getattr(result, "isError", False))
        structured = getattr(result, "structured_content", None) or getattr(
            result, "structuredContent", None
        )

        texts: list[str] = []
        for item in getattr(result, "content", []) or []:
            text = getattr(item, "text", None)
            if isinstance(text, str):
                texts.append(text)

        joined = "\n".join(texts).strip()
        payload: Any = structured if structured is not None else None
        if payload is None and joined:
            try:
                payload = json.loads(joined)
            except json.JSONDecodeError:
                payload = {"text": joined}
        if payload is None:
            payload = {}

        if is_error:
            message = joined or json.dumps(payload, default=str)
            raise Mem0MCPError(f"Mem0 tool '{name}' returned an error: {message[:500]}")
        return ToolCallResult(tool=name, payload=payload, is_error=False)

    # ---------------------------------------------------------------- status
    def check_connection(self) -> tuple[bool, str]:
        """Attempt a tool discovery call and return a UI-safe status message."""
        if not self.settings.mem0_configured:
            return False, "Mem0 MCP not configured (MEM0_API_KEY missing)"
        try:
            tools = self.list_tools(refresh=True)
        except Mem0AuthenticationError as exc:
            return False, str(exc)
        except Mem0MCPError as exc:
            return False, str(exc)
        names = ", ".join(sorted(tool.name for tool in tools)) or "none"
        return True, f"Mem0 MCP connected — tools: {names}"

    @property
    def last_error(self) -> str | None:
        """Most recent connection/tool error, if any."""
        return self._last_error

    def close(self) -> None:
        """Stop the session task and event loop (best effort)."""
        if self._loop is None:
            return
        if self._queue is not None:
            try:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, None)
            except RuntimeError:
                pass
        self._connected = False
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=2)
        self._loop = None
        self._loop_thread = None
