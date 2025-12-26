"""
Flacfetch API routes.
"""
from .config import router as config_router
from .credentials import router as credentials_router
from .download import router as download_router
from .health import router as health_router
from .search import router as search_router
from .torrents import router as torrents_router

__all__ = [
    "search_router",
    "download_router",
    "torrents_router",
    "health_router",
    "config_router",
    "credentials_router",
]

