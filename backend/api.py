"""FastAPI application exposing the synthetic product catalogue.

Run with::

    uvicorn backend.api:app --reload --port 8000

This service is deliberately independent of the chatbot workflow.
"""

from __future__ import annotations

from fastapi import FastAPI

from backend.routes import products

app = FastAPI(
    title="Retail Memory Assistant — Product Catalogue API",
    description=(
        "Synthetic headphone catalogue used by the Retail Memory Shopping Assistant POC. "
        "Products and prices are sample data, not live inventory."
    ),
    version="1.0.0",
)

app.include_router(products.router)


@app.get("/health", tags=["health"], summary="Service health")

def health() -> dict[str, object]:
    """Report API health and how many products are loaded."""
    catalogue = products.load_catalogue()
    return {
        "status": "ok",
        "service": "product-catalogue",
        "catalogue_version": catalogue.get("catalogue_version"),
        "product_count": len(catalogue["products"]),
        "currency": catalogue.get("currency", "INR"),
    }
