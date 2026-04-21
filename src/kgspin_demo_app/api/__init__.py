"""
kgspin-demo API - FastAPI server for SaaS deployment.

This module provides REST API endpoints for knowledge graph extraction,
designed for non-Anthropic users who can't use Claude Desktop.
"""

from .server import FASTAPI_AVAILABLE

if FASTAPI_AVAILABLE:
    from .server import app, create_app
    __all__ = ["app", "create_app", "FASTAPI_AVAILABLE"]
else:
    app = None
    create_app = None
    __all__ = ["FASTAPI_AVAILABLE"]
