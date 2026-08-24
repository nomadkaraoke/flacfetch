"""Tests for the keeper's yt-dlp cookie probe and self-heal remediation.

Covers the 2026-08-24 incident class: YouTube invalidates the keeper's
exported cookie snapshot server-side while the live browser session still
reports "logged in", so only a real yt-dlp extraction can prove the export
works — and a failed probe must trigger a browser relaunch, not an alert
suppressed by "keeper is actively managing".
"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from flacfetch.credential_keeper.probe import (
    ProbeOutcome,
    classify_probe_error,
    probe_cookies_sync,
)


# =============================================================================
# classify_probe_error
# =============================================================================


class TestClassifyProbeError:
    def test_bot_challenge_is_invalid_cookies(self):
        msg = (
            "ERROR: [youtube] DlhZYRGaHyE: Sign in to confirm you're not a bot. "
            "Use --cookies-from-browser or --cookies for the authentication."
        )
        assert classify_probe_error(msg) is ProbeOutcome.INVALID_COOKIES

    def test_bot_challenge_curly_apostrophe_is_invalid_cookies(self):
        msg = "Sign in to confirm you’re not a bot"
        assert classify_probe_error(msg) is ProbeOutcome.INVALID_COOKIES

    def test_rotated_cookies_warning_is_invalid_cookies(self):
        msg = (
            "The provided YouTube account cookies are no longer valid. "
            "They have likely been rotated in the browser as a security measure."
        )
        assert classify_probe_error(msg) is ProbeOutcome.INVALID_COOKIES

    def test_login_required_is_invalid_cookies(self):
        assert classify_probe_error("playability status: LOGIN_REQUIRED") is ProbeOutcome.INVALID_COOKIES

    def test_reworded_bot_challenge_still_invalid(self):
        """Markers are deliberately loose — a YouTube wording tweak must not
        downgrade dead cookies to 'transient' and disarm the self-heal."""
        assert classify_probe_error("Verify you're not a bot to continue") is ProbeOutcome.INVALID_COOKIES

    def test_network_error_is_transient(self):
        assert classify_probe_error("Connection reset by peer") is ProbeOutcome.TRANSIENT

    def test_rate_limit_is_transient(self):
        assert classify_probe_error("HTTP Error 429: Too Many Requests") is ProbeOutcome.TRANSIENT

    def test_empty_message_is_transient(self):
        assert classify_probe_error("") is ProbeOutcome.TRANSIENT
        assert classify_probe_error(None) is ProbeOutcome.TRANSIENT


# =============================================================================
# probe_cookies_sync
# =============================================================================


class TestProbeCookiesSync:
    def test_missing_file_is_transient(self, tmp_path):
        outcome, msg = probe_cookies_sync(str(tmp_path / "nope.txt"))
        assert outcome is ProbeOutcome.TRANSIENT
        assert "missing" in msg.lower()

    def test_none_path_is_transient(self):
        outcome, _ = probe_cookies_sync(None)
        assert outcome is ProbeOutcome.TRANSIENT

    def _cookie_file(self, tmp_path):
        f = tmp_path / "cookies.txt"
        f.write_text("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tabc\n")
        return str(f)

    def test_successful_extraction_is_ok(self, tmp_path):
        cookies = self._cookie_file(tmp_path)

        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = {"title": "Me at the zoo"}

        mock_yt_dlp = MagicMock()
        mock_yt_dlp.YoutubeDL.return_value = mock_ydl

        with patch.dict("sys.modules", {"yt_dlp": mock_yt_dlp}):
            outcome, msg = probe_cookies_sync(cookies)

        assert outcome is ProbeOutcome.OK
        assert "Me at the zoo" in msg

    def test_bot_challenge_extraction_is_invalid(self, tmp_path):
        cookies = self._cookie_file(tmp_path)

        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.side_effect = Exception(
            "Sign in to confirm you're not a bot"
        )

        mock_yt_dlp = MagicMock()
        mock_yt_dlp.YoutubeDL.return_value = mock_ydl

        with patch.dict("sys.modules", {"yt_dlp": mock_yt_dlp}):
            outcome, msg = probe_cookies_sync(cookies)

        assert outcome is ProbeOutcome.INVALID_COOKIES

    def test_network_failure_is_transient(self, tmp_path):
        cookies = self._cookie_file(tmp_path)

        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.side_effect = Exception("Connection timed out")

        mock_yt_dlp = MagicMock()
        mock_yt_dlp.YoutubeDL.return_value = mock_ydl

        with patch.dict("sys.modules", {"yt_dlp": mock_yt_dlp}):
            outcome, _ = probe_cookies_sync(cookies)

        assert outcome is ProbeOutcome.TRANSIENT

    def test_no_title_is_transient(self, tmp_path):
        cookies = self._cookie_file(tmp_path)

        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = {}

        mock_yt_dlp = MagicMock()
        mock_yt_dlp.YoutubeDL.return_value = mock_ydl

        with patch.dict("sys.modules", {"yt_dlp": mock_yt_dlp}):
            outcome, _ = probe_cookies_sync(cookies)

        assert outcome is ProbeOutcome.TRANSIENT


# =============================================================================
# refresh_and_validate_youtube (self-heal loop)
# =============================================================================


class TestRefreshAndValidateYoutube:
    """Exercises the remediation loop with injected fakes."""

    def _make(self, login_results=None, refresh_results=None, probe_results=None):
        """Build fakes; each *_results list is consumed one call at a time."""
        relaunches = []

        async def relaunch(browser):
            relaunches.append(1)
            browser["page"] = f"page{len(relaunches)}"
            browser["context"] = f"ctx{len(relaunches)}"

        login_iter = iter(login_results or [True] * 10)
        refresh_iter = iter(refresh_results or [True] * 10)
        probe_iter = iter(probe_results or [(ProbeOutcome.OK, "ok")] * 10)

        async def ensure_login(page):
            return next(login_iter)

        async def refresh(page, context):
            return next(refresh_iter)

        async def probe(path):
            return next(probe_iter)

        browser = {"pw": "pw0", "context": "ctx0", "page": "page0"}
        return browser, relaunch, ensure_login, refresh, probe, relaunches

    async def test_happy_path_no_relaunch(self):
        from flacfetch.credential_keeper.keeper import refresh_and_validate_youtube

        browser, relaunch, login, refresh, probe, relaunches = self._make()
        status = {}
        ok = await refresh_and_validate_youtube(
            browser, status, max_attempts=3,
            relaunch=relaunch, ensure_login=login, refresh=refresh, probe=probe,
        )
        assert ok is True
        assert relaunches == []
        assert status["youtube"]["last_probe_status"] == "ok"

    async def test_invalid_probe_triggers_relaunch_then_recovers(self):
        from flacfetch.credential_keeper.keeper import refresh_and_validate_youtube

        browser, relaunch, login, refresh, probe, relaunches = self._make(
            probe_results=[
                (ProbeOutcome.INVALID_COOKIES, "bot wall"),
                (ProbeOutcome.OK, "ok now"),
            ],
        )
        status = {}
        ok = await refresh_and_validate_youtube(
            browser, status, max_attempts=3,
            relaunch=relaunch, ensure_login=login, refresh=refresh, probe=probe,
        )
        assert ok is True
        assert len(relaunches) == 1
        assert status["youtube"]["last_probe_status"] == "ok"

    async def test_persistent_invalid_fails_after_max_attempts(self):
        from flacfetch.credential_keeper.keeper import refresh_and_validate_youtube

        browser, relaunch, login, refresh, probe, relaunches = self._make(
            probe_results=[(ProbeOutcome.INVALID_COOKIES, "still dead")] * 5,
        )
        status = {}
        ok = await refresh_and_validate_youtube(
            browser, status, max_attempts=3,
            relaunch=relaunch, ensure_login=login, refresh=refresh, probe=probe,
        )
        assert ok is False
        assert len(relaunches) == 2  # attempts 2 and 3 relaunch first
        assert status["youtube"]["last_failure_reason"] == "still dead"

    async def test_transient_probe_accepts_refresh_without_relaunch(self):
        from flacfetch.credential_keeper.keeper import refresh_and_validate_youtube

        browser, relaunch, login, refresh, probe, relaunches = self._make(
            probe_results=[(ProbeOutcome.TRANSIENT, "network blip")],
        )
        status = {}
        ok = await refresh_and_validate_youtube(
            browser, status, max_attempts=3,
            relaunch=relaunch, ensure_login=login, refresh=refresh, probe=probe,
        )
        assert ok is True
        assert relaunches == []

    async def test_login_failure_exhausts_attempts(self):
        from flacfetch.credential_keeper.keeper import refresh_and_validate_youtube

        browser, relaunch, login, refresh, probe, relaunches = self._make(
            login_results=[False] * 5,
        )
        status = {}
        ok = await refresh_and_validate_youtube(
            browser, status, max_attempts=2,
            relaunch=relaunch, ensure_login=login, refresh=refresh, probe=probe,
        )
        assert ok is False
        assert "re-login failed" in status["youtube"]["last_failure_reason"]

    async def test_refresh_failure_then_success_after_relaunch(self):
        from flacfetch.credential_keeper.keeper import refresh_and_validate_youtube

        browser, relaunch, login, refresh, probe, relaunches = self._make(
            refresh_results=[False, True],
        )
        status = {}
        ok = await refresh_and_validate_youtube(
            browser, status, max_attempts=3,
            relaunch=relaunch, ensure_login=login, refresh=refresh, probe=probe,
        )
        assert ok is True
        assert len(relaunches) == 1

    async def test_relaunch_first_restarts_browser_before_first_attempt(self):
        """When the periodic probe already proved the session's export dead,
        re-exporting from that session is a guaranteed no-op — attempt 1 must
        start from a fresh browser."""
        from flacfetch.credential_keeper.keeper import refresh_and_validate_youtube

        browser, relaunch, login, refresh, probe, relaunches = self._make()
        status = {}
        ok = await refresh_and_validate_youtube(
            browser, status, max_attempts=3, relaunch_first=True,
            relaunch=relaunch, ensure_login=login, refresh=refresh, probe=probe,
        )
        assert ok is True
        assert len(relaunches) == 1


# =============================================================================
# check_youtube_credentials (health check via shared probe)
# =============================================================================


class TestCheckYoutubeCredentialsProbe:
    def _with_cookie_file(self, tmp_path, monkeypatch):
        cookies = tmp_path / "cookies.txt"
        cookies.write_text("# Netscape HTTP Cookie File\n")
        monkeypatch.setenv("YOUTUBE_COOKIES_FILE", str(cookies))
        return str(cookies)

    def test_probe_ok_reports_ok(self, tmp_path, monkeypatch):
        from flacfetch.api.services import credential_check as cc

        self._with_cookie_file(tmp_path, monkeypatch)
        with patch(
            "flacfetch.credential_keeper.probe.probe_cookies_sync",
            return_value=(ProbeOutcome.OK, "Probe OK: extracted 'Me at the zoo'"),
        ):
            result = cc.check_youtube_credentials()
        assert result.status == cc.CredentialStatus.OK
        assert result.needs_human_action is False

    def test_probe_invalid_reports_expired_needs_action(self, tmp_path, monkeypatch):
        from flacfetch.api.services import credential_check as cc

        self._with_cookie_file(tmp_path, monkeypatch)
        with patch(
            "flacfetch.credential_keeper.probe.probe_cookies_sync",
            return_value=(ProbeOutcome.INVALID_COOKIES, "Sign in to confirm you're not a bot"),
        ):
            result = cc.check_youtube_credentials()
        assert result.status == cc.CredentialStatus.EXPIRED
        assert result.needs_human_action is True
        assert "credential-keeper" in result.fix_command

    def test_probe_rate_limited_reports_ok(self, tmp_path, monkeypatch):
        from flacfetch.api.services import credential_check as cc

        self._with_cookie_file(tmp_path, monkeypatch)
        with patch(
            "flacfetch.credential_keeper.probe.probe_cookies_sync",
            return_value=(ProbeOutcome.TRANSIENT, "HTTP Error 429: Too Many Requests"),
        ):
            result = cc.check_youtube_credentials()
        assert result.status == cc.CredentialStatus.OK
        assert result.needs_human_action is False

    def test_probe_transient_reports_error_without_action(self, tmp_path, monkeypatch):
        from flacfetch.api.services import credential_check as cc

        self._with_cookie_file(tmp_path, monkeypatch)
        with patch(
            "flacfetch.credential_keeper.probe.probe_cookies_sync",
            return_value=(ProbeOutcome.TRANSIENT, "Connection timed out"),
        ):
            result = cc.check_youtube_credentials()
        assert result.status == cc.CredentialStatus.ERROR
        assert result.needs_human_action is False

    def test_missing_cookie_file_reports_missing(self, tmp_path, monkeypatch):
        from flacfetch.api.services import credential_check as cc

        monkeypatch.setenv("YOUTUBE_COOKIES_FILE", str(tmp_path / "absent.txt"))
        result = cc.check_youtube_credentials()
        assert result.status == cc.CredentialStatus.MISSING


# =============================================================================
# _is_keeper_actively_managing per-service thresholds
# =============================================================================


class TestKeeperSuppressionThresholds:
    def _status_file(self, tmp_path, monkeypatch, service, hours_ago, extra=None):
        status = {
            service: {
                "last_refresh_status": "ok",
                "last_refresh": (
                    datetime.now(timezone.utc) - timedelta(hours=hours_ago)
                ).isoformat(),
                **(extra or {}),
            }
        }
        f = tmp_path / "keeper-status.json"
        f.write_text(json.dumps(status))
        monkeypatch.setenv("KEEPER_STATUS_FILE", str(f))

    def test_youtube_recent_refresh_suppresses(self, tmp_path, monkeypatch):
        from flacfetch.api.services.credential_check import _is_keeper_actively_managing

        self._status_file(tmp_path, monkeypatch, "youtube", hours_ago=1)
        assert _is_keeper_actively_managing("youtube") is True

    def test_youtube_stale_refresh_does_not_suppress(self, tmp_path, monkeypatch):
        """3h-old refresh must NOT suppress: the keeper probes every ~30 min,
        so dead cookies + a >2h-old refresh means self-heal is failing."""
        from flacfetch.api.services.credential_check import _is_keeper_actively_managing

        self._status_file(tmp_path, monkeypatch, "youtube", hours_ago=3)
        assert _is_keeper_actively_managing("youtube") is False

    def test_youtube_recent_probe_suppresses_despite_old_refresh(self, tmp_path, monkeypatch):
        """The keeper only refreshes every 8h but probes every 30 min — a
        recent successful probe must keep suppression active between refreshes."""
        from flacfetch.api.services.credential_check import _is_keeper_actively_managing

        self._status_file(
            tmp_path, monkeypatch, "youtube", hours_ago=5,
            extra={
                "last_probe_status": "ok",
                "last_probe": (
                    datetime.now(timezone.utc) - timedelta(minutes=20)
                ).isoformat(),
            },
        )
        assert _is_keeper_actively_managing("youtube") is True

    def test_youtube_stale_probe_and_refresh_do_not_suppress(self, tmp_path, monkeypatch):
        from flacfetch.api.services.credential_check import _is_keeper_actively_managing

        self._status_file(
            tmp_path, monkeypatch, "youtube", hours_ago=5,
            extra={
                "last_probe_status": "ok",
                "last_probe": (
                    datetime.now(timezone.utc) - timedelta(hours=2)
                ).isoformat(),
            },
        )
        assert _is_keeper_actively_managing("youtube") is False

    def test_youtube_failed_probe_does_not_suppress(self, tmp_path, monkeypatch):
        from flacfetch.api.services.credential_check import _is_keeper_actively_managing

        self._status_file(
            tmp_path, monkeypatch, "youtube", hours_ago=5,
            extra={
                "last_probe_status": "invalid_cookies",
                "last_probe": (
                    datetime.now(timezone.utc) - timedelta(minutes=10)
                ).isoformat(),
            },
        )
        assert _is_keeper_actively_managing("youtube") is False

    def test_spotify_keeps_24h_window(self, tmp_path, monkeypatch):
        from flacfetch.api.services.credential_check import _is_keeper_actively_managing

        self._status_file(tmp_path, monkeypatch, "spotify", hours_ago=12)
        assert _is_keeper_actively_managing("spotify") is True

    def test_no_status_file_does_not_suppress(self, tmp_path, monkeypatch):
        from flacfetch.api.services.credential_check import _is_keeper_actively_managing

        monkeypatch.setenv("KEEPER_STATUS_FILE", str(tmp_path / "absent.json"))
        assert _is_keeper_actively_managing("youtube") is False
