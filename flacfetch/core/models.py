from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional


class AudioFormat(Enum):
    FLAC = auto()
    MP3 = auto()
    AAC = auto()
    WAV = auto()
    OPUS = auto()
    VORBIS = auto()  # OGG Vorbis - Spotify source format (320kbps, converted to FLAC)
    OTHER = auto()

class MediaSource(Enum):
    WEB = auto()
    CD = auto()
    VINYL = auto()
    DVD = auto()
    CASSETTE = auto()
    OTHER = auto()

@dataclass(eq=True)
class Quality:
    format: AudioFormat
    bit_depth: Optional[int] = None  # e.g. 16, 24
    sample_rate: Optional[int] = None # e.g. 44100, 96000
    bitrate: Optional[int] = None # For lossy, e.g. 320
    media: MediaSource = MediaSource.OTHER

    def __lt__(self, other: 'Quality') -> bool:
        # Comparison logic for "better" quality
        # 1. Format: FLAC/WAV > Lossy
        if self.is_lossless() and not other.is_lossless():
            return False
        if not self.is_lossless() and other.is_lossless():
            return True

        # 2. If both lossless, check bit depth
        if self.is_lossless():
            s_bits = self.bit_depth or 16
            o_bits = other.bit_depth or 16
            if s_bits != o_bits:
                return s_bits < o_bits

            # 3. Check sample rate
            s_rate = self.sample_rate or 44100
            o_rate = other.sample_rate or 44100
            if s_rate != o_rate:
                return s_rate < o_rate

            # 4. Media preference: WEB > CD > Vinyl (Opinionated default)
            media_rank = {
                MediaSource.WEB: 3,
                MediaSource.CD: 2,
                MediaSource.VINYL: 1,
                MediaSource.DVD: 2,
                MediaSource.CASSETTE: 0,
                MediaSource.OTHER: 0
            }
            return media_rank.get(self.media, 0) < media_rank.get(other.media, 0)

        # 5. If lossy, check bitrate
        s_br = self.bitrate or 0
        o_br = other.bitrate or 0
        return s_br < o_br

    def is_lossless(self) -> bool:
        """Check if format is lossless (FLAC/WAV). Note: doesn't check source quality."""
        return self.format in (AudioFormat.FLAC, AudioFormat.WAV)

    def is_true_lossless(self, source_name: str = "") -> bool:
        """
        Check if truly lossless (not transcoded from lossy source).

        Spotify outputs FLAC but source is 320kbps Vorbis, so it's not true lossless.
        YouTube is always lossy.
        """
        if not self.is_lossless():
            return False
        # Spotify is transcoded from 320kbps Vorbis - not true lossless
        if source_name.lower() == "spotify":
            return False
        return True

    def __str__(self) -> str:
        parts = []
        parts.append(self.format.name)
        if self.is_lossless():
            if self.bit_depth: parts.append(f"{self.bit_depth}bit")
        elif self.bitrate:
            parts.append(f"{self.bitrate}kbps")
        parts.append(self.media.name)
        return " ".join(parts)

@dataclass
class TrackQuery:
    artist: str
    title: str

