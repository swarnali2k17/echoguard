"""Real ASVspoof 2019 LA benchmark for the anti-spoofing baseline.

Pulls the ASVspoof 2019 LA evaluation set from a public Hugging Face parquet
mirror (audio stored as FLAC bytes, all 16 kHz - condition-matched, so no
sample-rate shortcut), splits it SPEAKER-DISJOINT (train and eval never share a
voice), trains the baseline detector, and reports EER.

Unlike a quick in-the-wild dataset, this is condition-matched, so the number is
trustworthy for what it is: a simple handcrafted-feature + logistic-regression
baseline (state-of-the-art systems reach far lower EER).

Run:
    pip install datasets soundfile huggingface_hub scikit-learn
    python benchmark/asvspoof_hf.py              # 2 shards, 900/class train
    python benchmark/asvspoof_hf.py --shards 4 --per-class 1500
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import os
import numpy as np
import pandas as pd
import soundfile as sf
from huggingface_hub import hf_hub_download

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from echoguard.spoof import extract_features, SpoofDetector, equal_error_rate  # noqa: E402

REPO = "SpeechAntiSpoofingBenchmarks/ASVspoof2019_LA"
# label convention in this mirror: 0 = bonafide (genuine), 1 = spoof


def load_rows(n_shards):
    rows = []
    for i in range(n_shards):
        shard = f"data/test-{i:05d}-of-00009.parquet"
        df = pd.read_parquet(hf_hub_download(REPO, shard, repo_type="dataset"))
        for _, r in df.iterrows():
            try:
                spk = json.loads(r["notes"]).get("speaker_id", "?")
            except Exception:
                spk = "?"
            rows.append((spk, int(r["label"]), r["audio"]["bytes"]))
    return rows


def _featurize(subset):
    X, y = [], []
    for _spk, lab, b in subset:
        try:
            d, sr = sf.read(io.BytesIO(b))
            if d.ndim > 1:
                d = d.mean(axis=1)
            X.append(extract_features(d, sr)); y.append(lab)
        except Exception:
            pass
    return np.array(X), np.array(y)


def run(n_shards=2, per_class=900, seed=0):
    rows = load_rows(n_shards)
    speakers = sorted({s for s, _, _ in rows})
    rng = np.random.default_rng(seed); rng.shuffle(speakers)
    train_spk = set(speakers[: int(0.7 * len(speakers))])

    def take(is_train, k):
        bona = [r for r in rows if (r[0] in train_spk) == is_train and r[1] == 0]
        spoof = [r for r in rows if (r[0] in train_spk) == is_train and r[1] == 1]
        rng.shuffle(bona); rng.shuffle(spoof)
        return bona[:k] + spoof[:k]

    Xtr, ytr = _featurize(take(True, per_class))
    Xev, yev = _featurize(take(False, per_class // 2))
    det = SpoofDetector().fit(Xtr, ytr)
    eer = equal_error_rate(det.score(Xev), yev)
    print("ASVspoof 2019 LA (speaker-disjoint, balanced subset, baseline feature+LR)")
    print(f"  speakers: {len(train_spk)} train / {len(speakers)-len(train_spk)} eval")
    print(f"  clips:    {len(ytr)} train / {len(yev)} eval")
    print(f"  EER = {eer*100:.1f}%")
    return eer


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--shards", type=int, default=2)
    ap.add_argument("--per-class", type=int, default=900)
    a = ap.parse_args()
    run(a.shards, a.per_class)
