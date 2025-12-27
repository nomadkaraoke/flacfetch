"""
Download manager for flacfetch HTTP API.

Manages active downloads, tracks their status, and handles GCS uploads.
"""
import asyncio
import logging
import os
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..models import DownloadStatus

logger = logging.getLogger(__name__)


@dataclass
class DownloadTask:
    """Represents an active or completed download task."""
    download_id: str
    search_id: Optional[str] = None  # Optional for download_by_id
    result_index: int = -1  # -1 for download_by_id
    status: DownloadStatus = DownloadStatus.QUEUED
    progress: float = 0.0
    peers: int = 0
    download_speed_kbps: float = 0.0
    upload_speed_kbps: float = 0.0
    eta_seconds: Optional[int] = None
    provider: Optional[str] = None
    title: Optional[str] = None
    artist: Optional[str] = None
    output_filename: Optional[str] = None
    output_path: Optional[str] = None
    gcs_path: Optional[str] = None
    upload_to_gcs: bool = False
    gcs_destination: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    torrent_id: Optional[int] = None  # Transmission torrent ID
    # Fields for download_by_id (direct download without search)
    source_id: Optional[str] = None
    target_file: Optional[str] = None
    download_url: Optional[str] = None


@dataclass
class SearchCache:
    """Cached search results for download reference."""
    search_id: str
    artist: str
    title: str
    results: List[Any]  # List of Release objects
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _generate_descriptive_filename(search: SearchCache, release: Any) -> str:
    """
    Generate a descriptive filename that distinguishes different versions.

    Format: Artist - Title (Type, Year, Media, Quality, Provider) [source_id].ext

    Examples:
        Avril Lavigne - Unwanted (Album, 2002, CD, 16bit, RED) [t1234567]
        Avril Lavigne - Unwanted (Album, 2002, WEB, 24bit, OPS) [t987654]
        piri - dog (EP, 2024, WEB, 16bit, Spotify) [4iV5W9uYEdYUVa79Axb7Rh]
        Roy Orbison - Oh Pretty Woman (YouTube) [ZGdLIwE7RSg]
    """
    import re

    parts = []

    # Base: Artist - Title
    artist = search.artist or "Unknown Artist"
    title = search.title or "Unknown Title"
    base = f"{artist} - {title}"

    # Release type (Album, Single, EP, Live album, etc.)
    release_type = getattr(release, 'release_type', None)
    if release_type:
        parts.append(release_type)

    # Year
    year = getattr(release, 'year', None)
    if year:
        parts.append(str(year))

    # Media (CD, WEB, VINYL)
    quality = getattr(release, 'quality', None)
    if quality:
        media = getattr(quality, 'media', None)
        if media:
            media_name = media.name if hasattr(media, 'name') else str(media)
            if media_name != "OTHER":
                parts.append(media_name)

        # Bit depth (16bit, 24bit)
        bit_depth = getattr(quality, 'bit_depth', None)
        if bit_depth:
            parts.append(f"{bit_depth}bit")

    # Provider (RED, OPS, YouTube, Spotify)
    source = getattr(release, 'source_name', None)
    if source:
        parts.append(source)

    # Build metadata suffix
    if parts:
        suffix = f" ({', '.join(parts)})"
    else:
        suffix = ""

    # Add source ID for traceability (torrent ID, Spotify track ID, YouTube video ID)
    source_id = getattr(release, 'source_id', None)
    if source_id:
        # Prefix torrent IDs with 't' to distinguish from other IDs
        if source and source.upper() in ("RED", "OPS"):
            source_id_str = f" [t{source_id}]"
        else:
            source_id_str = f" [{source_id}]"
    else:
        source_id_str = ""

    filename = f"{base}{suffix}{source_id_str}"

    # Sanitize filename (remove/replace invalid characters)
    # Keep alphanumeric, spaces, hyphens, underscores, parentheses, commas, brackets
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    filename = re.sub(r'\s+', ' ', filename).strip()

    return filename


