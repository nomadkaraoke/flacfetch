"""Tests for GazelleProvider base class - Sphinx query sanitization and shared functionality."""

from unittest.mock import MagicMock

import pytest

from flacfetch.core.models import AudioFormat, MediaSource
from flacfetch.providers.gazelle import SPHINX_SPECIAL_CHARS, GazelleProvider


class ConcreteGazelleProvider(GazelleProvider):
    """Concrete implementation of GazelleProvider for testing."""

    @property
    def name(self):
        return "TEST"

    def search(self, query):
        return []


class TestSanitizeFilelistQuery:
    """Test _sanitize_filelist_query with comprehensive inputs."""

    @pytest.fixture
    def provider(self):
        """Create a concrete GazelleProvider for testing."""
        return ConcreteGazelleProvider(
            api_key="test",
            base_url="https://test.example",
            cache_subdir="test"
        )

    # =========================================================================
    # Core operator sanitization - THE BUG FIX
    # =========================================================================

    @pytest.mark.parametrize("input_query,expected", [
        # Question mark - THE BUG THAT TRIGGERED THIS FIX
        ("Is It Any Wonder?", "Is It Any Wonder"),
        ("What?!", "What"),
        ("Who? What? Where?", "Who What Where"),

        # Wildcards
        ("Track* Remix", "Track Remix"),
        ("*NSYNC", "NSYNC"),

        # Boolean operators
        ("Track - Artist", "Track Artist"),
        ("Artist & Artist", "Artist Artist"),
        ("Love|Hate", "Love Hate"),

        # Field and proximity operators
        ("@field search", "field search"),
        ("word~10", "word 10"),

        # Modifiers
        ("boost^2", "boost 2"),
        ("end$", "end"),
        ("exact=match", "exact match"),

        # Order operators
        ("strict<order", "strict order"),
        ("strict>order", "strict order"),

        # Quotes and escapes
        ('"Exact Phrase"', "Exact Phrase"),
        ("path\\escape", "path escape"),
        ("I'm With You", "I m With You"),
        ("Don't Stop", "Don t Stop"),
        ("Rock 'n' Roll", "Rock n Roll"),
    ])
    def test_operator_sanitization(self, provider, input_query, expected):
        """Test that all Sphinx operators are properly sanitized."""
        result = provider._sanitize_filelist_query(input_query)
        assert result == expected

    # =========================================================================
    # Syntax characters (existing behavior)
    # =========================================================================

    @pytest.mark.parametrize("input_query,expected", [
        ("Flight 717: Going To Denmark", "Flight 717 Going To Denmark"),
        ("Track (feat. Artist)", "Track feat Artist"),
        ("Song [Remix]", "Song Remix"),
        ("Part 1/3", "Part 1 3"),
        ("No! Way!", "No Way"),
        ("Item 1, 2, 3", "Item 1 2 3"),
        ("End. Start", "End Start"),
        ("Part A; Part B", "Part A Part B"),
        ("Track::(Remix)", "Track Remix"),
    ])
    def test_syntax_sanitization(self, provider, input_query, expected):
        """Test that syntax characters are properly sanitized."""
        result = provider._sanitize_filelist_query(input_query)
        assert result == expected

    # =========================================================================
    # International character support
    # =========================================================================

    @pytest.mark.parametrize("input_query", [
        "Björk",  # Icelandic/Swedish
        "日本語タイトル",  # Japanese
        "한국어",  # Korean
        "中文歌曲",  # Chinese
        "Привет",  # Russian (Cyrillic)
        "Café",  # French (accented)
        "Ñoño",  # Spanish
        "Müller",  # German (umlaut)
        "العربية",  # Arabic
        "עברית",  # Hebrew
        "Ελληνικά",  # Greek
        "ไทย",  # Thai
    ])
    def test_international_characters_preserved(self, provider, input_query):
        """International characters should pass through unchanged."""
        result = provider._sanitize_filelist_query(input_query)
        assert result == input_query

    # =========================================================================
    # Edge cases
    # =========================================================================

    @pytest.mark.parametrize("input_query,expected", [
        ("", ""),
        ("   ", ""),
        ("Normal Title", "Normal Title"),
        ("Multiple   Spaces", "Multiple Spaces"),
        ("  Leading", "Leading"),
        ("Trailing  ", "Trailing"),
    ])
    def test_edge_cases(self, provider, input_query, expected):
        """Test edge cases and whitespace handling."""
        result = provider._sanitize_filelist_query(input_query)
        assert result == expected

    # =========================================================================
    # Regression tests
    # =========================================================================

    def test_keane_is_it_any_wonder(self, provider):
        """Regression test: Job 9c1c3ea5 failed because '?' wasn't sanitized.

        The song 'Is It Any Wonder?' by Keane only returned YouTube results
        because the question mark broke the Sphinx tracker searches.
        """
        result = provider._sanitize_filelist_query("Is It Any Wonder?")
        assert result == "Is It Any Wonder"
        assert "?" not in result

    def test_all_special_chars_covered(self, provider):
        """Verify all characters in SPHINX_SPECIAL_CHARS are actually sanitized."""
        for char in SPHINX_SPECIAL_CHARS:
            test_input = f"before{char}after"
            result = provider._sanitize_filelist_query(test_input)
            assert char not in result, f"Character '{char}' was not sanitized"

    def test_special_chars_constant_complete(self):
        """Verify SPHINX_SPECIAL_CHARS contains all expected characters."""
        # These are all the characters that Gazelle's sph_escape_string() escapes
        expected_chars = set(r'()|\\-@~&<>!"/*$^=?:[],.;\'')
        assert SPHINX_SPECIAL_CHARS == expected_chars

    # =========================================================================
    # Complex real-world examples
    # =========================================================================

    @pytest.mark.parametrize("input_query,expected", [
        # Real song titles that could cause issues
        ("What Is Love?", "What Is Love"),
        ("R U Still Down? (Remember Me)", "R U Still Down Remember Me"),
        ("Who's That Girl?", "Who s That Girl"),
        ("Ain't No Sunshine", "Ain t No Sunshine"),
        ("AC/DC", "AC DC"),
        ("Guns N' Roses", "Guns N Roses"),
        ("P!nk", "P nk"),
        ("Ke$ha", "Ke ha"),
        ("will.i" + ".am", "will i am"),
        ("20/20", "20 20"),
        ("24:7", "24 7"),
        ("Mr. Brightside", "Mr Brightside"),
        ("...Baby One More Time", "Baby One More Time"),
        ("(I Can't Get No) Satisfaction", "I Can t Get No Satisfaction"),
    ])
    def test_real_world_titles(self, provider, input_query, expected):
        """Test sanitization with real song/artist names that contain special chars."""
        result = provider._sanitize_filelist_query(input_query)
        assert result == expected


