import yt_dlp # type: ignore
from typing import List, Optional
from ..core.interfaces import Provider
from ..core.models import TrackQuery, Release, Quality, AudioFormat, MediaSource

class YoutubeProvider(Provider):
    @property
    def name(self) -> str:
        return "YouTube"

    def search(self, query: TrackQuery) -> List[Release]:
        # Search for 5 results
        # Adding "topic" often helps find the auto-generated "Topic" channel results which are high quality audio
        search_query = f"ytsearch5:{query.artist} {query.title} topic"
        
        # Disable extract_flat to get formats and duration
        ydl_opts = {
            'quiet': True,
            'extract_flat': False, 
            'ignoreerrors': True,
            'no_warnings': True,
        }
        
        releases = []
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(search_query, download=False)
                if info and 'entries' in info:
                    for entry in info['entries']:
                        if not entry: continue
                        title = entry.get('title', 'Unknown')
                        url = entry.get('url') or entry.get('webpage_url')
                        
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
                        best_bitrate = 0
                        
                        for f in formats:
                            # Filter for audio-only if possible, or just best audio
                            if f.get('vcodec') == 'none' or f.get('acodec') != 'none':
                                abr = f.get('abr', 0) or 0
                                if abr > best_bitrate:
                                    best_bitrate = abr
                                    best_audio = f
                        
                        # Default values
                        fmt_enum = AudioFormat.AAC
                        bitrate = 192
                        size = None
                        
                        if best_audio:
                            # Determine format
                            ext = best_audio.get('ext', '')
                            if ext == 'opus' or best_audio.get('acodec', '').startswith('opus'):
                                fmt_enum = AudioFormat.OPUS
                            
                            bitrate = int(best_audio.get('abr', 192))
                            size = best_audio.get('filesize') or best_audio.get('filesize_approx')
                        
                        # Fallback size estimate if metadata missing but duration exists
                        if not size:
                             if duration:
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
                            year=year
                        ))
        except Exception as e:
            # print(f"YouTube search error: {e}")
            pass
            
        return releases
