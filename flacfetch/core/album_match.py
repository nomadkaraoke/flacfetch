"""Map an album's tracklist onto the audio files of one torrent.

Used by album search: one torrent covers many requested tracks, so instead of
N per-track searches we resolve every track's ``target_file`` from the
torrent's file list in one go.
"""

import html
import posixpath
import re
from dataclasses import dataclass, field
from typing import Optional

from .matching import calculate_match_score
from .models import Release

AUDIO_EXTENSIONS = (".flac", ".mp3", ".m4a", ".wav")

# A file must score at least this to be assigned to a track (same bar as the
# per-track target-file match in GazelleProvider._find_best_target_file).
MIN_TRACK_MATCH_SCORE = 0.6

# Small bonus when the file's track number equals the requested position. Only
# breaks ties between similar titles (e.g. a song and its reprise); it can't
# lift a non-matching title over MIN_TRACK_MATCH_SCORE.
POSITION_BONUS = 0.05

# "06 Title", "06 - Title", "06. Title", "1-06 Title" (disc-track), "A1 Title" is ignored.
_TRACK_NUMBER_RE = re.compile(r"^(?:\d{1,2}[-.])?(\d{1,3})(?=[\s.\-_])")
# Parenthesised/bracketed suffixes such as "(feat. X)" or "[Remastered]".
_BRACKETED_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")


@dataclass
class AlbumTrack:
    """One requested track of the album."""

    key: str
    title: str
    position: Optional[int] = None


@dataclass
class TrackFile:
    """The torrent file chosen for a requested track."""

    target_file: str
    target_file_size: Optional[int]
    match_score: float


@dataclass
class AlbumCandidate:
    """A torrent (as a Release, without a single target_file) plus its per-track files."""

    release: Release
    track_files: dict[str, TrackFile] = field(default_factory=dict)

    @property
    def matched_count(self) -> int:
        return len(self.track_files)


def parse_file_list(file_list_str: str) -> list[tuple[str, Optional[int]]]:
    """Parse a Gazelle ``fileList`` ("name{{{size}}}|||...") into audio (path, size) pairs."""
    if not file_list_str:
        return []
    files: list[tuple[str, Optional[int]]] = []
    for entry in file_list_str.split("|||"):
        size: Optional[int] = None
        name = entry
        if "{{{" in entry:
            name, _, raw_size = entry.partition("{{{")
            try:
                size = int(raw_size.rstrip("}"))
            except ValueError:
                size = None
        name = html.unescape(name)
        if name.lower().endswith(AUDIO_EXTENSIONS):
            files.append((name, size))
    return files


def file_track_number(path: str) -> Optional[int]:
    """The track number a file name starts with ("1-06 X.flac" → 6), if any."""
    m = _TRACK_NUMBER_RE.match(posixpath.basename(path))
    return int(m.group(1)) if m else None


def title_score(title: str, path: str) -> float:
    """How well a file matches a track title, also trying the title without
    bracketed suffixes ("Hypest Hype (feat. Tempa T)" vs "06 Hypest Hype (ft. Tempa T).flac")."""
    name = posixpath.basename(path)
    score = calculate_match_score(title, name)
    stripped = _BRACKETED_RE.sub("", title).strip()
    if stripped and stripped != title:
        score = max(score, calculate_match_score(stripped, name))
    return score


def match_album_tracks(
    files: list[tuple[str, Optional[int]]],
    tracks: list[AlbumTrack],
    min_score: float = MIN_TRACK_MATCH_SCORE,
) -> dict[str, TrackFile]:
    """Assign each track at most one file and each file at most one track.

    Greedy on score: the strongest title/file pairs are assigned first, so an
    exact "Time" claims "13 Time.flac" before "Time" could fall back to
    "14 Time (Wilkinson Remix).flac". Tracks with no file at ``min_score`` are
    left out of the result.
    """
    pairs: list[tuple[float, float, int, int]] = []
    for ti, track in enumerate(tracks):
        if not track.title or not track.title.strip():
            continue
        for fi, (path, _size) in enumerate(files):
            score = title_score(track.title, path)
            if score < min_score:
                continue
            ranked = score
            if track.position is not None and file_track_number(path) == track.position:
                ranked += POSITION_BONUS
            pairs.append((ranked, score, ti, fi))

    pairs.sort(key=lambda p: (-p[0], p[2], p[3]))
    used_tracks: set[int] = set()
    used_files: set[int] = set()
    result: dict[str, TrackFile] = {}
    for _ranked, score, ti, fi in pairs:
        if ti in used_tracks or fi in used_files:
            continue
        used_tracks.add(ti)
        used_files.add(fi)
        path, size = files[fi]
        result[tracks[ti].key] = TrackFile(target_file=path, target_file_size=size, match_score=score)
    return result
