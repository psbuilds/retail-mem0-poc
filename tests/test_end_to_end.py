"""End-to-end workflow tests.

These run the real LangGraph workflow, real recommendation engine and the real
FastAPI catalogue (through TestClient).  Only Mem0 and Ollama are replaced by
in-process fakes, because live credentials/models are environment-specific.
Live Mem0 verification lives in ``tests/test_memory.py`` and is marked ``live``.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent.nodes import WorkflowDeps
from app.agent.graph import build_graph
from app.config.settings import Settings
from app.memory.memory_manager import MemoryManager
from app.products.product_client import ProductAPIClient
from app.products.product_service import ProductService
from app.services.chatbot_service import process_message
from backend.api import app as product_app
from tests.conftest import FakeLLM, FakeMemoryService


@pytest.fixture
def real_product_service() -> ProductService:
    """ProductService talking to the real FastAPI app in-process."""
    client = TestClient(product_app)
    return ProductService(
        ProductAPIClient(base_url="http://testserver", http_client=client)
    )


def build_e2e_graph(
    real_product_service: ProductService,
    memory: FakeMemoryService,
    llm: FakeLLM,
    settings: Settings,
):
    deps = WorkflowDeps(
        memory_service=memory,  # type: ignore[arg-type]
        product_service=real_product_service,
        llm_client=llm,  # type: ignore[arg-type]
        settings=settings,
        memory_manager=MemoryManager(memory),  # type: ignore[arg-type]
    )
    return build_graph(deps=deps)


def test_scenario_save_then_recall_across_sessions(
    settings: Settings, real_product_service: ProductService
) -> None:
    memory = FakeMemoryService()
    llm = FakeLLM(
        requirements_by_message={
            "I usually prefer Sony headphones with noise cancellation.": {
                "is_shopping_request": False
            },
            "Suggest headphones for me.": {"is_shopping_request": True},
        },
        preferences=[
            {
                "text": "Usually prefers Sony headphones with noise cancellation.",
                "kind": "persistent",
                "confidence": 0.95,
            }
        ],
    )
    graph = build_e2e_graph(real_product_service, memory, llm, settings)

    saved = process_message(
        "customer_001",
        "conversation-1",
        "I usually prefer Sony headphones with noise cancellation.",
        graph=graph,
    )
    assert saved.memory_saved == ["Usually prefers Sony headphones with noise cancellation."]

    # A brand-new session must rely on Mem0 (here: the fake store) rather than
    # the previous LangGraph thread.
    recalled = process_message(
        "customer_001", "conversation-2", "Suggest headphones for me.", graph=graph
    )
    assert recalled.memory_status == "success"
    assert recalled.recommendations
    assert all(item["brand"] == "Sony" for item in recalled.recommendations)
    assert all(item["noise_cancellation"] for item in recalled.recommendations)
    assert recalled.response  # real LLM fake returned an explanation


def test_scenario_override_and_temporary_budget(
    settings: Settings, real_product_service: ProductService
) -> None:
    memory = FakeMemoryService(
        memories=["Usually prefers Sony headphones with noise cancellation."]
    )
    llm = FakeLLM(
        requirements={
            "is_shopping_request": True,
            "category": "headphones",
            "brand": "JBL",
            "max_budget": 3000,
        },
        preferences=[
            {
                "text": "Wants JBL headphones for this purchase.",
                "kind": "temporary",
                "confidence": 0.9,
            }
        ],
    )
    graph = build_e2e_graph(real_product_service, memory, llm, settings)

    result = process_message(
        "customer_001",
        "conversation-3",
        "Show me JBL headphones under ₹3,000 this time.",
        graph=graph,
    )

    assert result.requirements["brand"] == "JBL"
    assert result.requirements["max_budget"] == 3000
    assert [item["name"] for item in result.recommendations] == [
        "JBL Tune 520BT Wireless On-Ear Headphones",
        "JBL Tune 510BT Wireless On-Ear Headphones",
    ]
    assert result.memory_saved == []
    assert memory.added == []


def test_scenario_customer_isolation(
    settings: Settings, real_product_service: ProductService
) -> None:
    memory = FakeMemoryService(
        memories=["Usually prefers Sony headphones with noise cancellation."]
    )
    llm = FakeLLM(requirements={"is_shopping_request": True})
    graph = build_e2e_graph(real_product_service, memory, llm, settings)

    customer_one = process_message(
        "customer_001", "iso-1", "Suggest headphones for me.", graph=graph
    )
    customer_two = process_message(
        "customer_002", "iso-2", "Suggest headphones for me.", graph=graph
    )

    assert all(item["brand"] == "Sony" for item in customer_one.recommendations)
    assert customer_two.memory_status == "empty"
    assert customer_two.recommendations
    # customer_002 is not constrained by customer_001's Sony preference.
    assert any(item["brand"] != "Sony" for item in customer_two.recommendations)
    assert customer_two.requirements.get("brand") != "Sony"


def test_empty_catalogue_result_is_explained(
    settings: Settings, real_product_service: ProductService
) -> None:
    memory = FakeMemoryService()
    llm = FakeLLM(
        requirements={
            "is_shopping_request": True,
            "category": "headphones",
            "brand": "Sony",
            "max_budget": 500,
        }
    )
    graph = build_e2e_graph(real_product_service, memory, llm, settings)

    result = process_message("customer_003", "empty-1", "Sony headphones under 500", graph=graph)

    assert result.recommendations == []
    assert "couldn't find any products" in result.response


def test_chatbot_service_does_not_import_streamlit() -> None:
    """The reusable service must stay frontend-independent."""
    code = (
        "import sys; import app.services.chatbot_service; "
        "print('streamlit' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False"
