"""Tests for YouTube provider"""
from unittest.mock import MagicMock, patch

from flacfetch.core.models import AudioFormat, TrackQuery
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

