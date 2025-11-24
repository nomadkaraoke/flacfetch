import yt_dlp # type: ignore
import os
from typing import Optional
from ..core.interfaces import Downloader
from ..core.models import Release

class YoutubeDownloader(Downloader):
    def download(self, release: Release, output_path: str, output_filename: Optional[str] = None) -> str:
        """
        Download a YouTube video/audio.
        
        Args:
            release: Release object to download
            output_path: Directory to save the downloaded file
            output_filename: Optional specific filename (without extension)
        
        Returns:
            Path to the downloaded file
        """
        print(f"Downloading from YouTube: {release.title}...")
        
        # Determine output template
        if output_filename:
            # Remove extension if provided
            output_name = os.path.splitext(output_filename)[0]
            outtmpl = f'{output_path}/{output_name}.%(ext)s'
        else:
            outtmpl = f'{output_path}/%(title)s.%(ext)s'
        
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': outtmpl,
            'quiet': False,
        }
        
        downloaded_file = None
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(release.download_url, download=True)
                # Get the actual filename that was used
                downloaded_file = ydl.prepare_filename(info)
            print("YouTube download complete.")
            return downloaded_file
        except Exception as e:
            print(f"Error downloading from YouTube: {e}")
            raise