class TestSphinxSpecialCharsConstant:
    """Tests for the SPHINX_SPECIAL_CHARS constant."""

    def test_is_frozenset(self):
        """SPHINX_SPECIAL_CHARS should be a frozenset for performance."""
        assert isinstance(SPHINX_SPECIAL_CHARS, frozenset)

    def test_contains_question_mark(self):
        """The question mark MUST be included (root cause of the bug)."""
        assert "?" in SPHINX_SPECIAL_CHARS

    def test_contains_all_sphinx_operators(self):
        """All Sphinx extended query operators should be included."""
        operators = ['(', ')', '|', '-', '@', '~', '&', '<', '>', '!', '"', '/', '*', '$', '^', '\\', '=', '?']
        for op in operators:
            assert op in SPHINX_SPECIAL_CHARS, f"Operator '{op}' missing from SPHINX_SPECIAL_CHARS"

    def test_contains_separators(self):
        """Separator characters should be included."""
        separators = [':', '[', ']', ',', '.', ';', "'"]
        for sep in separators:
            assert sep in SPHINX_SPECIAL_CHARS, f"Separator '{sep}' missing from SPHINX_SPECIAL_CHARS"


class TestGazelleProviderInit:
    """Tests for GazelleProvider initialization."""

    def test_init_with_valid_base_url(self):
        """Provider initializes correctly with valid base_url."""
        provider = ConcreteGazelleProvider(
            api_key="test_key",
            base_url="https://example.com/api",
            cache_subdir="test"
        )
        assert provider.api_key == "test_key"
        assert provider.base_url == "https://example.com/api"
        assert provider.search_limit == 10
        assert provider.early_termination is True

    def test_init_strips_trailing_slash(self):
        """Provider strips trailing slash from base_url."""
        provider = ConcreteGazelleProvider(
            api_key="test",
            base_url="https://example.com/",
            cache_subdir="test"
        )
        assert provider.base_url == "https://example.com"

    def test_init_raises_on_empty_base_url(self):
        """Provider raises ValueError when base_url is empty."""
        with pytest.raises(ValueError, match="base_url is required"):
            ConcreteGazelleProvider(api_key="test", base_url="", cache_subdir="test")

    def test_init_raises_on_none_base_url(self):
        """Provider raises error when base_url is None."""
        with pytest.raises((ValueError, TypeError)):
            ConcreteGazelleProvider(api_key="test", base_url=None, cache_subdir="test")


