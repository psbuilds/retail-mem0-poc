"""Product catalogue API tests (unit, no network — uses FastAPI TestClient)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def test_health_reports_catalogue(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["product_count"] >= 20
    assert payload["currency"] == "INR"


def test_list_products(client: TestClient) -> None:
    response = client.get("/products")
    assert response.status_code == 200
    products = response.json()["products"]
    assert len(products) >= 20
    assert {product["brand"] for product in products} >= {
        "Sony",
        "JBL",
        "boAt",
        "Sennheiser",
        "Anker",
        "Realme",
    }
    # Default ordering matches the documented ranking: rating desc, price asc.
    first = products[0]
    assert first["rating"] >= products[-1]["rating"]


def test_get_product_by_id(client: TestClient) -> None:
    response = client.get("/products/HP001")
    assert response.status_code == 200
    assert response.json()["brand"] == "Sony"


def test_invalid_product_id_returns_404(client: TestClient) -> None:
    assert client.get("/products/does-not-exist").status_code == 404


def test_brand_filter_is_case_insensitive(client: TestClient) -> None:
    response = client.get("/products", params={"brand": "sony"})
    assert response.status_code == 200
    products = response.json()["products"]
    assert products
    assert all(product["brand"] == "Sony" for product in products)


def test_category_filter(client: TestClient) -> None:
    response = client.get("/products", params={"category": "earbuds"})
    products = response.json()["products"]
    assert products
    assert all(product["category"] == "earbuds" for product in products)


def test_budget_filter(client: TestClient) -> None:
    response = client.get("/products", params={"max_price": 3000})
    products = response.json()["products"]
    assert products
    assert all(product["price"] <= 3000 for product in products)


def test_wireless_and_noise_cancellation_filters(client: TestClient) -> None:
    response = client.get(
        "/products", params={"wireless": "true", "noise_cancellation": "true"}
    )
    products = response.json()["products"]
    assert products
    assert all(product["wireless"] and product["noise_cancellation"] for product in products)


def test_feature_filters(client: TestClient) -> None:
    response = client.get(
        "/products", params=[("features", "Bluetooth"), ("features", "Lightweight")]
    )
    products = response.json()["products"]
    assert products
    for product in products:
        features = {feature.casefold() for feature in product["features"]}
        assert {"bluetooth", "lightweight"} <= features


def test_search_endpoint_and_query(client: TestClient) -> None:
    response = client.get("/products/search", params={"q": "jbl"})
    assert response.status_code == 200
    assert response.json()["count"] >= 1
    assert all("jbl" in product["brand"].casefold() for product in response.json()["products"])


def test_no_matching_products_returns_empty_list(client: TestClient) -> None:
    response = client.get(
        "/products", params={"brand": "Sony", "max_price": 10, "category": "earbuds"}
    )
    assert response.status_code == 200
    assert response.json() == {"count": 0, "products": []}
