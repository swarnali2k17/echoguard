"""Shared harness for red-teaming EchoGuard's ultrasonic detector."""
from __future__ import annotations
import math
import numpy as np
from scipy import signal as sps

from echoguard.pipeline import Pipeline, SUSPICIOUS, HIGH_RISK, INSUFFICIENT_DATA

FLAGGED = {SUSPICIOUS, HIGH_RISK}
_PIPE = Pipeline()


def analyze(sig, sr):
    """Run the pipeline and pull out the numbers that drive the verdict.

    R = sqrt(rho_A * rho_B); rho_A = clip(r/0.10), rho_B = clip(excess_db/30).
    """
    rep = _PIPE.analyze(np.asarray(sig, dtype=np.float64), sr)
    ev = {f.name: f for f in rep.findings}
    oob = ev.get("out_of_band_energy")
    car = ev.get("carrier_peak")
    r = oob.evidence.get("out_of_band_ratio") if oob else None
    rho_A = oob.risk if oob else 0.0
    excess = car.evidence.get("excess_db") if car else None
    rho_B = car.risk if car else 0.0
    return {
        "verdict": rep.verdict,
        "R": rep.overall_risk,
        "flagged": rep.verdict in FLAGGED,
        "insufficient": rep.verdict == INSUFFICIENT_DATA,
        "r_oob": r,              # fraction of energy above 18 kHz
        "rho_A": rho_A,
        "excess_db": excess,     # carrier prominence above CFAR threshold
        "rho_B": rho_B,
        "peak_hz": car.evidence.get("peak_freq_hz") if car else None,
    }


def summarize(rows):
    """Mean over seed-rows."""
    R = np.array([x["R"] for x in rows])
    det = np.mean([x["flagged"] for x in rows])
    insuf = np.mean([x["insufficient"] for x in rows])
    def mean_of(key):
        vals = [x[key] for x in rows if x[key] is not None]
        return float(np.mean(vals)) if vals else float("nan")
    return {
        "n": len(rows),
        "det_rate": float(det),
        "insuf_rate": float(insuf),
        "R_mean": float(np.mean(R)),
        "R_max": float(np.max(R)),
        "rho_A_mean": mean_of("rho_A"),
        "rho_B_mean": mean_of("rho_B"),
        "r_oob_mean": mean_of("r_oob"),
        "excess_db_mean": mean_of("excess_db"),
    }


def adc_capture(x, fs_in, fs_target, order=8):
    """Model a real device ADC: steep anti-alias low-pass then resample.

    8th-order Butterworth (zero-phase) at 0.45*fs_target, then polyphase resample.
    """
    wc = 0.45 * fs_target
    sos = sps.butter(order, wc / (fs_in / 2.0), btype="low", output="sos")
    xf = sps.sosfiltfilt(sos, x)
    g = math.gcd(int(round(fs_in)), int(round(fs_target)))
    up = int(round(fs_target)) // g
    down = int(round(fs_in)) // g
    y = sps.resample_poly(xf, up, down)
    return y.astype(np.float64)


def band_energy(x, sr, lo, hi):
    f, p = sps.welch(x, fs=sr, nperseg=min(len(x), 8192))
    m = (f >= lo) & (f < hi)
    if not np.any(m):
        return 0.0
    return float(np.trapezoid(p[m], f[m]))


def norm(x):
    peak = float(np.max(np.abs(x))) or 1.0
    return (x / peak * 0.9).astype(np.float64)
