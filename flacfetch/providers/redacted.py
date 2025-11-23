import requests
from typing import List, Optional, Any, Dict
from ..core.interfaces import Provider
from ..core.models import TrackQuery, Release, Quality, AudioFormat, MediaSource
from ..core.log import get_logger

logger = get_logger("RedactedProvider")

class RedactedProvider(Provider):
    BASE_URL = "https://redacted.sh"
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({"Authorization": self.api_key})
        
    @property
    def name(self) -> str:
        return "Redacted"

    def search(self, query: TrackQuery) -> List[Release]:
        url = f"{self.BASE_URL}/ajax.php"
        params = {
            "action": "browse",
            "searchstr": f"{query.artist} {query.title}",
        }
        
        logger.debug(f"Searching Redacted: {params['searchstr']}")
        
        try:
            resp = self.session.get(url, params=params, timeout=10)
            if resp.status_code != 200:
                logger.error(f"Redacted API returned {resp.status_code}: {resp.text[:200]}")
                return []
                
            try:
                data = resp.json()
            except ValueError:
                logger.error("Redacted API returned invalid JSON")
                return []
            
            if data["status"] != "success":
                logger.warning(f"Redacted API status not success: {data.get('status')} - Response: {data}")
                return []
                
            releases = []
            results = data.get("response", {}).get("results", [])
            logger.debug(f"Found {len(results)} groups in Redacted")
            
            for group in results:
                artist = group["artist"]
                group_name = group["groupName"]
                
                # Check strict matching if needed, but for now take all
                
                for torrent in group.get("torrents", []):
                    quality = self._parse_quality(torrent)
                    dl_url = f"{self.BASE_URL}/ajax.php?action=download&id={torrent['torrentId']}"
                    
                    releases.append(Release(
                        title=group_name,
                        artist=artist,
                        quality=quality,
                        source_name=self.name,
                        download_url=dl_url,
                        size_bytes=torrent.get("size")
                    ))
                    
            return releases
        except requests.RequestException as e:
            logger.error(f"Connection error to Redacted: {e}")
            return []
        except Exception as e:
            logger.exception(f"Unexpected error in RedactedProvider: {e}")
            return []

    def fetch_artifact(self, release: Release) -> Optional[bytes]:
        if not release.download_url:
            return None
        try:
            logger.info(f"Fetching artifact from {release.download_url}")
            resp = self.session.get(release.download_url, timeout=10)
            if resp.status_code == 200:
                return resp.content
            else:
                logger.error(f"Failed to fetch artifact: Status {resp.status_code}")
        except Exception as e:
            logger.error(f"Error fetching artifact: {e}")
        return None

    def _parse_quality(self, torrent_data: Dict[str, Any]) -> Quality:
        format_str = torrent_data.get("format", "").upper()
        encoding = torrent_data.get("encoding", "")
        media_str = torrent_data.get("media", "").upper()
        
        # Format
        if format_str == "FLAC":
            fmt = AudioFormat.FLAC
        elif format_str == "MP3":
            fmt = AudioFormat.MP3
        elif format_str == "AAC":
            fmt = AudioFormat.AAC
        elif format_str == "WAV":
            fmt = AudioFormat.WAV
        else:
            fmt = AudioFormat.OTHER
            
        # Media
        media_map = {
            "WEB": MediaSource.WEB,
            "CD": MediaSource.CD,
            "VINYL": MediaSource.VINYL,
            "DVD": MediaSource.DVD,
            "CASSETTE": MediaSource.CASSETTE
        }
        media = media_map.get(media_str, MediaSource.OTHER)
        
        # Bit depth / Bitrate
        bit_depth = None
        bitrate = None
        
        if fmt in (AudioFormat.FLAC, AudioFormat.WAV):
            if "24bit" in encoding:
                bit_depth = 24
            else:
                bit_depth = 16
        elif fmt in (AudioFormat.MP3, AudioFormat.AAC):
            # Parse "320", "V0 (VBR)"
            if "320" in encoding:
                bitrate = 320
            elif "V0" in encoding:
                bitrate = 245
            elif "V2" in encoding:
                bitrate = 190
            elif "APS" in encoding:
                bitrate = 215 # Approx VBR
            elif "APX" in encoding:
                bitrate = 245 # Approx VBR
            elif "192" in encoding:
                bitrate = 192
            elif "256" in encoding:
                bitrate = 256
            
        return Quality(
            format=fmt,
            bit_depth=bit_depth,
            bitrate=bitrate,
            media=media
        )
