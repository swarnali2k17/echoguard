"""Benign ultrasonic beacons: false alarms vs capture noise.

Usage:
    python benchmark/beacon_fp.py              # 19-22 kHz x 40-20 dB SNR, 40 seeds each
    python benchmark/beacon_fp.py --seeds 10   # quicker

Steady unmodulated ultrasound is common and benign: retail analytics beacons
around 19 kHz, switch-mode power-supply whine at 20-22 kHz, some monitors and
TVs. `carrier_peak` is meant to recognise these as bare tones and hold the
verdict down, which it does while the tone is clean.

This harness measures what happens when the capture is noisy. The bare-tone
test compares sideband energy against a local noise floor, and the estimate is
clamped at zero (`sideband_ratio_db`). On a noisy capture that quantity is
dominated by the noise rather than by modulation, and the clamp keeps only its
positive excursions - so part of the time a bare tone reads as a modulated
carrier. The verdict for one beacon then depends on the noise realisation.

These are synthetic tones, not recordings: the figure characterises the
detector's modulation test, and is not a false-positive rate for real rooms.
"""

from __future__ import annotations

import argparse
from collections import Counter

import numpy as np

from echoguard.pipeline import Pipeline, SUSPICIOUS, HIGH_RISK

FLAGGED = {SUSPICIOUS, HIGH_RISK}
SR = 48_000
CARRIERS_HZ = (19_000, 20_000, 21_000, 22_000)
SNRS_DB = (40, 35, 30, 25, 20)


def beacon(freq_hz: float, snr_db: float, seed: int, secs: float = 2.0) -> np.ndarray:
    """An unmodulated tone plus broadband noise at the given SNR. Benign by construction."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(SR * secs)) / SR
    x = np.sin(2 * np.pi * freq_hz * t) + 10 ** (-snr_db / 20.0) * rng.standard_normal(len(t))
    return 0.7 * x / (np.abs(x).max() + 1e-12)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="False alarms on benign unmodulated ultrasound.")
    ap.add_argument("--seeds", type=int, default=40, help="noise realisations per cell (default 40)")
    args = ap.parse_args(argv)

    pipeline = Pipeline()
    total = flagged = 0
    print(f"{'carrier':>9} {'SNR dB':>7} {'flagged':>9}   verdicts")
    print("-" * 64)
    for freq in CARRIERS_HZ:
        for snr in SNRS_DB:
            counts = Counter(
                pipeline.analyze(beacon(freq, snr, seed), SR).verdict
                for seed in range(args.seeds)
            )
            hits = sum(counts[v] for v in FLAGGED)
            total += args.seeds
            flagged += hits
            mix = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
            print(f"{freq:>9} {snr:>7} {hits:>4}/{args.seeds:<4} {mix}")
    rate = 100.0 * flagged / total if total else 0.0
    print("-" * 64)
    print(f"benign beacons flagged: {flagged}/{total} = {rate:.0f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