@dataclass
class Release:
    title: str
    artist: str
    quality: Quality
    source_name: str # e.g. "RED", "OPS", "YouTube"
    download_url: Optional[str] = None
    info_hash: Optional[str] = None # For torrents
    size_bytes: Optional[int] = None

    # Extra Metadata
    year: Optional[int] = None
    edition_info: Optional[str] = None # e.g. "Deluxe Edition", "Remaster 2020"
    label: Optional[str] = None
    catalogue_number: Optional[str] = None
    release_type: Optional[str] = None # e.g. "Album", "Single"
    seeders: Optional[int] = None

    # YouTube / Streaming Metadata
    channel: Optional[str] = None
    view_count: Optional[int] = None
    duration_seconds: Optional[int] = None

    # Selective Download Info
    target_file: Optional[str] = None
    target_file_size: Optional[int] = None # Size of the specific file
    track_pattern: Optional[str] = None # The track title to search for if target_file is not yet resolved
    match_score: float = 0.0 # 0.0 to 1.0, higher is better match for the track title

    # API index for remote CLI matching
    api_index: Optional[int] = None  # Original index in API response for accurate selection

    # Source identification for traceability
    source_id: Optional[str] = None  # Unique ID from source (e.g., YouTube video ID, Spotify track ID, torrent ID)

    def to_dict(self) -> dict:
        """
        Serialize Release to a dictionary for API/JSON transmission.

        This enables remote CLIs to receive full release data and display
        it with the same rich formatting as local CLIs.
        """
        return {
            "title": self.title,
            "artist": self.artist,
            "source_name": self.source_name,
            "download_url": self.download_url,
            "info_hash": self.info_hash,
            "size_bytes": self.size_bytes,
            "year": self.year,
            "edition_info": self.edition_info,
            "label": self.label,
            "catalogue_number": self.catalogue_number,
            "release_type": self.release_type,
            "seeders": self.seeders,
            "channel": self.channel,
            "view_count": self.view_count,
            "duration_seconds": self.duration_seconds,
            "target_file": self.target_file,
            "target_file_size": self.target_file_size,
            "track_pattern": self.track_pattern,
            "match_score": self.match_score,
            "api_index": self.api_index,
            "source_id": self.source_id,
            # Quality as nested dict
            "quality": {
                "format": self.quality.format.name,
                "bit_depth": self.quality.bit_depth,
                "sample_rate": self.quality.sample_rate,
                "bitrate": self.quality.bitrate,
                "media": self.quality.media.name,
            },
            # Computed properties for convenience
            "formatted_size": self.formatted_size,
            "formatted_duration": self.formatted_duration,
            "formatted_views": self.formatted_views,
            "is_lossless": self.quality.is_lossless(),
            "quality_str": str(self.quality),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Release":
        """
        Reconstruct a Release from a dictionary.

        This enables remote CLIs to reconstruct Release objects from API data
        for display with full formatting.
        """
        # Parse quality
        quality_data = data.get("quality", {})
        if isinstance(quality_data, dict):
            quality = Quality(
                format=AudioFormat[quality_data.get("format", "OTHER")],
                bit_depth=quality_data.get("bit_depth"),
                sample_rate=quality_data.get("sample_rate"),
                bitrate=quality_data.get("bitrate"),
                media=MediaSource[quality_data.get("media", "OTHER")],
            )
        else:
            # Fallback for legacy data
            quality = Quality(format=AudioFormat.OTHER)

        return cls(
            title=data.get("title", ""),
            artist=data.get("artist", ""),
            quality=quality,
            source_name=data.get("source_name", "Unknown"),
            download_url=data.get("download_url"),
            info_hash=data.get("info_hash"),
            size_bytes=data.get("size_bytes"),
            year=data.get("year"),
            edition_info=data.get("edition_info"),
            label=data.get("label"),
            catalogue_number=data.get("catalogue_number"),
            release_type=data.get("release_type"),
            seeders=data.get("seeders"),
            channel=data.get("channel"),
            view_count=data.get("view_count"),
            duration_seconds=data.get("duration_seconds"),
            target_file=data.get("target_file"),
            target_file_size=data.get("target_file_size"),
            track_pattern=data.get("track_pattern"),
            match_score=data.get("match_score", 0.0),
            api_index=data.get("api_index") or data.get("index"),  # Support both field names
            source_id=data.get("source_id"),
        )

    @property
    def formatted_size(self) -> str:
        size = self.target_file_size if self.target_file_size is not None else self.size_bytes
        if size is None:
            return "?"

        s = float(size)
        for unit in ['B', 'KB', 'MB', 'GB']:
            if s < 1024.0:
                return f"{s:.1f} {unit}"
            s /= 1024.0
        return f"{s:.1f} TB"

    @property
    def formatted_duration(self) -> Optional[str]:
        if self.duration_seconds is None:
            return None
        m, s = divmod(self.duration_seconds, 60)
        return f"{m}:{s:02d}"

    @property
    def formatted_views(self) -> Optional[str]:
        if self.view_count is None:
            return None
        if self.view_count >= 1_000_000:
            return f"{self.view_count/1_000_000:.1f}M"
        if self.view_count >= 1_000:
            return f"{self.view_count/1_000:.1f}K"
        return str(self.view_count)

    @property
    def source_url(self) -> Optional[str]:
        """
        Generate a URL to the source for easy copy/paste.

        Returns URL formats:
        - YouTube: https://youtu.be/{video_id}
        - Spotify: https://open.spotify.com/track/{track_id}
        - RED/OPS: Uses download_url (contains torrent page link)
        """
        if not self.source_id:
            return None

        source = self.source_name.lower()
        if source == "youtube":
            return f"https://youtu.be/{self.source_id}"
        elif source == "spotify":
            return f"https://open.spotify.com/track/{self.source_id}"
        elif source in ("red", "ops"):
            # For trackers, we can't construct a public URL without the base URL
            # Return None and let the CLI handle it with download_url if available
            return None
        return None

    def __str__(self) -> str:
        # This is the basic string representation, CLI will handle colorization
        parts = [f"[{self.source_name}]"]

        # If YouTube, include channel
        if self.source_name == "YouTube" and self.channel:
             parts.append(f"{self.channel}: {self.title}")
        else:
             parts.append(f"{self.artist} - {self.title}")

        # Detailed metadata string similar to private tracker UI
        # Format: Year / Label / Cat# / Edition / Media
        meta_components = []
        if self.year: meta_components.append(str(self.year))
        if self.label: meta_components.append(self.label)
        if self.catalogue_number: meta_components.append(self.catalogue_number)
        if self.edition_info: meta_components.append(self.edition_info)
        # Media is part of quality usually, but sometimes useful here.
        # The UI puts Media at the end of this string.
        meta_components.append(self.quality.media.name)

        if meta_components:
            parts.append(f"[{' / '.join(meta_components)}]")

        parts.append(f"({self.quality})")

        if self.seeders is not None:
            parts.append(f"Seeders: {self.seeders}")

        parts.append(f"- {self.formatted_size}")

        if self.duration_seconds:
            parts.append(f"({self.formatted_duration})")

        if self.target_file:
            parts.append(f"\n   -> File: {self.target_file}")

        return " ".join(parts)
