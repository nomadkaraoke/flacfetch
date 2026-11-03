import logging
from typing import Optional

import yt_dlp  # type: ignore

from ..core.interfaces import Provider
from ..core.models import AudioFormat, MediaSource, Quality, Release, TrackQuery
from ..credential_keeper.refresh_request import cookies_mtime, wait_for_fresh_cookies
from ..downloaders.youtube import (
    get_cookies_file,
    get_ytdlp_base_opts,
    is_bot_block_error,
    ytdlp_opts_isolated,
)

logger = logging.getLogger(__name__)


class _YtdlpErrorLog:
    """yt-dlp logger that keeps errors, which ``ignoreerrors`` otherwise only prints."""

    def __init__(self):
        self.errors: list[str] = []

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        self.errors.append(msg)
        logger.warning(f"yt-dlp search error: {msg}")


class YoutubeProvider(Provider):
    """
    YouTube search provider using yt-dlp.

    Supports authenticated searches via cookies when configured.
    """

    def __init__(self, cookies_file: Optional[str] = None):
        """
        Initialize YouTube provider.

        Args:
            cookies_file: Optional path to cookies file for authenticated searches.
                         If not provided, will auto-detect from environment.
        """
        self.cookies_file = cookies_file

    @property
    def name(self) -> str:
        return "YouTube"

    def search(self, query: TrackQuery) -> list[Release]:
        """Search YouTube. A bot wall gets fresh cookies and one retry.

        Sets ``last_search_error`` when the results can't be trusted as complete.
        """
        cookies_file = self.cookies_file or get_cookies_file()
        baseline = cookies_mtime(cookies_file)

        releases, errors, failed = self._search_once(query)
        if any(is_bot_block_error(e) for e in errors) and wait_for_fresh_cookies(cookies_file, baseline):
            logger.info("Retrying YouTube search with fresh cookies")
            releases, errors, failed = self._search_once(query)

        self.last_search_error = self._search_error(releases, errors, failed)
        return releases

    @staticmethod
    def _search_error(releases: list[Release], errors: list[str], failed: Optional[str]) -> Optional[str]:
        """A bot wall or exception always counts. Other per-video errors count only
        with no results: a removed video among the top 5 is a normal search."""
        for error in errors:
            if is_bot_block_error(error):
                return error
        if failed:
            return failed
        if errors and not releases:
            return errors[0]
        return None

    def _search_once(self, query: TrackQuery) -> tuple[list[Release], list[str], Optional[str]]:
        """One yt-dlp run. Returns (releases, logged errors, exception text)."""
        # Search for 5 results. Keep the query plain: a " topic" suffix (meant to
        # surface auto-generated Topic channel audio) pushed the actual song out of
        # the top 5 entirely for lesser-known tracks (Playa Fly - N God We Trust).
        # select_best() still prefers Topic/official uploads when they do appear.
        search_query = f"ytsearch5:{query.artist} {query.title}"

        # Get base options (includes cookies if available)
        ydl_opts = get_ytdlp_base_opts(self.cookies_file)

        # Add search-specific options
        error_log = _YtdlpErrorLog()
        ydl_opts.update({
            'quiet': True,
            'extract_flat': False,
            'ignoreerrors': True,
            'no_warnings': True,
            'logger': error_log,
        })

        releases = []
        failed = None
        try:
            with ytdlp_opts_isolated(ydl_opts) as opts, yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(search_query, download=False)
                if info and 'entries' in info:
                    for entry in info['entries']:
                        if not entry: continue
                        title = entry.get('title', 'Unknown')

                        # Extract video ID for source identification
                        vid_id = entry.get('id')

                        # URL Generation: Prefer webpage_url, fall back to constructed, then direct
                        url = entry.get('webpage_url')
                        if not url:
                            if vid_id:
                                url = f"https://youtu.be/{vid_id}"
                            else:
                                url = entry.get('url')

                        # Extract Metadata
                        channel = entry.get('uploader') or entry.get('channel')
                        view_count = entry.get('view_count')
                        duration = entry.get('duration')

                        # Extract Year
                        upload_date = entry.get('upload_date') # YYYYMMDD
                        year = None
                        if upload_date and len(upload_date) >= 4:
                            try:
                                year = int(upload_date[:4])
                            except ValueError:
                                pass

                        # Find best audio format
                        formats = entry.get('formats', [])
                        best_audio = None
                        best_bitrate = 0.0

                        # Helper to get bitrate safely
                        def get_fmt_bitrate(f):
                            if f.get('abr'):
                                return float(f['abr'])
                            # Known ITAGs
                            fid = f.get('format_id')
                            if fid == '251': return 130.0 # Opus 160k target, usually ~130k avg
                            if fid == '140': return 128.0 # AAC 128k
                            if fid == '250': return 70.0
                            if fid == '249': return 50.0
                            if fid == '139': return 48.0
                            if fid == '18': return 96.0 # MP4 360p AAC
                            if fid == '22': return 192.0 # MP4 720p AAC
                            return 0.0

                        for f in formats:
                            # Only consider formats that contain audio
                            if f.get('acodec') == 'none':
                                continue

                            br = get_fmt_bitrate(f)

                            # Optimization: Prefer audio-only (vcodec='none') if bitrates are close?
                            # Actually, let's just find max bitrate.
                            if br > best_bitrate:
                                best_bitrate = br
                                best_audio = f
                            elif br == best_bitrate and br > 0 and best_audio:
                                # Tie-breaker: Prefer audio-only container
                                if f.get('vcodec') == 'none' and best_audio.get('vcodec') != 'none':
                                    best_audio = f

                        # Default values
                        fmt_enum = AudioFormat.AAC
                        bitrate = None
                        size = None

                        if best_audio:
                            acodec = best_audio.get('acodec', '')
                            ext = best_audio.get('ext', '')
                            if 'opus' in acodec or ext == 'opus':
                                fmt_enum = AudioFormat.OPUS

                            if best_bitrate > 0:
                                bitrate = int(best_bitrate)

                            # Filesize logic:
                            # If audio-only, use filesize.
                            if best_audio.get('vcodec') == 'none':
                                size = best_audio.get('filesize') or best_audio.get('filesize_approx')

                            # If we still don't have size (because video+audio container or missing meta),
                            # estimate from bitrate if we have it.
                            if not size and bitrate and duration:
                                size = int(duration * (bitrate * 1024 / 8))

                        quality = Quality(
                            format=fmt_enum,
                            bitrate=bitrate,
                            media=MediaSource.WEB
                        )

                        releases.append(Release(
                            title=title,
                            artist=query.artist,
                            quality=quality,
                            source_name=self.name,
                            download_url=url,
                            size_bytes=size,
                            channel=channel,
                            view_count=view_count,
                            duration_seconds=duration,
                            year=year,
                            source_id=vid_id,
                        ))
        except Exception as e:
            logger.warning(f"YouTube search failed: {e}")
            failed = str(e)

        return releases, error_log.errors, failed
