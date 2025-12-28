"""
Flacfetch HTTP API - FastAPI application.

This module provides the main FastAPI application for the flacfetch HTTP API,
enabling remote search and download of audio files.

Usage:
    # Run directly
    uvicorn flacfetch.api.main:app --host 0.0.0.0 --port 8080

    # Or via CLI
    flacfetch serve --port 8080

    # Or programmatically
    from flacfetch.api import create_app, run_server
    app = create_app()
    run_server(app, port=8080)
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import config_router, credentials_router, download_router, health_router, search_router, torrents_router
from .services import get_disk_manager, get_download_manager, get_search_cache_service, set_server_started_at

logger = logging.getLogger(__name__)

# Default settings
DEFAULT_PORT = 8080
DEFAULT_HOST = "0.0.0.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for startup/shutdown events.
    """
    # Startup
    logger.info("Starting flacfetch HTTP API...")

    # Track server start time
    set_server_started_at(datetime.now(timezone.utc))

    # Initialize singleton services
    _ = get_download_manager()
    _ = get_disk_manager()
    _ = get_search_cache_service()

    # Start background cleanup tasks
    cleanup_task = asyncio.create_task(_background_cleanup_loop())
    cache_cleanup_task = asyncio.create_task(_cache_cleanup_loop())

    logger.info("Flacfetch HTTP API started")

    yield

    # Shutdown
    logger.info("Shutting down flacfetch HTTP API...")
    cleanup_task.cancel()
    cache_cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    try:
        await cache_cleanup_task
    except asyncio.CancelledError:
        pass
    logger.info("Flacfetch HTTP API stopped")


async def _background_cleanup_loop():
    """
    Background task that periodically checks disk space and cleans up if needed.
    """
    disk_manager = get_disk_manager()
    check_interval = int(os.environ.get("FLACFETCH_CLEANUP_INTERVAL", "300"))  # 5 minutes

    while True:
        try:
            await asyncio.sleep(check_interval)

            if disk_manager.needs_cleanup():
                logger.info("Disk space low, triggering automatic cleanup")
                try:
                    import transmission_rpc
                    host = os.environ.get("TRANSMISSION_HOST", "localhost")
                    port = int(os.environ.get("TRANSMISSION_PORT", "9091"))
                    client = transmission_rpc.Client(host=host, port=port, timeout=10)

                    removed, freed = disk_manager.cleanup_oldest(client)
                    if removed > 0:
                        logger.info(f"Auto-cleanup: removed {removed} torrents, freed {freed / (1024**2):.1f} MB")
                except Exception as e:
                    logger.error(f"Auto-cleanup failed: {e}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in cleanup loop: {e}")


async def _cache_cleanup_loop():
    """
    Background task that periodically cleans up expired search cache entries.
    Runs once per day by default.
    """
    cleanup_interval = int(os.environ.get("FLACFETCH_CACHE_CLEANUP_INTERVAL", "86400"))  # 24 hours

    while True:
        try:
            await asyncio.sleep(cleanup_interval)

            cache_service = get_search_cache_service()
            removed = await cache_service.cleanup_expired()

            if removed > 0:
                logger.info(f"Cache cleanup: removed {removed} expired entries")

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in cache cleanup loop: {e}")


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        Configured FastAPI application instance
    """
    app = FastAPI(
        title="Flacfetch API",
        description="HTTP API for searching and downloading high-quality audio files",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Add CORS middleware (permissive for API access)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routers
    app.include_router(health_router)
    app.include_router(search_router)
    app.include_router(download_router)
    app.include_router(torrents_router)
    app.include_router(config_router)
    app.include_router(credentials_router)

    return app


def run_server(
    app: Optional[FastAPI] = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    log_level: str = "info",
):
    """
    Run the flacfetch HTTP API server.

    Args:
        app: FastAPI application (created if not provided)
        host: Host to bind to
        port: Port to listen on
        log_level: Logging level
    """
    import uvicorn

    if app is None:
        app = create_app()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    logger.info(f"Starting flacfetch API server on {host}:{port}")

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=log_level,
    )


# Create app instance for uvicorn
app = create_app()


if __name__ == "__main__":
    run_server()

