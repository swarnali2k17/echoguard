"""Synthetic signal generators for testing the detectors.

IMPORTANT: these are detector-validation fixtures only. They produce generic
signals with particular *spectral shapes* (band-limited noise, a high-frequency
tone, an amplitude-modulated carrier). They contain no speech, no command, and
no recoverable content, and cannot be used to command any device. Their sole
purpose is to check that EchoGuard's detectors fire on the right spectral
signatures and stay quiet on benign audio.
"""

from __future__ import annotations

import numpy as np
from scipy.io import wavfile
from scipy import signal as sps


def _bandlimited_noise(duration: float, sample_rate: int, low: float, high: float, seed: int = 0):
    rng = np.random.default_rng(seed)
    n = int(duration * sample_rate)
    noise = rng.standard_normal(n)
    sos = sps.butter(6, [low, high], btype="bandpass", fs=sample_rate, output="sos")
    return sps.sosfilt(sos, noise)


def benign_speechlike(duration: float = 1.0, sample_rate: int = 48_000, seed: int = 1) -> np.ndarray:
    """Speech-like audio: energy confined to the voice band (~150 Hz - 4 kHz)."""
    sig = _bandlimited_noise(duration, sample_rate, 150.0, 4_000.0, seed)
    return _normalise(sig)


def out_of_band(duration: float = 1.0, sample_rate: int = 48_000, tone_hz: float = 20_000.0,
                seed: int = 2, mod_depth: float = 0.9) -> np.ndarray:
    """Benign-looking base plus a strong carrier above 18 kHz (injection signature).

    The carrier is AM-modulated by speech-bandwidth noise (150 Hz-4 kHz), as an
    injected command would modulate it, so it carries sidebands. A bare tone
    is a beacon, and the carrier detector treats it as one.
    """
    base = _bandlimited_noise(duration, sample_rate, 150.0, 4_000.0, seed) * 0.3
    n = int(duration * sample_rate)
    t = np.arange(n) / sample_rate
    mod = _bandlimited_noise(duration, sample_rate, 150.0, 4_000.0, seed + 1000)
    mod = mod / (np.max(np.abs(mod)) or 1.0)
    tone = (1.0 + mod_depth * mod) * np.sin(2 * np.pi * tone_hz * t)
    return _normalise(base + tone)


def modulated_carrier(duration: float = 1.0, sample_rate: int = 48_000, carrier_hz: float = 21_000.0,
                      mod_hz: float = 1_000.0) -> np.ndarray:
    """An amplitude-modulated high-frequency carrier (carrier-peak signature).

    This is a pure AM test tone - a carrier whose amplitude varies at mod_hz.
    It carries no information payload; it only reproduces the narrowband-high-
    peak-with-sidebands spectral shape the carrier detector looks for.
    """
    n = int(duration * sample_rate)
    t = np.arange(n) / sample_rate
    envelope = 0.5 * (1.0 + np.sin(2 * np.pi * mod_hz * t))
    carrier = np.sin(2 * np.pi * carrier_hz * t)
    return _normalise(envelope * carrier)


def _normalise(sig: np.ndarray) -> np.ndarray:
    peak = float(np.max(np.abs(sig))) if sig.size else 1.0
    if peak == 0:
        peak = 1.0
    return (sig / peak * 0.9).astype(np.float32)


def write_wav(path: str, sig: np.ndarray, sample_rate: int) -> None:
    """Write a float signal to a 16-bit PCM WAV file."""
    pcm = np.clip(sig, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16)
    wavfile.write(path, sample_rate, pcm)
