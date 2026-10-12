"""
src.api — FastAPI Webhook Application Factory.
"""

from fastapi import FastAPI
from src.api.routes import router
from src.api.meta_routes import router as meta_router

def create_app() -> FastAPI:
    """Application factory for the Sensationz WhatsApp Bot API."""
    app = FastAPI(
        title="Sensationz WhatsApp AI Assistant",
        description="Scalable Multi-Course WhatsApp AI Assistant and Automation Service",
        version="2.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.include_router(router)
    app.include_router(meta_router)
    return app
