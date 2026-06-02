"""
API Routers for Meridian Backend.

Contains route handlers for:
- chat: Chat endpoints for agent interactions
- products: Trip catalog endpoints (legacy /api/products alias)
- memory: Traveler memory endpoints
- diagnostics: RLS-probe and other live system diagnostics
"""

from .chat import router as chat_router
from .products import router as packages_router, legacy_router as products_router
from .memory import router as memory_router
from .diagnostics import router as diagnostics_router

__all__ = [
    "chat_router",
    "products_router",
    "packages_router",
    "memory_router",
    "diagnostics_router",
]
