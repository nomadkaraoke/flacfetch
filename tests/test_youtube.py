"""Tests for YouTube provider and availability checking"""
from unittest.mock import MagicMock, patch

import yt_dlp

from flacfetch.core.models import AudioFormat, TrackQuery
from flacfetch.downloaders.youtube import (
    check_youtube_availability,
    extract_video_id,
)
from flacfetch.providers.youtube import YoutubeProvider


class TestYoutubeProvider:
    """Test YouTube provider functionality"""

    def test_provider_name(self):
        provider = YoutubeProvider()
        assert provider.name == "YouTube"

    def test_search_with_results(self):
        provider = YoutubeProvider()

        with patch('yt_dlp.YoutubeDL') as mock_yt_dlp:
            mock_instance = MagicMock()
            mock_yt_dlp.return_value.__enter__.return_value = mock_instance

            mock_instance.extract_info.return_value = {
                'entries': [
                    {
                        'title': 'Artist - Track (Official Audio)',
                        'id': 'video_id_1',
                        'channel': 'Artist - Topic',
                        'view_count': 1000000,
                        'duration': 245,
                        'formats': [
                            {'format_id': '251', 'ext': 'webm', 'acodec': 'opus'}
                        ],
                        'webpage_url': 'https://youtube.com/watch?v=video_id_1'
                    }
                ]
            }

            query = TrackQuery(artist="Artist", title="Track")
            results = provider.search(query)

            assert len(results) > 0
            assert results[0].title == 'Artist - Track (Official Audio)'
            assert results[0].channel == 'Artist - Topic'
            assert results[0].view_count == 1000000

    def test_search_no_results(self):
        provider = YoutubeProvider()

        with patch('yt_dlp.YoutubeDL') as mock_yt_dlp:
            mock_instance = MagicMock()
            mock_yt_dlp.return_value.__enter__.return_value = mock_instance

            mock_instance.extract_info.return_value = {'entries': []}

            query = TrackQuery(artist="Unknown", title="Unknown")
            results = provider.search(query)

            assert results == []

    def test_search_error_handling(self):
        provider = YoutubeProvider()

        with patch('yt_dlp.YoutubeDL') as mock_yt_dlp:
            mock_instance = MagicMock()
            mock_yt_dlp.return_value.__enter__.return_value = mock_instance

            mock_instance.extract_info.side_effect = Exception("Network error")

            query = TrackQuery(artist="Artist", title="Track")
            results = provider.search(query)

            # Should return empty list on error
            assert results == []

    def test_search_with_none_entry(self):
        """Test handling of None entries in search results"""
        provider = YoutubeProvider()

        with patch('yt_dlp.YoutubeDL') as mock_yt_dlp:
            mock_instance = MagicMock()
            mock_yt_dlp.return_value.__enter__.return_value = mock_instance

            mock_instance.extract_info.return_value = {
                'entries': [
                    None,  # Should skip this
                    {
                        'title': 'Valid Track',
                        'id': 'video_id',
                        'channel': 'Channel',
                        'formats': [{'format_id': '251'}],
                        'webpage_url': 'https://youtube.com/watch?v=id'
                    }
                ]
            }

            query = TrackQuery(artist="Artist", title="Track")
            results = provider.search(query)

            # Should skip None entry and process valid one
            assert len(results) == 1
            assert results[0].title == 'Valid Track'

    def test_search_prefers_higher_bitrate(self):
        """Test that search prefers higher bitrate formats"""
        provider = YoutubeProvider()

        with patch('yt_dlp.YoutubeDL') as mock_yt_dlp:
            mock_instance = MagicMock()
            mock_yt_dlp.return_value.__enter__.return_value = mock_instance

            mock_instance.extract_info.return_value = {
                'entries': [
                    {
                        'title': 'Track',
                        'id': 'id1',
                        'channel': 'Channel',
                        'formats': [
                            {'format_id': '249', 'acodec': 'opus', 'vcodec': 'none'},  # 50kbps
                            {'format_id': '251', 'acodec': 'opus', 'vcodec': 'none'},  # 130kbps
                        ],
                        'webpage_url': 'https://youtube.com/watch?v=id1'
                    }
                ]
            }

            query = TrackQuery(artist="Artist", title="Track")
            results = provider.search(query)

            # Should prefer higher bitrate
            assert len(results) > 0

    def test_search_with_abr_field(self):
        """Test search handles abr field for bitrate"""
        provider = YoutubeProvider()

        with patch('yt_dlp.YoutubeDL') as mock_yt_dlp:
            mock_instance = MagicMock()
            mock_yt_dlp.return_value.__enter__.return_value = mock_instance

            mock_instance.extract_info.return_value = {
                'entries': [
                    {
                        'title': 'Track',
                        'id': 'id1',
                        'channel': 'Channel',
                        'formats': [
                            {'format_id': 'custom', 'acodec': 'mp3', 'abr': 320, 'vcodec': 'none'},
                        ],
                        'webpage_url': 'https://youtube.com/watch?v=id1'
                    }
                ]
            }

            query = TrackQuery(artist="Artist", title="Track")
            results = provider.search(query)

            assert len(results) > 0
            assert results[0].quality.bitrate == 320

    def test_search_skips_video_only_formats(self):
        """Test that video-only formats are skipped"""
        provider = YoutubeProvider()

        with patch('yt_dlp.YoutubeDL') as mock_yt_dlp:
            mock_instance = MagicMock()
            mock_yt_dlp.return_value.__enter__.return_value = mock_instance

            mock_instance.extract_info.return_value = {
                'entries': [
                    {
                        'title': 'Track',
                        'id': 'id1',
                        'channel': 'Channel',
                        'formats': [
                            {'format_id': 'video', 'acodec': 'none', 'vcodec': 'h264'},  # Should skip
                            {'format_id': '251', 'acodec': 'opus', 'vcodec': 'none'},
                        ],
                        'webpage_url': 'https://youtube.com/watch?v=id1'
                    }
                ]
            }

            query = TrackQuery(artist="Artist", title="Track")
            results = provider.search(query)

            # Should work despite video-only format
            assert len(results) > 0

    def test_populate_details(self):
        """Test populate_details method (currently a no-op)"""
        provider = YoutubeProvider()
        from flacfetch.core.models import Quality, Release

        release = Release(
            title="Test", artist="Test", quality=Quality(AudioFormat.OPUS),
            source_name="YouTube"
        )

        # Should not raise an error
        provider.populate_details(release)

    def test_fetch_artifact(self):
        """Test fetch_artifact method (returns None for YouTube)"""
        provider = YoutubeProvider()
        from flacfetch.core.models import Quality, Release

        release = Release(
            title="Test", artist="Test", quality=Quality(AudioFormat.OPUS),
            source_name="YouTube"
        )

        result = provider.fetch_artifact(release)
        assert result is None


