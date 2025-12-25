from __future__ import annotations

import sys
from pathlib import Path

import numpy as np


def _import_detect_transcode():
    # `scripts/` isn't a Python package; add it to sys.path for testing helpers.
    repo_root = Path(__file__).resolve().parents[1]
    scripts_dir = repo_root / "scripts"
    sys.path.insert(0, str(scripts_dir))
    import detect_transcode as dt  # type: ignore

    return dt


def test_hf_noise_floor_and_fill_detects_missing_haze():
    dt = _import_detect_transcode()

    sr = 44100
    dur = 4.0
    t = np.arange(int(sr * dur)) / sr

    # Two halves: loud then quiet.
    loud = t < (dur / 2)
    quiet = ~loud

    # Base audio: 1 kHz sine, loud then quiet.
    y_base = np.zeros_like(t, dtype=np.float32)
    y_base[loud] = (0.6 * np.sin(2 * np.pi * 1000.0 * t[loud])).astype(np.float32)
    y_base[quiet] = (0.03 * np.sin(2 * np.pi * 1000.0 * t[quiet])).astype(np.float32)

    # Create HF "haze" noise (> 19 kHz) only in the quiet half (mimics hiss/dither showing up in Spek).
    rng = np.random.default_rng(0)
    noise = rng.standard_normal(t.shape[0]).astype(np.float32)

    from scipy import signal

    sos = signal.butter(6, 19000.0, btype="highpass", fs=sr, output="sos")
    hf_noise = signal.sosfilt(sos, noise).astype(np.float32)

    # Lossless-like: quiet section contains low-level HF haze.
    y_lossless_like = y_base.copy()
    y_lossless_like[quiet] += 0.0020 * hf_noise[quiet]

    # Lossy-like: HF haze is missing/suppressed in the quiet section.
    y_lossy_like = y_base.copy()
    y_lossy_like[quiet] += 0.00005 * hf_noise[quiet]  # tiny residual

    res_lossless, floor_db_lossless, _sparsity_lossless, fill_lossless = dt.analyze_hf_noise_floor(
        y_lossless_like, sr, verbose=True
    )
    res_lossy, floor_db_lossy, _sparsity_lossy, fill_lossy = dt.analyze_hf_noise_floor(y_lossy_like, sr, verbose=True)

    assert floor_db_lossless is not None and fill_lossless is not None
    assert floor_db_lossy is not None and fill_lossy is not None

    # Lossless-like should have higher HF floor (less negative) and higher fill.
    assert floor_db_lossless > floor_db_lossy + 6.0
    assert fill_lossless > fill_lossy + 0.30

    # Sanity: the heuristic score should be more suspicious for the lossy-like signal.
    assert res_lossy.score > res_lossless.score


