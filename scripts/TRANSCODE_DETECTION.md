# Audio Transcode Detection

A standalone tool for detecting whether a lossless audio file (FLAC, WAV, AIFF, etc.) was likely transcoded from a lossy source (MP3, AAC, Vorbis, Opus).

## Quick Start

```bash
# Install dependencies
pip install numpy scipy soundfile matplotlib

# Analyze a file
python scripts/detect_transcode.py music.flac

# With spectrogram visualization
python scripts/detect_transcode.py music.flac --plot

# Verbose output
python scripts/detect_transcode.py music.flac --verbose

# JSON output for scripting
python scripts/detect_transcode.py music.flac --json
```

## How It Works

The script uses multiple detection techniques to identify lossy compression artifacts:

### 1. Frequency Cutoff Detection

Lossy codecs like MP3 and AAC use a **low-pass filter** to remove high frequencies that are "less important" to human hearing. This creates a characteristic "shelf" or hard cutoff in the spectrum:

| Codec | Bitrate | Typical Cutoff |
|-------|---------|----------------|
| MP3   | 128 kbps | ~16.0 kHz |
| MP3   | 192 kbps | ~18.5 kHz |
| MP3   | 256 kbps | ~19.5 kHz |
| MP3   | 320 kbps | ~20.5 kHz |
| AAC   | 128 kbps | ~15.5 kHz |
| AAC   | 256 kbps | ~19.5 kHz |

**Modern codecs like Opus and Vorbis** do NOT have hard cutoffs—they use psychoacoustic modeling that preserves high frequencies, making them much harder to detect.

### 2. Spectral Flatness Analysis

Lossy compression tends to "smooth out" the spectral content, especially in quiet sections. Genuine lossless audio typically has more variation in spectral flatness over time.

### 3. Pre-echo Detection

All modern lossy codecs (MP3, AAC, Vorbis, Opus) use the **MDCT (Modified Discrete Cosine Transform)**, which processes audio in overlapping blocks. This can cause **pre-echo**—a faint "ghost" of loud sounds appearing slightly before the actual transient.

This is most noticeable on:
- Drum hits
- Percussive sounds
- Sharp attacks after silence

### 4. Spectral Hole Detection

Lossy codecs use **psychoacoustic masking** to remove frequencies that would be "masked" (made inaudible) by louder nearby frequencies. This can leave unnatural gaps in the spectrum.

### 5. High-Frequency (HF) Noise-Floor / “Haze” Detection (Spek-style cue)

When you look at a spectrogram in tools like **Spek**, genuine lossless sources (especially CD rips or anything that went through an analog chain) often show a faint, fairly uniform **blue/purple “haze”** near the very top of the spectrum (e.g. ~19–22 kHz at 44.1 kHz sample rate).

That haze is typically **low-level wideband energy**:

- Dither/noise shaping from bit-depth conversion
- Tape/analog hiss or converter noise
- Accumulated low-level noise in the mastering chain

High-bitrate perceptual codecs (Vorbis/Opus) often preserve HF energy on transients, but can **suppress or quantize** that very-low-level HF noise in quieter sections, making the HF background look **darker/sparser** even if there’s no obvious hard cutoff.

This detector therefore measures:

- **HF noise floor in quiet frames**: median HF-band power when the mid-band is quiet
- **HF fill**: how “filled in” the upper HF band is (fraction of bins above a very low dBFS threshold)

This is **not a proof** of lossy vs lossless (some masters are genuinely “clean” in HF), but it’s a useful indicator for cases like Spotify 320 kbps Vorbis captured into FLAC.

## Output Interpretation

### Confidence Levels

| Level | Meaning |
|-------|---------|
| `DEFINITELY_LOSSY` | Strong evidence of lossy compression (probability > 75%) |
| `PROBABLY_LOSSY` | Likely compressed (probability 50-75%) |
| `UNCERTAIN` | Cannot determine with confidence (probability 35-50%) |
| `PROBABLY_LOSSLESS` | Likely authentic lossless (probability 15-35%) |
| `DEFINITELY_LOSSLESS` | No compression artifacts detected (probability < 15%) |

### Exit Codes

- `0` - Probably lossless
- `1` - Uncertain
- `2` - Probably lossy

### Example Output

