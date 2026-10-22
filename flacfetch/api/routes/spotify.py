"""
Spotify listening-budget endpoint.
"""
import logging
import time

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..auth import verify_api_key
from ..models import SpotifyBudgetResponse
from ..services.spotify_budget import (
    PROBE_BATCH_SECONDS,
    BudgetConfig,
    check_batch,
    get_spotify_ledger,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["spotify"])


@router.get("/spotify/budget", response_model=SpotifyBudgetResponse)
async def get_spotify_budget(api_key: str = Depends(verify_api_key)) -> SpotifyBudgetResponse:
    """Rolling-window Spotify listening usage and whether a batch download may start now."""
    cfg = BudgetConfig.from_env()
    now = time.time()
    try:
        usage = get_spotify_ledger().usage(now, cfg.window_s)
    except Exception as e:
        logger.warning(f"Spotify ledger unavailable for /spotify/budget: {e}")
        return JSONResponse(
            status_code=503,
            content={"detail": {"code": "budget_unavailable", "retry_after_s": 60}},
            headers={"Retry-After": "60"},
        )
    ok, _code, retry = check_batch(usage, PROBE_BATCH_SECONDS, cfg, now)
    return SpotifyBudgetResponse(
        window_s=cfg.window_s,
        total_s=usage.total_s,
        batch_s=usage.batch_s,
        batch_budget_s=cfg.batch_budget_s,
        hard_cap_s=cfg.hard_cap_s,
        batch_in_flight=usage.batch_in_flight,
        interactive_in_flight=usage.interactive_in_flight,
        batch_allowed_now=ok,
        retry_after_s=retry,
    )
