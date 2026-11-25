"""Deterministic recommendation engine tests (pure unit tests)."""

from __future__ import annotations

from typing import Any

import pytest

from app.recommendations.recommendation_service import (
    filter_products,
    rank_products,
    recommend,
)


def product(
    product_id: str,
    brand: str,
    price: float,
    rating: float,
    *,
    wireless: bool = True,
    noise_cancellation: bool = True,
    features: list[str] | None = None,
    category: str = "headphones",
) -> dict[str, Any]:
    return {
        "product_id": product_id,
        "name": f"{brand} {product_id}",
        "brand": brand,
        "category": category,
        "price": price,
        "rating": rating,
        "wireless": wireless,
        "noise_cancellation": noise_cancellation,
        "features": features or ["Bluetooth"],
    }


@pytest.fixture
def products() -> list[dict[str, Any]]:
    return [
        product("A", "Sony", 9000, 4.6, features=["Bluetooth", "Lightweight"]),
        product("B", "Sony", 5000, 4.6, features=["Bluetooth"]),
        product("C", "JBL", 3000, 4.2, noise_cancellation=False),
        product("D", "boAt", 2000, 4.2, wireless=False, noise_cancellation=False),
        product("E", "Anker", 4000, 4.8, features=["Extra Bass", "Lightweight"]),
    ]


def test_brand_filter(products: list[dict[str, Any]]) -> None:
    results = filter_products(products, {"brand": "jbl"})
    assert [item["product_id"] for item in results] == ["C"]


def test_budget_filter(products: list[dict[str, Any]]) -> None:
    results = filter_products(products, {"max_budget": 4000})
    assert {item["product_id"] for item in results} == {"C", "D", "E"}


def test_wireless_and_noise_cancellation_filter(products: list[dict[str, Any]]) -> None:
    results = filter_products(products, {"wireless": True, "noise_cancellation": True})
    assert {item["product_id"] for item in results} == {"A", "B", "E"}


def test_required_features(products: list[dict[str, Any]]) -> None:
    results = filter_products(products, {"required_features": ["lightweight"]})
    assert {item["product_id"] for item in results} == {"A", "E"}


def test_excluded_features(products: list[dict[str, Any]]) -> None:
    results = filter_products(products, {"excluded_features": ["Lightweight"]})
    assert {item["product_id"] for item in results} == {"B", "C", "D"}


def test_excluded_product_ids_and_brands(products: list[dict[str, Any]]) -> None:
    results = filter_products(
        products, {"excluded_product_ids": ["A"], "excluded_brands": ["JBL"]}
    )
    assert {item["product_id"] for item in results} == {"B", "D", "E"}


def test_ranking_by_rating_then_price(products: list[dict[str, Any]]) -> None:
    ranked = rank_products(products)
    assert [item["product_id"] for item in ranked] == ["E", "B", "A", "D", "C"]


def test_recommend_applies_limit(products: list[dict[str, Any]]) -> None:
    results = recommend(products, {"category": "headphones"}, limit=2)
    assert [item["product_id"] for item in results] == ["E", "B"]


def test_no_matching_results(products: list[dict[str, Any]]) -> None:
    assert recommend(products, {"brand": "Sennheiser"}) == []
    assert recommend(products, {"max_budget": 10}) == []


def test_input_products_are_not_mutated(products: list[dict[str, Any]]) -> None:
    snapshot = [dict(item) for item in products]
    recommend(products, {"brand": "Sony"}, limit=1)
    assert products == snapshot
