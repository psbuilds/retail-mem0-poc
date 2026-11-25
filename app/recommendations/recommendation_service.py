"""Deterministic product filtering and ranking.

The LLM never selects products.  It only explains the products this module
returns.  Filtering applies hard requirements; ranking is transparent:
higher rating first, then lower price, then product ID for a stable order.
"""

from __future__ import annotations

from typing import Any, Iterable

DEFAULT_LIMIT = 5


def _as_lower_set(values: Iterable[str] | None) -> set[str]:
    """Normalise an iterable of strings into a case-insensitive set."""
    return {str(value).strip().casefold() for value in values or [] if str(value).strip()}


def _as_list(value: Any) -> list[str]:
    """Coerce a scalar or list into a clean list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value)]


def filter_products(
    products: list[dict[str, Any]],
    requirements: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return only products that satisfy every hard requirement.

    Supported requirement keys (all optional):
        category, brand, max_budget, wireless, noise_cancellation,
        required_features, excluded_features, excluded_brands,
        excluded_product_ids.

    ``brand`` may be a string or a list.  Product data is never modified.
    """
    category = requirements.get("category")
    brands = _as_lower_set(_as_list(requirements.get("brand")))
    excluded_brands = _as_lower_set(_as_list(requirements.get("excluded_brands")))
    required_features = _as_lower_set(_as_list(requirements.get("required_features")))
    excluded_features = _as_lower_set(_as_list(requirements.get("excluded_features")))
    excluded_ids = _as_lower_set(
        _as_list(
            requirements.get("excluded_product_ids")
            or requirements.get("exclude_product_ids")
        )
    )
    max_budget = requirements.get("max_budget")
    wireless = requirements.get("wireless")
    noise_cancellation = requirements.get("noise_cancellation")

    results: list[dict[str, Any]] = []
    for product in products:
        if category and str(product.get("category", "")).casefold() != str(category).casefold():
            continue
        if brands and str(product.get("brand", "")).casefold() not in brands:
            continue
        if str(product.get("brand", "")).casefold() in excluded_brands:
            continue
        if str(product.get("product_id", "")).casefold() in excluded_ids:
            continue
        if max_budget is not None:
            try:
                if float(product.get("price", 0)) > float(max_budget):
                    continue
            except (TypeError, ValueError):
                continue
        if wireless is not None and bool(product.get("wireless")) is not bool(wireless):
            continue
        if noise_cancellation is not None and bool(
            product.get("noise_cancellation")
        ) is not bool(noise_cancellation):
            continue

        features = _as_lower_set(_as_list(product.get("features")))
        if required_features and not required_features.issubset(features):
            continue
        if excluded_features and excluded_features & features:
            continue

        results.append(product)

    return results


def rank_products(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort products by rating descending, then price ascending.

    A stable product-ID tie-breaker keeps output deterministic across runs.
    """

    def sort_key(product: dict[str, Any]) -> tuple[float, float, str]:
        try:
            rating = float(product.get("rating", 0))
        except (TypeError, ValueError):
            rating = 0.0
        try:
            price = float(product.get("price", float("inf")))
        except (TypeError, ValueError):
            price = float("inf")
        return (-rating, price, str(product.get("product_id", "")))

    return sorted(products, key=sort_key)


def recommend(
    products: list[dict[str, Any]],
    requirements: dict[str, Any],
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Filter, rank, and truncate the catalogue for a shopping request.

    Returns an empty list when nothing matches.  No fallback or invented
    products are ever produced.
    """
    if limit <= 0:
        return []
    matching = filter_products(products, requirements)
    return rank_products(matching)[:limit]
