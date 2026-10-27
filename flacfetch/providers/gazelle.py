"""Base provider for Gazelle-based private music trackers (RED, OPS, etc.)."""

import html
import json
import threading
import time
from abc import abstractmethod
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import requests
from urllib3.exceptions import NewConnectionError

from ..core.album_match import AlbumCandidate, AlbumTrack, match_album_tracks, parse_file_list
from ..core.interfaces import Provider
from ..core.log import get_logger
from ..core.matching import calculate_match_score
from ..core.models import AudioFormat, MediaSource, Quality, Release, TrackQuery

logger = get_logger("GazelleProvider")

# Album search fetches file lists for only this many torrent groups: a browse by
# artist + album name returns the album itself (and maybe a deluxe edition) first.
ALBUM_SEARCH_MAX_GROUPS = 3

# All characters that break Sphinx filelist search
# Based on Gazelle's sph_escape_string() in sphinxql.class.php:112-131
# Plus blend_chars from sphinx.conf
#
# Operators: ( ) | - @ ~ & < > ! " / * $ ^ \ = ?
# Syntax: : [ ]
# Separators: , . ;
# Quotes: ' (apostrophe breaks searches like "I'm With You")
SPHINX_SPECIAL_CHARS = frozenset(r'()|\\-@~&<>!"/*$^=?:[],.;\'')


