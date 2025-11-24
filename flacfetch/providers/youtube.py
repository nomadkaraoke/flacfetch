import yt_dlp # type: ignore
from typing import List
from ..core.interfaces import Provider
from ..core.models import TrackQuery, Release, Quality, AudioFormat, MediaSource

class YoutubeProvider(Provider):
    @property
    def name(self) -> str:
        return "YouTube"

    def search(self, query: TrackQuery) -> List[Release]:
        # Search for 5 results
        search_query = f"ytsearch5:{query.artist} {query.title}"
        # Disable extract_flat to get formats and duration, allowing size estimation
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
                        duration = entry.get('duration', 0)
                        
                        # Estimate size: 192kbps * duration
                        # 192 kbps = 24 KB/s
                        estimated_size = int(duration * 24 * 1024) if duration else None
                        
                        # YouTube usually serves Opus (WebM) or AAC (M4A). 
                        # Best audio is often Opus ~160kbps or AAC ~128kbps (high varies).
                        # Labeling as "AAC/Opus" or just "Web Audio"
                        
                        quality = Quality(
                            format=AudioFormat.AAC, # Generic container label for CLI
                            bitrate=192, # Average high quality estimate
                            media=MediaSource.WEB
                        )
                        
                        releases.append(Release(
                            title=title,
                            artist=query.artist, 
                            quality=quality,
                            source_name=self.name,
                            download_url=url,
                            size_bytes=estimated_size
                        ))
        except Exception as e:
            # print(f"YouTube search error: {e}")
            pass
            
        return releases
