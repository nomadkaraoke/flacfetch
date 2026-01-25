"""Provider for OPS private music tracker."""

from ..core.models import Release, TrackQuery
from .gazelle import GazelleProvider


class OPSProvider(GazelleProvider):
    """Provider for OPS private music tracker.

    Requires both an API key and base URL to be provided.
    The base URL should be set via the OPS_API_URL environment variable
    for security reasons (to avoid hardcoding tracker URLs in source code).
    """

    def __init__(self, api_key: str, base_url: str):
        """Initialize the OPS provider.

        Args:
            api_key: API key for authentication
            base_url: Base URL of the tracker API (e.g., from OPS_API_URL env var)
        """
        if not base_url:
            raise ValueError("base_url is required for OPSProvider. Set OPS_API_URL environment variable.")
        super().__init__(api_key, base_url, cache_subdir="ops")

    @property
    def name(self) -> str:
        return "OPS"

    def search(self, query: TrackQuery) -> list[Release]:
        """Search OPS for releases matching the query.

        Args:
            query: Track query with artist and title

        Returns:
            List of matching releases
        """
        return self._search_browse(query)
