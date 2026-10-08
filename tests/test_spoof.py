"""Anti-spoofing module: EER correctness/speed, feature sample-rate invariance, protocol parsing."""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

pytest.importorskip("sklearn")

from echoguard.spoof import equal_error_rate, extract_features, SpoofDetector  # noqa: E402


def test_eer_matches_sklearn_roc():
    from sklearn.metrics import roc_curve
    rng = np.random.default_rng(0)
    for n in (50, 500, 5000):
        s = rng.standard_normal(n)
        y = (rng.random(n) < 0.5).astype(int)
        s[y == 1] += 1.0
        fpr, tpr, _ = roc_curve(y, s)
        fnr = 1 - tpr
        i = int(np.nanargmin(np.abs(fnr - fpr)))
        ref = (fpr[i] + fnr[i]) / 2
        assert abs(equal_error_rate(s, y) - ref) < 2.0 / np.sqrt(n)


def test_eer_perfect_and_random():
    y = np.array([0] * 50 + [1] * 50)
    assert equal_error_rate(np.r_[np.zeros(50), np.ones(50)], y) == 0.0
    assert abs(equal_error_rate(np.r_[np.ones(50), np.zeros(50)], y) - 1.0) < 1e-9


def test_eer_rejects_degenerate_input():
    with pytest.raises(ValueError):
        equal_error_rate([], [])
    with pytest.raises(ValueError):
        equal_error_rate([0.1, 0.2], [1, 1])


def test_eer_is_fast_on_large_inputs():
    import time
    rng = np.random.default_rng(0)
    s = rng.standard_normal(600_000)
    y = (rng.random(600_000) < 0.5).astype(int)
    t0 = time.time()
    equal_error_rate(s, y)
    assert time.time() - t0 < 2.0


def test_features_are_sample_rate_invariant():
    from scipy import signal as sps
    rng = np.random.default_rng(0)
    # Speech-band content: resampling is transparent here, unlike white noise at the band edge.
    sos = sps.butter(6, [150, 3800], btype="bandpass", fs=16_000, output="sos")
    x16 = sps.sosfilt(sos, rng.standard_normal(16_000))
    x48 = sps.resample_poly(x16, 3, 1)
    f16, f48 = extract_features(x16, 16_000), extract_features(x48, 48_000)
    assert f16.shape == (16,)
    assert abs(f16[0] - f48[0]) / f16[0] < 0.02          # centroid
    assert np.allclose(f16[5:8], f48[5:8], atol=0.01)    # band ratios
    # And the confound itself is gone: the same clip's centroid no longer depends on its rate.
    assert abs(f16[0] - f48[0]) < 100


def test_features_handle_stereo_and_reject_nan():
    x = np.random.default_rng(0).standard_normal((16_000, 2))
    assert extract_features(x, 16_000).shape == (16,)
    x[10, 0] = np.nan
    with pytest.raises(ValueError):
        extract_features(x, 16_000)


def test_detector_round_trip():
    rng = np.random.default_rng(0)
    X = rng.standard_normal((80, 16))
    y = (rng.random(80) < 0.5).astype(int)
    X[y == 1, 0] += 3.0
    det = SpoofDetector().fit(X, y)
    assert equal_error_rate(det.score(X), y) < 0.2


def test_asvspoof_protocol_parser():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "benchmark"))
    import asvspoof
    import tempfile
    lines = (
        "LA_0079 LA_T_1138215 - - bonafide\n"
        "LA_0079 LA_T_1271820 - A01 spoof\n"
        "\n"
        "LA_0080 LA_T_9999999.flac - - bonafide\n"
        "garbage line without key\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(lines)
    rows = asvspoof._read_protocol(f.name)
    assert rows == [("LA_T_1138215", 0), ("LA_T_1271820", 1), ("LA_T_9999999.flac", 0)]
