import requests
import time
import itertools
import os
import html
from pathlib import Path
from typing import Optional, Any
from ..core.interfaces import Provider
from ..core.models import TrackQuery, Release, Quality, AudioFormat, MediaSource
from ..core.log import get_logger
from ..core.matching import calculate_match_score

logger = get_logger("OPSProvider")

class OPSProvider(Provider):
    BASE_URL = "https://orpheus.network"
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({"Authorization": self.api_key})
        self.search_limit = 20 # Default limit
        
        # Setup persistent cache
        self.cache_dir = Path.home() / ".flacfetch" / "cache" / "ops"
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            logger.debug(f"OPS cache directory: {self.cache_dir}")
        except Exception as e:
            logger.warning(f"Could not create cache directory: {e}")
            self.cache_dir = None
        
    @property
    def name(self) -> str:
        return "OPS"

    def search(self, query: TrackQuery) -> list[Release]:
        url = f"{self.BASE_URL}/ajax.php"
        params = {
            "action": "browse",
            "artistname": query.artist,
            "filelist": query.title,
            # Filter for FLAC only at API level to reduce response size and processing
            "format": "FLAC" 
        }
        
        logger.debug(f"Searching OPS with params: {params}")
        
        try:
            resp = self.session.get(url, params=params, timeout=10)
            if resp.status_code != 200:
                logger.error(f"OPS API returned {resp.status_code}: {resp.text[:200]}")
                return []
                
            try:
                data = resp.json()
            except ValueError:
                logger.error("OPS API returned invalid JSON")
                return []
            
            if data["status"] != "success":
                logger.warning(f"OPS API status not success: {data.get('status')} - Response: {data}")
                return []
            
            browse_results = data.get("response", {}).get("results", [])
            logger.debug(f"Found {len(browse_results)} groups in OPS response")
            
            group_ids: set[int] = set()
            for group in browse_results:
                gid = group.get("groupId")
                if gid:
                    group_ids.add(gid)
            
            # Limit the number of groups we fetch details for
            sorted_group_ids = sorted(list(group_ids), reverse=True) # Newest first typically implies higher ID? Not always reliable but okay.
            # Or rely on search order (relevance/time). browse_results is already ordered by API default (Time descending usually).
            
            # Actually browse_results order is best.
            ordered_group_ids = []
            seen = set()
            for g in browse_results:
                gid = g.get("groupId")
                if gid and gid not in seen:
                    ordered_group_ids.append(gid)
                    seen.add(gid)
            
            limited_group_ids = ordered_group_ids[:self.search_limit]
            
            if len(ordered_group_ids) > self.search_limit:
                logger.info(f"Limiting detailed fetch to top {self.search_limit} groups (out of {len(ordered_group_ids)} found)")
            
            logger.info(f"Fetching details for {len(limited_group_ids)} groups to resolve file lists...")
            
            releases = []
            for gid in limited_group_ids:
                group_releases = self._fetch_group_details(gid, query.title)
                releases.extend(group_releases)
                time.sleep(1.1) 
                    
            logger.info(f"Total matching tracks parsed from OPS: {len(releases)}")
            return releases
        except requests.RequestException as e:
            logger.error(f"Connection error to OPS: {e}")
            return []
        except Exception as e:
            logger.exception(f"Unexpected error in OPSProvider: {e}")
            return []

    def _fetch_group_details(self, group_id: int, track_title: str) -> list[Release]:
        url = f"{self.BASE_URL}/ajax.php"
        params = {"action": "torrentgroup", "id": group_id}
        
        try:
            resp = self.session.get(url, params=params, timeout=10)
            if resp.status_code != 200:
                return []
            
            data = resp.json()
            if data["status"] != "success":
                return []
                
            response = data.get("response", {})
            group = response.get("group", {})
            torrents = response.get("torrents", [])
            
            artist = group.get("musicInfo", {}).get("artists", [{}])[0].get("name", "Unknown")
            group_name = group.get("name")
            group_year = group.get("year")
            release_type_id = group.get("releaseType")
            
            release_type_map = {
                1: "Album", 3: "Soundtrack", 5: "EP", 6: "Anthology", 
                7: "Compilation", 9: "Single", 11: "Live album", 13: "Remix",
                14: "Bootleg", 15: "Interview", 16: "Mixtape", 17: "Demo",
                18: "Concert Recording", 19: "DJ Mix", 21: "Unknown"
            }
            release_type_str = release_type_map.get(release_type_id, "Other")

            releases = []
            for torrent in torrents:
                # Filter for Lossless only (FLAC/WAV)
                # Even if we filter in search, group details returns ALL formats in that group.
                quality = self._parse_quality(torrent)
                if not quality.is_lossless():
                    continue

                file_list_str = torrent.get("fileList", "")
                target_file, target_size, match_score = self._find_best_target_file(file_list_str, track_title)
                
                if not target_file:
                    continue
                    
                dl_url = f"{self.BASE_URL}/ajax.php?action=download&id={torrent['id']}"
                
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
                
                label = remaster_record_label or group.get("recordLabel")
                cat_num = remaster_catalogue_number or group.get("catalogueNumber")
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
                    release_type=release_type_str,
                    seeders=torrent.get("seeders", 0),
                    target_file=target_file,
                    target_file_size=target_size,
                    match_score=match_score,
                    track_pattern=track_title # Ensure pattern is passed for highlighting
                )
                releases.append(r)
                
            return releases
            
        except Exception as e:
            logger.error(f"Error fetching group details for {group_id}: {e}")
            return []

    def fetch_artifact(self, release: Release) -> Optional[bytes]:
        if not release.download_url:
            return None
            
        # Extract Torrent ID for caching
        torrent_id = None
        try:
            if "id=" in release.download_url:
                torrent_id = release.download_url.split("id=")[1].split("&")[0]
        except IndexError:
            pass

        # Check Cache
        if torrent_id and self.cache_dir:
            cache_path = self.cache_dir / f"{torrent_id}.torrent"
            if cache_path.exists():
                try:
                    logger.info(f"Found torrent in cache: {cache_path}")
                    with open(cache_path, "rb") as f:
                        data = f.read()
                    if len(data) > 0:
                        return data
                except Exception as e:
                    logger.warning(f"Error reading from cache: {e}")

        try:
            # Ensure we don't re-download local files
            if release.download_url.startswith("/") or release.download_url.startswith("file://"):
                return None

            logger.info(f"Fetching artifact from {release.download_url}")
            
            # Ensure session has the API key (it should from __init__)
            # Some users report needing to append &usetoken=1 for download actions if using API key
            url = release.download_url
            # Remove usetoken=1 unless explicitly requested (it consumes FL tokens)
            # if "usetoken=1" not in url:
            #    url += "&usetoken=1"

            # OPS sometimes requires 'authkey' or 'passkey' for download actions if not using API key correctly,
            # but API key should supersede.
            # Let's ensure we are not getting a redirect that drops headers.
            
            # Log Request Details
            req_headers = self.session.headers.copy()
            # Mask API Key for logs
            if "Authorization" in req_headers:
                req_headers["Authorization"] = req_headers["Authorization"][:4] + "..." + req_headers["Authorization"][-4:]
            
            logger.debug(f"Downloading artifact from: {url}")
            logger.debug(f"Request Headers: {req_headers}")

            resp = self.session.get(url, timeout=10, allow_redirects=False)
            
            logger.debug(f"Response Status: {resp.status_code}")
            logger.debug(f"Response Headers: {dict(resp.headers)}")
            
            if resp.status_code != 200:
                try:
                    # Try to decode as JSON first if it's an API error
                    content = resp.json()
                    logger.debug(f"Response Body (JSON): {content}")
                    
                    if content.get("status") == "failure" and "already downloaded" in content.get("error", ""):
                        logger.error(f"OPS Limit Reached: {content.get('error')}")
                        logger.info("Tip: You can download the .torrent file manually from the website and place it in the cache directory:")
                        if self.cache_dir:
                            logger.info(f"  {self.cache_dir}/{torrent_id}.torrent")
                except:
                    # Fallback to text/raw
                    logger.debug(f"Response Body (Text/Raw): {resp.text[:500]}")

            if resp.status_code in (301, 302, 303, 307, 308):
                redirect_url = resp.headers.get("Location")
                logger.debug(f"OPS download redirected to: {redirect_url}")
                if redirect_url:
                    # If redirect is relative, make absolute
                    if redirect_url.startswith("/"):
                        redirect_url = self.BASE_URL + redirect_url
                    # Follow redirect manually to ensure headers are kept if same domain,
                    # or just let requests handle it if we didn't set allow_redirects=False.
                    # Actually, requests keeps headers for same-domain redirects.
                    # But if it redirects to a CDN, it might drop auth.
                    resp = self.session.get(redirect_url, timeout=10)

            if resp.status_code == 200:
                logger.debug(f"Artifact fetched successfully ({len(resp.content)} bytes)")
                if len(resp.content) < 1000:
                     # Suspiciously small, might be an error page?
                     logger.warning(f"Artifact seems too small ({len(resp.content)} bytes). Content sample: {resp.content[:100]}")
                
                # Save to Cache
                if torrent_id and self.cache_dir and len(resp.content) > 0:
                    try:
                        cache_path = self.cache_dir / f"{torrent_id}.torrent"
                        with open(cache_path, "wb") as f:
                            f.write(resp.content)
                        logger.debug(f"Cached torrent to {cache_path}")
                    except Exception as e:
                        logger.warning(f"Failed to cache torrent: {e}")
                        
                return resp.content
            elif resp.status_code == 429:
                logger.warning("Rate limited while fetching artifact. Retrying in 2s...")
                time.sleep(2)
                return self.fetch_artifact(release)
            else:
                logger.error(f"Failed to fetch artifact: Status {resp.status_code}")
                # 403 usually means "Account is disabled" or "Not logged in" or "Can't download this torrent"
                # Check if we need to refresh session or something? 
                # But usually API key is static.
        except Exception as e:
            logger.error(f"Error fetching artifact: {e}")
        return None

    def _find_best_target_file(self, file_list_str: str, track_title: str) -> tuple[Optional[str], Optional[int], float]:
        if not file_list_str:
            return None, None, 0.0
            
        files = file_list_str.split("|||")
        
        best_match = None
        best_size = None
        best_score = 0.0
        
        for f_entry in files:
            size = 0
            if "{{{" in f_entry:
                parts = f_entry.split("{{{")
                fname = parts[0]
                try:
                    size = int(parts[1].rstrip("}"))
                except (ValueError, IndexError):
                    size = 0
            else:
                fname = f_entry
            
            # Decode HTML entities (e.g., &amp; -> &)
            fname = html.unescape(fname)
                
            if not any(fname.lower().endswith(ext) for ext in ['.flac', '.mp3', '.m4a', '.wav']):
                continue
                
            score = calculate_match_score(track_title, fname)
            if score > best_score:
                best_score = score
                best_match = fname
                best_size = size
        
        if best_score > 0.6:
            return best_match, best_size, best_score
            
        return None, None, 0.0

    def _parse_quality(self, torrent_data: dict[str, Any]) -> Quality:
        format_str = torrent_data.get("format", "").upper()
        encoding = torrent_data.get("encoding", "")
        media_str = torrent_data.get("media", "").upper()
        
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
            
        media_map = {
            "WEB": MediaSource.WEB,
            "CD": MediaSource.CD,
            "VINYL": MediaSource.VINYL,
            "DVD": MediaSource.DVD,
            "CASSETTE": MediaSource.CASSETTE
        }
        media = media_map.get(media_str, MediaSource.OTHER)
        
        bit_depth = None
        bitrate = None
        
        if fmt in (AudioFormat.FLAC, AudioFormat.WAV):
            if "24bit" in encoding:
                bit_depth = 24
            else:
                bit_depth = 16
        elif fmt in (AudioFormat.MP3, AudioFormat.AAC):
            if "320" in encoding:
                bitrate = 320
            elif "V0" in encoding:
                bitrate = 245
            elif "V2" in encoding:
                bitrate = 190
            elif "APS" in encoding:
                bitrate = 215
            elif "APX" in encoding:
                bitrate = 245
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

