import html
import time
from pathlib import Path
from typing import Any, Optional

import requests

from ..core.interfaces import Provider
from ..core.log import get_logger
from ..core.matching import calculate_match_score
from ..core.models import AudioFormat, MediaSource, Quality, Release, TrackQuery

logger = get_logger("OPSProvider")

class OPSProvider(Provider):
    """Provider for OPS private music tracker.

    Requires both an API key and base URL to be provided.
    The base URL should be set via the OPS_API_URL environment variable
    for security reasons (to avoid hardcoding tracker URLs in source code).
    """

    def __init__(self, api_key: str, base_url: str):
        """Initialize the OPS provider.

        Args:
            api_key: API key for authentication
            base_url: Base URL of the tracker API (e.g., from OPS_API_URL env var)
        """
        if not base_url:
            raise ValueError("base_url is required for OPSProvider. Set OPS_API_URL environment variable.")

        self.api_key = api_key
        self.base_url = base_url.rstrip('/')  # Remove trailing slash if present
        self.session = requests.Session()
        self.session.headers.update({"Authorization": self.api_key})
        self.search_limit = 10  # Default limit (reduced for faster searches)

        # Early termination settings
        self.early_termination = True  # Stop fetching if we find an excellent result
        self.early_termination_seeders = 50  # Seeder threshold for early termination
        self.early_termination_release_types = {"Album", "Single", "EP"}  # Release types that qualify

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

    def _sanitize_filelist_query(self, query: str) -> str:
        """
        Sanitize filelist query for Sphinx search.

        Sphinx treats certain characters as special operators that break the search:
        - : (colon) - field search operator
        - / (slash) - path separator or operator
        - () (parentheses) - grouping
        - [] (brackets) - character classes
        - ! (exclamation) - NOT operator
        - , (comma) - separator
        - . (period) - wildcard
        - ; (semicolon) - separator

        We remove these characters to allow the search to work properly.
        """
        # Characters that break Sphinx filelist search
        special_chars = r':/()\[\]!,.;'

        # Remove special characters
        sanitized = query
        for char in special_chars:
            sanitized = sanitized.replace(char, ' ')

        # Collapse multiple spaces into one
        sanitized = ' '.join(sanitized.split())

        logger.debug(f"Sanitized filelist query: '{query}' -> '{sanitized}'")
        return sanitized

    def search(self, query: TrackQuery) -> list[Release]:
        url = f"{self.base_url}/ajax.php"

        # Sanitize the filelist query to remove Sphinx special operators
        sanitized_title = self._sanitize_filelist_query(query.title) if query.title else ""

        params = {
            "action": "browse",
            "artistname": query.artist,
            "filelist": sanitized_title,
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
            sorted(group_ids, reverse=True) # Newest first typically implies higher ID? Not always reliable but okay.
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

            logger.info(f"Fetching details for up to {len(limited_group_ids)} groups to resolve file lists...")

            releases = []
            excellent_found = False
            groups_fetched = 0

            # Rate limit: 10 requests per 10 seconds for API key auth
            # We already made 1 request (browse), so we need to pace the torrentgroup calls
            # Sleep BEFORE each torrentgroup call to ensure we don't exceed the limit

            for gid in limited_group_ids:
                # Sleep BEFORE the request to ensure proper spacing from previous request
                # This ensures we never exceed 10 requests in any 10-second window
                time.sleep(1.1)

                group_releases = self._fetch_group_details(gid, query.title)
                releases.extend(group_releases)
                groups_fetched += 1

                # Check for early termination
                if self.early_termination and not excellent_found:
                    for r in group_releases:
                        if (r.seeders and r.seeders >= self.early_termination_seeders and
                            r.release_type in self.early_termination_release_types):
                            excellent_found = True
                            logger.info(f"Found excellent result: {r.artist} - {r.title} ({r.seeders} seeders, {r.release_type})")
                            break

                # If we found an excellent result, fetch a few more groups then stop
                if excellent_found and groups_fetched >= min(5, len(limited_group_ids)):
                    logger.info(f"Early termination: stopping after {groups_fetched} groups (found excellent result)")
                    break

            logger.info(f"Total matching tracks parsed from OPS: {len(releases)}")
            return releases
        except requests.RequestException as e:
            logger.error(f"Connection error to OPS: {e}")
            return []
        except Exception as e:
            logger.exception(f"Unexpected error in OPSProvider: {e}")
            return []

    def _fetch_group_details(self, group_id: int, track_title: str) -> list[Release]:
        url = f"{self.base_url}/ajax.php"
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

                dl_url = f"{self.base_url}/ajax.php?action=download&id={torrent['id']}"

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

                # Extract torrent ID for source identification
                torrent_id = str(torrent.get("id", ""))

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
                    track_pattern=track_title,  # Ensure pattern is passed for highlighting
                    source_id=torrent_id,
                )
                releases.append(r)

            return releases

        except Exception as e:
            logger.error(f"Error fetching group details for {group_id}: {e}")
            return []

    def fetch_artifact_by_id(self, source_id: str) -> Optional[bytes]:
        """Fetch .torrent file by torrent ID directly, without needing a Release object."""
        if not source_id:
            return None

        # Check Cache first
        if self.cache_dir:
            cache_path = self.cache_dir / f"{source_id}.torrent"
            if cache_path.exists():
                try:
                    logger.info(f"Found torrent in cache: {cache_path}")
                    with open(cache_path, "rb") as f:
                        data = f.read()
                    if len(data) > 0:
                        return data
                except Exception as e:
                    logger.warning(f"Error reading from cache: {e}")

        # Construct download URL from torrent ID
        url = f"{self.base_url}/ajax.php?action=download&id={source_id}"
        return self._fetch_torrent_from_url(url, source_id)

    def _fetch_torrent_from_url(self, url: str, torrent_id: Optional[str] = None) -> Optional[bytes]:
        """Internal method to fetch a torrent file from a URL and cache it."""
        try:
            # Ensure we don't re-download local files
            if url.startswith("/") or url.startswith("file://"):
                return None

            logger.info(f"Fetching artifact from {url}")

            # Log Request Details (masked)
            req_headers = self.session.headers.copy()
            if "Authorization" in req_headers:
                req_headers["Authorization"] = req_headers["Authorization"][:4] + "..." + req_headers["Authorization"][-4:]
            logger.debug(f"Downloading artifact from: {url}")
            logger.debug(f"Request Headers: {req_headers}")

            resp = self.session.get(url, timeout=10, allow_redirects=False)
            logger.debug(f"Response Status: {resp.status_code}")
            logger.debug(f"Response Headers: {dict(resp.headers)}")

            if resp.status_code != 200:
                try:
                    content = resp.json()
                    logger.debug(f"Response Body (JSON): {content}")
                    if content.get("status") == "failure" and "already downloaded" in content.get("error", ""):
                        logger.error(f"OPS Limit Reached: {content.get('error')}")
                        logger.info("Tip: You can download the .torrent file manually from the website and place it in the cache directory:")
                        if self.cache_dir and torrent_id:
                            logger.info(f"  {self.cache_dir}/{torrent_id}.torrent")
                except:
                    logger.debug(f"Response Body (Text/Raw): {resp.text[:500]}")

            if resp.status_code in (301, 302, 303, 307, 308):
                redirect_url = resp.headers.get("Location")
                logger.debug(f"OPS download redirected to: {redirect_url}")
                if redirect_url:
                    if redirect_url.startswith("/"):
                        redirect_url = self.base_url + redirect_url
                    resp = self.session.get(redirect_url, timeout=10)

            if resp.status_code == 200:
                logger.debug(f"Artifact fetched successfully ({len(resp.content)} bytes)")
                if len(resp.content) < 1000:
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
                return self._fetch_torrent_from_url(url, torrent_id)
            else:
                logger.error(f"Failed to fetch artifact: Status {resp.status_code}")
        except Exception as e:
            logger.error(f"Error fetching artifact: {e}")
        return None

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

        # Check Cache first (reuse fetch_artifact_by_id if we have a torrent_id)
        if torrent_id:
            return self.fetch_artifact_by_id(torrent_id)

        # Fallback: fetch directly from URL without caching
        return self._fetch_torrent_from_url(release.download_url)

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
