from typing import Optional

import yt_dlp  # type: ignore

from ..core.interfaces import Provider
from ..core.models import AudioFormat, MediaSource, Quality, Release, TrackQuery
from ..downloaders.youtube import get_ytdlp_base_opts


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
        # Search for 5 results
        # Adding "topic" often helps find the auto-generated "Topic" channel results which are high quality audio
        search_query = f"ytsearch5:{query.artist} {query.title} topic"

        # Get base options (includes cookies if available)
        ydl_opts = get_ytdlp_base_opts(self.cookies_file)

        # Add search-specific options
        ydl_opts.update({
            'quiet': True,
            'extract_flat': False,
            'ignoreerrors': True,
            'no_warnings': True,
        })

        releases = []
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
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
        except Exception:
            # print(f"YouTube search error: {e}")
            pass

        return releases
