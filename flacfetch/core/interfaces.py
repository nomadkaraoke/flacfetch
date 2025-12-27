from abc import ABC, abstractmethod
from typing import Optional

from .models import Release, TrackQuery


class Provider(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def search(self, query: TrackQuery) -> list[Release]:
        pass

    def fetch_artifact(self, release: Release) -> Optional[bytes]:
        """
        Fetch the .torrent file or other metadata artifact required for download.
        Returns None if not applicable (e.g. public URLs).
        """
        return None

    def fetch_artifact_by_id(self, source_id: str) -> Optional[bytes]:
        """
        Fetch the .torrent file or other artifact by source ID directly.
        This allows downloading without needing a full Release object,
        which is useful when the Release was serialized and stored.

        Args:
            source_id: The source-specific ID (e.g., torrent ID for RED/OPS)

        Returns:
            The artifact bytes (e.g., .torrent file contents), or None if not applicable
        """
        return None

    def populate_details(self, release: Release) -> None:
        """
        Populate additional details for the release (e.g. file list) that were not available during search.
        This modifies the release object in-place.
        """
        pass

class Downloader(ABC):
    @abstractmethod
    def download(self, release: Release, output_path: str, output_filename: Optional[str] = None) -> str:
        """
        Download a release to the specified output path.

        Args:
            release: Release object to download
            output_path: Directory to save the downloaded file
            output_filename: Optional specific filename for the output

        Returns:
            Path to the downloaded file
        """
        pass

class InteractionHandler(ABC):
    @abstractmethod
    def select_release(self, releases: list[Release]) -> Optional[Release]:
        """
        Prompt the user (or use logic) to select one release from the list.
        Returns None if selection is cancelled.
        """
        pass
