"""flacfetch - Search and download high-quality audio from multiple sources."""

__version__ = "0.8.2"
__author__ = "Andrew Beveridge"
__email__ = "andrew@beveridge.uk"

# Core models
from .core.models import AudioFormat, MediaSource, Quality, Release, TrackQuery

# Display utilities for both local and remote CLIs
from .interface.cli import (
    CLIHandler,
    Colors,
    format_release_line,
    print_releases,
)

__all__ = [
    # Core models
    "Release",
    "Quality",
    "AudioFormat",
    "MediaSource",
    "TrackQuery",
    # Display utilities
    "format_release_line",
    "print_releases",
    "Colors",
    "CLIHandler",
]
