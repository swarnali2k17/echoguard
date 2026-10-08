"""Run EchoGuard over the real-audio corpus: whole clip + 0.5 s + 0.2 s windows.

Outputs results.csv (one row per analysed segment) and clipinfo.csv (bandwidth / upsampling check).
"""
import csv
import os
import sys
import numpy as np
from scipy import signal as sps

from echoguard.audio import load_wav
from echoguard.pipeline import Pipeline

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "corpus")
MAX_WINDOWS = 5
WINDOWS = [0.5, 0.2]

pipe = Pipeline()
rows = list(csv.DictReader(open(os.path.join(CORPUS, "manifest.csv"))))
out = []
clipinfo = []


def analyse(sig, sr, meta, kind, start):
    rep = pipe.analyze(sig, sr)
    f = {x.name: x for x in rep.findings}
    e_oob, e_car, e_spec = f["out_of_band_energy"].evidence, f["carrier_peak"].evidence, f["spectral_profile"].evidence
    out.append(dict(meta, kind=kind, start_s=round(start, 3), seg_dur=round(len(sig) / sr, 3),
                    verdict=rep.verdict, overall=round(rep.overall_risk, 4),
                    risk_oob=round(f["out_of_band_energy"].risk, 4), risk_carrier=round(f["carrier_peak"].risk, 4),
                    risk_spectral=round(f["spectral_profile"].risk, 4),
                    r_oob=e_oob.get("out_of_band_ratio", float("nan")),
                    p_db=e_car.get("prominence_db", float("nan")), thr_db=e_car.get("noise_threshold_db", float("nan")),
                    excess_db=e_car.get("excess_db", float("nan")), peak_hz=e_car.get("peak_freq_hz", float("nan")),
                    hb_frac=e_car.get("high_band_fraction", float("nan")), rolloff_hz=e_spec.get("rolloff_99_hz", float("nan")),
                    rms_dbfs=round(20 * np.log10(np.sqrt(np.mean(sig ** 2)) + 1e-12), 1)))


def bandwidth_check(sig, sr):
    """Find the largest drop between adjacent 1 kHz bands of the median PSD (brick-wall detector)."""
    f, p = sps.welch(sig, fs=sr, nperseg=4096)
    pdb = 10 * np.log10(p + 1e-30)
    edges = np.arange(0, sr / 2 + 1, 1000.0)
    med = np.array([np.median(pdb[(f >= lo) & (f < hi)]) if np.any((f >= lo) & (f < hi)) else np.nan
                    for lo, hi in zip(edges[:-1], edges[1:])])
    best = (0.0, None)
    for i in range(4, len(med) - 1):
        if np.isnan(med[i]) or np.isnan(med[i + 1]):
            continue
        below = np.nanmin(med[max(0, i - 3):i + 1])
        above = np.nanmax(med[i + 1:])
        drop = below - above
        if drop > best[0]:
            best = (drop, edges[i + 1])
    # dynamic range: peak band vs floor band
    return best[0], best[1], float(np.nanmax(med) - np.nanmin(med))


for r in rows:
    path = os.path.join(CORPUS, r["file"])
    sig, sr = load_wav(path)
    meta = {k: r[k] for k in ("file", "category", "source", "license", "sample_rate", "duration_s")}
    analyse(sig, sr, meta, "full", 0.0)
    for w in WINDOWS:
        n = int(w * sr)
        if len(sig) < n:
            continue
        nwin = min(MAX_WINDOWS, len(sig) // n)
        starts = np.linspace(0, len(sig) - n, nwin).astype(int)
        for s in starts:
            seg = sig[s:s + n]
            if np.sqrt(np.mean(seg ** 2)) < 1e-5:
                continue
            analyse(seg, sr, meta, f"win{w}", s / sr)
    drop, fcut, dr = bandwidth_check(sig, sr)
    clipinfo.append(dict(file=r["file"], category=r["category"], sr=sr, max_drop_db=round(drop, 1),
                         drop_above_hz=fcut, dyn_range_db=round(dr, 1)))

with open(os.path.join(HERE, "results.csv"), "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(out[0].keys()))
    w.writeheader(); w.writerows(out)
with open(os.path.join(HERE, "clipinfo.csv"), "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(clipinfo[0].keys()))
    w.writeheader(); w.writerows(clipinfo)
print("segments", len(out), "clips", len(clipinfo))