class TestFindBestTargetFile:
    """Tests for _find_best_target_file method."""

    @pytest.fixture
    def provider(self):
        """Create a concrete GazelleProvider for testing."""
        return ConcreteGazelleProvider(
            api_key="test",
            base_url="https://test.example",
            cache_subdir="test"
        )

    def test_empty_file_list(self, provider):
        """Returns None for empty file list."""
        result = provider._find_best_target_file("", "Some Track")
        assert result == (None, None, 0.0)

    def test_finds_matching_flac(self, provider):
        """Finds FLAC file matching track title."""
        file_list = "01 - Fear Not.flac{{{30000000}}}|||02 - Other Song.flac{{{25000000}}}"
        fname, size, score = provider._find_best_target_file(file_list, "Fear Not")
        assert fname == "01 - Fear Not.flac"
        assert size == 30000000
        assert score > 0.6

    def test_finds_matching_mp3(self, provider):
        """Finds MP3 file matching track title."""
        file_list = "01 - My Song.mp3{{{5000000}}}"
        fname, size, score = provider._find_best_target_file(file_list, "My Song")
        assert fname == "01 - My Song.mp3"
        assert size == 5000000
        assert score > 0.6

    def test_finds_matching_wav(self, provider):
        """Finds WAV file matching track title."""
        file_list = "01 - My Song.wav{{{50000000}}}"
        fname, size, score = provider._find_best_target_file(file_list, "My Song")
        assert fname == "01 - My Song.wav"
        assert size == 50000000

    def test_finds_matching_m4a(self, provider):
        """Finds M4A file matching track title."""
        file_list = "01 - My Song.m4a{{{8000000}}}"
        fname, size, score = provider._find_best_target_file(file_list, "My Song")
        assert fname == "01 - My Song.m4a"

    def test_decodes_html_entities(self, provider):
        """Decodes HTML entities in filenames."""
        file_list = "01 - Rock &amp; Roll.flac{{{30000000}}}"
        fname, size, score = provider._find_best_target_file(file_list, "Rock & Roll")
        assert fname == "01 - Rock & Roll.flac"
        assert "&amp;" not in fname

    def test_no_match_returns_none(self, provider):
        """Returns None when no file matches well enough."""
        file_list = "01 - Completely Different Song.flac{{{30000000}}}"
        result = provider._find_best_target_file(file_list, "Fear Not")
        assert result == (None, None, 0.0)

    def test_ignores_non_audio_files(self, provider):
        """Ignores non-audio files like images and logs."""
        file_list = "cover.jpg{{{500000}}}|||folder.log{{{1000}}}|||01 - Track.flac{{{30000000}}}"
        fname, size, score = provider._find_best_target_file(file_list, "Track")
        assert fname == "01 - Track.flac"

    def test_handles_file_without_size(self, provider):
        """Handles file entry without size braces."""
        file_list = "01 - My Song.flac"
        fname, size, score = provider._find_best_target_file(file_list, "My Song")
        assert fname == "01 - My Song.flac"
        assert size == 0

    def test_handles_malformed_size(self, provider):
        """Handles malformed size in file entry."""
        file_list = "01 - My Song.flac{{{notanumber}}}"
        fname, size, score = provider._find_best_target_file(file_list, "My Song")
        assert fname == "01 - My Song.flac"
        assert size == 0

    def test_selects_best_match_from_multiple(self, provider):
        """Selects the best matching file from multiple candidates."""
        file_list = "01 - Intro.flac{{{1000}}}|||02 - Fear Not.flac{{{2000}}}|||03 - Fear Not (Remix).flac{{{3000}}}"
        fname, size, score = provider._find_best_target_file(file_list, "Fear Not")
        # Should match "02 - Fear Not.flac" over the remix
        assert fname == "02 - Fear Not.flac"
        assert size == 2000


