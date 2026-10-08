"""Synthetic sensitivity analysis for EchoGuard's detector.

This characterises the detector by sweeping controlled SYNTHETIC probe signals
and measuring where it flags and where it doesn't. It is NOT a real-attack
benchmark and makes NO claim about real-world detection: the probes are generic
tones over a benign base, with no voice command and nothing that drives a
device. Their only purpose is to map the detector's response surface - in
particular, to quantify the known weak-carrier blind spot of the corroboration
rule (an attack must show out-of-band energy AND a carrier to be flagged).

Two sweeps:
  A) carrier strength   - how strong the high-frequency component must be before
                          the detector flags it (the sensitivity floor).
  B) carrier frequency  - which part of the band the detector actually covers
                          (exposes the gap just below the 18 kHz out-of-band cut).

Real-attack detection rate still requires the capture lab (see the Lab Plan).
"""

from __future__ import annotations

import csv
import os
import numpy as np
from scipy import signal as sps

from echoguard.pipeline import Pipeline, SUSPICIOUS, HIGH_RISK

SR = 48_000
DURATION = 1.0
FLAGGED = {SUSPICIOUS, HIGH_RISK}
_pipe = Pipeline()


def _speech_base(seed):
    rng = np.random.default_rng(seed)
    n = int(DURATION * SR)
    sos = sps.butter(6, [150, 3800], btype="bandpass", fs=SR, output="sos")
    return sps.sosfilt(sos, rng.standard_normal(n))


def _probe(alpha, fc, seed):
    """Benign speech-band base + a high-frequency carrier scaled by alpha.

    The carrier is AM-modulated (depth 0.9) by a speech-bandwidth signal, as an
    injected command would modulate it. An unmodulated tone is a beacon, not an
    attack, and since v0.2.0 the carrier detector treats it as one.
    """
    base = _speech_base(seed)
    base = base / (np.max(np.abs(base)) or 1.0)
    mod = _speech_base(seed + 1000)
    mod = mod / (np.max(np.abs(mod)) or 1.0)
    t = np.arange(int(DURATION * SR)) / SR
    carrier = (1.0 + 0.9 * mod) * np.sin(2 * np.pi * fc * t)
    sig = base + alpha * carrier
    return (sig / (np.max(np.abs(sig)) or 1.0) * 0.9).astype(np.float32)


def _detection_rate(alpha, fc, n=10):
    hits = 0
    for seed in range(n):
        v = _pipe.analyze(_probe(alpha, fc, seed + 1), SR).verdict
        hits += 1 if v in FLAGGED else 0
    return hits / n


def sweep_strength(fc=21_000.0):
    alphas = np.logspace(-2, np.log10(2.0), 14)
    return [(round(float(a), 4), _detection_rate(a, fc)) for a in alphas]


def sweep_frequency(alpha=0.3):
    freqs = list(range(15_000, 24_000, 1_000))
    return [(f, _detection_rate(alpha, float(f))) for f in freqs]


def main(out_dir="benchmark"):
    os.makedirs(out_dir, exist_ok=True)
    strength = sweep_strength()
    freq = sweep_frequency()

    with open(os.path.join(out_dir, "sensitivity_strength.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["carrier_alpha", "detection_rate"]); w.writerows(strength)
    with open(os.path.join(out_dir, "sensitivity_frequency.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["carrier_freq_hz", "detection_rate"]); w.writerows(freq)

    # find the sensitivity floor: smallest alpha with >=50% detection
    floor = next((a for a, r in strength if r >= 0.5), None)
    print("carrier-strength sweep (alpha -> detection):")
    for a, r in strength:
        print(f"  alpha={a:<7} detection={r:.0%}")
    print(f"sensitivity floor (>=50% detection): alpha = {floor}")
    print("\ncarrier-frequency sweep (Hz -> detection):")
    for fhz, r in freq:
        print(f"  {fhz} Hz  detection={r:.0%}")
    return strength, freq, floor


if __name__ == "__main__":
    main()
