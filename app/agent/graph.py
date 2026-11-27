"""LangGraph workflow wiring.

The graph is no longer a fixed pipeline.  ``route_query`` produces an ordered
allowlisted plan (``pending_steps``), and every processing node pops its own
step.  Shared conditional edges call ``nodes.next_step`` so the graph executes
exactly the operations the message needs.

A checkpointer keyed by ``thread_id`` keeps conversation context per session,
which is separate from long-term Mem0 memory.
"""

from __future__ import annotations

from functools import lru_cache, partial
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.agent import nodes
from app.agent.nodes import WorkflowDeps
from app.agent.state import AgentState
from app.config.settings import get_settings
from app.llm.ollama_client import OllamaClient
from app.memory.memory_manager import MemoryManager
from app.memory.memory_service import get_memory_service
from app.products.product_service import ProductService
from app.routing.classifier import QueryRouter


def build_default_deps() -> WorkflowDeps:
    """Create the production service bundle (one MCP session per process)."""
    memory_service = get_memory_service()
    settings = get_settings()
    return WorkflowDeps(
        memory_service=memory_service,
        product_service=ProductService(),
        llm_client=OllamaClient(),
        settings=settings,
        memory_manager=MemoryManager(memory_service),
        router=QueryRouter(settings),
    )


def dispatch_map() -> dict[str, str]:
    """Return the conditional-edge destination map.

    Every destination the planner may emit is registered here; anything else
    resolves through ``finalize_turn`` so the turn always closes.
    """
    mapping: dict[str, str] = {name: name for name in nodes.PLANNED_NODES}
    mapping[END] = END
    return mapping


def build_graph(
    deps: WorkflowDeps | None = None,
    checkpointer: Any | None = None,
) -> Any:
    """Build and compile the shopping assistant graph.

    Parameters:
        deps: Service bundle; defaults to the production services.
        checkpointer: LangGraph checkpointer; defaults to an in-memory saver.

    Returns:
        A compiled graph that can be invoked with
        ``config={"configurable": {"thread_id": session_id}}``.
    """
    deps = deps or build_default_deps()
    checkpointer = checkpointer or InMemorySaver()

    builder = StateGraph(AgentState)
    builder.add_node("prepare_turn", partial(nodes.prepare_turn, deps=deps))
    builder.add_node("route_query", partial(nodes.route_query, deps=deps))
    builder.add_node("manage_memory", partial(nodes.manage_memory, deps=deps))
    builder.add_node("retrieve_memory", partial(nodes.retrieve_memory, deps=deps))
    builder.add_node("understand_request", partial(nodes.understand_request, deps=deps))
    builder.add_node("search_products", partial(nodes.search_products, deps=deps))
    builder.add_node("recommend_products", partial(nodes.recommend_products, deps=deps))
    builder.add_node("generate_response", partial(nodes.generate_response, deps=deps))
    builder.add_node("finalize_turn", partial(nodes.finalize_turn, deps=deps))

    builder.add_edge(START, "prepare_turn")
    builder.add_edge("prepare_turn", "route_query")

    # Every scheduled node dispatches through the same queue head.
    destinations = dispatch_map()
    for node_name in (
        "route_query",
        "manage_memory",
        "retrieve_memory",
        "understand_request",
        "search_products",
        "recommend_products",
        "generate_response",
    ):
        builder.add_conditional_edges(
            node_name,
            nodes.next_step,
            destinations,
        )

    builder.add_edge("finalize_turn", END)

    return builder.compile(checkpointer=checkpointer)


@lru_cache(maxsize=1)
def get_graph() -> Any:
    """Return the process-wide compiled graph used by the chatbot service."""
    return build_graph()