class TestParseQuality:
    """Tests for _parse_quality method."""

    @pytest.fixture
    def provider(self):
        """Create a concrete GazelleProvider for testing."""
        return ConcreteGazelleProvider(
            api_key="test",
            base_url="https://test.example",
            cache_subdir="test"
        )

    # Format parsing
    def test_parse_flac_format(self, provider):
        """Parses FLAC format correctly."""
        quality = provider._parse_quality({"format": "FLAC", "encoding": "Lossless", "media": "CD"})
        assert quality.format == AudioFormat.FLAC

    def test_parse_mp3_format(self, provider):
        """Parses MP3 format correctly."""
        quality = provider._parse_quality({"format": "MP3", "encoding": "320", "media": "WEB"})
        assert quality.format == AudioFormat.MP3

    def test_parse_aac_format(self, provider):
        """Parses AAC format correctly."""
        quality = provider._parse_quality({"format": "AAC", "encoding": "256", "media": "WEB"})
        assert quality.format == AudioFormat.AAC

    def test_parse_wav_format(self, provider):
        """Parses WAV format correctly."""
        quality = provider._parse_quality({"format": "WAV", "encoding": "Lossless", "media": "CD"})
        assert quality.format == AudioFormat.WAV

    def test_parse_unknown_format(self, provider):
        """Unknown format maps to OTHER."""
        quality = provider._parse_quality({"format": "OGG", "encoding": "", "media": ""})
        assert quality.format == AudioFormat.OTHER

    def test_format_case_insensitive(self, provider):
        """Format parsing is case-insensitive."""
        quality = provider._parse_quality({"format": "flac", "encoding": "Lossless", "media": "CD"})
        assert quality.format == AudioFormat.FLAC

    # Media source parsing
    def test_parse_web_media(self, provider):
        """Parses WEB media source."""
        quality = provider._parse_quality({"format": "FLAC", "encoding": "Lossless", "media": "WEB"})
        assert quality.media == MediaSource.WEB

    def test_parse_cd_media(self, provider):
        """Parses CD media source."""
        quality = provider._parse_quality({"format": "FLAC", "encoding": "Lossless", "media": "CD"})
        assert quality.media == MediaSource.CD

    def test_parse_vinyl_media(self, provider):
        """Parses VINYL media source."""
        quality = provider._parse_quality({"format": "FLAC", "encoding": "24bit Lossless", "media": "VINYL"})
        assert quality.media == MediaSource.VINYL

    def test_parse_dvd_media(self, provider):
        """Parses DVD media source."""
        quality = provider._parse_quality({"format": "FLAC", "encoding": "24bit Lossless", "media": "DVD"})
        assert quality.media == MediaSource.DVD

    def test_parse_cassette_media(self, provider):
        """Parses CASSETTE media source."""
        quality = provider._parse_quality({"format": "FLAC", "encoding": "Lossless", "media": "CASSETTE"})
        assert quality.media == MediaSource.CASSETTE

    def test_parse_unknown_media(self, provider):
        """Unknown media maps to OTHER."""
        quality = provider._parse_quality({"format": "FLAC", "encoding": "Lossless", "media": "SACD"})
        assert quality.media == MediaSource.OTHER

    # Bit depth parsing
    def test_parse_16bit_flac(self, provider):
        """FLAC without 24bit marker defaults to 16-bit."""
        quality = provider._parse_quality({"format": "FLAC", "encoding": "Lossless", "media": "CD"})
        assert quality.bit_depth == 16

    def test_parse_24bit_flac(self, provider):
        """FLAC with 24bit marker is 24-bit."""
        quality = provider._parse_quality({"format": "FLAC", "encoding": "24bit Lossless", "media": "WEB"})
        assert quality.bit_depth == 24

    def test_parse_16bit_wav(self, provider):
        """WAV without 24bit marker defaults to 16-bit."""
        quality = provider._parse_quality({"format": "WAV", "encoding": "Lossless", "media": "CD"})
        assert quality.bit_depth == 16

    def test_parse_24bit_wav(self, provider):
        """WAV with 24bit marker is 24-bit."""
        quality = provider._parse_quality({"format": "WAV", "encoding": "24bit Lossless", "media": "CD"})
        assert quality.bit_depth == 24

    # Bitrate parsing
    def test_parse_320_bitrate(self, provider):
        """MP3 320 kbps encoding."""
        quality = provider._parse_quality({"format": "MP3", "encoding": "320", "media": "WEB"})
        assert quality.bitrate == 320

    def test_parse_v0_bitrate(self, provider):
        """MP3 V0 (VBR ~245 kbps)."""
        quality = provider._parse_quality({"format": "MP3", "encoding": "V0 (VBR)", "media": "WEB"})
        assert quality.bitrate == 245

    def test_parse_v2_bitrate(self, provider):
        """MP3 V2 (VBR ~190 kbps)."""
        quality = provider._parse_quality({"format": "MP3", "encoding": "V2 (VBR)", "media": "WEB"})
        assert quality.bitrate == 190

    def test_parse_192_bitrate(self, provider):
        """MP3 192 kbps encoding."""
        quality = provider._parse_quality({"format": "MP3", "encoding": "192", "media": "WEB"})
        assert quality.bitrate == 192

    def test_parse_256_bitrate(self, provider):
        """AAC 256 kbps encoding."""
        quality = provider._parse_quality({"format": "AAC", "encoding": "256", "media": "WEB"})
        assert quality.bitrate == 256

    def test_parse_aps_bitrate(self, provider):
        """MP3 APS (~215 kbps)."""
        quality = provider._parse_quality({"format": "MP3", "encoding": "APS (VBR)", "media": "WEB"})
        assert quality.bitrate == 215

    def test_parse_apx_bitrate(self, provider):
        """MP3 APX (~245 kbps)."""
        quality = provider._parse_quality({"format": "MP3", "encoding": "APX (VBR)", "media": "WEB"})
        assert quality.bitrate == 245

    def test_empty_torrent_data(self, provider):
        """Handles empty/missing fields gracefully."""
        quality = provider._parse_quality({})
        assert quality.format == AudioFormat.OTHER
        assert quality.media == MediaSource.OTHER
        assert quality.bit_depth is None
        assert quality.bitrate is None


