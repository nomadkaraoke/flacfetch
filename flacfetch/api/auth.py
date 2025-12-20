"""
API key authentication for flacfetch HTTP API.
"""
import os
from typing import Optional

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

# API key header
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_api_key() -> Optional[str]:
    """Get the configured API key from environment."""
    return os.environ.get("FLACFETCH_API_KEY")


async def verify_api_key(api_key: Optional[str] = Security(api_key_header)) -> str:
    """
    Verify the API key from request header.

    Raises HTTPException 401 if key is missing or invalid.
    """
    expected_key = get_api_key()

    # If no API key is configured, allow all requests (dev mode)
    if not expected_key:
        return "dev-mode"

    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="Missing API key. Provide X-API-Key header.",
        )

    if api_key != expected_key:
        raise HTTPException(
            status_code=401,
            detail="Invalid API key.",
        )

    return api_key