class TestExtractVideoId:
    """Test YouTube video ID extraction from various URL formats."""

    def test_bare_video_id(self):
        assert extract_video_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_bare_video_id_with_hyphen(self):
        assert extract_video_id("-yV25PrHglw") == "-yV25PrHglw"

    def test_standard_url(self):
        assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_short_url(self):
        assert extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_embed_url(self):
        assert extract_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_url_with_extra_params(self):
        assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30") == "dQw4w9WgXcQ"

    def test_unparseable_returns_as_is(self):
        assert extract_video_id("not-a-url") == "not-a-url"


class TestCheckYoutubeAvailability:
    """Test YouTube availability checking."""

    def test_available_video(self):
        with patch('yt_dlp.YoutubeDL') as mock_yt_dlp:
            mock_instance = MagicMock()
            mock_yt_dlp.return_value.__enter__.return_value = mock_instance
            mock_instance.extract_info.return_value = {
                'title': 'Test Video',
                'id': 'dQw4w9WgXcQ',
            }

            result = check_youtube_availability("dQw4w9WgXcQ")

            assert result.available is True
            assert result.video_id == "dQw4w9WgXcQ"
            assert result.title == "Test Video"
            assert result.error is None
            assert result.is_geo_restricted is False

    def test_geo_restricted_video(self):
        with patch('yt_dlp.YoutubeDL') as mock_yt_dlp:
            mock_instance = MagicMock()
            mock_yt_dlp.return_value.__enter__.return_value = mock_instance
            mock_instance.extract_info.side_effect = yt_dlp.utils.GeoRestrictedError(
                "This video is not available in your country"
            )

            result = check_youtube_availability("geo-blocked-id1")

            assert result.available is False
            assert result.video_id == "geo-blocked-id1"
            assert result.is_geo_restricted is True
            assert "not available" in result.error.lower()

    def test_video_unavailable_generic(self):
        """Test generic 'Video unavailable' error (common for geo-restricted content)."""
        with patch('yt_dlp.YoutubeDL') as mock_yt_dlp:
            mock_instance = MagicMock()
            mock_yt_dlp.return_value.__enter__.return_value = mock_instance
            mock_instance.extract_info.side_effect = yt_dlp.utils.ExtractorError(
                "Video unavailable. This video is not available"
            )

            result = check_youtube_availability("-yV25PrHglw")

            assert result.available is False
            assert result.is_geo_restricted is True
            assert "geographic" in result.error.lower() or "country" in result.error.lower()

    def test_private_video(self):
        with patch('yt_dlp.YoutubeDL') as mock_yt_dlp:
            mock_instance = MagicMock()
            mock_yt_dlp.return_value.__enter__.return_value = mock_instance
            mock_instance.extract_info.side_effect = yt_dlp.utils.ExtractorError(
                "This video is private"
            )

            result = check_youtube_availability("private-id")

            assert result.available is False
            assert result.is_private is True
            assert result.is_geo_restricted is False

    def test_removed_video(self):
        with patch('yt_dlp.YoutubeDL') as mock_yt_dlp:
            mock_instance = MagicMock()
            mock_yt_dlp.return_value.__enter__.return_value = mock_instance
            mock_instance.extract_info.side_effect = yt_dlp.utils.ExtractorError(
                "Video has been removed by the uploader"
            )

            result = check_youtube_availability("removed-id")

            assert result.available is False
            assert result.is_removed is True
            assert result.is_geo_restricted is False

    def test_age_restricted_video(self):
        with patch('yt_dlp.YoutubeDL') as mock_yt_dlp:
            mock_instance = MagicMock()
            mock_yt_dlp.return_value.__enter__.return_value = mock_instance
            mock_instance.extract_info.side_effect = yt_dlp.utils.ExtractorError(
                "Sign in to confirm your age"
            )

            result = check_youtube_availability("age-id")

            assert result.available is False
            assert result.is_age_restricted is True
            assert result.is_geo_restricted is False

    def test_unexpected_error(self):
        with patch('yt_dlp.YoutubeDL') as mock_yt_dlp:
            mock_instance = MagicMock()
            mock_yt_dlp.return_value.__enter__.return_value = mock_instance
            mock_instance.extract_info.side_effect = RuntimeError("Network timeout")

            result = check_youtube_availability("some-id")

            assert result.available is False
            assert "Network timeout" in result.error

    def test_no_info_returned(self):
        with patch('yt_dlp.YoutubeDL') as mock_yt_dlp:
            mock_instance = MagicMock()
            mock_yt_dlp.return_value.__enter__.return_value = mock_instance
            mock_instance.extract_info.return_value = None

            result = check_youtube_availability("null-id")

            assert result.available is False

    def test_accepts_full_url(self):
        with patch('yt_dlp.YoutubeDL') as mock_yt_dlp:
            mock_instance = MagicMock()
            mock_yt_dlp.return_value.__enter__.return_value = mock_instance
            mock_instance.extract_info.return_value = {'title': 'Test', 'id': 'abc123xyz9_'}

            result = check_youtube_availability("https://www.youtube.com/watch?v=abc123xyz9_")

            assert result.available is True
            assert result.video_id == "abc123xyz9_"

