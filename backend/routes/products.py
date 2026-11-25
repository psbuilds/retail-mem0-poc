"""Product catalogue routes for the FastAPI backend.

The catalogue is loaded once from ``backend/data/products.json`` and filtered
in memory.  This module owns all catalogue query logic; the chatbot application
never reads the JSON file directly.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

CATALOGUE_PATH = Path(__file__).resolve().parents[1] / "data" / "products.json"

router = APIRouter(prefix="/products", tags=["products"])


@lru_cache(maxsize=1)
def load_catalogue() -> dict[str, Any]:
    """Load and cache the synthetic product catalogue.

    Returns:
        The parsed JSON document, including ``products`` and metadata.

    Raises:
        RuntimeError: If the catalogue file is missing or invalid JSON.
    """
    try:
        with CATALOGUE_PATH.open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:  # pragma: no cover - deployment error
        raise RuntimeError(f"Product catalogue not found at {CATALOGUE_PATH}") from exc
    except json.JSONDecodeError as exc:  # pragma: no cover - data error
        raise RuntimeError(f"Product catalogue is not valid JSON: {exc}") from exc


def all_products() -> list[dict[str, Any]]:
    """Return every product in the catalogue."""
    return load_catalogue()["products"]


def _matches_features(product: dict[str, Any], required: list[str] | None) -> bool:
    """Return True when the product has every required feature (case-insensitive)."""
    if not required:
        return True
    product_features = {feature.casefold() for feature in product.get("features", [])}
    return all(feature.casefold() in product_features for feature in required)


def filter_products(
    category: str | None = None,
    brand: str | None = None,
    max_price: float | None = None,
    min_price: float | None = None,
    wireless: bool | None = None,
    noise_cancellation: bool | None = None,
    features: list[str] | None = None,
    q: str | None = None,
) -> list[dict[str, Any]]:
    """Filter the catalogue by the supported query parameters.

    Brand matching is case-insensitive.  ``features`` are comma-separated values
    from a repeated query parameter and must all be present on the product.
    """
    required_features: list[str] = []
    for value in features or []:
        required_features.extend(
            part.strip() for part in value.split(",") if part.strip()
        )

    results: list[dict[str, Any]] = []
    for product in all_products():
        if category and product["category"].casefold() != category.casefold():
            continue
        if brand and product["brand"].casefold() != brand.casefold():
            continue
        if max_price is not None and product["price"] > max_price:
            continue
        if min_price is not None and product["price"] < min_price:
            continue
        if wireless is not None and product["wireless"] is not wireless:
            continue
        if (
            noise_cancellation is not None
            and product["noise_cancellation"] is not noise_cancellation
        ):
            continue
        if not _matches_features(product, required_features):
            continue
        if q:
            haystack = " ".join(
                [product["name"], product["brand"], *product.get("features", [])]
            ).casefold()
            if q.casefold() not in haystack:
                continue
        results.append(product)

    return sorted(results, key=lambda item: (-item["rating"], item["price"]))


def _filter_from_query(
    category: str | None,
    brand: str | None,
    max_price: float | None,
    min_price: float | None,
    wireless: bool | None,
    noise_cancellation: bool | None,
    features: list[str] | None,
    q: str | None,
) -> list[dict[str, Any]]:
    """Thin wrapper so ``/products`` and ``/products/search`` share one code path."""
    return filter_products(
        category=category,
        brand=brand,
        max_price=max_price,
        min_price=min_price,
        wireless=wireless,
        noise_cancellation=noise_cancellation,
        features=features,
        q=q,
    )


COMMON_QUERY_DOCS = {
    "category": "Filter by category, e.g. headphones or earbuds.",
    "brand": "Filter by brand (case-insensitive), e.g. Sony.",
    "max_price": "Maximum price in INR (inclusive).",
    "min_price": "Minimum price in INR (inclusive).",
    "wireless": "Only wireless (true) or wired (false) products.",
    "noise_cancellation": "Require active noise cancellation.",
    "features": "Repeatable feature filter, e.g. ?features=Bluetooth&features=Lightweight.",
    "q": "Case-insensitive text match on name, brand or features.",
}


@router.get("", summary="List products")
@router.get("/", include_in_schema=False)
def list_products(
    category: str | None = Query(None, description=COMMON_QUERY_DOCS["category"]),
    brand: str | None = Query(None, description=COMMON_QUERY_DOCS["brand"]),
    max_price: float | None = Query(None, ge=0, description=COMMON_QUERY_DOCS["max_price"]),
    min_price: float | None = Query(None, ge=0, description=COMMON_QUERY_DOCS["min_price"]),
    wireless: bool | None = Query(None, description=COMMON_QUERY_DOCS["wireless"]),
    noise_cancellation: bool | None = Query(
        None, description=COMMON_QUERY_DOCS["noise_cancellation"]
    ),
    features: list[str] | None = Query(None, description=COMMON_QUERY_DOCS["features"]),
    q: str | None = Query(None, description=COMMON_QUERY_DOCS["q"]),
) -> dict[str, Any]:
    """Return catalogue products matching the supplied filters."""
    results = _filter_from_query(
        category, brand, max_price, min_price, wireless, noise_cancellation, features, q
    )
    return {"count": len(results), "products": results}


@router.get("/search", summary="Search products")
def search_products(
    category: str | None = Query(None, description=COMMON_QUERY_DOCS["category"]),
    brand: str | None = Query(None, description=COMMON_QUERY_DOCS["brand"]),
    max_price: float | None = Query(None, ge=0, description=COMMON_QUERY_DOCS["max_price"]),
    min_price: float | None = Query(None, ge=0, description=COMMON_QUERY_DOCS["min_price"]),
    wireless: bool | None = Query(None, description=COMMON_QUERY_DOCS["wireless"]),
    noise_cancellation: bool | None = Query(
        None, description=COMMON_QUERY_DOCS["noise_cancellation"]
    ),
    features: list[str] | None = Query(None, description=COMMON_QUERY_DOCS["features"]),
    q: str | None = Query(None, description=COMMON_QUERY_DOCS["q"]),
) -> dict[str, Any]:
    """Search the catalogue. Same filters and response shape as ``/products``."""
    results = _filter_from_query(
        category, brand, max_price, min_price, wireless, noise_cancellation, features, q
    )
    return {"count": len(results), "products": results}


@router.get("/{product_id}", summary="Get one product")
def get_product(product_id: str) -> dict[str, Any]:
    """Return a single product by ID, or HTTP 404 when it does not exist."""
    for product in all_products():
        if product["product_id"].casefold() == product_id.casefold():
            return product
    raise HTTPException(status_code=404, detail=f"Product {product_id} not found")
