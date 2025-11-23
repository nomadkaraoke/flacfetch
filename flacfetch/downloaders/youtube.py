import yt_dlp # type: ignore
from ..core.interfaces import Downloader
from ..core.models import Release

class YoutubeDownloader(Downloader):
    def download(self, release: Release, output_path: str) -> None:
        print(f"Downloading from YouTube: {release.title}...")
        ydl_opts = {
            'format': 'bestaudio/best',
            # Save to output_path. If output_path is a directory, use template.
            # If it's a file, typically we can't easily force filename with yt-dlp without template.
            'outtmpl': f'{output_path}/%(title)s.%(ext)s',
            'quiet': False,
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([release.download_url])
            print("YouTube download complete.")
        except Exception as e:
            print(f"Error downloading from YouTube: {e}")

