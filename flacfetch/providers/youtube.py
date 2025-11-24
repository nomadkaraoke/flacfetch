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
        search_query = f"ytsearch5:{query.artist} {query.title}"
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
                             duration = entry.get('duration', 0)
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
                            size_bytes=size
                        ))
        except Exception as e:
            # print(f"YouTube search error: {e}")
            pass
            
        return releases
