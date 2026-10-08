"""Benchmark the experimental anti-spoofing module on ASVspoof-style data.

Two modes:

  --selftest
      Builds a small SYNTHETIC genuine-vs-spoof set, trains, and reports EER.
      Validates the whole pipeline runs end to end. The number is NOT an
      ASVspoof result - it only proves the plumbing works.

  --train <protocol> --eval <protocol> --audio <dir>
      Real mode. Reads ASVspoof protocol files (space-separated, with the
      filename column and a bonafide/spoof label column), extracts features,
      trains on the train split, and reports EER on the eval split.
      Needs `soundfile` for FLAC:  pip install soundfile

ASVspoof download: ASVspoof 2021 (https://zenodo.org/records/4835108),
ASVspoof 5 (https://www.sciencedirect.com/science/article/pii/S0885230825000506).
Datasets are tens of GB - run this locally where the data lives.
"""

from __future__ import annotations

import argparse
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from echoguard.spoof import extract_features, SpoofDetector, equal_error_rate  # noqa: E402

SR = 16_000


# ---------- synthetic self-test ----------
def _genuine(seed):
    from scipy import signal as sps
    rng = np.random.default_rng(seed)
    n = SR
    x = sps.sosfilt(sps.butter(6, [100, 3800], btype="bandpass", fs=SR, output="sos"),
                    rng.standard_normal(n))
    return x / (np.max(np.abs(x)) or 1.0)


def _spoof(seed):
    # Over-smoothed spectrum + faint high-band artefact: a stand-in for
    # vocoder/replay colouration. Only to exercise the classifier.
    from scipy import signal as sps
    rng = np.random.default_rng(1000 + seed)
    n = SR
    base = sps.sosfilt(sps.butter(6, [100, 3000], btype="bandpass", fs=SR, output="sos"),
                       rng.standard_normal(n))
    smooth = np.convolve(base, np.ones(8) / 8, mode="same")
    artefact = 0.05 * sps.sosfilt(sps.butter(6, [4000, 6000], btype="bandpass", fs=SR, output="sos"),
                                  rng.standard_normal(n))
    x = smooth + artefact
    return x / (np.max(np.abs(x)) or 1.0)


def selftest(n_per_class=60):
    X, y = [], []
    for i in range(n_per_class):
        X.append(extract_features(_genuine(i), SR)); y.append(0)
        X.append(extract_features(_spoof(i), SR)); y.append(1)
    X = np.array(X); y = np.array(y)
    # split
    rng = np.random.default_rng(0)
    perm = rng.permutation(len(y))
    cut = int(0.6 * len(y))
    tr, ev = perm[:cut], perm[cut:]
    det = SpoofDetector().fit(X[tr], y[tr])
    scores = det.score(X[ev])
    eer = equal_error_rate(scores, y[ev])
    print(f"[selftest] synthetic genuine-vs-spoof, {len(y)} clips")
    print(f"[selftest] EER = {eer*100:.1f}%   (pipeline OK; NOT an ASVspoof number)")
    return eer


# ---------- real ASVspoof mode ----------
def _read_protocol(path):
    """Return list of (filename, label) where label 1=spoof, 0=bonafide.

    ASVspoof 2019/2021 protocol lines look like
        LA_0079 LA_T_1138215 - - bonafide
        LA_0079 LA_T_1271820 - A01 spoof
    i.e. speaker-id, utterance-id, (sub-fields), key. The utterance id is the
    SECOND token; the first is the speaker. ASVspoof 5 uses more columns but
    keeps speaker first, utterance second, key as 'bonafide'/'spoof'.
    """
    rows = []
    with open(path) as f:
        for line in f:
            parts = line.split()
            if len(parts) < 2:
                continue
            lowered = [p.lower() for p in parts]
            label = 1 if "spoof" in lowered else (0 if "bonafide" in lowered else None)
            if label is None:
                continue
            explicit = next((p for p in parts if p.lower().endswith((".flac", ".wav"))), None)
            fname = explicit if explicit else parts[1]
            rows.append((fname, label))
    return rows


def _load_audio(audio_dir, fname):
    import soundfile as sf
    for ext in ("", ".flac", ".wav"):
        p = os.path.join(audio_dir, fname + ext)
        if os.path.exists(p):
            data, sr = sf.read(p)
            if data.ndim > 1:
                data = data.mean(axis=1)
            return data, sr
    raise FileNotFoundError(fname)


def _featurize(rows, audio_dir, limit=None):
    X, y = [], []
    for i, (fname, label) in enumerate(rows):
        if limit and i >= limit:
            break
        try:
            data, sr = _load_audio(audio_dir, fname)
        except FileNotFoundError:
            continue
        X.append(extract_features(data, sr)); y.append(label)
    return np.array(X), np.array(y)


def real_mode(train_proto, eval_proto, audio_dir, limit=None):
    print("Extracting train features...")
    Xtr, ytr = _featurize(_read_protocol(train_proto), audio_dir, limit)
    print(f"  {len(ytr)} train clips ({int(ytr.sum())} spoof)")
    print("Extracting eval features...")
    Xev, yev = _featurize(_read_protocol(eval_proto), audio_dir, limit)
    print(f"  {len(yev)} eval clips ({int(yev.sum())} spoof)")
    det = SpoofDetector().fit(Xtr, ytr)
    eer = equal_error_rate(det.score(Xev), yev)
    print(f"\nASVspoof EER = {eer*100:.2f}%  (baseline feature+LR detector)")
    return eer


def main():
    ap = argparse.ArgumentParser(description="Benchmark the anti-spoofing baseline.")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--train"); ap.add_argument("--eval"); ap.add_argument("--audio")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    real_args = (args.train, args.eval, args.audio)
    if args.selftest:
        selftest()
    elif all(real_args):
        real_mode(args.train, args.eval, args.audio, args.limit)
    elif any(real_args):
        ap.error("real mode needs all of --train, --eval and --audio")
    else:
        selftest()


if __name__ == "__main__":
    main()
