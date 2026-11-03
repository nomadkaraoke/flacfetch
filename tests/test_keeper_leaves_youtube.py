"""The keeper must leave youtube.com after exporting cookies."""
from unittest.mock import AsyncMock, MagicMock, patch


class TestRefreshLeavesYoutubeTab:
    """Since 2026-08-25, exports whose tab left youtube.com within 15 min passed
    the next probe 96 of 97 times; exports that stayed on it passed 30 of 1,700."""

    def _page(self, *, signed_out=False, goto_error=None):
        page = MagicMock()
        page.goto = AsyncMock(side_effect=goto_error)
        page.wait_for_timeout = AsyncMock()
        page.query_selector = AsyncMock(return_value=object() if signed_out else None)
        return page

    def _visited(self, page):
        return [call.args[0] for call in page.goto.call_args_list]

    async def _refresh(self, page, **patches):
        from flacfetch.credential_keeper import youtube

        with patch.object(youtube, "extract_netscape_cookies", AsyncMock(return_value="c")), \
             patch.object(youtube, "upload_cookies", AsyncMock(return_value=patches.get("uploaded", True))):
            return await youtube.refresh_youtube_cookies(page, MagicMock())

    async def test_parks_after_successful_export(self):
        page = self._page()
        assert await self._refresh(page) is True
        assert self._visited(page) == ["https://www.youtube.com", "about:blank"]

    async def test_parks_after_failed_upload(self):
        page = self._page()
        assert await self._refresh(page, uploaded=False) is False
        assert self._visited(page)[-1] == "about:blank"

    async def test_parks_when_signed_out(self):
        page = self._page(signed_out=True)
        assert await self._refresh(page) is False
        assert self._visited(page)[-1] == "about:blank"

    async def test_park_failure_does_not_fail_the_refresh(self):
        page = self._page(goto_error=[None, RuntimeError("page crashed")])
        assert await self._refresh(page) is True
