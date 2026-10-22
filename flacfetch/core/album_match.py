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

# A file must score above this to be assigned to a track (same bar as the
# per-track target-file match in GazelleProvider._find_best_target_file).
MIN_TRACK_MATCH_SCORE = 0.6

# Matches weaker than an exact title need corroboration: a title found inside a
# longer file name ("Time" in "05 Time Is Running Out.flac") is only accepted at
# STRONG_MATCH_SCORE or when the file's track number equals the requested
# position; a fuzzy-only match (typo tolerance) needs both FUZZY_MATCH_SCORE and
# the position. Album mode assigns many tracks at once from a torrent that may be
# a different edition, so a wrong-but-plausible file must not slip through.
STRONG_MATCH_SCORE = 0.9
FUZZY_MATCH_SCORE = 0.85

# Small bonus when the file's track number equals the requested position. Only
# breaks ties between similar titles (e.g. a song and its reprise); it can't
# lift a non-matching title over MIN_TRACK_MATCH_SCORE.
POSITION_BONUS = 0.05

# "06 Title", "06 - Title", "06. Title", "1-06 Title" (disc-track), "A1 Title" is ignored.
_TRACK_NUMBER_RE = re.compile(r"^(?:\d{1,2}[-.])?(\d{1,3})(?=[\s.\-_])")
# Bracketed suffixes that don't change which recording it is ("(feat. X)",
# "[Remastered 2011]", "(Mono)"); stripped from titles and file names alike.
# Version suffixes ("(Live)", "(Remix)") are NOT stripped: they're another recording.
_NEUTRAL_BRACKET_RE = re.compile(
    r"\s*[\(\[]\s*(?:feat\.?|ft\.?|featuring|with|remaster(?:ed)?|\d{4}\s+remaster(?:ed)?|"
    r"mono|stereo|explicit|clean|bonus track)\b[^\)\]]*[\)\]]",
    re.IGNORECASE,
)
# Words that mark a different recording of the same song.
_VERSION_MARKERS = frozenset({
    "live", "remix", "mix", "acoustic", "demo", "edit", "instrumental", "reprise",
    "unplugged", "session", "sessions", "version", "dub", "vip", "karaoke", "a cappella", "acapella",
})
_WORD_RE = re.compile(r"[a-z0-9]+")


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


def _strip_neutral(text: str) -> str:
    return _NEUTRAL_BRACKET_RE.sub("", text).strip()


def _file_title(path: str) -> str:
    """The file name without folder and extension."""
    name = posixpath.basename(path)
    return name.rsplit(".", 1)[0] if "." in name else name


def version_markers(text: str) -> frozenset:
    """Version words ("live", "remix", ...) in a title or file name."""
    words = set(_WORD_RE.findall(text.lower()))
    return frozenset(m for m in _VERSION_MARKERS if m in words)


def title_score(title: str, path: str) -> float:
    """How well a file matches a track title (0..1), ignoring neutral suffixes such
    as "(feat. X)" on either side. 0.0 when the two disagree on version markers:
    "Song (Live)" never matches "01 Song.flac", nor "Song" "02 Song (Live).flac"."""
    want = _strip_neutral(title) or title
    have = _strip_neutral(_file_title(path)) or _file_title(path)
    if version_markers(want) != version_markers(have):
        return 0.0
    # calculate_match_score strips a file extension (everything after the last
    # dot), so give it one: "05 Mr. Brightside" must not become "05 Mr".
    return calculate_match_score(want, f"{have}.flac")


def _accept(score: float, title: str, path: str, position: Optional[int]) -> bool:
    """Whether a title/file score is good enough to assign (see STRONG_MATCH_SCORE)."""
    if score <= MIN_TRACK_MATCH_SCORE:
        return False
    if score >= 1.0:
        return True
    position_agrees = position is not None and file_track_number(path) == position
    want = (_strip_neutral(title) or title).lower()
    have = (_strip_neutral(_file_title(path)) or _file_title(path)).lower()
    if re.search(r"\b" + re.escape(want) + r"\b", have):
        return score >= STRONG_MATCH_SCORE or position_agrees
    return score >= FUZZY_MATCH_SCORE and position_agrees


def match_album_tracks(
    files: list[tuple[str, Optional[int]]],
    tracks: list[AlbumTrack],
    min_score: float = MIN_TRACK_MATCH_SCORE,
) -> dict[str, TrackFile]:
    """Assign each track at most one file and each file at most one track.

    Greedy on score: the strongest title/file pairs are assigned first. A file is
    only a candidate when its version markers agree with the title and the match
    is strong enough (``_accept``). Tracks with no acceptable file are left out of
    the result: better a per-track search or a human pick than the wrong recording.
    """
    pairs: list[tuple[float, float, int, int]] = []
    for ti, track in enumerate(tracks):
        if not track.title or not track.title.strip():
            continue
        for fi, (path, _size) in enumerate(files):
            score = title_score(track.title, path)
            if score <= min_score or not _accept(score, track.title, path, track.position):
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
