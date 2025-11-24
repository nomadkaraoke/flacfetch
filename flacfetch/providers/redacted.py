import requests
from typing import List, Optional, Any, Dict
from ..core.interfaces import Provider
from ..core.models import TrackQuery, Release, Quality, AudioFormat, MediaSource
from ..core.log import get_logger
from ..core.matching import calculate_match_score, clean_filename

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
        # Updated search params to use filelist search
        params = {
            "action": "browse",
            "artistname": query.artist,
            "filelist": query.title
        }
        
        logger.debug(f"Searching Redacted with params: {params}")
        
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
            logger.debug(f"Found {len(results)} groups in Redacted response")
            
            # Debug log keys of the first torrent in first group to verify structure
            if results and results[0].get("torrents"):
                logger.debug(f"Sample torrent keys: {list(results[0]['torrents'][0].keys())}")

            for group in results:
                artist = group["artist"]
                group_name = group["groupName"]
                group_year = group.get("groupYear")
                
                torrents = group.get("torrents", [])
                logger.debug(f"Processing group '{group_name}' ({group_year}) with {len(torrents)} torrents")

                for torrent in torrents:
                    # Try to find matching file in fileList if present
                    file_list_str = torrent.get("fileList", "")
                    target_file = None
                    
                    if file_list_str:
                         target_file = self._find_target_file(file_list_str, query.title)
                         if not target_file:
                             # Skip if we are certain this torrent doesn't contain the song
                             continue
                    else:
                        # fileList missing (lazy load)
                        target_file = None
                        
                    quality = self._parse_quality(torrent)
                    dl_url = f"{self.BASE_URL}/ajax.php?action=download&id={torrent['torrentId']}"
                    
                    # Construct edition info
                    edition_parts = []
                    remaster_title = torrent.get("remasterTitle")
                    remaster_year = torrent.get("remasterYear")
                    remaster_record_label = torrent.get("remasterRecordLabel")
                    remaster_catalogue_number = torrent.get("remasterCatalogueNumber")
                    
                    if torrent.get("remastered"):
                        if remaster_title:
                            edition_parts.append(remaster_title)
                        if remaster_year:
                            edition_parts.append(f"{remaster_year}")
                    
                    edition_info = " ".join(edition_parts) if edition_parts else None
                    
                    # Use remaster info or group info
                    label = remaster_record_label or group.get("groupRecordLabel")
                    cat_num = remaster_catalogue_number or group.get("groupCatalogueNumber")
                    year = remaster_year if (torrent.get("remastered") and remaster_year) else group_year

                    r = Release(
                        title=group_name,
                        artist=artist,
                        quality=quality,
                        source_name=self.name,
                        download_url=dl_url,
                        size_bytes=torrent.get("size"),
                        year=year,
                        edition_info=edition_info,
                        label=label,
                        catalogue_number=cat_num,
                        target_file=target_file,
                        track_pattern=query.title # Store so we can resolve later
                    )
                    releases.append(r)
                    
            logger.info(f"Total matching tracks parsed from Redacted: {len(releases)}")
            return releases
        except requests.RequestException as e:
            logger.error(f"Connection error to Redacted: {e}")
            return []
        except Exception as e:
            logger.exception(f"Unexpected error in RedactedProvider: {e}")
            return []

    def populate_details(self, release: Release) -> None:
        """
        Fetches detailed torrent info to resolve file list and target file.
        """
        if release.target_file:
            return 
            
        try:
            import urllib.parse
            parsed = urllib.parse.urlparse(release.download_url)
            qs = urllib.parse.parse_qs(parsed.query)
            torrent_id = qs.get('id', [None])[0]
            
            if not torrent_id:
                logger.warning(f"Could not extract torrent ID from {release.download_url}")
                return
                
            logger.info(f"Fetching details for torrent {torrent_id} to resolve target file...")
            url = f"{self.BASE_URL}/ajax.php"
            params = {"action": "torrent", "id": torrent_id}
            
            resp = self.session.get(url, params=params, timeout=10)
            if resp.status_code != 200:
                logger.error(f"Failed to fetch torrent details: {resp.status_code}")
                return
                
            data = resp.json()
            if data["status"] != "success":
                logger.error("Torrent details API failed")
                return
                
            # Response structure: { response: { torrent: { fileList: "..." } } }
            t_data = data.get("response", {}).get("torrent", {})
            file_list_str = t_data.get("fileList", "")
            
            if file_list_str and release.track_pattern:
                target = self._find_target_file(file_list_str, release.track_pattern)
                if target:
                    release.target_file = target
                    logger.info(f"Resolved target file: {target}")
                else:
                    logger.warning(f"Track pattern '{release.track_pattern}' not found in file list for torrent {torrent_id}")
            else:
                logger.warning("No file list found in details")
                
        except Exception as e:
            logger.exception(f"Error populating details: {e}")

    def fetch_artifact(self, release: Release) -> Optional[bytes]:
        if not release.download_url:
            return None
        try:
            logger.info(f"Fetching artifact from {release.download_url}")
            resp = self.session.get(release.download_url, timeout=10)
            if resp.status_code == 200:
                logger.debug(f"Artifact fetched successfully ({len(resp.content)} bytes)")
                return resp.content
            else:
                logger.error(f"Failed to fetch artifact: Status {resp.status_code}")
        except Exception as e:
            logger.error(f"Error fetching artifact: {e}")
        return None

    def _find_target_file(self, file_list_str: str, track_title: str) -> Optional[str]:
        # Format: "filename{{{size}}}|||filename{{{size}}}..."
        if not file_list_str:
            return None
            
        files = file_list_str.split("|||")
        
        best_match = None
        best_score = 0.0
        
        for f_entry in files:
            # remove size part {{{...}}}
            if "{{{" in f_entry:
                fname = f_entry.split("{{{")[0]
            else:
                fname = f_entry
                
            # Skip non-audio files
            if not any(fname.lower().endswith(ext) for ext in ['.flac', '.mp3', '.m4a', '.wav']):
                continue
                
            score = calculate_match_score(track_title, fname)
            if score > best_score:
                best_score = score
                best_match = fname
        
        # Only accept matches that are reasonably good matches or contain the full phrase
        if best_score > 0.6: # Threshold
            logger.debug(f"Best file match: '{best_match}' (Score: {best_score:.2f}) for '{track_title}'")
            return best_match
            
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
