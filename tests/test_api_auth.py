"""
Tests for flacfetch API authentication module.
"""
import os
from unittest.mock import patch

import pytest


class TestGetAPIKey:
    """Tests for the get_api_key function."""

    def test_get_api_key_from_env(self):
        """Test that get_api_key returns env var value."""
        with patch.dict(os.environ, {'FLACFETCH_API_KEY': 'test-secret'}):
            from flacfetch.api.auth import get_api_key
            assert get_api_key() == 'test-secret'

    def test_get_api_key_missing_returns_none(self):
        """Test that missing env var returns None."""
        env = os.environ.copy()
        env.pop('FLACFETCH_API_KEY', None)
        with patch.dict(os.environ, env, clear=True):
            from flacfetch.api.auth import get_api_key
            result = get_api_key()
            assert result is None


@pytest.mark.asyncio
class TestVerifyAPIKey:
    """Tests for API key verification."""

    async def test_verify_correct_key(self):
        """Test that correct key is accepted."""
        with patch.dict(os.environ, {'FLACFETCH_API_KEY': 'correct-key'}):
            from flacfetch.api.auth import verify_api_key
            result = await verify_api_key('correct-key')
            assert result == 'correct-key'

    async def test_verify_wrong_key_raises(self):
        """Test that wrong key raises HTTPException."""
        with patch.dict(os.environ, {'FLACFETCH_API_KEY': 'correct-key'}):
            from fastapi import HTTPException

            from flacfetch.api.auth import verify_api_key

            with pytest.raises(HTTPException) as exc_info:
                await verify_api_key('wrong-key')

            assert exc_info.value.status_code == 401
            assert "Invalid" in exc_info.value.detail

    async def test_verify_missing_key_raises(self):
        """Test that missing key raises HTTPException."""
        with patch.dict(os.environ, {'FLACFETCH_API_KEY': 'correct-key'}):
            from fastapi import HTTPException

            from flacfetch.api.auth import verify_api_key

            with pytest.raises(HTTPException) as exc_info:
                await verify_api_key('')

            assert exc_info.value.status_code == 401

    async def test_verify_none_key_raises(self):
        """Test that None key raises HTTPException."""
        with patch.dict(os.environ, {'FLACFETCH_API_KEY': 'correct-key'}):
            from fastapi import HTTPException

            from flacfetch.api.auth import verify_api_key

            with pytest.raises(HTTPException) as exc_info:
                await verify_api_key(None)

            assert exc_info.value.status_code == 401

    async def test_dev_mode_accepts_any_key(self):
        """Test that dev mode (no key configured) accepts any key."""
        env = os.environ.copy()
        env.pop('FLACFETCH_API_KEY', None)

        with patch.dict(os.environ, env, clear=True):
            from flacfetch.api.auth import verify_api_key
            # In dev mode (no key set), should return dev-mode
            result = await verify_api_key('any-key')
            assert result == 'dev-mode'

    async def test_dev_mode_accepts_none_key(self):
        """Test that dev mode accepts None key too."""
        env = os.environ.copy()
        env.pop('FLACFETCH_API_KEY', None)

        with patch.dict(os.environ, env, clear=True):
            from flacfetch.api.auth import verify_api_key
            result = await verify_api_key(None)
            assert result == 'dev-mode'
