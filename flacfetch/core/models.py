from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional, List, Any

class AudioFormat(Enum):
    FLAC = auto()
    MP3 = auto()
    AAC = auto()
    WAV = auto()
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
        return self.format in (AudioFormat.FLAC, AudioFormat.WAV)
        
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
    source_name: str # e.g. "Redacted", "Bandcamp"
    download_url: Optional[str] = None
    info_hash: Optional[str] = None # For torrents
    size_bytes: Optional[int] = None
    
    # Extra Metadata
    year: Optional[int] = None
    edition_info: Optional[str] = None # e.g. "Deluxe Edition", "Remaster 2020"
    label: Optional[str] = None
    catalogue_number: Optional[str] = None
    
    # Selective Download Info
    target_file: Optional[str] = None
    track_pattern: Optional[str] = None # The track title to search for if target_file is not yet resolved
    
    @property
    def formatted_size(self) -> str:
        if self.size_bytes is None:
            return "?"
        
        size = float(self.size_bytes)
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} TB"

    def __str__(self) -> str:
        parts = [f"[{self.source_name}]"]
        parts.append(f"{self.artist} - {self.title}")
        
        meta = []
        if self.year:
            meta.append(str(self.year))
        if self.edition_info:
            meta.append(self.edition_info)
        
        if meta:
            parts.append(f"[{', '.join(meta)}]")
            
        parts.append(f"({self.quality})")
        
        if self.size_bytes:
            parts.append(f"- {self.formatted_size}")
            
        if self.target_file:
            parts.append(f"[Target: {self.target_file}]")
            
        return " ".join(parts)
