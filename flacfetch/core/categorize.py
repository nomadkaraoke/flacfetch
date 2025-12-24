"""
Categorization logic for organizing search results into meaningful groups.

This module provides functionality to categorize and deduplicate release results
for cleaner, more organized display in the CLI.
"""

from dataclasses import dataclass
from typing import Optional

from .models import Release, TrackQuery


@dataclass
class Category:
    """A category of releases with a name and list of releases."""
    name: str
    releases: list[Release]
    max_display: int = 3  # Max releases to show in condensed view


@dataclass
class CategorizedResults:
    """Container for categorized search results."""
    categories: list[Category]
    all_releases: list[Release]  # Full flat list for 'more' command
    total_count: int

    def get_display_releases(self) -> list[Release]:
        """Get flattened list of releases for display (respecting max_display per category)."""
        result = []
        for cat in self.categories:
            result.extend(cat.releases[:cat.max_display])
        return result

    def get_release_by_index(self, index: int) -> Optional[Release]:
        """Get release by 1-based display index."""
        display_releases = self.get_display_releases()
        if 1 <= index <= len(display_releases):
            return display_releases[index - 1]
        return None


def categorize_releases(
    releases: list[Release],
    query: Optional[TrackQuery] = None,
) -> CategorizedResults:
    """
    Categorize releases into meaningful groups for display.

    Categories (in display order):
    1. Top Seeded - Highest seeder count regardless of type
    2. Album Releases - From albums by the searched artist
    3. Hi-Res 24-bit - Highest quality releases
    4. Singles - Single releases of the track
    5. Live Versions - Live albums, bootlegs, concert recordings
    6. Compilations - VA compilations, soundtracks
    7. Vinyl Rips - Vinyl media releases
    8. Non-Matching Artist - When artist doesn't match search (likely compilations)
    9. YouTube/Lossy - YouTube and other lossy sources

    Each release only appears in ONE category (the first it qualifies for in priority order).

    Args:
        releases: List of Release objects to categorize
        query: Optional TrackQuery for artist matching

    Returns:
        CategorizedResults with organized categories
    """
    if not releases:
        return CategorizedResults(categories=[], all_releases=[], total_count=0)

    searched_artist = query.artist.lower() if query and query.artist else None

    # Track which releases have been assigned to prevent duplicates
    assigned_ids = set()

    def get_release_id(r: Release) -> str:
        """Generate unique ID for a release to prevent duplicates."""
        return f"{r.source_name}:{r.download_url or r.title}:{r.artist}"

    def is_assigned(r: Release) -> bool:
        return get_release_id(r) in assigned_ids

    def mark_assigned(r: Release):
        assigned_ids.add(get_release_id(r))

    def artist_matches(r: Release) -> bool:
        """Check if release artist matches searched artist."""
        if not searched_artist or not r.artist:
            return True  # No artist to compare
        release_artist = r.artist.lower()
        return (release_artist == searched_artist or
                searched_artist in release_artist or
                release_artist in searched_artist)

    # Category definitions with filters
    categories = []

    # Helper to check true lossless (not Spotify transcoded)
    def is_true_lossless(r: Release) -> bool:
        return r.quality and r.quality.is_true_lossless(r.source_name)

    # 1. Top Seeded (TRUE lossless with high seeders - excludes Spotify)
    top_seeded = []
    for r in releases:
        if is_assigned(r):
            continue
        if is_true_lossless(r) and r.seeders and r.seeders >= 50:
            if artist_matches(r):
                top_seeded.append(r)
                mark_assigned(r)
    top_seeded.sort(key=lambda r: r.seeders or 0, reverse=True)
    if top_seeded:
        categories.append(Category(name="TOP SEEDED", releases=top_seeded[:5], max_display=3))

    # 2. Album Releases (TRUE lossless albums - excludes Spotify)
    albums = []
    for r in releases:
        if is_assigned(r):
            continue
        if is_true_lossless(r) and r.release_type == "Album":
            if artist_matches(r):
                albums.append(r)
                mark_assigned(r)
    albums.sort(key=lambda r: r.seeders or 0, reverse=True)
    if albums:
        categories.append(Category(name="ALBUM RELEASES", releases=albums[:5], max_display=3))

    # 3. Hi-Res 24-bit (TRUE lossless)
    hires = []
    for r in releases:
        if is_assigned(r):
            continue
        if (is_true_lossless(r) and
            r.quality.bit_depth and r.quality.bit_depth >= 24):
            if artist_matches(r):
                hires.append(r)
                mark_assigned(r)
    hires.sort(key=lambda r: r.seeders or 0, reverse=True)
    if hires:
        categories.append(Category(name="HI-RES 24-BIT", releases=hires[:5], max_display=3))

    # 4. Singles (TRUE lossless)
    singles = []
    for r in releases:
        if is_assigned(r):
            continue
        if is_true_lossless(r) and r.release_type in ("Single", "EP"):
            if artist_matches(r):
                singles.append(r)
                mark_assigned(r)
    singles.sort(key=lambda r: r.seeders or 0, reverse=True)
    if singles:
        categories.append(Category(name="SINGLES", releases=singles[:3], max_display=2))

    # 5. Live Versions (TRUE lossless)
    live = []
    for r in releases:
        if is_assigned(r):
            continue
        if is_true_lossless(r):
            if r.release_type in ("Live album", "Bootleg", "Concert Recording"):
                live.append(r)
                mark_assigned(r)
    live.sort(key=lambda r: r.seeders or 0, reverse=True)
    if live:
        categories.append(Category(name="LIVE VERSIONS", releases=live[:3], max_display=2))

    # 6. Compilations (TRUE lossless, including soundtracks)
    compilations = []
    for r in releases:
        if is_assigned(r):
            continue
        if is_true_lossless(r):
            if r.release_type in ("Compilation", "Soundtrack", "Anthology"):
                compilations.append(r)
                mark_assigned(r)
    compilations.sort(key=lambda r: r.seeders or 0, reverse=True)
    if compilations:
        categories.append(Category(name="COMPILATIONS", releases=compilations[:3], max_display=2))

    # 7. Vinyl Rips (TRUE lossless)
    vinyl = []
    for r in releases:
        if is_assigned(r):
            continue
        if is_true_lossless(r):
            from .models import MediaSource
            if r.quality.media == MediaSource.VINYL:
                vinyl.append(r)
                mark_assigned(r)
    vinyl.sort(key=lambda r: r.seeders or 0, reverse=True)
    if vinyl:
        categories.append(Category(name="VINYL RIPS", releases=vinyl[:3], max_display=2))

    # 8. Non-Matching Artist (TRUE lossless releases where artist doesn't match)
    non_matching = []
    for r in releases:
        if is_assigned(r):
            continue
        if is_true_lossless(r) and not artist_matches(r):
            non_matching.append(r)
            mark_assigned(r)
    non_matching.sort(key=lambda r: r.seeders or 0, reverse=True)
    if non_matching:
        categories.append(Category(name="OTHER ARTISTS", releases=non_matching[:3], max_display=2))

    # 9. Remaining TRUE Lossless (catch-all for any remaining true lossless)
    remaining_lossless = []
    for r in releases:
        if is_assigned(r):
            continue
        if is_true_lossless(r):
            remaining_lossless.append(r)
            mark_assigned(r)
    remaining_lossless.sort(key=lambda r: r.seeders or 0, reverse=True)
    if remaining_lossless:
        categories.append(Category(name="OTHER LOSSLESS", releases=remaining_lossless[:3], max_display=2))

    # 10. Spotify (lossy source transcoded to FLAC - better than YouTube but not true lossless)
    spotify = []
    for r in releases:
        if is_assigned(r):
            continue
        if r.source_name.lower() == "spotify":
            spotify.append(r)
            mark_assigned(r)
    # Sort by popularity (stored in view_count for Spotify)
    spotify.sort(key=lambda r: r.view_count or 0, reverse=True)
    if spotify:
        categories.append(Category(name="SPOTIFY (320kbps source)", releases=spotify[:5], max_display=3))

    # 11. YouTube/Lossy
    lossy = []
    for r in releases:
        if is_assigned(r):
            continue
        # All remaining are lossy (YouTube, etc.)
        lossy.append(r)
        mark_assigned(r)
    # Sort YouTube by view count
    lossy.sort(key=lambda r: (r.view_count or 0, r.seeders or 0), reverse=True)
    if lossy:
        categories.append(Category(name="YOUTUBE/LOSSY", releases=lossy[:3], max_display=3))

    return CategorizedResults(
        categories=categories,
        all_releases=releases,
        total_count=len(releases)
    )


def is_excellent_match(release: Release, query: Optional[TrackQuery] = None) -> bool:
    """
    Check if a release is an "excellent" match for aggressive auto-selection.

    Criteria:
    - Lossless format
    - Artist exactly matches search (or very close)
    - Release type is Album, Single, or EP (not compilation)
    - 50+ seeders

    Args:
        release: Release to check
        query: TrackQuery with search terms

    Returns:
        True if release meets excellent match criteria
    """
    if not release.quality or not release.quality.is_lossless():
        return False

    if not release.seeders or release.seeders < 50:
        return False

    if release.release_type not in ("Album", "Single", "EP"):
        return False

    # Check artist match
    if query and query.artist and release.artist:
        searched_artist = query.artist.lower()
        release_artist = release.artist.lower()
        # Require exact or very close match
        if not (release_artist == searched_artist or
                searched_artist in release_artist or
                release_artist in searched_artist):
            return False

    return True

