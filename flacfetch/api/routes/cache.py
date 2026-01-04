"""
Cache management endpoints for flacfetch HTTP API.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..auth import verify_api_key
from ..services import get_search_cache_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cache", tags=["cache"])


class CacheStatsResponse(BaseModel):
    """Response with cache statistics."""
    count: int = Field(..., description="Number of cached entries")
    total_size_bytes: int = Field(..., description="Total size of cache in bytes")
    oldest_entry: Optional[str] = Field(None, description="ISO timestamp of oldest entry")
    newest_entry: Optional[str] = Field(None, description="ISO timestamp of newest entry")
    configured: bool = Field(..., description="Whether GCS cache is configured")


class ClearCacheRequest(BaseModel):
    """Request to clear a specific cache entry."""
    artist: str = Field(..., description="Artist name")
    title: str = Field(..., description="Track title")


class ClearCacheResponse(BaseModel):
    """Response after clearing cache."""
    status: str
    message: str
    deleted: bool = Field(..., description="Whether an entry was deleted")


class ClearAllCacheResponse(BaseModel):
    """Response after clearing all cache entries."""
    status: str
    message: str
    deleted_count: int = Field(..., description="Number of entries deleted")


@router.get("/stats", response_model=CacheStatsResponse)
async def get_cache_stats(
    api_key: str = Depends(verify_api_key),
) -> CacheStatsResponse:
    """
    Get search cache statistics.

    Returns count of cached entries, total size, and age range.
    """
    cache_service = get_search_cache_service()
    stats = await cache_service.get_stats()

    return CacheStatsResponse(
        count=stats.get("count", 0),
        total_size_bytes=stats.get("total_size_bytes", 0),
        oldest_entry=stats.get("oldest_entry"),
        newest_entry=stats.get("newest_entry"),
        configured=stats.get("configured", False),
    )


@router.delete("/search", response_model=ClearCacheResponse)
async def clear_search_cache_entry(
    request: ClearCacheRequest,
    api_key: str = Depends(verify_api_key),
) -> ClearCacheResponse:
    """
    Clear a specific search cache entry by artist and title.

    Use this when cached results are stale (e.g., after flacfetch updates).
    The next search for this artist/title will fetch fresh results.
    """
    cache_service = get_search_cache_service()

    logger.info(f"Clearing cache for: {request.artist} - {request.title}")
    deleted = await cache_service.delete_cache_entry(request.artist, request.title)

    if deleted:
        return ClearCacheResponse(
            status="success",
            message=f"Cache entry cleared for '{request.artist}' - '{request.title}'",
            deleted=True,
        )
    else:
        return ClearCacheResponse(
            status="success",
            message=f"No cache entry found for '{request.artist}' - '{request.title}'",
            deleted=False,
        )


@router.delete("", response_model=ClearAllCacheResponse)
async def clear_all_cache(
    api_key: str = Depends(verify_api_key),
) -> ClearAllCacheResponse:
    """
    Clear all search cache entries.

    WARNING: This deletes all cached search results. Use sparingly.
    Typically used after major flacfetch updates that affect search behavior.
    """
    cache_service = get_search_cache_service()

    logger.warning("Clearing ALL cache entries")
    deleted_count = await cache_service.clear_all()

    return ClearAllCacheResponse(
        status="success",
        message=f"Cleared {deleted_count} cache entries",
        deleted_count=deleted_count,
    )
