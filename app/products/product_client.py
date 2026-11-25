"""HTTP client for the FastAPI product catalogue API.

This is the only place in the chatbot application that knows the product API's
URL shape.  The recommendation engine works on plain dictionaries, so it never
depends on HTTP details.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config.settings import Settings, get_settings


class ProductAPIError(RuntimeError):
    """Raised when the product API cannot be reached or returns an error."""


class ProductAPIClient:
    """Small typed wrapper around the product catalogue endpoints."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        http_client: httpx.Client | None = None,
        settings: Settings | None = None,
    ) -> None:
        settings = settings or get_settings()
        self.base_url = (base_url or settings.product_api_url).rstrip("/")
        self.timeout = timeout or settings.product_api_timeout_seconds
        self._client = http_client

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Send an HTTP request and translate transport problems into ProductAPIError."""
        url = f"{self.base_url}{path}"
        try:
            if self._client is not None:
                # Injected clients (including TestClient) manage their own timeout.
                response = self._client.request(method, url, **kwargs)
            else:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise ProductAPIError(
                f"Product API is unavailable at {self.base_url}: {exc}"
            ) from exc

        if response.status_code == 404:
            raise ProductAPIError(f"Product API returned 404 for {path}")
        if response.status_code >= 400:
            raise ProductAPIError(
                f"Product API error {response.status_code} for {path}: {response.text[:200]}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ProductAPIError("Product API returned a non-JSON response") from exc

    def health(self) -> dict[str, Any]:
        """Return the product API health payload."""
        return self._request("GET", "/health")

    def list_products(
        self,
        *,
        category: str | None = None,
        brand: str | None = None,
        max_price: float | None = None,
        wireless: bool | None = None,
        noise_cancellation: bool | None = None,
        features: list[str] | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return products matching the supplied filters.

        ``features`` is sent as repeated query parameters, which the FastAPI
        route accepts.  ``None`` values are omitted entirely so the backend uses
        its own defaults.
        """
        params: dict[str, Any] = {}
        if category:
            params["category"] = category
        if brand:
            params["brand"] = brand
        if max_price is not None:
            params["max_price"] = max_price
        if wireless is not None:
            params["wireless"] = str(wireless).lower()
        if noise_cancellation is not None:
            params["noise_cancellation"] = str(noise_cancellation).lower()
        if query:
            params["q"] = query
        feature_values = [feature for feature in features or [] if feature]
        if feature_values:
            params["features"] = feature_values

        payload = self._request("GET", "/products", params=params)
        products = payload.get("products")
        if not isinstance(products, list):
            raise ProductAPIError("Product API response did not contain a products list")
        return products

    def search_products(self, query: str) -> list[dict[str, Any]]:
        """Free-text search across product names, brands and features."""
        return self.list_products(query=query)

    def get_product(self, product_id: str) -> dict[str, Any]:
        """Fetch a single product by ID."""
        payload = self._request("GET", f"/products/{product_id}")
        if not isinstance(payload, dict):
            raise ProductAPIError("Product API returned an unexpected product payload")
        return payload
