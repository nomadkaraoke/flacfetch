"""
Flacfetch HTTP API module.

This module provides a FastAPI-based HTTP API for the flacfetch library,
enabling remote search and download of audio files from various sources.

Usage:
    flacfetch serve --port 8080

    # Or programmatically:
    from flacfetch.api import create_app
    app = create_app()
"""
from .main import create_app, run_server

__all__ = ["create_app", "run_server"]