```
=================================================================
TRANSCODE ANALYSIS: suspicious_file.flac
=================================================================

Verdict: PROBABLY LOSSY
Lossy Probability: 67.3%
Suspected Source: MP3 ~192kbps or AAC ~128kbps

Detection Results:
  • Frequency Cutoff: 18432 Hz (⚠️  SUSPICIOUS)
  • Spectral Flatness Variation: 0.0234 (⚠️  Low variation (suspicious))
  • Pre-echo Artifacts: ✓ No
  • Spectral Holes: ✓ No

File Information:
  • Sample Rate: 44100 Hz
  • Bit Depth: 16 bits
  • Channels: 2
  • Duration: 234.5s
  • Nyquist Frequency: 22050 Hz
```

## Limitations

### What This Tool Can Detect

✅ MP3 at most bitrates (especially < 256 kbps)  
✅ AAC-LC at most bitrates  
✅ Low-bitrate Vorbis (< 160 kbps)  
✅ Re-encoding (lossy → lossy → lossless)  

### What This Tool Cannot Reliably Detect

❌ High-bitrate Opus (> 128 kbps) — nearly transparent  
❌ High-bitrate Vorbis (> 256 kbps) — excellent HF preservation  
❌ AAC-HE with SBR (Spectral Band Replication reconstructs HF)  
❌ Lossy-mastered originals that never had HF content  

### False Positives

Some legitimate scenarios may trigger false positives:
- **Vintage recordings** — Original masters from tape may naturally lack HF
- **Heavily processed audio** — Heavy limiting/compression affects spectral flatness
- **Low sample rate sources** — 32kHz DAT recordings have 16kHz Nyquist

### False Negatives

Some transcodes may not be detected:
- **Opus 128+ kbps** — Virtually indistinguishable from lossless
- **Lossless from lossy master** — If the studio master was already lossy

## Advanced Usage

### Batch Processing

```bash
# Analyze all FLAC files and find suspicious ones
for f in *.flac; do
    python scripts/detect_transcode.py "$f" --json 2>/dev/null | \
        jq -r 'select(.probability_lossy > 0.5) | .file'
done
```

### Integration with flacfetch

This tool can be used to verify downloads:

```python
from pathlib import Path
import subprocess
import json

def verify_download(audio_path: Path) -> bool:
    """Return True if file appears to be genuine lossless."""
    result = subprocess.run(
        ["python", "scripts/detect_transcode.py", str(audio_path), "--json"],
        capture_output=True,
        text=True
    )
    
    if result.returncode == 0:
        return True  # Probably lossless
    
    data = json.loads(result.stdout)
    if data["probability_lossy"] > 0.6:
        print(f"WARNING: {audio_path.name} appears to be transcoded from {data['suspected_codec']}")
        return False
    
    return True
```

## Technical Background

### Why Frequency Cutoffs Exist

MP3 and AAC encoders allocate bits to frequency bands based on psychoacoustic importance. At lower bitrates, high frequencies get fewer bits and are eventually cut entirely:

```
Bitrate allocation (simplified):
┌─────────────────────────────────────────┐
│ Low frequencies (bass)      [many bits] │
│ Mid frequencies (vocals)    [many bits] │
│ High frequencies (cymbals)  [few bits]  │
│ Very high freq (>16kHz)     [cut off]   │
└─────────────────────────────────────────┘
```

### The MDCT Problem

MDCT processes audio in overlapping 1024 or 2048 sample blocks. When a loud transient occurs:

```
Block N-1: [quiet quiet quiet LOUD]  ← Codec sees this block
Block N:   [LOUD loud quiet quiet]   ← And this block

Problem: Energy from LOUD leaks into the reconstruction of quiet samples
Result: Pre-echo before the transient
```

Modern codecs use **window switching** and **temporal noise shaping** to minimize this, but artifacts can remain.

## References

- Kim & Rafii (2018) - "Lossy Audio Compression Identification" - EUSIPCO
- ICASSP 2017 - "Codec Independent Lossy Audio Compression Detection"  
- PANNs: Large-Scale Pretrained Audio Neural Networks (arxiv:1912.10211)
- HuggingFace Encoding-Detection Dataset

## Future Improvements

For even better detection (especially of modern codecs), consider:

1. **Machine Learning Approach** — Train a CNN on spectrograms from the Encoding-Detection dataset
2. **MDCT coefficient analysis** — Look for quantization patterns in the transform domain
3. **Frame boundary detection** — Search for codec-specific block sizes (1152 for MP3, 1024 for AAC)

