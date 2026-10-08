"""Input validation, audio loading, windowing/streaming, CLI exit codes, JSON schema."""

from __future__ import annotations

import json
import os

import numpy as np
import pytest
from scipy.io import wavfile

from echoguard import Pipeline, StreamAnalyzer, InvalidInput
from echoguard.audio import load_wav, to_float_mono
from echoguard.cli import run, EXIT_READ_ERROR, EXIT_INVALID_INPUT, EXIT_USAGE
from echoguard.pipeline import CLEAR, HIGH_RISK, SUSPICIOUS, INSUFFICIENT_DATA
from tests import synth

SR = 48_000


# --- validation ---------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    np.array([]),
    np.zeros((10, 10, 2)),
    np.array(["a", "b"]),
    np.zeros(10),                       # shorter than MIN_SAMPLES
])
def test_rejects_unusable_signal(bad):
    with pytest.raises(InvalidInput):
        Pipeline().analyze(bad, SR)


def test_rejects_non_finite():
    x = 0.1 * np.random.default_rng(0).standard_normal(SR)
    x[100] = np.nan
    with pytest.raises(InvalidInput):
        Pipeline().analyze(x, SR)
    x[100] = np.inf
    with pytest.raises(InvalidInput):
        Pipeline().analyze(x, SR)


@pytest.mark.parametrize("sr", [0, -1, "x", None])
def test_rejects_bad_sample_rate(sr):
    x = 0.1 * np.random.default_rng(0).standard_normal(SR)
    with pytest.raises(InvalidInput):
        Pipeline().analyze(x, sr)


def test_accepts_stereo_and_lists():
    x = 0.1 * np.random.default_rng(0).standard_normal(SR)
    assert Pipeline().analyze(np.stack([x, x], axis=1), SR).verdict == CLEAR
    assert Pipeline().analyze(list(x), SR).verdict == CLEAR
    assert Pipeline().analyze(x.astype(np.float32), np.int32(SR)).sample_rate == SR


def test_exact_36k_is_unassessable():
    # Nyquist == 18 kHz: the out-of-band band is empty, so it must not be "checked and clean".
    x = 0.1 * np.random.default_rng(0).standard_normal(36_000)
    assert Pipeline().analyze(x, 36_000).verdict == INSUFFICIENT_DATA


# --- audio loading -----------------------------------------------------------

def test_uint8_wav_is_centred(tmp_path):
    p = tmp_path / "u8.wav"
    wavfile.write(str(p), SR, np.full(SR, 128, dtype=np.uint8))  # digital silence
    x, sr = load_wav(str(p))
    assert sr == SR and np.abs(x).max() < 1e-6


@pytest.mark.parametrize("dtype", [np.int16, np.int32, np.float32])
def test_pcm_variants_load_to_unit_range(tmp_path, dtype):
    rng = np.random.default_rng(1)
    x = np.clip(0.3 * rng.standard_normal(SR), -0.99, 0.99)
    if np.issubdtype(dtype, np.integer):
        data = np.round(x * np.iinfo(dtype).max).astype(dtype)
    else:
        data = x.astype(dtype)
    p = tmp_path / "f.wav"
    wavfile.write(str(p), SR, data)
    y, sr = load_wav(str(p))
    assert y.dtype == np.float32 and np.abs(y).max() <= 1.0
    assert np.corrcoef(x, y)[0, 1] > 0.999


def test_multichannel_mixdown():
    x = np.ones((100, 6), dtype=np.int16) * 1000
    assert to_float_mono(x).shape == (100,)


# --- level floor -------------------------------------------------------------

def test_near_silent_tail_with_dither_tone_is_clear():
    # A 19 kHz line at -70 dBFS in an otherwise silent window: 100% of nothing is not evidence.
    rng = np.random.default_rng(0)
    t = np.arange(SR) / SR
    x = 1e-4 * rng.standard_normal(SR) + 3e-4 * np.sin(2 * np.pi * 19_190 * t)
    r = Pipeline().analyze(x, SR)
    assert r.verdict == CLEAR
    oob = next(f for f in r.findings if f.name == "out_of_band_energy")
    assert oob.evidence["out_of_band_level_dbfs"] < -60


def test_broad_resonance_is_not_a_carrier():
    # Band-limited noise 19-21 kHz over speech-band base: lots of energy above 18 kHz,
    # a peak high over the median, but no narrow line. Must not corroborate.
    from scipy import signal as sps
    rng = np.random.default_rng(3)
    base = synth.benign_speechlike(sample_rate=SR)
    sos = sps.butter(4, [19_000, 21_000], btype="bandpass", fs=SR, output="sos")
    hiss = sps.sosfilt(sos, rng.standard_normal(SR))
    x = base + 0.3 * hiss / np.abs(hiss).max()
    r = Pipeline().analyze(x, SR)
    carrier = next(f for f in r.findings if f.name == "carrier_peak")
    assert carrier.evidence["narrowness"] < 0.6
    assert r.verdict == CLEAR


