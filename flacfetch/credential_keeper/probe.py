"""Probe exported YouTube cookies with a real yt-dlp extraction.

The keeper's browser can report "Google session active - logged in" while
YouTube has already invalidated the exported cookie snapshot server-side
(observed on the netcup datacenter IP 2026-08-23/24: exports went dead within
hours of a fresh browser launch while the live session stayed "logged in",
taking YouTube search results down for ~30h). The only trustworthy health
signal is the one production uses: a real yt-dlp metadata extraction with the
exported cookie file.

On the flagged datacenter IP, anonymous access always returns LOGIN_REQUIRED
("Sign in to confirm you're not a bot") even with a PO token, so a successful
extraction proves the account cookies are alive.
"""
import asyncio
import logging
import os
from enum import Enum

logger = logging.getLogger(__name__)

# Public, stable, non-age-restricted probe target: "Me at the zoo" — the first
# video ever uploaded to YouTube. If this is ever unavailable, override here.
PROBE_VIDEO_URL = os.environ.get(
    "KEEPER_PROBE_VIDEO_URL", "https://www.youtube.com/watch?v=jNQXAC9IVRw"
)

# Substrings (lowercased) that prove the cookies themselves are dead/rejected,
# as opposed to a transient network / YouTube hiccup.
_INVALID_COOKIE_MARKERS = (
    "sign in to confirm you're not a bot",
    "sign in to confirm you’re not a bot",  # curly apostrophe variant
    "cookies are no longer valid",
    "login_required",
    "please sign in",
)


class ProbeOutcome(str, Enum):
    OK = "ok"
    INVALID_COOKIES = "invalid_cookies"
    TRANSIENT = "transient"


def classify_probe_error(error_message: str) -> ProbeOutcome:
    """Classify a yt-dlp extraction error: dead cookies vs transient failure."""
    lowered = (error_message or "").lower()
    if any(marker in lowered for marker in _INVALID_COOKIE_MARKERS):
        return ProbeOutcome.INVALID_COOKIES
    return ProbeOutcome.TRANSIENT


def probe_cookies_sync(cookies_file: str) -> tuple[ProbeOutcome, str]:
    """Run a yt-dlp metadata extraction using ``cookies_file``.

    Returns (outcome, message). Never raises: any failure is classified.
    """
    if not cookies_file or not os.path.exists(cookies_file):
        return ProbeOutcome.TRANSIENT, f"Cookie file missing: {cookies_file}"

    try:
        import yt_dlp

        from ..downloaders.youtube import get_ytdlp_cache_dir, ytdlp_opts_isolated

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "cookiefile": cookies_file,
            "skip_download": True,
        }
        # The keeper process may not have FLACFETCH_YTDLP_CACHE_DIR set; its
        # HOME/.cache is the Spotify token FILE on the server, so yt-dlp's
        # default cache path would raise NotADirectoryError. Disable caching
        # outright when no dedicated dir is configured — this is a probe, the
        # cache buys nothing.
        if not get_ytdlp_cache_dir():
            ydl_opts["cachedir"] = False

        with ytdlp_opts_isolated(ydl_opts) as opts, yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(PROBE_VIDEO_URL, download=False)

        title = (info or {}).get("title")
        if title:
            return ProbeOutcome.OK, f"Probe OK: extracted '{title[:50]}'"
        return ProbeOutcome.TRANSIENT, "Probe extraction returned no title"

    except Exception as e:
        outcome = classify_probe_error(str(e))
        return outcome, f"Probe failed ({outcome.value}): {e}"


async def probe_cookies(cookies_file: str) -> tuple[ProbeOutcome, str]:
    """Async wrapper around probe_cookies_sync (yt-dlp is synchronous)."""
    return await asyncio.to_thread(probe_cookies_sync, cookies_file)
