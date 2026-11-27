# Documentation index — Retail Memory Shopping Assistant

This folder explains the actual implementation, file by file, plus the overall
architecture and workflows. If you open a Python file, the table below points to
its explanation.

## Architecture and workflows

| Document | What it explains |
|---|---|
| [overall_architecture.md](overall_architecture.md) | Components, boundaries, technology choices |
| [application_workflow.md](application_workflow.md) | One chat turn from Streamlit to routed response and memory outcome |
| [routing_workflow.md](routing_workflow.md) | Jev routing, guards, fallback, step planning and all required scenario paths |
| [memory_workflow.md](memory_workflow.md) | Mem0 MCP connection, scoping, retrieval, persistence, isolation |
| [langgraph_workflow.md](langgraph_workflow.md) | Routed step queue, nodes, checkpointing, follow-ups |
| [recommendation_workflow.md](recommendation_workflow.md) | Filtering, precedence, soft-memory relaxation, ranking |
| [data_flow.md](data_flow.md) | How data moves between every component |
| [configuration_and_project_files.md](configuration_and_project_files.md) | `.env`, requirements, run script, pytest config, JSON catalogue, root docs |

## Application source files

| Source file | Explanation |
|---|---|
| `app/main.py` | [app_main.md](app_main.md) |
| `app/config/settings.py` | [settings.md](settings.md) |
| `app/ui/streamlit_app.py` | [streamlit_app.md](streamlit_app.md) |
| `app/services/chatbot_service.py` | [chatbot_service.md](chatbot_service.md) |
| `app/agent/state.py` | [agent_state.md](agent_state.md) |
| `app/agent/prompts.py` | [agent_prompts.md](agent_prompts.md) |
| `app/agent/nodes.py` | [agent_nodes.md](agent_nodes.md) |
| `app/agent/graph.py` | [agent_graph.md](agent_graph.md) |
| `app/routing/__init__.py` | [query_router.md](query_router.md) |
| `app/routing/models.py` | [routing_models.md](routing_models.md) |
| `app/routing/questions.py` | [routing_questions.md](routing_questions.md) |
| `app/routing/jev_client.py` | [jev_client.md](jev_client.md) |
| `app/routing/rules.py` | [routing_rules.md](routing_rules.md) |
| `app/routing/classifier.py` | [routing_classifier.md](routing_classifier.md) |
| `app/memory/memory_client.py` | [memory_client.md](memory_client.md) |
| `app/memory/memory_service.py` | [memory_service.md](memory_service.md) |
| `app/memory/memory_manager.py` | [memory_manager.md](memory_manager.md) |
| `app/products/product_client.py` | [product_client.md](product_client.md) |
| `app/products/product_service.py` | [product_service.md](product_service.md) |
| `app/recommendations/recommendation_service.py` | [recommendation_service.md](recommendation_service.md) |
| `app/llm/ollama_client.py` | [ollama_client.md](ollama_client.md) |

## Backend, scripts and tests

| Source file | Explanation |
|---|---|
| `backend/api.py` | [product_api.md](product_api.md) |
| `backend/routes/products.py` | [product_routes.md](product_routes.md) |
| `scripts/check_services.py` | [check_services.md](check_services.md) |
| `tests/conftest.py` | [conftest.md](conftest.md) |
| `tests/test_products.py` | [test_products.md](test_products.md) |
| `tests/test_recommendations.py` | [test_recommendations.md](test_recommendations.md) |
| `tests/test_memory.py` | [test_memory.md](test_memory.md) |
| `tests/test_agent.py` | [test_agent.md](test_agent.md) |
| `tests/test_routing.py` | [test_routing.md](test_routing.md) |
| `tests/test_workflows.py` | [test_workflows.md](test_workflows.md) |
| `tests/test_end_to_end.py` | [test_end_to_end.md](test_end_to_end.md) |

## Package initialisers

| Source file | Explanation |
|---|---|
| `app/__init__.py` | [init_app.md](init_app.md) |
| `app/config/__init__.py` | [init_app_config.md](init_app_config.md) |
| `app/ui/__init__.py` | [init_app_ui.md](init_app_ui.md) |
| `app/services/__init__.py` | [init_app_services.md](init_app_services.md) |
| `app/agent/__init__.py` | [init_app_agent.md](init_app_agent.md) |
| `app/routing/__init__.py` | [init_app_routing.md](init_app_routing.md) |
| `app/memory/__init__.py` | [init_app_memory.md](init_app_memory.md) |
| `app/products/__init__.py` | [init_app_products.md](init_app_products.md) |
| `app/recommendations/__init__.py` | [init_app_recommendations.md](init_app_recommendations.md) |
| `app/llm/__init__.py` | [init_app_llm.md](init_app_llm.md) |
| `backend/__init__.py` | [init_backend.md](init_backend.md) |
| `backend/routes/__init__.py` | [init_backend_routes.md](init_backend_routes.md) |
| `tests/__init__.py` | [init_tests.md](init_tests.md) |
| `scripts/__init__.py` | [init_scripts.md](init_scripts.md) |

## Reading order

1. `overall_architecture.md`
2. `application_workflow.md`
3. `settings.md` and `chatbot_service.md`
4. `routing_workflow.md` → `query_router.md` → `routing_classifier.md` → `routing_rules.md`
5. `agent_state.md` → `agent_nodes.md` → `agent_graph.md`
6. `memory_client.md` → `memory_service.md` → `memory_manager.md`
7. `product_api.md` → `product_routes.md` → `product_client.md` → `product_service.md`
8. `recommendation_service.md` → `ollama_client.md`
9. `streamlit_app.md`
10. `memory_workflow.md`, `recommendation_workflow.md`, `data_flow.md`
11. tests and `check_services.md`
