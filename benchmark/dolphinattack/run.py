"""Run EchoGuard over the public DolphinAttack demo dataset and characterise its baseband.

The dataset (USSLab, Zhejiang University; Zhang et al., CCS 2017) is 2,934
recordings of inaudible commands captured by the victim phone's own
microphone - i.e. already demodulated by the device and stored at 16 kHz.
See README.md in this directory for how to obtain it. It is not redistributed
here and carries no stated licence.

Usage:
    python benchmark/dolphinattack/run.py <dir containing *.wav and annotation.txt> [--out results.csv]

Reports: verdict counts (full clip and 0.5 s windows), per-device and
per-distance tables, and the baseband statistics a 16 kHz detector would have
to work with (energy below 100 Hz, 0-8 kHz band shape, strongest line in the
upper band, level).
"""

from __future__ import annotations

import argparse
import collections
import csv
import glob
import os
import sys

import numpy as np
from scipy.signal import find_peaks, welch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from echoguard.audio import load_wav  # noqa: E402
from echoguard.pipeline import Pipeline  # noqa: E402

_trapz = getattr(np, "trapezoid", None) or getattr(np, "trapz")  # noqa: B009
WINDOW_SEC = 0.5


def baseband_stats(x: np.ndarray, sr: int) -> dict:
    fq, psd = welch(x, fs=sr, nperseg=2048)
    tot = _trapz(psd, fq) or 1e-20
    lo = fq < 100
    frac_lt100 = float(_trapz(psd[lo], fq[lo]) / tot) if lo.sum() > 1 else float("nan")
    bands = []
    for b in range(0, 8000, 1000):
        m = (fq >= b) & (fq < b + 1000)
        bands.append(float(_trapz(psd[m], fq[m]) / tot) if m.sum() > 1 else 0.0)
    top = fq >= 4000
    pdb = 10 * np.log10(psd[top] + 1e-20)
    peaks, props = find_peaks(pdb, prominence=6)
    if len(peaks):
        j = int(np.argmax(props["prominences"]))
        top_f, top_prom = float(fq[top][peaks[j]]), float(props["prominences"][j])
    else:
        top_f, top_prom = float("nan"), 0.0
    rms_db = float(20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-12))
    return {"frac_lt100": frac_lt100, "top_peak_hz": top_f, "top_peak_prom_db": top_prom,
            "rms_dbfs": rms_db, **{f"band{k}k": v for k, v in enumerate(bands)}}


def run(data_dir: str, out_csv: str) -> list[dict]:
    files = sorted(glob.glob(os.path.join(data_dir, "*.wav")))
    if not files:
        sys.exit(f"no WAV files under {data_dir}")
    pipe = Pipeline()
    rows: list[dict] = []
    win_verdicts: collections.Counter = collections.Counter()
    for f in files:
        x, sr = load_wav(f)
        base = os.path.basename(f)[:-4]
        try:
            dev, pcmd, dist = base.split("+")
            spk = pcmd.split("_")[0]
        except ValueError:
            dev, spk, dist = "unknown", "unknown", "unknown"
        rep = pipe.analyze(x, sr)
        for w in pipe.analyze_windows(x, sr, window_sec=WINDOW_SEC, hop_sec=WINDOW_SEC).windows:
            win_verdicts[w.verdict] += 1
        rows.append({"file": base, "device": dev, "speaker": spk, "distance": dist, "sr": sr,
                     "duration_s": round(len(x) / sr, 3), "verdict": rep.verdict,
                     "overall_risk": round(rep.overall_risk, 4), **baseband_stats(x, sr)})

    with open(out_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    print(f"{len(rows)} clips, all at {sorted({r['sr'] for r in rows})} Hz")
    print("full-clip verdicts :", dict(collections.Counter(r["verdict"] for r in rows)))
    print(f"{WINDOW_SEC} s window verdicts:", dict(win_verdicts))
    for key in ("device", "distance"):
        groups: dict = collections.defaultdict(list)
        for r in rows:
            groups[r[key]].append(r)
        print(f"\nper {key:9s}    n  verdict            frac<100Hz  top-peak Hz  prom dB  rms dBFS")
        for k in sorted(groups):
            g = groups[k]
            v = collections.Counter(r["verdict"] for r in g).most_common(1)[0][0]
            print(f"  {k:14s} {len(g):4d}  {v:18s} {np.median([r['frac_lt100'] for r in g]):9.3f}  "
                  f"{np.nanmedian([r['top_peak_hz'] for r in g]):11.0f}  {np.median([r['top_peak_prom_db'] for r in g]):7.1f}  "
                  f"{np.median([r['rms_dbfs'] for r in g]):8.1f}")
    b = np.array([[r[f"band{k}k"] for k in range(8)] for r in rows]).mean(0)
    print("\nmean 0-8 kHz band fractions (1 kHz bins):", np.round(b, 3))
    print("frac<100Hz percentiles 5/50/95:", np.round(np.percentile([r["frac_lt100"] for r in rows], [5, 50, 95]), 3))
    print(f"wrote {out_csv}")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir", help="directory with the DolphinAttack *.wav files")
    ap.add_argument("--out", default="dolphinattack_results.csv")
    a = ap.parse_args()
    run(a.data_dir, a.out)


if __name__ == "__main__":
    main()
