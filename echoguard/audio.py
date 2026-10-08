"""Audio loading helpers.

Loads a WAV file into a mono float32 signal in [-1, 1] together with its true
sample rate. The sample rate matters: detecting ultrasonic / near-ultrasonic
energy requires a capture whose Nyquist frequency reaches into that band
(>= ~36 kHz sample rate to see 18 kHz). EchoGuard reports honestly when the
capture bandwidth is too low to assess the high-frequency bands.
"""

from __future__ import annotations

import numpy as np
from scipy.io import wavfile


def load_wav(path: str) -> tuple[np.ndarray, int]:
    """Load a WAV file as (mono float32 signal in [-1, 1], sample_rate)."""
    sample_rate, data = wavfile.read(path)
    return to_float_mono(data), int(sample_rate)


def to_float_mono(data: np.ndarray) -> np.ndarray:
    """Convert raw WAV samples to a mono float32 signal normalised to [-1, 1].

    Integer PCM is scaled by the full range of its dtype; floating-point WAV
    is assumed already near [-1, 1] and only hard-clipped if out of range.
    """
    data = np.asarray(data)

    # Mix down to mono if multi-channel.
    if data.ndim > 1:
        data = data.mean(axis=1)

    if np.issubdtype(data.dtype, np.unsignedinteger):
        # WAV stores 8-bit PCM unsigned with silence at the mid-point.
        info = np.iinfo(data.dtype)
        mid = (info.max + 1) / 2.0
        data = (data.astype(np.float64) - mid) / mid
    elif np.issubdtype(data.dtype, np.integer):
        info = np.iinfo(data.dtype)
        # Use the larger magnitude of the signed range as the normaliser.
        scale = float(max(abs(info.min), abs(info.max)))
        data = data.astype(np.float64) / scale
    else:
        data = data.astype(np.float64)
        peak = float(np.max(np.abs(data))) if data.size else 0.0
        if peak > 1.0:
            data = data / peak

    return np.clip(data, -1.0, 1.0).astype(np.float32)
