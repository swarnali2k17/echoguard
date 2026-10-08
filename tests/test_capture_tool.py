"""The capture tool's file paths (ingest, monitor --file, manifest) without a mic."""

from __future__ import annotations

import csv
import json
import os
import sys

import numpy as np
from scipy.io import wavfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools", "capture"))
import record as cap  # noqa: E402
from tests import synth  # noqa: E402

SR = 48_000


def _wav(path, sig, sr=SR):
    wavfile.write(str(path), sr, (np.clip(sig, -1, 1) * 32767).astype(np.int16))
    return str(path)


def test_ingest_writes_manifest_sidecar_and_verdict(tmp_path):
    _wav(tmp_path / "raw1.wav", synth.out_of_band(sample_rate=SR))
    _wav(tmp_path / "raw2.wav", synth.benign_speechlike(sample_rate=SR))
    out = tmp_path / "corpus"
    rc = cap.main(["ingest", str(tmp_path / "raw1.wav"), str(tmp_path / "raw2.wav"),
                   "--out", str(out), "--label", "probe", "--track", "P",
                   "--device", "Pixel 8 / Android 15", "--room", "roomA", "--distance-cm", "60"])
    assert rc == 0
    wavs = sorted(f for f in os.listdir(out) if f.endswith(".wav"))
    assert len(wavs) == 2
    assert wavs[0].startswith("Pixel8__P__060cm__roomA__")
    # sidecar carries metadata and the live verdict
    side = json.load(open(os.path.join(out, wavs[0] + ".json")))
    assert side["label"] == "probe" and side["capture_rate"] == SR
    assert "echoguard_verdict" in side and "verdict" in side["echoguard_verdict"]
    # manifest has one row per clip in the documented schema
    rows = list(csv.DictReader(open(os.path.join(out, "manifest.csv"))))
    assert len(rows) == 2 and set(rows[0]) == set(cap.FIELDS)
    assert {r["label"] for r in rows} == {"probe"}


def test_ingest_skips_unreadable_without_crashing(tmp_path, capsys):
    bad = tmp_path / "notaudio.wav"
    bad.write_text("nope")
    _wav(tmp_path / "ok.wav", synth.benign_speechlike(sample_rate=SR))
    good = str(tmp_path / "ok.wav")
    out = tmp_path / "corpus"
    rc = cap.main(["ingest", str(bad), good, "--out", str(out), "--label", "benign"])
    assert rc == 0
    assert len(list(csv.DictReader(open(os.path.join(out, "manifest.csv"))))) == 1
    assert "skip" in capsys.readouterr().err


def test_monitor_file_reports_windows(tmp_path, capsys):
    x = np.concatenate([synth.benign_speechlike(duration=2.0, sample_rate=SR),
                        synth.out_of_band(duration=1.0, sample_rate=SR)])
    p = _wav(tmp_path / "long.wav", x)
    assert cap.main(["monitor", "--file", p, "--hop", "0.5"]) == 0
    out = capsys.readouterr().out
    assert "summary:" in out and "HIGH_RISK" in out


def test_no_detect_omits_verdict(tmp_path):
    src = _wav(tmp_path / "r.wav", synth.benign_speechlike(sample_rate=SR))
    out = tmp_path / "c"
    cap.main(["ingest", src, "--out", str(out), "--label", "benign", "--no-detect"])
    wavs = [f for f in os.listdir(out) if f.endswith(".json")]
    assert "echoguard_verdict" not in json.load(open(os.path.join(out, wavs[0])))
