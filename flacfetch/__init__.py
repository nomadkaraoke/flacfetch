"""flacfetch - Search and download high-quality audio from multiple sources."""

__version__ = "0.3.4"
__author__ = "Andrew Beveridge"
__email__ = "andrew@beveridge.uk"

# Core models
from .core.models import Release, Quality, AudioFormat, MediaSource, TrackQuery

# Display utilities for both local and remote CLIs
from .interface.cli import (
    format_release_line,
    print_releases,
    Colors,
    CLIHandler,
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