# --- windowing / streaming ---------------------------------------------------

def _burst_in_long_clip():
    quiet = synth.benign_speechlike(duration=10.0, sample_rate=SR)
    burst = 0.5 * synth.out_of_band(duration=0.5, sample_rate=SR)
    tail = synth.benign_speechlike(duration=9.5, sample_rate=SR, seed=7)
    return np.concatenate([quiet, burst, tail])


def test_windowed_analysis_finds_short_burst():
    x = _burst_in_long_clip()
    whole = Pipeline().analyze(x, SR)
    windowed = Pipeline().analyze_windows(x, SR, window_sec=1.0, hop_sec=0.5)
    assert windowed.summary.verdict == HIGH_RISK
    assert abs(windowed.summary.start_sec - 10.0) <= 0.5
    assert windowed.summary.overall_risk >= whole.overall_risk
    assert len(windowed.windows) == 39


def test_windowed_short_clip_is_single_window():
    x = synth.benign_speechlike(duration=0.4, sample_rate=SR)
    w = Pipeline().analyze_windows(x, SR)
    assert len(w.windows) == 1 and w.summary.verdict == CLEAR


def test_stream_matches_windowed():
    x = _burst_in_long_clip()
    s = StreamAnalyzer(SR, window_sec=1.0, hop_sec=0.5)
    out = []
    for i in range(0, len(x), 7_001):
        out.extend(s.push(x[i:i + 7_001]))
    out.extend(s.flush())
    assert max(o.overall_risk for o in out) >= 0.66
    starts = [o.start_sec for o in out]
    assert starts == sorted(starts) and starts[1] - starts[0] == pytest.approx(0.5)


def test_stream_rejects_nan():
    s = StreamAnalyzer(SR)
    with pytest.raises(InvalidInput):
        list(s.push(np.array([np.nan] * 100)))


# --- JSON schema -------------------------------------------------------------

def test_json_is_strict_and_stable():
    rng = np.random.default_rng(0)
    keys = None
    for sig, sr in [
        (synth.out_of_band(sample_rate=SR), SR),
        (synth.benign_speechlike(sample_rate=SR), SR),
        (0.1 * rng.standard_normal(16_000), 16_000),
        (1e-4 * rng.standard_normal(SR), SR),
    ]:
        d = Pipeline().analyze(sig, sr).to_dict()
        json.dumps(d, allow_nan=False)  # raises on NaN/inf or numpy scalars
        assert set(d) == {"verdict", "overall_risk", "sample_rate", "duration_sec", "start_sec", "findings"}
        this = {f["name"]: tuple(sorted(f["evidence"])) for f in d["findings"]}
        if keys is None:
            keys = this
        assert this == keys, "evidence keys must not depend on the branch taken"
    w = Pipeline().analyze_windows(synth.benign_speechlike(duration=3.0, sample_rate=SR), SR).to_dict()
    json.dumps(w, allow_nan=False)
    assert {"window_sec", "hop_sec", "windows"} <= set(w)


# --- CLI ---------------------------------------------------------------------

def _wav(tmp_path, name, data, sr=SR):
    p = tmp_path / name
    wavfile.write(str(p), sr, data)
    return str(p)


def test_cli_exit_codes(tmp_path, capsys):
    ok = _wav(tmp_path, "ok.wav", (synth.benign_speechlike(sample_rate=SR) * 32767).astype(np.int16))
    bad = _wav(tmp_path, "bad.wav", (synth.out_of_band(sample_rate=SR) * 32767).astype(np.int16))
    nan = np.zeros(SR, dtype=np.float32)
    nan[5] = np.nan
    nanp = _wav(tmp_path, "nan.wav", nan)
    low = _wav(tmp_path, "low.wav", (synth.benign_speechlike(sample_rate=16_000) * 32767).astype(np.int16), 16_000)

    assert run(["scan", ok]) == 0
    assert run(["scan", bad]) == 2
    assert run(["scan", low]) == 4
    assert run(["scan", nanp]) == EXIT_INVALID_INPUT
    assert run(["scan", os.path.join(str(tmp_path), "missing.wav")]) == EXIT_READ_ERROR
    assert run(["scan"]) == EXIT_USAGE
    assert run(["scan", ok, "--jsn"]) == EXIT_USAGE
    assert run(["scan", ok, "--json"]) == 0
    out = capsys.readouterr().out
    payload = json.loads(out.strip().split("\n{", 1)[-1].join(["{", ""]) if not out.startswith("{") else out)
    assert payload["verdict"] == CLEAR


def test_cli_windowed(tmp_path, capsys):
    x = _burst_in_long_clip()
    p = _wav(tmp_path, "long.wav", (x * 32767).astype(np.int16))
    assert run(["scan", p, "--window", "1", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == HIGH_RISK and len(payload["windows"]) == 39
    assert any(w["verdict"] in (HIGH_RISK, SUSPICIOUS) for w in payload["windows"])
