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
        ydl_opts = {
            'quiet': True,
            'extract_flat': True, # Don't fetch full details for speed
            'ignoreerrors': True,
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
                        
                        # YouTube is generally lossy
                        quality = Quality(
                            format=AudioFormat.AAC, 
                            bitrate=192, # Optimistic estimate
                            media=MediaSource.WEB
                        )
                        
                        releases.append(Release(
                            title=title,
                            artist=query.artist, 
                            quality=quality,
                            source_name=self.name,
                            download_url=url
                        ))
        except Exception as e:
            # print(f"YouTube search error: {e}")
            pass
            
        return releases

