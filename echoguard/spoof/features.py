"""Lightweight spectral features for anti-spoofing (numpy/scipy only).

Per-frame spectral descriptors known to differ between genuine speech and
replayed/synthetic speech (centroid, bandwidth, roll-off, flatness, band-energy
ratios, zero-crossing rate), aggregated to a fixed-length mean+std vector.
This is a transparent baseline feature set, not a learned front-end.

Every clip is resampled to FEATURE_RATE first. The descriptors are absolute-
frequency quantities, so without this a corpus whose genuine and spoofed
clips come at different sample rates is separable by the rate alone - the
confound documented in PILOT_SPOOF.md.
"""

from __future__ import annotations

import numpy as np
from scipy import signal as sps

from ..audio import to_float_mono

FEATURE_RATE = 16_000


def _frames(sig, sr, win_ms=25.0, hop_ms=10.0):
    w = int(sr * win_ms / 1000)
    h = int(sr * hop_ms / 1000)
    if len(sig) < w:
        sig = np.pad(sig, (0, w - len(sig)))
    idx = range(0, len(sig) - w + 1, h)
    window = np.hanning(w)
    return np.array([sig[i:i + w] * window for i in idx])


def _frame_descriptors(frame, sr):
    spec = np.abs(np.fft.rfft(frame)) + 1e-12
    freqs = np.fft.rfftfreq(len(frame), 1 / sr)
    power = spec ** 2
    total = power.sum()
    centroid = float((freqs * power).sum() / total)
    bandwidth = float(np.sqrt(((freqs - centroid) ** 2 * power).sum() / total))
    cumulative = np.cumsum(power) / total
    rolloff = float(freqs[np.searchsorted(cumulative, 0.85)])
    flatness = float(np.exp(np.log(spec).mean()) / spec.mean())
    zcr = float(np.mean(np.abs(np.diff(np.sign(frame))) > 0))
    nyq = sr / 2

    def band(lo, hi):
        m = (freqs >= lo) & (freqs < hi)
        return float(power[m].sum() / total) if m.any() else 0.0

    return [centroid, bandwidth, rolloff, flatness, zcr,
            band(0, 1000), band(1000, 4000), band(4000, min(8000, nyq))]


def extract_features(sig, sr: int) -> np.ndarray:
    """Return a fixed-length feature vector (mean+std of per-frame descriptors).

    Accepts mono or multi-channel input at any sample rate; resamples to 16 kHz.
    Raises ValueError on empty or non-finite input.
    """
    sig = to_float_mono(np.asarray(sig)).astype(np.float64)
    if sig.size == 0:
        raise ValueError("empty signal")
    if not np.all(np.isfinite(sig)):
        raise ValueError("signal contains NaN or infinite samples")
    sr = int(sr)
    if sr != FEATURE_RATE:
        g = np.gcd(sr, FEATURE_RATE)
        sig = sps.resample_poly(sig, FEATURE_RATE // g, sr // g)
        sr = FEATURE_RATE
    peak = np.max(np.abs(sig)) if sig.size else 0.0
    if peak > 0:
        sig = sig / peak
    frames = _frames(sig, sr)
    desc = np.array([_frame_descriptors(f, sr) for f in frames])
    return np.concatenate([desc.mean(axis=0), desc.std(axis=0)])
