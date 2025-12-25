#!/usr/bin/env python3
"""
Audio Transcode Detector
========================
Detects whether a lossless audio file (FLAC, WAV, etc.) was likely transcoded
from a lossy source (MP3, AAC, Vorbis, Opus, etc.).

Techniques used:
1. Frequency cutoff detection - MP3/AAC have characteristic high-frequency rolloff
2. Spectral flatness analysis - Lossy codecs reduce spectral detail
3. Pre-echo detection - MDCT-based codecs cause temporal smearing
4. Spectral hole detection - Some codecs remove masked frequencies

Usage:
    python detect_transcode.py <audio_file> [--verbose] [--plot]

Requirements:
    pip install numpy scipy soundfile matplotlib

Author: flacfetch project
License: MIT
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from numpy.typing import NDArray


# Optional imports for plotting
try:
    import matplotlib.pyplot as plt

    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False


class Confidence(Enum):
    """Confidence levels for transcode detection."""

    LOSSY_DETECTED = "lossy_detected"           # Clear lossy artifacts found
    PROBABLY_LOSSY = "probably_lossy"           # Suspicious patterns
    UNCERTAIN = "uncertain"                      # Can't determine
    NO_ARTIFACTS = "no_artifacts"               # No lossy artifacts detected

    @property
    def color(self) -> str:
        """ANSI color code for terminal output."""
        colors = {
            "lossy_detected": "\033[91m",       # Red
            "probably_lossy": "\033[93m",       # Yellow
            "uncertain": "\033[90m",            # Gray
            "no_artifacts": "\033[92m",         # Green
        }
        return colors.get(self.value, "\033[0m")

    @property
    def display_text(self) -> str:
        """Human-readable display text."""
        texts = {
            "lossy_detected": "LOSSY ARTIFACTS DETECTED",
            "probably_lossy": "PROBABLY LOSSY",
            "uncertain": "UNCERTAIN",
            "no_artifacts": "NO LOSSY ARTIFACTS DETECTED",
        }
        return texts.get(self.value, self.value.upper())


@dataclass
class CodecSignature:
    """Known characteristics of lossy codecs."""

    name: str
    typical_cutoffs: list[tuple[int, float]]  # (bitrate, cutoff_hz)
    has_hard_cutoff: bool
    frame_size: int  # samples


CODEC_SIGNATURES = {
    "mp3": CodecSignature(
        name="MP3 (MPEG-1 Layer III)",
        typical_cutoffs=[
            (128, 16000),
            (160, 17000),
            (192, 18500),
            (256, 19500),
            (320, 20500),
        ],
        has_hard_cutoff=True,
        frame_size=1152,
    ),
    "aac_lc": CodecSignature(
        name="AAC-LC",
        typical_cutoffs=[
            (128, 15500),
            (192, 18000),
            (256, 19500),
            (320, 20500),
        ],
        has_hard_cutoff=True,
        frame_size=1024,
    ),
    "vorbis": CodecSignature(
        name="Vorbis (OGG)",
        typical_cutoffs=[
            (128, 18000),
            (192, 20000),
            (256, 22000),
        ],
        has_hard_cutoff=False,  # Uses noise shaping
        frame_size=2048,
    ),
    "opus": CodecSignature(
        name="Opus",
        typical_cutoffs=[
            (96, 20000),
            (128, 20000),
            (192, 20000),
        ],
        has_hard_cutoff=False,  # Nearly transparent
        frame_size=960,  # at 48kHz
    ),
}


@dataclass
class AnalysisResult:
    """Results from a single analysis technique."""

    name: str
    score: float  # 0.0 = definitely lossless, 1.0 = definitely lossy
    confidence: float  # How confident we are in this score
    details: str


@dataclass
class TranscodeAnalysis:
    """Complete analysis of an audio file."""

    file_path: Path
    confidence: Confidence
    probability_lossy: float
    detected_cutoff_hz: float | None
    spectral_flatness_score: float
    pre_echo_detected: bool
    spectral_holes_detected: bool
    suspected_codec: str | None
    suspected_bitrate: int | None
    hf_noise_floor_db: float | None
    hf_noise_sparsity: float | None
    hf_noise_fill: float | None
    hf_texture_band: str | None
    hf_texture_score: float | None
    hf_texture_details: dict | None
    individual_results: list[AnalysisResult] = field(default_factory=list)
    details: dict = field(default_factory=dict)

    def print_report(self, verbose: bool = False) -> None:
        """Print a formatted report to stdout."""
        reset = "\033[0m"
        bold = "\033[1m"

        print(f"\n{'='*65}")
        print(f"{bold}TRANSCODE ANALYSIS: {self.file_path.name}{reset}")
        print(f"{'='*65}")

        verdict_text = self.confidence.display_text
        print(f"\n{bold}Verdict:{reset} {self.confidence.color}{verdict_text}{reset}")
        print(f"{bold}Lossy Probability:{reset} {self.probability_lossy:.1%}")

        # Add caveat for "no artifacts" verdict
        if self.confidence == Confidence.NO_ARTIFACTS:
            print(f"\n{bold}⚠ Note:{reset} High-bitrate lossy (Vorbis/Opus 256kbps+) may be undetectable")

        if self.suspected_codec:
            print(f"{bold}Suspected Source:{reset} {self.suspected_codec}", end="")
            if self.suspected_bitrate:
                print(f" @ ~{self.suspected_bitrate}kbps")
            else:
                print()

        print(f"\n{bold}Detection Results:{reset}")

        # Frequency cutoff
        if self.detected_cutoff_hz:
            nyquist = self.details.get("nyquist_hz", 22050)
            if self.detected_cutoff_hz < nyquist - 1000:
                status = "⚠️  SUSPICIOUS"
                color = "\033[93m"
            else:
                status = "✓ Normal"
                color = "\033[92m"
            print(f"  • Frequency Cutoff: {color}{self.detected_cutoff_hz:.0f} Hz{reset} ({status})")
        else:
            print("  • Frequency Cutoff: \033[92mNone detected (full spectrum)\033[0m ✓")

        # Spectral flatness
        flatness_status = "⚠️  Low variation (suspicious)" if self.spectral_flatness_score < 0.03 else "✓ Normal"
        print(f"  • Spectral Flatness Variation: {self.spectral_flatness_score:.4f} ({flatness_status})")

        # Pre-echo
        pre_echo_text = "\033[93m⚠️  Yes (MDCT artifacts)\033[0m" if self.pre_echo_detected else "\033[92m✓ No\033[0m"
        print(f"  • Pre-echo Artifacts: {pre_echo_text}")

        # Spectral holes
        holes_text = (
            "\033[93m⚠️  Yes (psychoacoustic masking)\033[0m"
            if self.spectral_holes_detected
            else "\033[92m✓ No\033[0m"
        )
        print(f"  • Spectral Holes: {holes_text}")

        # HF noise floor / gating (useful for high-bitrate Vorbis/Opus cases)
        if self.hf_noise_floor_db is not None and self.hf_noise_sparsity is not None:
            # Heuristic: very low HF floor and high sparsity suggests noise suppression / lossy behavior
            fill_txt = ""
            if self.hf_noise_fill is not None:
                fill_txt = f", fill: {self.hf_noise_fill:.2f}"
            suspicious = (self.hf_noise_floor_db < -105.0) and (
                (self.hf_noise_sparsity > 0.35) or (self.hf_noise_fill is not None and self.hf_noise_fill < 0.25)
            )
            if suspicious:
                hf_status = "\033[93m⚠️  SUSPICIOUS\033[0m"
            else:
                hf_status = "\033[92m✓ Normal\033[0m"
            print(
                f"  • HF Noise Floor (quiet frames): {self.hf_noise_floor_db:.1f} dB (sparsity: {self.hf_noise_sparsity:.2f}{fill_txt}) {hf_status}"
            )

        # HF texture / patchiness (captures Spek-visible "striping" vs continuous haze)
        if self.hf_texture_band and self.hf_texture_score is not None and self.hf_texture_details:
            suspicious = self.hf_texture_score >= 0.55
            status = "\033[93m⚠️  SUSPICIOUS\033[0m" if suspicious else "\033[92m✓ Normal\033[0m"
            d = self.hf_texture_details
            print(
                "  • HF Texture (quiet frames): "
                f"{self.hf_texture_band} "
                f"(fill med/p10: {d.get('fill_median', 0):.2f}/{d.get('fill_p10', 0):.2f}, "
                f"Δfill: {d.get('fill_drop', 0):.2f}; "
                f"HF floor med/p10: {d.get('hf_floor_db_median', 0):.1f}/{d.get('hf_floor_db_p10', 0):.1f} dB, "
                f"ΔdB: {d.get('hf_floor_db_drop', 0):.1f}) "
                f"{status}"
            )

        print(f"\n{bold}File Information:{reset}")
        print(f"  • Sample Rate: {self.details.get('sample_rate', 'Unknown')} Hz")
        print(f"  • Bit Depth: {self.details.get('bit_depth', 'Unknown')} bits")
        print(f"  • Channels: {self.details.get('channels', 'Unknown')}")
        print(f"  • Duration: {self.details.get('duration_seconds', 0):.1f}s")
        print(f"  • Nyquist Frequency: {self.details.get('nyquist_hz', 0):.0f} Hz")

        if verbose and self.individual_results:
            print(f"\n{bold}Detailed Analysis:{reset}")
            for result in self.individual_results:
                print(f"\n  [{result.name}]")
                print(f"    Score: {result.score:.2f} (confidence: {result.confidence:.2f})")
                print(f"    {result.details}")

        print()


def load_audio(file_path: str | Path) -> tuple[NDArray[np.float32], int, dict]:
    """
    Load an audio file using soundfile (preferred) or librosa.
    Returns (audio_data, sample_rate, metadata).
    """
    import soundfile as sf

    file_path = Path(file_path)

    # Get metadata
    info = sf.info(str(file_path))
    metadata = {
        "sample_rate": info.samplerate,
        "channels": info.channels,
        "duration_seconds": info.duration,
        "format": info.format,
        "subtype": info.subtype,
        "bit_depth": 16,  # Default
    }

    # Determine bit depth from subtype
    subtype = info.subtype.upper()
    if "16" in subtype:
        metadata["bit_depth"] = 16
    elif "24" in subtype:
        metadata["bit_depth"] = 24
    elif "32" in subtype:
        metadata["bit_depth"] = 32
    elif "FLOAT" in subtype:
        metadata["bit_depth"] = 32

    # Load audio (mono, preserve sample rate)
    data, sr = sf.read(str(file_path), dtype="float32", always_2d=True)

    # Convert to mono by averaging channels
    if data.shape[1] > 1:
        data = np.mean(data, axis=1)
    else:
        data = data[:, 0]

    return data, sr, metadata


def compute_spectrogram(
    y: NDArray[np.float32], sr: int, n_fft: int = 4096, hop_length: int = 512
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """
    Compute spectrogram using numpy/scipy (no librosa required for core function).
    Returns (magnitude_spectrogram, frequencies, times).
    """
    from scipy import signal

    # Compute STFT
    frequencies, times, Zxx = signal.stft(y, fs=sr, nperseg=n_fft, noverlap=n_fft - hop_length, window="hann")

    # Convert to magnitude
    magnitude = np.abs(Zxx)

    return magnitude, frequencies, times


def analyze_frequency_cutoff(y: NDArray[np.float32], sr: int, verbose: bool = False) -> AnalysisResult:
    """
    Detect hard frequency cutoffs typical of MP3/AAC.

    Uses a reference-based approach: compare high-frequency power to mid-frequency power.
    A hard cutoff is detected when power drops to near-zero relative to mid frequencies.
    """
    magnitude, frequencies, _ = compute_spectrogram(y, sr, n_fft=4096)

    # Average power across time for each frequency bin
    mean_power = np.mean(magnitude, axis=1)

    # Normalize
    max_power = np.max(mean_power)
    if max_power == 0:
        return AnalysisResult(name="Frequency Cutoff", score=0.0, confidence=0.0, details="Could not analyze - silent audio")

    normalized = mean_power / max_power
    nyquist = sr / 2

    # Get reference power from mid-frequencies (5-10kHz) where most audio has content
    mid_freq_mask = (frequencies >= 5000) & (frequencies <= 10000)
    mid_freq_power = np.mean(normalized[mid_freq_mask])

    if mid_freq_power < 0.0001:
        return AnalysisResult(
            name="Frequency Cutoff",
            score=0.0,
            confidence=0.2,
            details="Very low mid-frequency content - cannot reliably analyze",
        )

    # Define threshold: frequencies with power < 1% of mid-frequency average are "dead"
    dead_threshold = mid_freq_power * 0.01

    # Find the highest frequency that still has significant power
    # Scan from Nyquist downward to find where audio content exists
    high_freq_mask = frequencies > 10000
    high_freq_indices = np.where(high_freq_mask)[0]

    if len(high_freq_indices) == 0:
        return AnalysisResult(
            name="Frequency Cutoff",
            score=0.0,
            confidence=0.5,
            details=f"Sample rate too low to analyze HF content (sr={sr})",
        )

    # Find the effective cutoff: highest frequency with power > dead_threshold
    effective_cutoff = None
    for idx in reversed(high_freq_indices):
        if normalized[idx] > dead_threshold:
            effective_cutoff = frequencies[idx]
            break

    if effective_cutoff is None:
        # No significant HF content at all
        effective_cutoff = 10000.0

    # Calculate how far below Nyquist the cutoff is
    gap_to_nyquist = nyquist - effective_cutoff

    # Also check for hard cutoff pattern: sharp drop between adjacent frequencies
    # Look for where power ratio drops by 10x or more within a small frequency range
    hard_cutoff_detected = False
    hard_cutoff_freq = None

    for i in range(len(high_freq_indices) - 3):
        idx = high_freq_indices[i]
        idx_next = high_freq_indices[i + 3]  # Check 3 bins ahead (~35 Hz at 4096 FFT)

        power_here = normalized[idx]
        power_ahead = normalized[idx_next]

        # If power drops by 10x or more, it's a hard cutoff
        if power_here > dead_threshold and power_ahead < dead_threshold * 0.1:
            hard_cutoff_detected = True
            hard_cutoff_freq = frequencies[idx]
            break

    # Calculate gap as percentage of Nyquist (more meaningful across sample rates)
    gap_percentage = gap_to_nyquist / nyquist * 100

    # Also check if there's ANY significant power near Nyquist (95%)
    near_nyquist_idx = np.argmin(np.abs(frequencies - nyquist * 0.95))
    near_nyquist_power = normalized[near_nyquist_idx]
    has_near_nyquist_content = near_nyquist_power > dead_threshold

    # Determine score based on findings
    if hard_cutoff_detected and hard_cutoff_freq:
        # Hard cutoff detected - very suspicious
        if gap_percentage > 15:
            score = 0.95
            confidence = 0.95
            details = f"Hard cutoff at {hard_cutoff_freq:.0f} Hz (Nyquist: {nyquist:.0f} Hz) - likely MP3/AAC"
        elif gap_percentage > 8:
            score = 0.85
            confidence = 0.9
            details = f"Cutoff at {hard_cutoff_freq:.0f} Hz (Nyquist: {nyquist:.0f} Hz) - possibly high-bitrate lossy"
        else:
            score = 0.5
            confidence = 0.7
            details = f"Slight cutoff at {hard_cutoff_freq:.0f} Hz (Nyquist: {nyquist:.0f} Hz)"
    elif gap_percentage > 10:
        # Large percentage gap - very suspicious even without hard cutoff
        # This catches YouTube/Opus which has gradual rolloff but clear gap
        score = 0.85
        confidence = 0.85
        details = f"Significant HF gap - content ends at {effective_cutoff:.0f} Hz ({gap_percentage:.0f}% below Nyquist)"
    elif gap_percentage > 5:
        # Moderate gap - suspicious
        score = 0.6
        confidence = 0.7
        details = f"Limited HF content - effective range up to {effective_cutoff:.0f} Hz (Nyquist: {nyquist:.0f} Hz)"
    elif gap_percentage > 2:
        # Small gap - could be natural rolloff or high-bitrate lossy
        score = 0.3
        confidence = 0.5
        details = f"Slight HF rolloff - content up to {effective_cutoff:.0f} Hz"
    elif not has_near_nyquist_content:
        # No gap but no power near Nyquist - might be suspicious
        score = 0.2
        confidence = 0.4
        details = f"Low power near Nyquist - content up to {effective_cutoff:.0f} Hz"
    else:
        # Full spectrum content with power near Nyquist
        score = 0.0
        confidence = 0.9
        details = f"Full spectrum up to {effective_cutoff:.0f} Hz (Nyquist: {nyquist:.0f} Hz)"

    return AnalysisResult(name="Frequency Cutoff", score=score, confidence=confidence, details=details)


def analyze_spectral_flatness(y: NDArray[np.float32], sr: int, verbose: bool = False) -> AnalysisResult:
    """
    Lossy compression tends to reduce spectral complexity in quiet sections.
    This measures the variation in spectral flatness over time.
    """
    # Compute spectrogram with smaller FFT for time resolution
    magnitude, frequencies, times = compute_spectrogram(y, sr, n_fft=2048, hop_length=512)

    # Compute spectral flatness for each frame
    # Spectral flatness = geometric_mean / arithmetic_mean
    epsilon = 1e-10

    flatness_values = []
    for frame in magnitude.T:
        frame = frame + epsilon
        geometric_mean = np.exp(np.mean(np.log(frame)))
        arithmetic_mean = np.mean(frame)
        flatness = geometric_mean / arithmetic_mean
        flatness_values.append(flatness)

    flatness_array = np.array(flatness_values)

    # Measure variation - lossless typically has more variation
    flatness_std = np.std(flatness_array)

    # Score based on variation
    # Low variation might indicate lossy compression smoothing
    if flatness_std < 0.02:
        score = 0.6
        confidence = 0.4
        details = f"Low spectral flatness variation ({flatness_std:.4f}) - possibly compressed"
    elif flatness_std < 0.05:
        score = 0.3
        confidence = 0.3
        details = f"Moderate spectral flatness variation ({flatness_std:.4f})"
    else:
        score = 0.1
        confidence = 0.5
        details = f"High spectral flatness variation ({flatness_std:.4f}) - likely authentic"

    return AnalysisResult(name="Spectral Flatness", score=score, confidence=confidence, details=details)


def detect_pre_echo(y: NDArray[np.float32], sr: int, verbose: bool = False) -> AnalysisResult:
    """
    MDCT-based codecs (MP3, AAC, Vorbis, Opus) can cause pre-echo before transients.
    This is most noticeable on sharp attacks like drums.
    """
    from scipy import signal

    # Find transients using onset detection
    # Simple approach: look for large amplitude increases

    # Compute envelope
    analytic = signal.hilbert(y)
    envelope = np.abs(analytic)

    # Smooth envelope
    window_size = int(sr * 0.01)  # 10ms window
    if window_size > 0:
        envelope_smooth = np.convolve(envelope, np.ones(window_size) / window_size, mode="same")
    else:
        envelope_smooth = envelope

    # Find peaks (potential transients)
    peaks, properties = signal.find_peaks(
        envelope_smooth, height=np.max(envelope_smooth) * 0.3, distance=int(sr * 0.1)  # At least 100ms apart
    )

    pre_echo_count = 0
    total_checked = 0

    # Check for pre-echo before each peak
    pre_echo_window = int(sr * 0.02)  # 20ms before

    for peak_idx in peaks[:30]:  # Check first 30 peaks
        if peak_idx < pre_echo_window + int(sr * 0.05):
            continue

        total_checked += 1

        # Get the quiet region before the transient
        quiet_region = envelope_smooth[peak_idx - pre_echo_window - int(sr * 0.03) : peak_idx - pre_echo_window]
        pre_region = envelope_smooth[peak_idx - pre_echo_window : peak_idx]

        if len(quiet_region) == 0 or len(pre_region) == 0:
            continue

        quiet_level = np.mean(quiet_region)
        pre_level = np.mean(pre_region)

        # Pre-echo: energy builds up before the transient in an unnatural way
        if quiet_level > 0 and pre_level > quiet_level * 2.5:
            # Also check that this isn't just a gradual fade-in
            gradient = np.gradient(pre_region)
            if np.mean(gradient) > 0 and np.std(gradient) < np.mean(gradient) * 2:
                pre_echo_count += 1

    # Calculate score
    if total_checked == 0:
        score = 0.0
        confidence = 0.1
        details = "Could not find suitable transients to analyze"
    else:
        pre_echo_ratio = pre_echo_count / total_checked

        if pre_echo_ratio > 0.3:
            score = 0.8
            confidence = 0.6
            details = f"Pre-echo detected in {pre_echo_count}/{total_checked} transients ({pre_echo_ratio:.0%})"
        elif pre_echo_ratio > 0.1:
            score = 0.4
            confidence = 0.4
            details = f"Possible pre-echo in {pre_echo_count}/{total_checked} transients"
        else:
            score = 0.1
            confidence = 0.5
            details = f"No significant pre-echo detected ({pre_echo_count}/{total_checked})"

    return AnalysisResult(name="Pre-echo Detection", score=score, confidence=confidence, details=details)


def detect_spectral_holes(y: NDArray[np.float32], sr: int, verbose: bool = False) -> AnalysisResult:
    """
    Lossy codecs use psychoacoustic masking to remove 'inaudible' frequencies.
    This can leave 'holes' in the spectrum that wouldn't occur naturally.
    """
    magnitude, frequencies, times = compute_spectrogram(y, sr, n_fft=4096)

    # Look at time-averaged spectrum
    mean_spectrum = np.mean(magnitude, axis=1)

    # Normalize
    max_val = np.max(mean_spectrum)
    if max_val == 0:
        return AnalysisResult(name="Spectral Holes", score=0.0, confidence=0.0, details="Could not analyze - silent audio")

    normalized = mean_spectrum / max_val

    # Look for unusual gaps in the mid-frequency range (1kHz - 10kHz)
    mid_freq_mask = (frequencies > 1000) & (frequencies < 10000)
    mid_freq_indices = np.where(mid_freq_mask)[0]

    if len(mid_freq_indices) < 10:
        return AnalysisResult(name="Spectral Holes", score=0.0, confidence=0.2, details="Not enough frequency resolution")

    mid_freq_power = normalized[mid_freq_indices]

    # Look for sudden dips that recover
    holes_found = 0
    threshold = 0.01

    for i in range(1, len(mid_freq_power) - 1):
        if mid_freq_power[i] < threshold:
            # Check if neighbors have significant energy
            left = mid_freq_power[i - 1] if i > 0 else 0
            right = mid_freq_power[i + 1] if i < len(mid_freq_power) - 1 else 0

            if left > threshold * 10 and right > threshold * 10:
                holes_found += 1

    if holes_found > 5:
        score = 0.7
        confidence = 0.5
        details = f"Found {holes_found} potential spectral holes (psychoacoustic masking artifacts)"
    elif holes_found > 2:
        score = 0.4
        confidence = 0.4
        details = f"Found {holes_found} possible spectral gaps"
    else:
        score = 0.1
        confidence = 0.5
        details = "Spectrum appears continuous"

    return AnalysisResult(name="Spectral Holes", score=score, confidence=confidence, details=details)


def analyze_hf_noise_floor(
    y: NDArray[np.float32], sr: int, verbose: bool = False
) -> tuple[AnalysisResult, float | None, float | None, float | None]:
    """
    Detect "missing HF haze" / noise-floor suppression.

    Motivation (matches what Spek makes visually obvious):
    - Many genuine CD rips / analog-sourced masters have a low-level, fairly stationary wideband noise floor
      (dither, tape hiss, analog chain noise) that remains visible up to Nyquist.
    - High-bitrate perceptual codecs (Vorbis/Opus) often preserve *some* HF energy on transients, but can
      suppress/quantize low-level HF noise in quiet passages, producing a darker/sparser HF background.

    This heuristic focuses on HF-band average power during "quiet" frames (based on mid-band power),
    and a sparsity/gating measure of how often HF power collapses far below its typical quiet-frame level.

    Returns (AnalysisResult, hf_floor_db, hf_sparsity, hf_fill).
    """
    nyquist = sr / 2
    if nyquist < 12000:
        return (
            AnalysisResult(
                name="HF Noise Floor",
                score=0.0,
                confidence=0.2,
                details=f"Sample rate too low for HF noise-floor analysis (Nyquist={nyquist:.0f} Hz)",
            ),
            None,
            None,
            None,
        )

    magnitude, frequencies, _times = compute_spectrogram(y, sr, n_fft=8192, hop_length=1024)
    power = magnitude * magnitude

    # Mid band for "loudness" proxy (avoid bass dominance)
    mid_mask = (frequencies >= 2000) & (frequencies <= 10000)
    # HF band near the top of spectrum
    hf_start = max(16000.0, nyquist * 0.72)
    hf_end = nyquist * 0.98  # avoid edge artifacts near Nyquist bin
    hf_mask = (frequencies >= hf_start) & (frequencies <= hf_end)

    if np.count_nonzero(mid_mask) < 5 or np.count_nonzero(hf_mask) < 5:
        return (
            AnalysisResult(
                name="HF Noise Floor",
                score=0.0,
                confidence=0.2,
                details="Not enough frequency resolution for HF noise-floor analysis",
            ),
            None,
            None,
            None,
        )

    frame_mid = np.mean(power[mid_mask, :], axis=0)

    # For Spek-like "upper band haze" comparisons, focus very close to Nyquist too
    hf_high_start = max(19000.0, nyquist * 0.86)
    hf_high_mask = (frequencies >= hf_high_start) & (frequencies <= hf_end)
    if np.count_nonzero(hf_high_mask) < 5:
        hf_high_mask = hf_mask

    frame_hf = np.mean(power[hf_high_mask, :], axis=0)

    # Normalize to peak to get a consistent dBFS-like scale for "fill" thresholding
    peak_power = float(np.max(power))
    if peak_power <= 0:
        return (
            AnalysisResult(name="HF Noise Floor", score=0.0, confidence=0.1, details="Could not analyze - silent audio"),
            None,
            None,
            None,
        )
    power_norm = power / (peak_power + 1e-20)

    # Pick frames using mid-band percentiles.
    #
    # Nuance: For heavily limited tracks, the "quiet" percentiles can still be musically dense.
    # Spek-visible lossy-vs-lossless differences often show up more strongly in *very quiet / near-silence*
    # regions (intros/outros/gaps), where codecs may suppress the HF noise floor.
    p1 = float(np.percentile(frame_mid, 1))
    p5 = float(np.percentile(frame_mid, 5))
    p25 = float(np.percentile(frame_mid, 25))
    if p25 <= 0:
        return (
            AnalysisResult(name="HF Noise Floor", score=0.0, confidence=0.1, details="Could not analyze - silent/near-silent audio"),
            None,
            None,
            None,
        )

    # "Quiet" window: exclude extreme silence but keep quieter material.
    quiet_mask = (frame_mid > p5) & (frame_mid <= p25)
    # "Very quiet" window: include near-silence (but still above a tiny floor to avoid pure digital zero).
    tiny_floor = max(p1 * 0.5, 1e-12)
    very_quiet_mask = (frame_mid > tiny_floor) & (frame_mid <= p5)
    if np.count_nonzero(quiet_mask) < 25:
        # Fallback: widen window if track is consistently loud or too short
        p40 = float(np.percentile(frame_mid, 40))
        quiet_mask = (frame_mid > p5) & (frame_mid <= p40)

    if np.count_nonzero(quiet_mask) < 10 and np.count_nonzero(very_quiet_mask) < 10:
        return (
            AnalysisResult(
                name="HF Noise Floor",
                score=0.0,
                confidence=0.2,
                details="Not enough quiet frames to analyze HF noise-floor behavior",
            ),
            None,
            None,
            None,
        )

    eps = 1e-20
    hf_db = 10.0 * np.log10(frame_hf + eps)
    def _robust_median(mask: NDArray[np.bool_]) -> float | None:
        if np.count_nonzero(mask) < 10:
            return None
        return float(np.median(hf_db[mask]))

    hf_floor_quiet = _robust_median(quiet_mask)
    hf_floor_vquiet = _robust_median(very_quiet_mask)
    hf_floor_candidates = [v for v in (hf_floor_quiet, hf_floor_vquiet) if v is not None]
    if not hf_floor_candidates:
        return (
            AnalysisResult(
                name="HF Noise Floor",
                score=0.0,
                confidence=0.2,
                details="Could not compute HF floor (no suitable quiet frames)",
            ),
            None,
            None,
            None,
        )
    # Use the lower (more negative) floor as the "worst-case" HF noise-floor presence.
    hf_floor_db = float(min(hf_floor_candidates))

    # Sparsity: how often HF drops far below its typical quiet level (codec noise suppression / gating)
    # 12 dB below the median is a fairly strong drop.
    sparsity_mask = quiet_mask | very_quiet_mask
    hf_sparsity = float(np.mean(hf_db[sparsity_mask] < (hf_floor_db - 12.0)))

    # Fill: fraction of HF bins above a very low threshold in quiet frames.
    # If there is a "blue haze", many bins should still be above ~-110 dBFS.
    hf_bins_dbfs = 10.0 * np.log10(power_norm[hf_high_mask, :] + 1e-20)
    hf_fill_per_frame = np.mean(hf_bins_dbfs > -110.0, axis=0)
    fill_mask = quiet_mask | very_quiet_mask
    hf_fill = float(np.median(hf_fill_per_frame[fill_mask]))

    # Score heuristics (calibrated conservatively; meant as a "nudge" not a definitive proof):
    # - Very low HF floor suggests HF energy is being suppressed in quiet sections.
    # - High sparsity suggests HF is "patchy" rather than stationary haze.
    if (hf_floor_db < -112 and hf_sparsity > 0.45) or (hf_floor_db < -112 and hf_fill < 0.15):
        score = 0.75
        confidence = 0.65
        details = f"Very low/sparse HF floor in quiet frames (hf_floor={hf_floor_db:.1f} dB, sparsity={hf_sparsity:.2f}, fill={hf_fill:.2f})"
    elif (hf_floor_db < -108 and hf_sparsity > 0.35) or (hf_floor_db < -108 and hf_fill < 0.22):
        score = 0.55
        confidence = 0.55
        details = f"Low/sparse HF floor in quiet frames (hf_floor={hf_floor_db:.1f} dB, sparsity={hf_sparsity:.2f}, fill={hf_fill:.2f})"
    elif (hf_floor_db < -105 and hf_sparsity > 0.25) or (hf_floor_db < -105 and hf_fill < 0.30):
        score = 0.35
        confidence = 0.45
        details = f"Somewhat low HF floor in quiet frames (hf_floor={hf_floor_db:.1f} dB, sparsity={hf_sparsity:.2f}, fill={hf_fill:.2f})"
    else:
        score = 0.10
        confidence = 0.45
        details = f"HF floor looks stationary (hf_floor={hf_floor_db:.1f} dB, sparsity={hf_sparsity:.2f}, fill={hf_fill:.2f})"

    if verbose:
        details += (
            f" | HF band: {hf_high_start:.0f}-{hf_end:.0f} Hz"
            f"; quiet frames: {int(np.count_nonzero(quiet_mask))}"
            f"; very quiet frames: {int(np.count_nonzero(very_quiet_mask))}"
        )

    return AnalysisResult(name="HF Noise Floor", score=score, confidence=confidence, details=details), hf_floor_db, hf_sparsity, hf_fill


def analyze_hf_multiband_texture(
    y: NDArray[np.float32], sr: int, verbose: bool = False
) -> tuple[AnalysisResult, str | None, float | None, dict | None]:
    """
    Detect "striping/patchiness" in high-frequency bands during quiet/very-quiet frames.

    This targets what Spek often makes obvious even when average HF power looks similar:
    - Lossless sources often show a relatively stationary HF "haze" (consistent background).
    - Perceptual codecs may retain HF on transients but collapse HF background intermittently,
      creating a darker, patchier top band (low 10th percentile fill / larger dropouts).

    Returns (AnalysisResult, worst_band_label, worst_band_score, worst_band_details).
    """
    nyquist = sr / 2
    if nyquist < 12000:
        return (
            AnalysisResult(
                name="HF Texture",
                score=0.0,
                confidence=0.2,
                details=f"Sample rate too low for HF texture analysis (Nyquist={nyquist:.0f} Hz)",
            ),
            None,
            None,
            None,
        )

    magnitude, frequencies, _times = compute_spectrogram(y, sr, n_fft=8192, hop_length=1024)
    power = magnitude * magnitude
    peak_power = float(np.max(power))
    if peak_power <= 0:
        return (
            AnalysisResult(name="HF Texture", score=0.0, confidence=0.1, details="Could not analyze - silent audio"),
            None,
            None,
            None,
        )
    power_norm = power / (peak_power + 1e-20)

    mid_mask = (frequencies >= 2000) & (frequencies <= 10000)
    if np.count_nonzero(mid_mask) < 5:
        return (
            AnalysisResult(name="HF Texture", score=0.0, confidence=0.2, details="Not enough mid-band resolution"),
            None,
            None,
            None,
        )

    frame_mid = np.mean(power[mid_mask, :], axis=0)
    p1 = float(np.percentile(frame_mid, 1))
    p5 = float(np.percentile(frame_mid, 5))
    p25 = float(np.percentile(frame_mid, 25))
    if p25 <= 0:
        return (
            AnalysisResult(name="HF Texture", score=0.0, confidence=0.1, details="Could not analyze - silent/near-silent audio"),
            None,
            None,
            None,
        )

    quiet_mask = (frame_mid > p5) & (frame_mid <= p25)
    tiny_floor = max(p1 * 0.5, 1e-12)
    very_quiet_mask = (frame_mid > tiny_floor) & (frame_mid <= p5)
    mask_frames = quiet_mask | very_quiet_mask
    n_frames = int(np.count_nonzero(mask_frames))
    if n_frames < 30:
        return (
            AnalysisResult(
                name="HF Texture",
                score=0.0,
                confidence=0.2,
                details="Not enough quiet/very-quiet frames for HF texture analysis",
            ),
            None,
            None,
            None,
        )

    # Bands chosen to align with common Spek observations at 44.1k (top haze often >18k).
    # Clamp to Nyquist to keep working for other sample rates.
    band_edges = [
        (16000.0, 18000.0),
        (18000.0, 20000.0),
        (20000.0, nyquist * 0.98),
    ]

    def _band_metrics(f_lo: float, f_hi: float) -> dict | None:
        if f_lo >= nyquist:
            return None
        lo = f_lo
        hi = min(f_hi, nyquist * 0.98)
        if hi <= lo:
            return None
        band_mask = (frequencies >= lo) & (frequencies <= hi)
        if np.count_nonzero(band_mask) < 5:
            return None

        # "Fill" is Spek-like: fraction of bins above a very low threshold in dBFS.
        band_bins_dbfs = 10.0 * np.log10(power_norm[band_mask, :] + 1e-20)
        fill_per_frame = np.mean(band_bins_dbfs > -110.0, axis=0)

        fill_med = float(np.median(fill_per_frame[mask_frames]))
        fill_p10 = float(np.percentile(fill_per_frame[mask_frames], 10))
        fill_drop = float(fill_med - fill_p10)

        # Also track HF band floor for these frames
        frame_band_power = np.mean(power[band_mask, :], axis=0)
        band_db = 10.0 * np.log10(frame_band_power + 1e-20)
        band_db_med = float(np.median(band_db[mask_frames]))
        band_db_p10 = float(np.percentile(band_db[mask_frames], 10))
        band_db_drop = float(band_db_med - band_db_p10)

        return {
            "band_hz": (float(lo), float(hi)),
            "fill_median": fill_med,
            "fill_p10": fill_p10,
            "fill_drop": fill_drop,
            "hf_floor_db_median": band_db_med,
            "hf_floor_db_p10": band_db_p10,
            "hf_floor_db_drop": band_db_drop,
            "n_frames": n_frames,
        }

    best = None
    best_label = None
    best_score = -1.0

    for lo, hi in band_edges:
        m = _band_metrics(lo, hi)
        if not m:
            continue

        # Score: focus on *dropouts* (patchiness) and low fill in very top bands.
        # The thresholds are intentionally conservative; we only want to flag strong Spek-like striping.
        fill_med = m["fill_median"]
        fill_drop = m["fill_drop"]
        db_drop = m["hf_floor_db_drop"]

        score = 0.10
        confidence = 0.45

        # Strong patchiness: big dropout tail (fill collapse) + large dB-drop tail.
        # We intentionally require a *fill* tail collapse here to avoid false positives on real lossless
        # material where musical HF content is naturally intermittent (especially in 16–18 kHz).
        if fill_drop > 0.25 and db_drop > 8.0:
            score = 0.70
            confidence = 0.60
        elif fill_drop > 0.18 and db_drop > 6.0:
            score = 0.50
            confidence = 0.55
        elif fill_med < 0.25 and fill_drop > 0.10:
            # Consistently dark in this band
            score = 0.55
            confidence = 0.55

        # Prefer higher bands when scores tie (more indicative of codec behavior)
        label = f"{int(lo/1000)}–{int(min(hi, nyquist)/1000)} kHz"
        tie_break = lo / 1000.0
        if (score > best_score) or (score == best_score and (best is None or tie_break > best["band_hz"][0] / 1000.0)):
            best_score = score
            best = m
            best_label = label
            best_conf = confidence

    if best is None or best_label is None:
        return (
            AnalysisResult(name="HF Texture", score=0.0, confidence=0.2, details="Could not compute HF texture metrics"),
            None,
            None,
            None,
        )

    details = (
        f"Most suspicious HF band: {best_label} "
        f"(fill med/p10={best['fill_median']:.2f}/{best['fill_p10']:.2f}, Δfill={best['fill_drop']:.2f}; "
        f"HF floor med/p10={best['hf_floor_db_median']:.1f}/{best['hf_floor_db_p10']:.1f} dB, "
        f"ΔdB={best['hf_floor_db_drop']:.1f})"
    )
    if verbose:
        details += f" | quiet frames: {int(np.count_nonzero(quiet_mask))}; very quiet frames: {int(np.count_nonzero(very_quiet_mask))}"

    return (
        AnalysisResult(name="HF Texture", score=float(best_score), confidence=float(best_conf), details=details),
        best_label,
        float(best_score),
        best,
    )


def identify_codec(cutoff_hz: float | None, sr: int) -> tuple[str | None, int | None]:
    """
    Attempt to identify the source codec and bitrate from the frequency cutoff.
    """
    if cutoff_hz is None:
        return None, None

    # Match against known codec signatures
    best_match = None
    best_bitrate = None

    for _codec_id, signature in CODEC_SIGNATURES.items():
        if not signature.has_hard_cutoff:
            continue

        for bitrate, typical_cutoff in signature.typical_cutoffs:
            # Allow 500Hz tolerance
            if abs(cutoff_hz - typical_cutoff) < 500:
                best_match = signature.name
                best_bitrate = bitrate
                break

        if best_match:
            break

    # If no exact match, estimate from cutoff alone
    if best_match is None and cutoff_hz < sr / 2 - 1000:
        if cutoff_hz < 16500:
            best_match = "MP3 or AAC (low bitrate)"
            best_bitrate = 128
        elif cutoff_hz < 18000:
            best_match = "MP3 ~192kbps or AAC ~128kbps"
            best_bitrate = 192
        elif cutoff_hz < 19500:
            best_match = "MP3 ~256kbps or AAC ~192kbps"
            best_bitrate = 256
        elif cutoff_hz < 20500:
            best_match = "MP3 320kbps or high-bitrate AAC"
            best_bitrate = 320

    return best_match, best_bitrate


def analyze_file(file_path: str | Path, verbose: bool = False) -> TranscodeAnalysis:
    """
    Main analysis function - runs all detection techniques on an audio file.
    """
    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    # Load audio
    y, sr, metadata = load_audio(file_path)

    # Run all analyses
    results: list[AnalysisResult] = []

    # 1. Frequency cutoff detection
    cutoff_result = analyze_frequency_cutoff(y, sr, verbose)
    results.append(cutoff_result)

    # 2. Spectral flatness analysis
    flatness_result = analyze_spectral_flatness(y, sr, verbose)
    results.append(flatness_result)

    # 3. Pre-echo detection
    pre_echo_result = detect_pre_echo(y, sr, verbose)
    results.append(pre_echo_result)

    # 4. Spectral hole detection
    holes_result = detect_spectral_holes(y, sr, verbose)
    results.append(holes_result)

    # 5. HF noise floor / gating (helps with high-bitrate Vorbis/Opus where cutoffs aren't obvious)
    hf_noise_result, hf_floor_db, hf_sparsity, hf_fill = analyze_hf_noise_floor(y, sr, verbose)
    results.append(hf_noise_result)

    # 6. HF multiband texture / patchiness (captures Spek-visible striping)
    hf_texture_result, hf_texture_band, hf_texture_score, hf_texture_details = analyze_hf_multiband_texture(y, sr, verbose)
    results.append(hf_texture_result)

    # Extract specific findings
    detected_cutoff = None
    # Parse cutoff frequency from various detail message formats
    import re
    cutoff_patterns = [
        r"cutoff at (\d+)",           # "Hard cutoff at 20000 Hz"
        r"Cutoff at (\d+)",           # "Cutoff at 20000 Hz"
        r"range up to (\d+)",          # "effective range up to 20062 Hz"
        r"content up to (\d+)",        # "content up to 20000 Hz"
        r"ends at (\d+)",              # "content ends at 20062 Hz"
        r"spectrum up to (\d+)",       # "Full spectrum up to 22050 Hz"
    ]
    for pattern in cutoff_patterns:
        match = re.search(pattern, cutoff_result.details)
        if match:
            detected_cutoff = float(match.group(1))
            break

    # Identify probable codec
    suspected_codec, suspected_bitrate = identify_codec(detected_cutoff, sr)

    # Calculate weighted probability with emphasis on frequency cutoff
    # Frequency cutoff is the most reliable indicator - weight it 3x
    weights = {
        "Frequency Cutoff": 3.0,
        "Spectral Flatness": 1.0,
        "Pre-echo Detection": 1.0,
        "Spectral Holes": 1.0,
        "HF Noise Floor": 2.0,
        "HF Texture": 1.5,
    }

    total_weight = sum(r.confidence * weights.get(r.name, 1.0) for r in results)
    if total_weight > 0:
        probability = sum(r.score * r.confidence * weights.get(r.name, 1.0) for r in results) / total_weight
    else:
        probability = 0.0

    # Additionally, if ANY single test has very high score+confidence, set a floor
    # This prevents one strong signal from being diluted by weak signals
    for r in results:
        if r.score >= 0.8 and r.confidence >= 0.8:
            # Strong signal - probability should be at least this high
            floor = r.score * 0.9  # Slight reduction but still high
            probability = max(probability, floor)

    # Determine confidence level
    if probability > 0.70:
        confidence = Confidence.LOSSY_DETECTED
    elif probability > 0.45:
        confidence = Confidence.PROBABLY_LOSSY
    elif probability > 0.25:
        confidence = Confidence.UNCERTAIN
    else:
        confidence = Confidence.NO_ARTIFACTS

    # Extract spectral flatness score
    flatness_score = 0.0
    if "(" in flatness_result.details:
        try:
            flatness_score = float(flatness_result.details.split("(")[1].split(")")[0])
        except (IndexError, ValueError):
            pass

    return TranscodeAnalysis(
        file_path=file_path,
        confidence=confidence,
        probability_lossy=probability,
        detected_cutoff_hz=detected_cutoff,
        spectral_flatness_score=flatness_score,
        pre_echo_detected=pre_echo_result.score > 0.5,
        spectral_holes_detected=holes_result.score > 0.5,
        suspected_codec=suspected_codec,
        suspected_bitrate=suspected_bitrate,
        hf_noise_floor_db=hf_floor_db,
        hf_noise_sparsity=hf_sparsity,
        hf_noise_fill=hf_fill,
        hf_texture_band=hf_texture_band,
        hf_texture_score=hf_texture_score,
        hf_texture_details=hf_texture_details,
        individual_results=results,
        details={
            "sample_rate": sr,
            "nyquist_hz": sr / 2,
            "bit_depth": metadata.get("bit_depth", "Unknown"),
            "channels": metadata.get("channels", "Unknown"),
            "duration_seconds": metadata.get("duration_seconds", 0),
            "format": metadata.get("format", "Unknown"),
        },
    )


def plot_spectrogram(file_path: str | Path, output_path: str | Path | None = None) -> None:
    """
    Generate and optionally save a spectrogram visualization.
    """
    if not HAS_MATPLOTLIB:
        print("matplotlib not installed. Install with: pip install matplotlib")
        return

    y, sr, metadata = load_audio(file_path)
    magnitude, frequencies, times = compute_spectrogram(y, sr, n_fft=4096, hop_length=512)

    # Convert to dB
    magnitude_db = 20 * np.log10(magnitude + 1e-10)

    plt.figure(figsize=(14, 6))

    # Main spectrogram
    plt.subplot(1, 2, 1)
    plt.pcolormesh(times, frequencies, magnitude_db, shading="gouraud", cmap="magma")
    plt.colorbar(label="dB")
    plt.ylabel("Frequency (Hz)")
    plt.xlabel("Time (s)")
    plt.title(f"Spectrogram: {Path(file_path).name}")
    plt.ylim(0, sr / 2)

    # High frequency detail
    plt.subplot(1, 2, 2)
    hf_mask = frequencies > 10000
    plt.pcolormesh(times, frequencies[hf_mask], magnitude_db[hf_mask], shading="gouraud", cmap="magma")
    plt.colorbar(label="dB")
    plt.ylabel("Frequency (Hz)")
    plt.xlabel("Time (s)")
    plt.title("High Frequency Detail (>10kHz)")

    # Add Nyquist line
    plt.axhline(y=sr / 2, color="red", linestyle="--", alpha=0.5, label=f"Nyquist ({sr/2:.0f} Hz)")
    plt.legend()

    plt.tight_layout()

    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        print(f"Spectrogram saved to: {output_path}")
    else:
        plt.show()


def main():
    parser = argparse.ArgumentParser(
        description="Detect whether an audio file was transcoded from a lossy source",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s music.flac
  %(prog)s music.flac --verbose
  %(prog)s music.flac --plot
  %(prog)s music.flac --plot --save-plot spectrum.png
        """,
    )

    parser.add_argument("audio_file", type=Path, help="Path to the audio file to analyze")

    parser.add_argument("-v", "--verbose", action="store_true", help="Show detailed analysis results")

    parser.add_argument("-p", "--plot", action="store_true", help="Display spectrogram visualization")

    parser.add_argument("--save-plot", type=Path, metavar="PATH", help="Save spectrogram to file instead of displaying")

    parser.add_argument("-j", "--json", action="store_true", help="Output results as JSON")

    args = parser.parse_args()

    if not args.audio_file.exists():
        print(f"Error: File not found: {args.audio_file}", file=sys.stderr)
        sys.exit(1)

    try:
        result = analyze_file(args.audio_file, verbose=args.verbose)

        if args.json:
            import json

            output = {
                "file": str(result.file_path),
                "confidence": result.confidence.value,
                "probability_lossy": result.probability_lossy,
                "detected_cutoff_hz": result.detected_cutoff_hz,
                "suspected_codec": result.suspected_codec,
                "suspected_bitrate": result.suspected_bitrate,
                "pre_echo_detected": result.pre_echo_detected,
                "spectral_holes_detected": result.spectral_holes_detected,
                "hf_noise_floor_db": result.hf_noise_floor_db,
                "hf_noise_sparsity": result.hf_noise_sparsity,
                "hf_noise_fill": result.hf_noise_fill,
                "hf_texture_band": result.hf_texture_band,
                "hf_texture_score": result.hf_texture_score,
                "hf_texture_details": result.hf_texture_details,
                "details": result.details,
            }
            print(json.dumps(output, indent=2))
        else:
            result.print_report(verbose=args.verbose)

        if args.plot or args.save_plot:
            plot_spectrogram(args.audio_file, args.save_plot)

        # Exit code based on result
        if result.confidence in (Confidence.LOSSY_DETECTED, Confidence.PROBABLY_LOSSY):
            sys.exit(2)  # Likely lossy
        elif result.confidence == Confidence.UNCERTAIN:
            sys.exit(1)  # Uncertain
        else:
            sys.exit(0)  # Likely lossless

    except Exception as e:
        print(f"Error analyzing file: {e}", file=sys.stderr)
        if args.verbose:
            import traceback

            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