class DownloadManager:
    """
    Manages download tasks and search result caching.

    Thread-safe for use with async background tasks.
    """

    def __init__(
        self,
        keep_seeding: bool = True,
        download_dir: Optional[str] = None,
        gcs_bucket: Optional[str] = None,
    ):
        """
        Initialize download manager.

        Args:
            keep_seeding: Keep torrents seeding after download completes
            download_dir: Directory for downloads
            gcs_bucket: GCS bucket for uploads
        """
        self.keep_seeding = keep_seeding
        self.download_dir = download_dir or os.environ.get(
            "FLACFETCH_DOWNLOAD_DIR",
            tempfile.gettempdir()
        )
        self.gcs_bucket = gcs_bucket or os.environ.get("GCS_BUCKET")

        # Thread-safe storage
        self._lock = threading.Lock()
        self._downloads: Dict[str, DownloadTask] = {}
        self._searches: Dict[str, SearchCache] = {}

        # FetchManager instance (lazily initialized)
        self._fetch_manager = None

        # Search cache TTL (1 hour)
        self._search_ttl_seconds = 3600

    def _get_fetch_manager(self):
        """Lazily initialize and return the FetchManager."""
        if self._fetch_manager is None:
            from flacfetch.core.manager import FetchManager  # noqa: I001
            from flacfetch.downloaders.youtube import YoutubeDownloader
            from flacfetch.providers.youtube import YoutubeProvider

            self._fetch_manager = FetchManager()

            # Add YouTube provider (always available)
            self._fetch_manager.add_provider(YoutubeProvider())
            self._fetch_manager.register_downloader("YouTube", YoutubeDownloader())

            # Add RED provider if configured (requires both key and URL)
            red_key = os.environ.get("RED_API_KEY")
            red_url = os.environ.get("RED_API_URL")
            if red_key and red_url:
                try:
                    from flacfetch.downloaders.torrent import TorrentDownloader
                    from flacfetch.providers.red import REDProvider

                    self._fetch_manager.add_provider(REDProvider(api_key=red_key, base_url=red_url))
                    self._fetch_manager.register_downloader(
                        "RED",
                        TorrentDownloader(keep_seeding=self.keep_seeding)
                    )
                    logger.info("RED provider initialized")
                except ImportError as e:
                    logger.warning(f"Could not initialize RED provider: {e}")

            # Add OPS provider if configured (requires both key and URL)
            ops_key = os.environ.get("OPS_API_KEY")
            ops_url = os.environ.get("OPS_API_URL")
            if ops_key and ops_url:
                try:
                    from flacfetch.downloaders.torrent import TorrentDownloader
                    from flacfetch.providers.ops import OPSProvider

                    self._fetch_manager.add_provider(OPSProvider(api_key=ops_key, base_url=ops_url))
                    self._fetch_manager.register_downloader(
                        "OPS",
                        TorrentDownloader(keep_seeding=self.keep_seeding)
                    )
                    logger.info("OPS provider initialized")
                except ImportError as e:
                    logger.warning(f"Could not initialize OPS provider: {e}")

            # Add Spotify provider if configured
            spotify_client_id = os.environ.get("SPOTIPY_CLIENT_ID")
            spotify_client_secret = os.environ.get("SPOTIPY_CLIENT_SECRET")
            if spotify_client_id and spotify_client_secret:
                try:
                    from flacfetch.downloaders.spotify import SpotifyDownloader
                    from flacfetch.providers.spotify import SpotifyProvider

                    spotify_provider = SpotifyProvider(
                        client_id=spotify_client_id,
                        client_secret=spotify_client_secret,
                    )
                    self._fetch_manager.add_provider(spotify_provider)
                    self._fetch_manager.register_downloader(
                        "Spotify",
                        SpotifyDownloader(provider=spotify_provider)
                    )
                    logger.info("Spotify provider initialized")
                except ImportError as e:
                    logger.warning(f"Could not initialize Spotify provider: {e}")
                except Exception as e:
                    logger.warning(f"Spotify provider initialization failed: {e}")

            # Set default provider priority
            available = [p.name for p in self._fetch_manager.providers]
            priority = [n for n in ["RED", "OPS", "Spotify", "YouTube"] if n in available]
            if priority:
                self._fetch_manager.set_provider_priority(priority)

        return self._fetch_manager

    def cache_search(
        self,
        search_id: str,
        artist: str,
        title: str,
        results: List[Any],
    ) -> None:
        """Cache search results for later download."""
        with self._lock:
            self._searches[search_id] = SearchCache(
                search_id=search_id,
                artist=artist,
                title=title,
                results=results,
            )

    def get_search(self, search_id: str) -> Optional[SearchCache]:
        """Get cached search results."""
        with self._lock:
            cache = self._searches.get(search_id)
            if cache:
                # Check TTL
                age = (datetime.now(timezone.utc) - cache.created_at).total_seconds()
                if age > self._search_ttl_seconds:
                    del self._searches[search_id]
                    return None
            return cache

    def create_download(
        self,
        search_id: str,
        result_index: int,
        output_filename: Optional[str] = None,
        upload_to_gcs: bool = False,
        gcs_destination: Optional[str] = None,
    ) -> DownloadTask:
        """
        Create a new download task.

        Returns the task (queued, not started yet).
        """
        download_id = f"dl_{uuid.uuid4().hex[:12]}"

        # Get search cache for metadata
        search = self.get_search(search_id)
        provider = None
        title = None
        artist = None
        if search and 0 <= result_index < len(search.results):
            release = search.results[result_index]
            provider = getattr(release, 'source_name', None)
            title = getattr(release, 'title', None)
            artist = search.artist

        task = DownloadTask(
            download_id=download_id,
            search_id=search_id,
            result_index=result_index,
            output_filename=output_filename,
            upload_to_gcs=upload_to_gcs,
            gcs_destination=gcs_destination,
            provider=provider,
            title=title,
            artist=artist,
            started_at=datetime.now(timezone.utc),
        )

        with self._lock:
            self._downloads[download_id] = task

        return task

    def get_download(self, download_id: str) -> Optional[DownloadTask]:
        """Get a download task by ID."""
        with self._lock:
            return self._downloads.get(download_id)

    def update_download(self, download_id: str, **updates) -> None:
        """Update a download task."""
        with self._lock:
            if download_id in self._downloads:
                task = self._downloads[download_id]
                for key, value in updates.items():
                    if hasattr(task, key):
                        setattr(task, key, value)

    def list_downloads(self) -> List[DownloadTask]:
        """List all download tasks."""
        with self._lock:
            return list(self._downloads.values())

    async def execute_download(self, download_id: str) -> None:
        """
        Execute a download task (runs in background).

        This is the main download logic that:
        1. Gets the release from cache
        2. Downloads via FetchManager
        3. Optionally uploads to GCS
        4. Updates task status throughout
        """
        task = self.get_download(download_id)
        if not task:
            logger.error(f"Download task not found: {download_id}")
            return

        try:
            self.update_download(download_id, status=DownloadStatus.DOWNLOADING)

            # Get search cache
            search = self.get_search(task.search_id)
            if not search:
                raise ValueError(f"Search not found or expired: {task.search_id}")

            if task.result_index < 0 or task.result_index >= len(search.results):
                raise ValueError(f"Invalid result index: {task.result_index}")

            release = search.results[task.result_index]
            logger.info(f"Starting download: {release.artist} - {release.title} from {release.source_name}")

            # Determine output filename
            output_filename = task.output_filename
            if not output_filename:
                output_filename = _generate_descriptive_filename(search, release)

            # Execute download
            manager = self._get_fetch_manager()
            output_path = manager.download(
                release,
                self.download_dir,
                output_filename=output_filename,
            )

            self.update_download(
                download_id,
                output_path=output_path,
                progress=100.0,
            )

            logger.info(f"Download complete: {output_path}")

            # Upload to GCS if requested
            if task.upload_to_gcs and task.gcs_destination:
                self.update_download(download_id, status=DownloadStatus.UPLOADING)
                gcs_path = await self._upload_to_gcs(output_path, task.gcs_destination)
                self.update_download(download_id, gcs_path=gcs_path)
                logger.info(f"Uploaded to GCS: {gcs_path}")

            # Final status depends on whether it's a torrent (seeding) or not
            if release.source_name in ["RED", "OPS"] and self.keep_seeding:
                self.update_download(download_id, status=DownloadStatus.SEEDING)
            else:
                self.update_download(download_id, status=DownloadStatus.COMPLETE)

            self.update_download(download_id, completed_at=datetime.now(timezone.utc))

        except Exception as e:
            logger.error(f"Download failed: {e}", exc_info=True)
            self.update_download(
                download_id,
                status=DownloadStatus.FAILED,
                error=str(e),
            )

    def create_download_by_id(
        self,
        source_name: str,
        source_id: str,
        output_filename: Optional[str] = None,
        target_file: Optional[str] = None,
        download_url: Optional[str] = None,
        upload_to_gcs: bool = False,
        gcs_destination: Optional[str] = None,
    ) -> DownloadTask:
        """
        Create a download task for direct download by source ID (no search required).

        This is useful when you have stored the source_id from a previous search
        and want to download later without re-searching.
        """
        download_id = f"dl_{uuid.uuid4().hex[:12]}"

        task = DownloadTask(
            download_id=download_id,
            provider=source_name,
            source_id=source_id,
            output_filename=output_filename,
            target_file=target_file,
            download_url=download_url,
            upload_to_gcs=upload_to_gcs,
            gcs_destination=gcs_destination,
            started_at=datetime.now(timezone.utc),
        )

        with self._lock:
            self._downloads[download_id] = task

        return task

    async def execute_download_by_id(self, download_id: str) -> None:
        """
        Execute a download-by-id task (runs in background).

        Uses FetchManager.download_by_id() to download directly by source ID
        without needing a cached search/Release object.
        """
        task = self.get_download(download_id)
        if not task:
            logger.error(f"Download task not found: {download_id}")
            return

        try:
            self.update_download(download_id, status=DownloadStatus.DOWNLOADING)

            if not task.source_id or not task.provider:
                raise ValueError("source_id and provider are required for download_by_id")

            logger.info(f"Starting direct download: {task.provider} ID={task.source_id}")

            # Execute download using FetchManager.download_by_id
            manager = self._get_fetch_manager()
            output_path = manager.download_by_id(
                source_name=task.provider,
                source_id=task.source_id,
                output_path=self.download_dir,
                output_filename=task.output_filename,
                target_file=task.target_file,
                download_url=task.download_url,
            )

            self.update_download(
                download_id,
                output_path=output_path,
                progress=100.0,
            )

            logger.info(f"Download complete: {output_path}")

            # Upload to GCS if requested
            if task.upload_to_gcs and task.gcs_destination:
                self.update_download(download_id, status=DownloadStatus.UPLOADING)
                gcs_path = await self._upload_to_gcs(output_path, task.gcs_destination)
                self.update_download(download_id, gcs_path=gcs_path)
                logger.info(f"Uploaded to GCS: {gcs_path}")

            # Final status depends on whether it's a torrent (seeding) or not
            if task.provider in ["RED", "OPS"] and self.keep_seeding:
                self.update_download(download_id, status=DownloadStatus.SEEDING)
            else:
                self.update_download(download_id, status=DownloadStatus.COMPLETE)

            self.update_download(download_id, completed_at=datetime.now(timezone.utc))

        except Exception as e:
            logger.error(f"Download by ID failed: {e}", exc_info=True)
            self.update_download(
                download_id,
                status=DownloadStatus.FAILED,
                error=str(e),
            )

    async def _upload_to_gcs(self, local_path: str, gcs_destination: str) -> str:
        """
        Upload a file to GCS.

        Args:
            local_path: Local file path
            gcs_destination: GCS path prefix (e.g., "uploads/job123/audio/")

        Returns:
            Full GCS path (gs://bucket/path)
        """
        if not self.gcs_bucket:
            raise ValueError("GCS_BUCKET not configured")

        from google.cloud import storage

        client = storage.Client()
        bucket = client.bucket(self.gcs_bucket)

        # Build GCS path
        filename = os.path.basename(local_path)
        gcs_path = gcs_destination.rstrip('/') + '/' + filename

        blob = bucket.blob(gcs_path)

        # Upload in executor to not block
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            blob.upload_from_filename,
            local_path,
        )

        return f"gs://{self.gcs_bucket}/{gcs_path}"


# Singleton instance
_download_manager: Optional[DownloadManager] = None


def get_download_manager() -> DownloadManager:
    """Get the singleton DownloadManager instance."""
    global _download_manager
    if _download_manager is None:
        keep_seeding = os.environ.get("FLACFETCH_KEEP_SEEDING", "true").lower() == "true"
        _download_manager = DownloadManager(keep_seeding=keep_seeding)
    return _download_manager
