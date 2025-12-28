"""
Search result caching service using Google Cloud Storage.

Caches search results by normalized artist+title with configurable TTL.
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from flacfetch.core.models import Release

logger = logging.getLogger(__name__)

# Default TTL: 30 days
DEFAULT_TTL_DAYS = 30


class SearchCacheService:
    """
    Persistent search result cache using Google Cloud Storage.

    Caches search results by normalized artist+title with configurable TTL.
    All GCS operations run in a thread pool to avoid blocking the event loop.
    """

    def __init__(
        self,
        bucket_name: Optional[str] = None,
        ttl_days: int = DEFAULT_TTL_DAYS,
        prefix: str = "search-cache/",
    ):
        self.bucket_name = bucket_name or os.environ.get("GCS_BUCKET")
        self.ttl_days = ttl_days
        self.prefix = prefix
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="gcs-cache")
        self._client = None
        self._bucket = None

    def _get_client(self):
        """Lazily initialize GCS client."""
        if self._client is None:
            from google.cloud import storage

            self._client = storage.Client()
        return self._client

    def _get_bucket(self):
        """Lazily initialize bucket reference."""
        if self._bucket is None:
            self._bucket = self._get_client().bucket(self.bucket_name)
        return self._bucket

    @staticmethod
    def _normalize_part(s: str) -> str:
        """Normalize a string component: NFKC, lowercase, collapse whitespace."""
        s = unicodedata.normalize("NFKC", s)
        s = s.lower()
        s = re.sub(r"\s+", " ", s).strip()
        return s

    @staticmethod
    def normalize_cache_key(artist: str, title: str) -> str:
        """
        Generate normalized cache key from artist and title.

        Normalization steps:
        1. Unicode normalization (NFKC) - handles accents, ligatures
        2. Lowercase conversion
        3. Whitespace normalization (collapse multiple spaces, strip)
        4. Combine with delimiter and hash for safe, fixed-length key

        Returns:
            SHA256 hash prefix (first 32 chars) for consistent length
        """
        normalized_artist = SearchCacheService._normalize_part(artist)
        normalized_title = SearchCacheService._normalize_part(title)
        combined = f"{normalized_artist}|||{normalized_title}"
        hash_digest = hashlib.sha256(combined.encode("utf-8")).hexdigest()
        return hash_digest[:32]

    def _blob_path(self, cache_key: str) -> str:
        """Build GCS blob path from cache key."""
        return f"{self.prefix}{cache_key}.json"

    async def get_cached_search(
        self,
        artist: str,
        title: str,
    ) -> Optional[List[Release]]:
        """
        Retrieve cached search results if available and not expired.

        Returns None on cache miss, expiration, or any GCS error.
        GCS errors are logged but don't raise - fall back to fresh search.
        """
        if not self.bucket_name:
            logger.debug("GCS bucket not configured, skipping cache lookup")
            return None

        cache_key = self.normalize_cache_key(artist, title)
        blob_path = self._blob_path(cache_key)

        try:
            loop = asyncio.get_running_loop()
            data = await loop.run_in_executor(self._executor, self._read_blob_sync, blob_path)

            if data is None:
                return None

            # Check expiration
            expires_at_str = data.get("expires_at")
            if expires_at_str:
                expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) > expires_at:
                    logger.debug(f"Cache entry expired for key {cache_key}")
                    # Delete expired entry asynchronously
                    asyncio.create_task(self._delete_blob_async(blob_path))
                    return None

            # Deserialize releases
            results_data = data.get("results", [])
            releases = [Release.from_dict(r) for r in results_data]

            logger.info(f"Cache hit: {len(releases)} results for '{artist}' - '{title}'")
            return releases

        except Exception as e:
            logger.warning(f"Cache read error (falling back to fresh search): {e}")
            return None

    def _read_blob_sync(self, blob_path: str) -> Optional[dict]:
        """Synchronous GCS read (runs in executor)."""
        try:
            bucket = self._get_bucket()
            blob = bucket.blob(blob_path)

            if not blob.exists():
                return None

            content = blob.download_as_text()
            return json.loads(content)
        except Exception as e:
            logger.debug(f"Sync blob read failed: {e}")
            return None

    async def cache_search_results(
        self,
        artist: str,
        title: str,
        releases: List[Release],
    ) -> bool:
        """
        Cache search results to GCS.

        Returns True on success, False on failure.
        Failures are logged but don't raise - caching is best-effort.
        """
        if not self.bucket_name:
            logger.debug("GCS bucket not configured, skipping cache write")
            return False

        if not releases:
            logger.debug("No releases to cache")
            return False

        cache_key = self.normalize_cache_key(artist, title)
        blob_path = self._blob_path(cache_key)

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=self.ttl_days)

        cache_data = {
            "version": 1,
            "cache_key": cache_key,
            "artist": artist,
            "title": title,
            "normalized_artist": self._normalize_part(artist),
            "normalized_title": self._normalize_part(title),
            "created_at": now.isoformat().replace("+00:00", "Z"),
            "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
            "results_count": len(releases),
            "results": [r.to_dict() for r in releases],
        }

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self._executor, self._write_blob_sync, blob_path, cache_data)
            logger.info(f"Cached {len(releases)} results for '{artist}' - '{title}'")
            return True
        except Exception as e:
            logger.warning(f"Cache write error: {e}")
            return False

    def _write_blob_sync(self, blob_path: str, data: dict) -> None:
        """Synchronous GCS write (runs in executor)."""
        bucket = self._get_bucket()
        blob = bucket.blob(blob_path)
        blob.upload_from_string(json.dumps(data, indent=2, ensure_ascii=False), content_type="application/json")

    async def _delete_blob_async(self, blob_path: str) -> None:
        """Delete a blob asynchronously (best-effort)."""
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self._executor, self._delete_blob_sync, blob_path)
        except Exception as e:
            logger.debug(f"Failed to delete expired cache entry: {e}")

    def _delete_blob_sync(self, blob_path: str) -> None:
        """Synchronous blob deletion."""
        try:
            bucket = self._get_bucket()
            blob = bucket.blob(blob_path)
            blob.delete()
        except Exception:
            pass  # Best effort

    async def cleanup_expired(self) -> int:
        """
        Remove all expired cache entries.

        Lists all blobs in the cache prefix and deletes those past expiration.
        Returns count of deleted entries.
        """
        if not self.bucket_name:
            return 0

        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(self._executor, self._cleanup_expired_sync)
        except Exception as e:
            logger.error(f"Cache cleanup failed: {e}")
            return 0

    def _cleanup_expired_sync(self) -> int:
        """Synchronous cleanup (runs in executor)."""
        bucket = self._get_bucket()
        now = datetime.now(timezone.utc)
        deleted = 0

        # List all blobs with our prefix
        blobs = bucket.list_blobs(prefix=self.prefix)

        for blob in blobs:
            try:
                # Download and check expiration
                content = blob.download_as_text()
                data = json.loads(content)

                expires_at_str = data.get("expires_at")
                if expires_at_str:
                    expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
                    if now > expires_at:
                        blob.delete()
                        deleted += 1
                        logger.debug(f"Deleted expired cache: {blob.name}")
            except Exception as e:
                logger.debug(f"Error processing blob {blob.name}: {e}")

        return deleted


# Singleton instance
_search_cache_service: Optional[SearchCacheService] = None


def get_search_cache_service() -> SearchCacheService:
    """Get singleton SearchCacheService instance."""
    global _search_cache_service
    if _search_cache_service is None:
        ttl_days = int(os.environ.get("FLACFETCH_CACHE_TTL_DAYS", str(DEFAULT_TTL_DAYS)))
        _search_cache_service = SearchCacheService(ttl_days=ttl_days)
    return _search_cache_service
