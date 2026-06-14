"""Shared Secret Manager helpers for flacfetch.

Centralizes secret-version pruning so every code path that writes a new
version also destroys stale ones. Only ``versions/latest`` is ever read, but
each ENABLED version costs ~$0.06/month in replica storage, so unpruned
rotation (cookies refresh every 8h, Spotify token every 12h) accumulates
hundreds of dead versions.

History: the prune logic previously lived only in ``api/routes/config.py`` and
silently failed because the flacfetch service account held
``secretVersionAdder`` but not ``secretVersionManager`` (no ``versions.destroy``
permission). The June 2026 cost-reduction work fixed the IAM grant and routed
all write paths (including the credential health-check token writeback) through
this shared helper. See ``docs/archive/2026-06-14-gcp-cost-reduction-plan.md``
in the nomadkaraoke workspace.
"""
import logging

logger = logging.getLogger(__name__)


def destroy_old_secret_versions(client, secret_path: str, keep: int = 5) -> None:
    """Destroy ENABLED secret versions beyond the newest ``keep``.

    Args:
        client: A ``secretmanager.SecretManagerServiceClient``.
        secret_path: Full secret resource name
            (``projects/<project>/secrets/<secret>``).
        keep: Number of newest ENABLED versions to retain.

    Best-effort: failures (e.g. missing ``versions.destroy`` permission) are
    logged as warnings and never raised, so they cannot break the calling
    write path.
    """
    try:
        versions = list(client.list_secret_versions(request={"parent": secret_path}))
        enabled = [
            v for v in versions
            if v.state == v.State.ENABLED  # type: ignore[attr-defined]
        ]
        enabled.sort(key=lambda v: v.create_time, reverse=True)
        for v in enabled[keep:]:
            try:
                client.destroy_secret_version(request={"name": v.name})
            except Exception as e:
                logger.warning(f"Failed to destroy {v.name}: {e}")
    except Exception as e:
        logger.warning(f"Failed to prune old secret versions for {secret_path}: {e}")
