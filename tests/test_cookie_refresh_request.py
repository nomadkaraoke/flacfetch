"""Tests for the API -> keeper "cookies just got bot-blocked" signal."""
import os
import threading
import time

import pytest

from flacfetch.credential_keeper import refresh_request as rr


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setenv("KEEPER_STATUS_FILE", str(tmp_path / "keeper-status.json"))
    monkeypatch.setattr(rr, "_last_request_at", None)
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("old")
    return cookies, tmp_path / rr.REQUEST_FILENAME


def _bump(path):
    """Replace the cookie file the way the upload route does (mtime moves)."""
    path.write_text("new")
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))


def test_request_file_sits_next_to_status_file(paths):
    _, request_file = paths
    assert rr.request_refresh() is True
    assert request_file.exists()


def test_consume_returns_true_once(paths):
    rr.request_refresh()
    assert rr.consume_refresh_request() is True
    assert rr.consume_refresh_request() is False


def test_request_is_rate_limited_per_process(paths):
    _, request_file = paths
    assert rr.request_refresh() is True
    rr.consume_refresh_request()
    assert rr.request_refresh() is False
    assert not request_file.exists()


def test_unwritable_location_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("KEEPER_STATUS_FILE", str(tmp_path / "missing-dir" / "status.json"))
    monkeypatch.setattr(rr, "_last_request_at", None)
    assert rr.request_refresh() is False
    assert rr.consume_refresh_request() is False


def test_wait_returns_true_when_cookies_already_replaced(paths):
    cookies, request_file = paths
    baseline = rr.cookies_mtime(str(cookies))
    _bump(cookies)
    assert rr.wait_for_fresh_cookies(str(cookies), baseline, timeout=0.2) is True
    assert not request_file.exists()  # someone else already refreshed: no new request


def test_wait_returns_true_when_keeper_replaces_cookies(paths):
    cookies, request_file = paths
    baseline = rr.cookies_mtime(str(cookies))

    def keeper():
        while not request_file.exists():
            time.sleep(0.01)
        rr.consume_refresh_request()
        _bump(cookies)

    thread = threading.Thread(target=keeper)
    thread.start()
    try:
        assert rr.wait_for_fresh_cookies(str(cookies), baseline, timeout=5, poll=0.01) is True
    finally:
        thread.join()


def test_wait_times_out_when_keeper_never_answers(paths):
    cookies, _ = paths
    baseline = rr.cookies_mtime(str(cookies))
    assert rr.wait_for_fresh_cookies(str(cookies), baseline, timeout=0.05, poll=0.01) is False


def test_second_caller_shares_inflight_request_instead_of_waiting_again(paths):
    cookies, _ = paths
    baseline = rr.cookies_mtime(str(cookies))
    assert rr.wait_for_fresh_cookies(str(cookies), baseline, timeout=0.05, poll=0.01) is False
    # Within the cooldown, with the first caller's wait used up: no new request
    # and no second full wait.
    started = time.monotonic()
    assert rr.wait_for_fresh_cookies(str(cookies), baseline, timeout=30, poll=0.01) is False
    assert time.monotonic() - started < 1


def test_wait_gives_up_immediately_when_request_cannot_be_written(tmp_path, monkeypatch):
    monkeypatch.setenv("KEEPER_STATUS_FILE", str(tmp_path / "missing-dir" / "status.json"))
    monkeypatch.setattr(rr, "_last_request_at", None)
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("old")
    started = time.monotonic()
    assert rr.wait_for_fresh_cookies(str(cookies), rr.cookies_mtime(str(cookies)), timeout=30) is False
    assert time.monotonic() - started < 1


def test_no_cookie_file_configured_is_a_noop(paths):
    assert rr.wait_for_fresh_cookies(None, 0, timeout=30) is False
