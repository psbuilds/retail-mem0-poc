"""LangGraph workflow wiring.

One simple sequential graph coordinates the whole shopping turn.  A checkpointer
keyed by ``thread_id`` keeps conversation context per session, which is separate
from long-term Mem0 memory.
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


def build_default_deps() -> WorkflowDeps:
    """Create the production service bundle (one MCP session per process)."""
    memory_service = get_memory_service()
    return WorkflowDeps(
        memory_service=memory_service,
        product_service=ProductService(),
        llm_client=OllamaClient(),
        settings=get_settings(),
        memory_manager=MemoryManager(memory_service),
    )


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
    builder.add_node("retrieve_memory", partial(nodes.retrieve_memory, deps=deps))
    builder.add_node("understand_request", partial(nodes.understand_request, deps=deps))
    builder.add_node("search_products", partial(nodes.search_products, deps=deps))
    builder.add_node("recommend_products", partial(nodes.recommend_products, deps=deps))
    builder.add_node("generate_response", partial(nodes.generate_response, deps=deps))
    builder.add_node("update_memory", partial(nodes.update_memory, deps=deps))

    builder.add_edge(START, "prepare_turn")
    builder.add_edge("prepare_turn", "retrieve_memory")
    builder.add_edge("retrieve_memory", "understand_request")
    builder.add_edge("understand_request", "search_products")
    builder.add_edge("search_products", "recommend_products")
    builder.add_edge("recommend_products", "generate_response")
    builder.add_edge("generate_response", "update_memory")
    builder.add_edge("update_memory", END)

    return builder.compile(checkpointer=checkpointer)


@lru_cache(maxsize=1)
def get_graph() -> Any:
    """Return the process-wide compiled graph used by the chatbot service."""
    return build_graph()