class GazelleProvider(Provider):
    """Base class for Gazelle-based private music trackers.

    Provides shared functionality for RED, OPS, and future Gazelle trackers:
    - Sphinx query sanitization
    - Quality parsing
    - File list parsing and matching
    - Torrent artifact fetching with caching
    """

    # Freeleech (FL) tokens can only be spent on torrents up to 5 GB.
    # See the RED wiki: a token makes the whole torrent personal-freeleech.
    FL_TOKEN_MAX_BYTES = 5 * 1024 ** 3

    # Spending a token makes a torrent personal-freeleech for 7 days. Within that
    # window a re-download is already free, so we must NOT spend another token on
    # the same torrent. We track spends locally with this TTL.
    FL_TOKEN_FREELEECH_WINDOW_SECONDS = 7 * 24 * 3600

    # After a token spend fails for a torrent, don't retry it for this long.
    FL_TOKEN_FAILURE_BACKOFF_SECONDS = 600

    # RED lets a user fetch each .torrent file through the API only four times,
    # ever ("You have already downloaded this torrent file four times"); after
    # that it must come from a browser. Every request that may have counted is
    # recorded per torrent in an on-disk ledger, and we stop at this many so one
    # download is always left for a manual browser recovery.
    TORRENT_DOWNLOAD_BUDGET = 3

    # A request the tracker never answered (read timeout, dropped connection,
    # 5xx) may still have counted server-side: on 2026-10-23 RED stalled and
    # three timed-out fetches used up a torrent's limit with nothing cached.
    # After one, don't ask the tracker for that torrent again for this long.
    UNANSWERED_COOLDOWN_SECONDS = 300

    # (connect, read) timeouts for .torrent downloads. The file is a few KB, so
    # a slow response is the tracker stalling; waiting it out is far cheaper
    # than a retry that spends another of the four downloads.
    DOWNLOAD_TIMEOUT = (10, 60)

    def __init__(self, api_key: str, base_url: str, cache_subdir: str, use_fl_token: bool = False):
        """Initialize the Gazelle provider.

        Args:
            api_key: API key for authentication
            base_url: Base URL of the tracker API
            cache_subdir: Subdirectory name for cache (e.g., "red" or "ops")
            use_fl_token: When True, spend a Freeleech token on eligible downloads
                (torrent <= 5 GB and not already freeleech). Falls back to a normal
                download if the token cannot be spent. Default False (opt-in).
        """
        if not base_url:
            raise ValueError("base_url is required. Set the appropriate environment variable.")

        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.use_fl_token = use_fl_token
        # Serialize token spends so concurrent downloads of the same torrent
        # (common in album batches) can't each spend a token on it.
        self._token_lock = threading.Lock()
        # RED requires >= 1s between token spends; pace them (guarded by the lock).
        self._last_token_spend_time = 0.0
        # In-memory {torrent_id: spend_time} ledger, a fallback for when the
        # on-disk marker can't be written (no cache dir / disk error) so we still
        # don't double-spend on the same torrent within the freeleech window.
        self._token_spends: dict[str, float] = {}
        # {torrent_id: failure_time} for token spends that just failed (no tokens
        # left, ineligible, per-torrent download limit). Album batches send many
        # requests for one torrent at once; without this every sibling retries
        # the same doomed usetoken=1 request.
        self._token_failures: dict[str, float] = {}
        # Per-torrent-ID locks so concurrent requests for the same torrent make
        # ONE tracker fetch: the first caches the .torrent, the rest hit the cache.
        self._fetch_locks: dict[str, threading.Lock] = {}
        self._fetch_locks_guard = threading.Lock()
        # Per-torrent download ledgers (see TORRENT_DOWNLOAD_BUDGET), kept in
        # memory only when there is no cache dir to persist them in.
        self._download_ledgers: dict[str, dict] = {}
        # {torrent_id: reason} for the last failed fetch, surfaced in job errors.
        self.fetch_errors: dict[str, str] = {}
        self.session = requests.Session()
        self.session.headers.update({"Authorization": self.api_key})
        self.search_limit = 10

        # Early termination settings
        self.early_termination = True
        # Stop a track sweep early only on a really well-seeded hit; this stays at
        # 50 even though BEST_CHOICE_MIN_SEEDERS (what counts as a good pick) is 10,
        # so a modest match can't cut the sweep short before the studio release.
        self.early_termination_seeders = 50
        self.early_termination_release_types = {"Album", "Single", "EP"}

        # Setup persistent cache
        self.cache_dir = Path.home() / ".flacfetch" / "cache" / cache_subdir
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            logger.debug(f"{cache_subdir.upper()} cache directory: {self.cache_dir}")
        except Exception as e:
            logger.warning(f"Could not create cache directory: {e}")
            self.cache_dir = None

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the provider name (e.g., 'RED' or 'OPS')."""
        pass

    def _sanitize_filelist_query(self, query: str) -> str:
        """Sanitize filelist query for Sphinx search.

        Based on Gazelle's sph_escape_string() function, which escapes all
        characters that have special meaning in Sphinx extended query syntax.

        Reference: Gazelle/classes/sphinxql.class.php:112-131

        Characters sanitized:
        - Operators: ( ) | - @ ~ & < > ! " / * $ ^ \\ = ?
        - Syntax: : [ ]
        - Separators: , . ;
        - Quotes: ' (apostrophe breaks searches like "I'm With You")

        International characters (CJK, Cyrillic, Arabic, accented Latin, etc.)
        are NOT modified - Sphinx handles these via charset_table and ngram_chars.
        """
        sanitized = query
        for char in SPHINX_SPECIAL_CHARS:
            sanitized = sanitized.replace(char, ' ')

        # Collapse multiple spaces into one
        sanitized = ' '.join(sanitized.split())

        logger.debug(f"Sanitized filelist query: '{query}' -> '{sanitized}'")
        return sanitized

    def _find_best_target_file(self, file_list_str: str, track_title: str) -> tuple[Optional[str], Optional[int], float]:
        """Find the best matching file in a torrent's file list.

        Args:
            file_list_str: Pipe-separated file list from torrent metadata
            track_title: The track title to match against

        Returns:
            Tuple of (filename, size_bytes, match_score) or (None, None, 0.0) if no match
        """
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
        """Parse quality information from torrent metadata.

        Args:
            torrent_data: Torrent metadata dict from API response

        Returns:
            Quality object with format, bit depth, bitrate, and media source
        """
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

    def _fetch_torrent_from_url(self, url: str, torrent_id: Optional[str] = None, max_retries: int = 3) -> Optional[bytes]:
        """Fetch a torrent file from URL and cache it.

        Every request goes through the per-torrent download ledger: nothing is
        sent once the torrent's budget is used up or while it is cooling down
        after an unanswered request (see TORRENT_DOWNLOAD_BUDGET).

        Args:
            url: Download URL for the torrent file
            torrent_id: Optional torrent ID for caching (parsed from the URL if omitted)
            max_retries: Maximum number of retries on rate limiting (default 3)

        Returns:
            Torrent file contents as bytes, or None on failure
        """
        # Ensure we don't re-download local files
        if url.startswith("/") or url.startswith("file://"):
            return None

        torrent_id = torrent_id or self.torrent_id_from_url(url)
        kind = "token" if "usetoken=1" in url else "normal"

        retries = 0
        while retries <= max_retries:
            blocked = self._download_blocked_reason(torrent_id)
            if blocked:
                logger.error(f"{self.name}: not requesting .torrent {torrent_id}: {blocked}")
                self.fetch_errors[torrent_id] = blocked
                return None

            try:
                logger.info(f"Fetching artifact from {url}")

                # Log Request Details (masked)
                req_headers = self.session.headers.copy()
                if "Authorization" in req_headers:
                    req_headers["Authorization"] = req_headers["Authorization"][:4] + "..." + req_headers["Authorization"][-4:]
                logger.debug(f"Downloading artifact from: {url}")
                logger.debug(f"Request Headers: {req_headers}")

                resp = self.session.get(url, timeout=self.DOWNLOAD_TIMEOUT, allow_redirects=False)
                logger.debug(f"Response Status: {resp.status_code}")
                logger.debug(f"Response Headers: {dict(resp.headers)}")

                if resp.status_code != 200:
                    try:
                        content = resp.json()
                        logger.debug(f"Response Body (JSON): {content}")
                        if self._is_limit_error(content):
                            self._handle_limit_error(torrent_id, content)
                            return None
                    except Exception:
                        logger.debug(f"Response Body (Text/Raw): {resp.text[:500]}")

                if resp.status_code in (301, 302, 303, 307, 308):
                    redirect_url = resp.headers.get("Location")
                    logger.debug(f"{self.name} download redirected to: {redirect_url}")
                    if redirect_url:
                        if redirect_url.startswith("/"):
                            redirect_url = self.base_url + redirect_url
                        resp = self.session.get(redirect_url, timeout=self.DOWNLOAD_TIMEOUT)

                if resp.status_code == 200:
                    # The download endpoint returns raw bencoded .torrent bytes on
                    # success (content-type application/x-bittorrent) but a JSON
                    # error body on failure (e.g. a token that can't be spent, or
                    # "already downloaded"). A JSON body is still HTTP 200, so guard
                    # against caching/returning it as a bogus torrent.
                    content_type = resp.headers.get("Content-Type", "").lower()
                    is_torrent = resp.content[:1] == b"d" and "json" not in content_type
                    if not is_torrent:
                        self._record_download_request(torrent_id, kind, "refused")
                        try:
                            err = resp.json()
                            if self._is_limit_error(err):
                                self._handle_limit_error(torrent_id, err)
                                return None
                            logger.warning(f"{self.name} download returned an error instead of a torrent: {err.get('error', err)}")
                            if torrent_id:
                                self.fetch_errors[torrent_id] = f"{self.name} refused the .torrent download: {err.get('error', err)}"
                        except ValueError:
                            logger.warning(f"{self.name} download returned non-torrent content ({len(resp.content)} bytes): {resp.content[:100]}")
                        return None

                    self._record_download_request(torrent_id, kind, "ok")
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
                    self._record_download_request(torrent_id, kind, "refused")
                    retries += 1
                    if retries <= max_retries:
                        logger.warning(f"Rate limited while fetching artifact. Retry {retries}/{max_retries} in 2s...")
                        time.sleep(2)
                        continue
                    else:
                        logger.error(f"Rate limit exceeded after {max_retries} retries")
                        return None
                else:
                    # A 4xx is an explicit refusal (nothing served). A 5xx may have
                    # been generated and counted before the server failed.
                    outcome = "unanswered" if resp.status_code >= 500 else "refused"
                    self._record_download_request(torrent_id, kind, outcome)
                    logger.error(f"Failed to fetch artifact: Status {resp.status_code}")
                    if outcome == "unanswered":
                        self.fetch_errors[torrent_id] = f"{self.name} returned HTTP {resp.status_code} for the .torrent download"
                    return None
            except requests.exceptions.RequestException as e:
                logger.error(f"Error fetching artifact: {e}")
                if self._request_never_sent(e):
                    # DNS / refused / TLS / proxy failure: the tracker never saw it.
                    self.fetch_errors[torrent_id] = f"Could not connect to {self.name} ({type(e).__name__})"
                    return None
                # Read timeout / dropped connection: the tracker may have counted it.
                self._record_download_request(torrent_id, kind, "unanswered")
                self.fetch_errors[torrent_id] = f"{self.name} did not answer the .torrent download ({type(e).__name__})"
                return None
            except Exception as e:
                # Anything else may have happened after the tracker served the
                # file, so count it rather than risk an unrecorded download.
                self._record_download_request(torrent_id, kind, "unanswered")
                logger.error(f"Error fetching artifact: {e}")
                self.fetch_errors[torrent_id] = f"{self.name} .torrent download failed ({type(e).__name__})"
                return None

        return None

    @staticmethod
    def _request_never_sent(error: Exception) -> bool:
        """True when a requests error happened before the request reached the tracker."""
        if isinstance(error, (requests.exceptions.ConnectTimeout, requests.exceptions.SSLError, requests.exceptions.ProxyError)):
            return True
        if isinstance(error, requests.exceptions.ConnectionError):
            # Wraps urllib3's MaxRetryError; NewConnectionError (incl. DNS
            # NameResolutionError) means no connection was ever made.
            reason = getattr(error.args[0], "reason", None) if error.args else None
            return isinstance(reason, NewConnectionError)
        return False

    @staticmethod
    def _is_limit_error(body) -> bool:
        return isinstance(body, dict) and body.get("status") == "failure" and "already downloaded" in str(body.get("error", ""))

    def _handle_limit_error(self, torrent_id: Optional[str], body: dict) -> None:
        """The tracker says this torrent's download limit is used up."""
        logger.error(f"{self.name} Limit Reached: {body.get('error')}")
        if torrent_id:
            self._mark_download_limit_reached(torrent_id)
            self.fetch_errors[torrent_id] = self._limit_reached_message(torrent_id)

    # ---- per-torrent download ledger --------------------------------------

    def _download_ledger_path(self, torrent_id: str) -> Optional[Path]:
        if not self.cache_dir or not torrent_id:
            return None
        return self.cache_dir / f"{torrent_id}.downloads.json"

    def _load_download_ledger(self, torrent_id: str) -> dict:
        """The torrent's ledger: ``{"requests": [{t, kind, outcome}], "limit_reached": bool}``.

        Re-read from disk each time (when there is a cache dir) so an operator can
        inspect or reset a torrent's ledger file without restarting the service.
        """
        path = self._download_ledger_path(torrent_id)
        if not path:
            return self._download_ledgers.setdefault(torrent_id, {"requests": [], "limit_reached": False})
        if not path.exists():
            return {"requests": [], "limit_reached": False}
        try:
            ledger = json.loads(path.read_text())
            if not (isinstance(ledger, dict) and isinstance(ledger.get("requests"), list)
                    and all(isinstance(r, dict) for r in ledger["requests"])):
                raise ValueError("unexpected ledger shape")
            return ledger
        except (OSError, ValueError) as e:
            # An unreadable ledger must not unlock fresh downloads.
            logger.warning(f"{self.name}: unreadable download ledger for {torrent_id} ({e}); treating as used up")
            return {"requests": [], "limit_reached": True}

    def _record_download_request(self, torrent_id: Optional[str], kind: str, outcome: str) -> None:
        """Record one download-endpoint request.

        ``outcome`` is ``ok`` (torrent served), ``unanswered`` (timeout, dropped
        connection, 5xx: may have counted) or ``refused`` (an explicit error or
        429: nothing served, not counted).
        """
        if not torrent_id:
            return
        ledger = self._load_download_ledger(torrent_id)
        ledger["requests"].append({"t": time.time(), "kind": kind, "outcome": outcome})
        self._save_download_ledger(torrent_id, ledger)

    def _mark_download_limit_reached(self, torrent_id: Optional[str]) -> None:
        if not torrent_id:
            return
        ledger = self._load_download_ledger(torrent_id)
        ledger["limit_reached"] = True
        self._save_download_ledger(torrent_id, ledger)

    def _save_download_ledger(self, torrent_id: str, ledger: dict) -> None:
        path = self._download_ledger_path(torrent_id)
        if not path:
            return
        try:
            path.write_text(json.dumps(ledger))
        except OSError as e:
            logger.warning(f"{self.name}: could not write download ledger for {torrent_id}: {e}")

    def downloads_used(self, torrent_id: str) -> int:
        """Requests for this torrent that may have counted against the tracker's limit."""
        ledger = self._load_download_ledger(torrent_id)
        return sum(1 for r in ledger["requests"] if r.get("outcome") in ("ok", "unanswered"))

    def _download_blocked_reason(self, torrent_id: Optional[str]) -> Optional[str]:
        """Why the tracker must not be asked for this torrent right now, or None."""
        if not torrent_id:
            return None
        ledger = self._load_download_ledger(torrent_id)
        if ledger.get("limit_reached") or self.downloads_used(torrent_id) >= self.TORRENT_DOWNLOAD_BUDGET:
            return self._limit_reached_message(torrent_id)
        unanswered = [r.get("t", 0) for r in ledger["requests"] if r.get("outcome") == "unanswered"]
        if unanswered:
            wait = unanswered[-1] + self.UNANSWERED_COOLDOWN_SECONDS - time.time()
            if wait > 0:
                return (
                    f"{self.name} did not answer the last .torrent request for {torrent_id}; "
                    f"not asking again for {int(wait) + 1}s so a stalled tracker can't use up "
                    f"its download limit. Retry in a few minutes."
                )
        return None

    def _limit_reached_message(self, torrent_id: str) -> str:
        path = self.cache_dir / f"{torrent_id}.torrent" if self.cache_dir else f"<cache>/{torrent_id}.torrent"
        return (
            f"{self.name} .torrent download budget used up for torrent {torrent_id} "
            f"({self.downloads_used(torrent_id)} recorded requests). Download the .torrent "
            f"in a browser and save it as {path}, then retry."
        )

    def _token_marker_path(self, torrent_id: str) -> Optional[Path]:
        """Path of the local marker recording a token spend for a torrent."""
        if not self.cache_dir or not torrent_id:
            return None
        return self.cache_dir / f"{torrent_id}.tokened"

    def _token_recently_spent(self, torrent_id: str) -> bool:
        """True if we spent a token on this torrent within the freeleech window.

        Personal-freeleech lasts 7 days, so a re-download inside that window is
        already free -- spending another token would be wasteful. Checks both the
        in-memory ledger and the on-disk marker so it survives a missing/unwritable
        cache dir (in-memory) and process restarts (on-disk).
        """
        if not torrent_id:
            return False
        now = time.time()

        ts = self._token_spends.get(torrent_id)
        if ts is not None and now - ts < self.FL_TOKEN_FREELEECH_WINDOW_SECONDS:
            return True

        marker = self._token_marker_path(torrent_id)
        if marker and marker.exists():
            try:
                if now - marker.stat().st_mtime < self.FL_TOKEN_FREELEECH_WINDOW_SECONDS:
                    return True
            except OSError:
                pass
        return False

    def _record_token_spend(self, torrent_id: str) -> None:
        """Record that a token was spent (or the torrent is already free) for a torrent.

        Records in memory always (double-spend guard even without a cache dir) and
        on disk when possible (survives restarts).
        """
        if not torrent_id:
            return
        self._token_spends[torrent_id] = time.time()
        marker = self._token_marker_path(torrent_id)
        if not marker:
            return
        try:
            marker.touch()
        except OSError as e:
            logger.warning(f"{self.name}: could not write token marker for {torrent_id}: {e}")

    def _spend_token(self, base_url: str, torrent_id: Optional[str]) -> Optional[bytes]:
        """Fetch the .torrent via ``usetoken=1`` to register personal freeleech.

        Returns the torrent bytes on success (token spent, or the torrent was
        already free and the server just returned it), or None if the token
        could not be spent (too large, none left, error) so the caller can fall
        back to a normal download.
        """
        # RED rejects tokens spent < 1s apart. Callers hold self._token_lock, so
        # pacing here safely serializes spends across concurrent downloads. Only
        # paced against the last *successful* spend, so once tokens are exhausted
        # (all attempts fail) we don't add a delay to every download.
        elapsed = time.time() - self._last_token_spend_time
        if elapsed < 1.1:
            time.sleep(1.1 - elapsed)

        sep = "&" if "?" in base_url else "?"
        token_url = f"{base_url}{sep}usetoken=1"
        logger.info(f"{self.name}: attempting Freeleech token download for torrent {torrent_id}")
        content = self._fetch_torrent_from_url(token_url, torrent_id)
        if content:
            self._last_token_spend_time = time.time()
        return content

    def _fetch_with_optional_token(self, base_url: str, torrent_id: Optional[str], use_token: bool) -> Optional[bytes]:
        """Fetch a torrent, optionally spending a Freeleech token.

        When ``use_token`` is True, first attempt the download with ``usetoken=1``.
        If that fails for any reason (torrent too large, no tokens left, server
        error), transparently fall back to a normal (ratio-counted) download so a
        failed token spend never blocks the download.

        NOTE: this does not consult the local ``.torrent`` cache -- callers that
        want cache reuse (e.g. ``fetch_artifact_by_id``) handle that themselves,
        because a cached ``.torrent`` file does NOT mean the download is
        freeleech (the token must be registered server-side via ``usetoken=1``).

        Args:
            base_url: Download URL without the ``usetoken`` parameter
            torrent_id: Torrent ID for caching/logging (may be None)
            use_token: Whether to attempt spending a Freeleech token

        Returns:
            Torrent file contents as bytes, or None on failure
        """
        if use_token and not (torrent_id and self._token_recently_spent(torrent_id)):
            # Serialize so concurrent requests for the same torrent (album
            # batches fire many at once) can't each spend a token on it.
            with self._token_lock:
                if not (torrent_id and self._token_recently_spent(torrent_id)):
                    content = self._spend_token(base_url, torrent_id)
                    if content:
                        if torrent_id:
                            self._record_token_spend(torrent_id)
                        logger.info(f"{self.name}: spent a Freeleech token on torrent {torrent_id}")
                        return content
                    logger.warning(f"{self.name}: Freeleech token download failed for torrent {torrent_id}; falling back to normal download")

        return self._fetch_torrent_from_url(base_url, torrent_id)

    def _is_token_eligible(self, release: Release) -> bool:
        """Decide whether it's worth spending a Freeleech token on a release.

        Prefers the server's ``canUseToken`` flag (captured from the browse
        response), which is true only when the account currently holds a
        spendable token for that torrent -- this is what lets us automatically
        use tokens whenever any are available and stop once they run out.

        When ``canUseToken`` is unknown (e.g. the direct torrent-ID path), falls
        back to a local heuristic: skip already-free torrents and torrents over
        the 5 GB token limit. The attempt-and-fallback in _fetch_with_optional_token
        remains the final safety net.
        """
        if release.can_use_token is not None:
            if not release.can_use_token:
                logger.debug(f"{self.name}: server reports no spendable token for this torrent")
            return release.can_use_token

        if getattr(release, "is_freeleech", False):
            logger.debug(f"{self.name}: torrent already freeleech; not spending a token")
            return False
        if release.size_bytes and release.size_bytes > self.FL_TOKEN_MAX_BYTES:
            logger.info(
                f"{self.name}: torrent size {release.size_bytes} bytes exceeds the "
                f"{self.FL_TOKEN_MAX_BYTES}-byte FL token limit; not spending a token"
            )
            return False
        return True

    def get_cached_artifact(self, source_id: str) -> Optional[bytes]:
        """Return the locally cached .torrent for a torrent ID, or None.

        Never contacts the tracker. Lets callers reuse a torrent we already hold
        (e.g. one that's fully seeded) without spending a token or counting
        against the tracker's per-torrent .torrent download limit.
        """
        if not self.cache_dir or not source_id:
            return None
        cache_path = self.cache_dir / f"{source_id}.torrent"
        if not cache_path.exists():
            return None
        try:
            data = cache_path.read_bytes()
        except OSError as e:
            logger.warning(f"Error reading from cache: {e}")
            return None
        if not data:
            return None
        logger.info(f"Found torrent in cache: {cache_path}")
        return data

    @staticmethod
    def torrent_id_from_url(url: Optional[str]) -> Optional[str]:
        """Extract the torrent ID from a tracker download URL (``...&id=123``)."""
        if not url:
            return None
        values = parse_qs(urlparse(url).query).get("id")
        return values[0] if values else None

    def fetch_artifact_by_id(self, source_id: str, use_token: Optional[bool] = None) -> Optional[bytes]:
        """Fetch .torrent file by torrent ID directly.

        Args:
            source_id: The torrent ID
            use_token: Whether to spend a Freeleech token. Defaults to the
                provider-level ``use_fl_token`` setting when None. No torrent size
                is known via this path, so eligibility relies on the graceful
                fallback in ``_fetch_with_optional_token``.

        Returns:
            Torrent file contents as bytes, or None on failure
        """
        if not source_id:
            return None

        if use_token is None:
            use_token = self.use_fl_token

        url = f"{self.base_url}/ajax.php?action=download&id={source_id}"

        # Single-flight per torrent: an album batch fires one request per track at
        # once, and without this they all missed the (not-yet-written) cache and
        # each fetched the .torrent -- counting against the tracker's per-torrent
        # download limit (RED: four). Siblings now wait and take the cached copy.
        with self._fetch_lock(source_id):
            self.fetch_errors.pop(source_id, None)
            cached = self.get_cached_artifact(source_id)

            # If we should spend a token and haven't already tokened this torrent
            # within the freeleech window, register the token FIRST -- before the
            # cache short-circuit. A cached .torrent file does NOT make a download
            # freeleech; the token must be registered server-side via usetoken=1,
            # otherwise the (re-)download of the torrent data counts against ratio.
            # A successful token spend is itself a .torrent download, though, so
            # with a cached copy in hand only spend one while it leaves room in the
            # download budget: running out would cost far more than one torrent's ratio.
            if (
                use_token
                and not self._token_recently_spent(source_id)
                and not self._token_recently_failed(source_id)
                and not (cached and self.downloads_used(source_id) + 1 >= self.TORRENT_DOWNLOAD_BUDGET)
            ):
                with self._token_lock:
                    content = self._spend_token(url, source_id)
                    if content:
                        self._record_token_spend(source_id)
                        logger.info(f"{self.name}: spent a Freeleech token on torrent {source_id}")
                        return content
                    self._token_failures[source_id] = time.time()
                    logger.warning(f"{self.name}: Freeleech token could not be spent on torrent {source_id}; using cache/normal download")

            # Serve the cached .torrent if present (token, if any, is already
            # registered above; a cached torrent is fine for the actual data fetch).
            if cached:
                return cached

            # No token spent and no cache: fetch normally.
            return self._fetch_torrent_from_url(url, source_id)

    def _fetch_lock(self, torrent_id: str) -> threading.Lock:
        """The lock serializing tracker fetches for one torrent ID."""
        with self._fetch_locks_guard:
            return self._fetch_locks.setdefault(torrent_id, threading.Lock())

    def _token_recently_failed(self, torrent_id: str) -> bool:
        """True if a token spend on this torrent failed within the backoff window."""
        ts = self._token_failures.get(torrent_id)
        return ts is not None and time.time() - ts < self.FL_TOKEN_FAILURE_BACKOFF_SECONDS

    def fetch_artifact(self, release: Release) -> Optional[bytes]:
        """Fetch .torrent file for a release.

        Args:
            release: The release to fetch the torrent for

        Returns:
            Torrent file contents as bytes, or None on failure
        """
        if not release.download_url:
            return None

        # Decide whether to spend a Freeleech token on this specific release.
        # Unlike fetch_artifact_by_id, we have the torrent size and freeleech
        # status here, so we can avoid wasting tokens on ineligible torrents.
        use_token = self.use_fl_token and self._is_token_eligible(release)

        # Extract Torrent ID for caching
        torrent_id = self.torrent_id_from_url(release.download_url)

        # Delegate to the by-id path (handles token spend, cache reuse, fallback).
        if torrent_id:
            return self.fetch_artifact_by_id(torrent_id, use_token=use_token)

        # Fallback: fetch directly from URL without caching
        return self._fetch_with_optional_token(release.download_url, None, use_token)

    @abstractmethod
    def search(self, query: TrackQuery) -> list[Release]:
        """Search for releases matching the query.

        Args:
            query: Track query with artist and title

        Returns:
            List of matching releases
        """
        pass

    RELEASE_TYPE_MAP = {
        1: "Album", 3: "Soundtrack", 5: "EP", 6: "Anthology",
        7: "Compilation", 9: "Single", 11: "Live album", 13: "Remix",
        14: "Bootleg", 15: "Interview", 16: "Mixtape", 17: "Demo",
        18: "Concert Recording", 19: "DJ Mix", 21: "Unknown"
    }

    def _get_torrent_group(self, group_id: int) -> Optional[tuple[dict[str, Any], list[dict[str, Any]]]]:
        """Fetch a torrent group. Returns (group, torrents), or None on any failure."""
        url = f"{self.base_url}/ajax.php"
        params = {"action": "torrentgroup", "id": group_id}
        resp = self.session.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("status") != "success":
            return None
        response = data.get("response", {})
        return response.get("group", {}), response.get("torrents", [])

    def _build_release(
        self,
        group: dict[str, Any],
        torrent: dict[str, Any],
        quality: Quality,
        token_eligibility: dict[int, bool],
    ) -> Release:
        """Build a torrent-level Release (no target file) from group + torrent metadata."""
        artists = group.get("musicInfo", {}).get("artists", [])
        artist = artists[0].get("name", "Unknown") if artists else "Unknown"
        group_year = group.get("year")
        release_type_str = self.RELEASE_TYPE_MAP.get(group.get("releaseType"), "Other")

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

        # Already-free torrents don't need (and shouldn't waste) a token.
        is_freeleech = bool(
            torrent.get("isFreeleech")
            or torrent.get("isPersonalFreeleech")
            or torrent.get("isNeutralLeech")
            or torrent.get("isFreeload")
            or torrent.get("freeTorrent")
        )

        # canUseToken comes from the browse response (torrentgroup omits
        # it). It's the authoritative "a token can be spent here" signal:
        # true only when the account currently has a spendable token.
        can_use_token = token_eligibility.get(torrent.get("id"))

        return Release(
            title=group.get("name"),
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
            is_freeleech=is_freeleech,
            can_use_token=can_use_token,
            source_id=str(torrent.get("id", "")),
        )

    def _fetch_group_details(
        self,
        group_id: int,
        track_title: str,
        token_eligibility: Optional[dict[int, bool]] = None,
    ) -> list[Release]:
        """Fetch detailed torrent information for a group.

        Args:
            group_id: The group ID to fetch details for
            track_title: The track title to match against files
            token_eligibility: Optional map of {torrentId: canUseToken} captured
                from the browse response. The torrentgroup endpoint does not
                return canUseToken, so we carry it over from the search results.

        Returns:
            List of releases matching the track title
        """
        token_eligibility = token_eligibility or {}

        try:
            fetched = self._get_torrent_group(group_id)
            if fetched is None:
                return []
            group, torrents = fetched

            releases = []
            for torrent in torrents:
                # Filter for Lossless only (FLAC/WAV)
                quality = self._parse_quality(torrent)
                if not quality.is_lossless():
                    continue

                file_list_str = torrent.get("fileList", "")
                target_file, target_size, match_score = self._find_best_target_file(file_list_str, track_title)

                if not target_file:
                    continue

                r = self._build_release(group, torrent, quality, token_eligibility)
                r.target_file = target_file
                r.target_file_size = target_size
                r.match_score = match_score
                r.track_pattern = track_title
                releases.append(r)

            return releases

        except Exception as e:
            logger.error(f"Error fetching group details for {group_id}: {e}")
            return []

    def _browse(self, params: dict[str, Any], raise_on_error: bool = False) -> tuple[list[int], dict[int, bool]]:
        """Run a browse query. Returns (ordered unique group IDs, {torrentId: canUseToken}).

        A failed browse returns empty results, or raises when ``raise_on_error``
        (album search: a tracker outage must not look like "album not found").
        """
        url = f"{self.base_url}/ajax.php"

        def fail(message: str):
            if raise_on_error:
                raise RuntimeError(f"{self.name} browse failed: {message}")
            return [], {}

        resp = self.session.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            logger.error(f"{self.name} API returned {resp.status_code}: {resp.text[:200]}")
            return fail(f"HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError:
            logger.error(f"{self.name} API returned invalid JSON")
            return fail("invalid JSON")
        if data.get("status") != "success":
            logger.warning(f"{self.name} API status not success: {data.get('status')} - Response: {data}")
            return fail(f"status {data.get('status')}")

        ordered_group_ids: list[int] = []
        seen = set()
        token_eligibility: dict[int, bool] = {}
        for g in data.get("response", {}).get("results", []):
            gid = g.get("groupId")
            if gid and gid not in seen:
                ordered_group_ids.append(gid)
                seen.add(gid)
            for t in g.get("torrents", []):
                tid = t.get("torrentId")
                if tid is not None and "canUseToken" in t:
                    token_eligibility[tid] = bool(t.get("canUseToken"))
        return ordered_group_ids, token_eligibility

    def search_album(
        self,
        artist: str,
        album: str,
        tracks: list[AlbumTrack],
        max_groups: int = ALBUM_SEARCH_MAX_GROUPS,
    ) -> list[AlbumCandidate]:
        """Find lossless torrents of one album and map every requested track to a file.

        One browse by artist + album name, then the file list of the top few
        groups. Each lossless torrent becomes an AlbumCandidate whose
        ``track_files`` holds the tracks it covers; torrents covering none are dropped.
        """
        params = {
            "action": "browse",
            "artistname": artist,
            "groupname": self._sanitize_filelist_query(album),
            "format": "FLAC",
        }
        logger.info(f"Album search on {self.name}: {artist} - {album} ({len(tracks)} tracks)")
        # Browse errors (HTTP, JSON, connection) propagate: the route reports a
        # tracker outage as 502, distinct from "no album found" (404).
        group_ids, token_eligibility = self._browse(params, raise_on_error=True)

        candidates: list[AlbumCandidate] = []
        selected = group_ids[:max_groups]
        fetched_ok = 0
        for gid in selected:
            # Rate limit: sleep before each request (same pacing as _search_browse)
            time.sleep(1.1)
            try:
                fetched = self._get_torrent_group(gid)
            except Exception as e:  # noqa: BLE001 - one bad group must not sink the album
                logger.error(f"Error fetching group details for {gid}: {e}")
                continue
            if fetched is None:
                continue
            fetched_ok += 1
            group, torrents = fetched
            for torrent in torrents:
                quality = self._parse_quality(torrent)
                if not quality.is_lossless():
                    continue
                files = parse_file_list(torrent.get("fileList", ""))
                track_files = match_album_tracks(files, tracks)
                if not track_files:
                    continue
                release = self._build_release(group, torrent, quality, token_eligibility)
                candidates.append(AlbumCandidate(release=release, track_files=track_files))

        if selected and not fetched_ok:
            raise RuntimeError(f"{self.name}: all {len(selected)} group detail requests failed")

        logger.info(f"Album search on {self.name}: {len(candidates)} torrents cover at least one track")
        return candidates

    def _search_browse(self, query: TrackQuery) -> list[Release]:
        """Perform a browse search and fetch group details.

        This is the common search implementation used by both RED and OPS.

        Args:
            query: Track query with artist and title

        Returns:
            List of matching releases
        """
        # Sanitize the filelist query to remove Sphinx special operators
        sanitized_title = self._sanitize_filelist_query(query.title) if query.title else ""

        params = {
            "action": "browse",
            "artistname": query.artist,
            "filelist": sanitized_title,
            "format": "FLAC"
        }

        logger.debug(f"Searching {self.name} with params: {params}")

        try:
            ordered_group_ids, token_eligibility = self._browse(params)
            logger.debug(f"Found {len(ordered_group_ids)} groups in {self.name} response")

            limited_group_ids = ordered_group_ids[:self.search_limit]

            if len(ordered_group_ids) > self.search_limit:
                logger.info(f"Limiting detailed fetch to top {self.search_limit} groups (out of {len(ordered_group_ids)} found)")

            logger.info(f"Fetching details for up to {len(limited_group_ids)} groups to resolve file lists...")

            releases = []
            excellent_found = False
            groups_fetched = 0

            for gid in limited_group_ids:
                # Rate limit: sleep before each request
                time.sleep(1.1)

                group_releases = self._fetch_group_details(gid, query.title, token_eligibility)
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

            logger.info(f"Total matching tracks parsed from {self.name}: {len(releases)}")
            return releases
        except requests.RequestException as e:
            logger.error(f"Connection error to {self.name}: {e}")
            return []
        except Exception as e:
            logger.exception(f"Unexpected error in {self.name}Provider: {e}")
            return []
