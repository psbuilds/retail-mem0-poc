"""Application-level product operations.

The chatbot asks this service for a candidate set of products.  It deliberately
fetches a *superset* (category and budget only) and lets the deterministic
recommendation engine apply brand, feature and exclusion rules.  That keeps a
single source of truth for ranking and makes temporary overrides (for example
"JBL this time") straightforward.
"""

from __future__ import annotations

from typing import Any

from app.products.product_client import ProductAPIClient, ProductAPIError


class ProductService:
    """Retrieve catalogue products for the shopping workflow."""

    def __init__(self, client: ProductAPIClient | None = None) -> None:
        self.client = client or ProductAPIClient()
        self._brands: list[str] | None = None

    def available_brands(self) -> list[str]:
        """Return catalogue brand names (cached after the first successful call).

        Used by request understanding to recognise brand mentions and to parse
        retrieved memories.  Raises ProductAPIError when the API is unavailable.
        """
        if self._brands is None:
            products = self.client.list_products()
            self._brands = sorted({str(product["brand"]) for product in products})
        return list(self._brands)

    def fetch_products(
        self,
        *,
        category: str | None = None,
        max_budget: float | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return a candidate product list from the catalogue API.

        The API is filtered only by category / budget / free-text query here.
        Brand, wireless, noise-cancellation and feature rules are applied later by
        ``recommendation_service.recommend`` so precedence and exclusions are
        handled consistently in one place.

        Raises:
            ProductAPIError: If the API is unreachable or returns invalid data.
        """
        return self.client.list_products(
            category=category,
            max_price=max_budget,
            query=query,
        )

    def get_product(self, product_id: str) -> dict[str, Any]:
        """Fetch one product, raising ProductAPIError when unavailable."""
        return self.client.get_product(product_id)

    def health(self) -> dict[str, Any]:
        """Return product API health information."""
        return self.client.health()


def product_api_status() -> tuple[bool, str]:
    """Check product API availability without raising.

    Returns:
        ``(available, message)`` where message is safe to show in the UI.
    """
    try:
        payload = ProductAPIClient().health()
        count = payload.get("product_count", "?")
        return True, f"Product API online ({count} products)"
    except ProductAPIError as exc:
        return False, str(exc)
