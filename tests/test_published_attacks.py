"""Behaviour the published attacks demanded (SWhisper, MetaAttack), on synthetic signals.

The recordings themselves are private research data and never enter the repo;
these signals reproduce the physics that matters:
  - a ~16 kHz carrier amplitude-modulated by speech (SWhisper puts its carrier
    in the 15-18 kHz band, below the 18 kHz out-of-band edge);
  - a capture whose band above 18 kHz was never recorded (MetaAttack's 40 kHz
    carrier through a 48 kHz capture and an AAC encode; a "48 kHz" file holding
    16 kHz audio).
"""

import numpy as np
from scipy import signal as sps

from echoguard.pipeline import CLEAR, HIGH_RISK, INSUFFICIENT_DATA, SUSPICIOUS, Pipeline

SR = 48_000


def _speech_envelope_noise(n: int, rng) -> np.ndarray:
    """Speech-band noise (150 Hz - 4 kHz) switched on and off at a syllable rate, like a command."""
    sos = sps.butter(4, [150.0, 4_000.0], btype="bandpass", fs=SR, output="sos")
    x = sps.sosfilt(sos, rng.standard_normal(n))
    t = np.arange(n) / SR
    syllables = np.clip(np.sin(2 * np.pi * 3.5 * t), 0, None) ** 1.5
    return x / np.std(x) * syllables


def _near_ultrasonic_injection(seconds: float = 3.0, fc: float = 16_000.0, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    mod = _speech_envelope_noise(n, rng)
    x = (1.0 + 0.6 * mod / np.abs(mod).max()) * np.sin(2 * np.pi * fc * t)
    x = x + 10 ** (-50 / 20) * rng.standard_normal(n)          # room and microphone noise
    return 0.7 * x / np.abs(x).max()


def _beacon(seconds: float = 3.0, fc: float = 16_000.0, snr_db: float = 30.0, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    x = np.sin(2 * np.pi * fc * np.arange(n) / SR) + 10 ** (-snr_db / 20) * rng.standard_normal(n)
    return 0.7 * x / np.abs(x).max()


def test_speech_modulated_carrier_below_18khz_is_flagged():
    w = Pipeline().analyze_windows(_near_ultrasonic_injection(), SR)
    assert w.summary.verdict in (SUSPICIOUS, HIGH_RISK)
    near = next(f for f in w.summary.findings if f.name == "near_ultrasonic_carrier")
    assert near.risk >= 0.33 and 15_000 <= near.evidence["carrier_hz"] < 18_000
    assert near.evidence["fluctuation_f"] > near.evidence["fluctuation_f_crit"]


def test_steady_tone_below_18khz_is_a_beacon():
    for fc in (15_000.0, 15_734.0, 16_000.0, 17_000.0):
        for snr in (40.0, 20.0):
            w = Pipeline().analyze_windows(_beacon(fc=fc, snr_db=snr), SR)
            assert w.summary.verdict == CLEAR, (fc, snr, w.summary.verdict)


def test_capture_without_the_ultrasonic_band_cannot_be_cleared():
    rng = np.random.default_rng(3)
    n = 2 * SR
    # 16 kHz audio placed in a 48 kHz container: speech up to ~7.5 kHz, then only a flat floor
    sos16 = sps.butter(4, [150.0, 7_000.0], btype="bandpass", fs=16_000, output="sos")
    speech16 = sps.sosfilt(sos16, rng.standard_normal(n // 3))
    up = sps.resample_poly(speech16, 3, 1)[:n]
    up = 0.5 * up / np.abs(up).max() + 10 ** (-95 / 20) * rng.standard_normal(len(up))
    r = Pipeline().analyze(up, SR)
    assert r.verdict == INSUFFICIENT_DATA, r.verdict
    oob = next(f for f in r.findings if f.name == "out_of_band_energy")
    assert not oob.assessable and oob.evidence["content_cutoff_hz"] is not None

    # an AAC-style encode: the band above 18 kHz is digital silence
    x = _speech_envelope_noise(n, rng) + 10 ** (-80 / 20) * rng.standard_normal(n)
    sos = sps.ellip(12, 0.1, 140, 17_500, fs=SR, output="sos")
    x = sps.sosfiltfilt(sos, 0.5 * x / np.abs(x).max())
    assert Pipeline().analyze(x, SR).verdict == INSUFFICIENT_DATA


def test_ordinary_capture_with_a_noise_floor_is_still_cleared():
    rng = np.random.default_rng(4)
    n = 2 * SR
    sos = sps.butter(2, [150.0, 4_000.0], btype="bandpass", fs=SR, output="sos")
    x = sps.sosfilt(sos, rng.standard_normal(n))
    x = 0.5 * x / np.abs(x).max() + 10 ** (-90 / 20) * rng.standard_normal(n)
    assert Pipeline().analyze(x, SR).verdict == CLEAR