class TestFreeleechTokens:
    """Test Freeleech (FL) token spending behavior."""

    @pytest.fixture
    def provider(self, tmp_path):
        """Token-enabled concrete GazelleProvider with an isolated cache dir."""
        p = ConcreteGazelleProvider(
            api_key="test",
            base_url="https://test.example",
            cache_subdir="test",
            use_fl_token=True,
        )
        # Isolate the token ledger / .torrent cache per test.
        p.cache_dir = tmp_path
        return p

    @pytest.fixture
    def no_token_provider(self, tmp_path):
        """Default provider (token spending off) with an isolated cache dir."""
        p = ConcreteGazelleProvider(
            api_key="test",
            base_url="https://test.example",
            cache_subdir="test",
        )
        p.cache_dir = tmp_path
        return p

    def _release(self, **kwargs):
        from flacfetch.core.models import AudioFormat, Quality, Release
        defaults = dict(
            title="Album",
            artist="Artist",
            quality=Quality(format=AudioFormat.FLAC),
            source_name="TEST",
            download_url="https://test.example/ajax.php?action=download&id=42",
            source_id="42",
        )
        defaults.update(kwargs)
        return Release(**defaults)

    # ---- default off -------------------------------------------------------

    def test_use_fl_token_defaults_off(self, no_token_provider):
        assert no_token_provider.use_fl_token is False

    def test_use_fl_token_flag_on(self, provider):
        assert provider.use_fl_token is True

    # ---- eligibility -------------------------------------------------------

    def test_eligible_when_can_use_token_true(self, provider):
        assert provider._is_token_eligible(self._release(can_use_token=True)) is True

    def test_ineligible_when_can_use_token_false(self, provider):
        # Server authoritatively says no token can be spent (e.g. none left).
        assert provider._is_token_eligible(self._release(can_use_token=False)) is False

    def test_ineligible_when_already_freeleech(self, provider):
        # canUseToken unknown -> fall back to heuristic.
        assert provider._is_token_eligible(self._release(is_freeleech=True)) is False

    def test_ineligible_when_over_5gb(self, provider):
        big = provider.FL_TOKEN_MAX_BYTES + 1
        assert provider._is_token_eligible(self._release(size_bytes=big)) is False

    def test_eligible_when_small_and_not_free(self, provider):
        small = 300 * 1024 * 1024
        assert provider._is_token_eligible(self._release(size_bytes=small)) is True

    def test_can_use_token_overrides_size_heuristic(self, provider):
        # Explicit server signal wins over the local size guess.
        r = self._release(can_use_token=True, size_bytes=provider.FL_TOKEN_MAX_BYTES + 1)
        assert provider._is_token_eligible(r) is True

    # ---- token URL + fallback ---------------------------------------------

    def test_fetch_with_token_appends_usetoken(self, provider):
        provider._fetch_torrent_from_url = MagicMock(return_value=b"d...torrent")
        result = provider._fetch_with_optional_token(
            "https://test.example/ajax.php?action=download&id=42", "42", use_token=True
        )
        assert result == b"d...torrent"
        called_url = provider._fetch_torrent_from_url.call_args[0][0]
        assert "usetoken=1" in called_url

    def test_fetch_without_token_has_no_usetoken(self, provider):
        provider._fetch_torrent_from_url = MagicMock(return_value=b"d...torrent")
        provider._fetch_with_optional_token(
            "https://test.example/ajax.php?action=download&id=42", "42", use_token=False
        )
        called_url = provider._fetch_torrent_from_url.call_args[0][0]
        assert "usetoken" not in called_url

    def test_token_failure_falls_back_to_normal_download(self, provider):
        # First call (with token) fails, second (without) succeeds.
        provider._fetch_torrent_from_url = MagicMock(side_effect=[None, b"d...torrent"])
        result = provider._fetch_with_optional_token(
            "https://test.example/ajax.php?action=download&id=42", "42", use_token=True
        )
        assert result == b"d...torrent"
        assert provider._fetch_torrent_from_url.call_count == 2
        first_url = provider._fetch_torrent_from_url.call_args_list[0][0][0]
        second_url = provider._fetch_torrent_from_url.call_args_list[1][0][0]
        assert "usetoken=1" in first_url
        assert "usetoken" not in second_url

    # ---- fetch_artifact drives eligibility --------------------------------

    def test_fetch_artifact_spends_token_when_eligible(self, provider):
        provider.cache_dir = None  # skip cache path
        provider._fetch_torrent_from_url = MagicMock(return_value=b"d...torrent")
        provider.fetch_artifact(self._release(can_use_token=True))
        called_url = provider._fetch_torrent_from_url.call_args[0][0]
        assert "usetoken=1" in called_url

    def test_fetch_artifact_no_token_when_flag_off(self, no_token_provider):
        no_token_provider.cache_dir = None
        no_token_provider._fetch_torrent_from_url = MagicMock(return_value=b"d...torrent")
        no_token_provider.fetch_artifact(self._release(can_use_token=True))
        called_url = no_token_provider._fetch_torrent_from_url.call_args[0][0]
        assert "usetoken" not in called_url

    def test_fetch_artifact_no_token_when_ineligible(self, provider):
        provider.cache_dir = None
        provider._fetch_torrent_from_url = MagicMock(return_value=b"d...torrent")
        provider.fetch_artifact(self._release(can_use_token=False))
        called_url = provider._fetch_torrent_from_url.call_args[0][0]
        assert "usetoken" not in called_url

    # ---- content validation guards the cache ------------------------------

    def test_json_error_body_not_treated_as_torrent(self, provider):
        provider.cache_dir = None
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"Content-Type": "application/json"}
        resp.content = b'{"status":"failure","error":"You do not have any freeleech tokens left."}'
        resp.json.return_value = {"status": "failure", "error": "You do not have any freeleech tokens left."}
        provider.session.get = MagicMock(return_value=resp)
        assert provider._fetch_torrent_from_url("https://test.example/ajax.php?action=download&id=42&usetoken=1", "42") is None

    def test_valid_bencoded_torrent_accepted(self, provider):
        provider.cache_dir = None
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"Content-Type": "application/x-bittorrent"}
        resp.content = b"d8:announce" + b"x" * 2000  # bencoded dict, > 1000 bytes
        provider.session.get = MagicMock(return_value=resp)
        result = provider._fetch_torrent_from_url("https://test.example/ajax.php?action=download&id=42", "42")
        assert result == resp.content

    # ---- token spend must NOT be skipped by a cached .torrent -------------
    # (regression: a cached .torrent file does not make a download freeleech)

    def test_token_spent_even_when_torrent_cached(self, provider):
        # Pre-seed a cached .torrent for id 42.
        (provider.cache_dir / "42.torrent").write_bytes(b"d8:announce...cached")
        provider._fetch_torrent_from_url = MagicMock(return_value=b"d8:announce...fresh")

        result = provider.fetch_artifact_by_id("42")

        # Must have hit the download endpoint with usetoken=1 rather than
        # short-circuiting on the cache.
        assert provider._fetch_torrent_from_url.called
        called_url = provider._fetch_torrent_from_url.call_args[0][0]
        assert "usetoken=1" in called_url
        assert result == b"d8:announce...fresh"
        # A ledger marker should now exist so we don't re-spend.
        assert provider._token_recently_spent("42")

    def test_no_respend_within_freeleech_window(self, provider):
        # A recent token marker means the torrent is already personal-freeleech.
        (provider.cache_dir / "77.tokened").touch()
        (provider.cache_dir / "77.torrent").write_bytes(b"d8:announce...cached")
        provider._fetch_torrent_from_url = MagicMock(return_value=b"should-not-be-called")

        result = provider.fetch_artifact_by_id("77")

        # No usetoken call; served from cache instead.
        assert not provider._fetch_torrent_from_url.called
        assert result == b"d8:announce...cached"

    def test_token_marker_respects_ttl(self, provider):
        import os, time as _t
        marker = provider.cache_dir / "88.tokened"
        marker.touch()
        # Backdate the marker beyond the 7-day window.
        old = _t.time() - provider.FL_TOKEN_FREELEECH_WINDOW_SECONDS - 3600
        os.utime(marker, (old, old))
        assert provider._token_recently_spent("88") is False

    def test_failed_token_falls_back_to_cache(self, provider):
        (provider.cache_dir / "99.torrent").write_bytes(b"d8:announce...cached")
        # Token attempt fails (e.g. >5GB / none left) -> None.
        provider._fetch_torrent_from_url = MagicMock(return_value=None)

        result = provider.fetch_artifact_by_id("99")

        # Attempted the token (usetoken=1) once, then fell back to cache.
        assert provider._fetch_torrent_from_url.call_count == 1
        assert "usetoken=1" in provider._fetch_torrent_from_url.call_args[0][0]
        assert result == b"d8:announce...cached"
        # No marker recorded on failure -> a later call may retry.
        assert not provider._token_recently_spent("99")

    def test_in_memory_ledger_prevents_respend_without_cache_dir(self, provider):
        # No cache dir -> on-disk marker impossible; in-memory ledger must still
        # prevent a second token spend on the same torrent.
        provider.cache_dir = None
        provider._fetch_torrent_from_url = MagicMock(return_value=b"d8:announce...fresh")

        first = provider.fetch_artifact_by_id("55")
        assert first == b"d8:announce...fresh"
        assert provider._fetch_torrent_from_url.call_count == 1
        assert provider._token_recently_spent("55") is True

        # Second call: no marker on disk, but in-memory ledger blocks re-spend.
        # With no cache and token skipped, it does a single normal (no-token) fetch.
        provider._fetch_torrent_from_url.reset_mock()
        second = provider.fetch_artifact_by_id("55")
        assert second == b"d8:announce...fresh"
        assert provider._fetch_torrent_from_url.call_count == 1
        assert "usetoken=1" not in provider._fetch_torrent_from_url.call_args[0][0]

    def test_no_token_serves_cache_without_endpoint_call(self, no_token_provider):
        (no_token_provider.cache_dir / "42.torrent").write_bytes(b"d8:announce...cached")
        no_token_provider._fetch_torrent_from_url = MagicMock(return_value=b"fresh")
        result = no_token_provider.fetch_artifact_by_id("42")
        assert not no_token_provider._fetch_torrent_from_url.called
        assert result == b"d8:announce...cached"
