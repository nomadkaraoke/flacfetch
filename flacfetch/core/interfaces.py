from abc import ABC, abstractmethod
from typing import List, Optional
from .models import TrackQuery, Release

class Provider(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def search(self, query: TrackQuery) -> List[Release]:
        pass

    def fetch_artifact(self, release: Release) -> Optional[bytes]:
        """
        Fetch the .torrent file or other metadata artifact required for download.
        Returns None if not applicable (e.g. public URLs).
        """
        return None

class Downloader(ABC):
    @abstractmethod
    def download(self, release: Release, output_path: str) -> None:
        pass

class InteractionHandler(ABC):
    @abstractmethod
    def select_release(self, releases: List[Release]) -> Optional[Release]:
        """
        Prompt the user (or use logic) to select one release from the list.
        Returns None if selection is cancelled.
        """
        pass

