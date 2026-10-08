"""Tests: detectors fire on the right spectral signatures, stay quiet on benign audio."""

from __future__ import annotations

import numpy as np
import pytest

from echoguard import Pipeline
from echoguard.pipeline import CLEAR, HIGH_RISK, SUSPICIOUS, INSUFFICIENT_DATA
from echoguard.detectors.ultrasonic import OutOfBandEnergyDetector
from echoguard.detectors.spectral import SpectralProfileDetector
from tests import synth

SR = 48_000


def test_benign_is_clear():
    sig = synth.benign_speechlike(sample_rate=SR)
    report = Pipeline().analyze(sig, SR)
    assert report.verdict == CLEAR
    assert report.overall_risk < 0.33


def test_out_of_band_flagged_high():
    sig = synth.out_of_band(sample_rate=SR)
    report = Pipeline().analyze(sig, SR)
    assert report.verdict == HIGH_RISK
    oob = next(f for f in report.findings if f.name == "out_of_band_energy")
    assert oob.risk >= 0.66


def test_modulated_carrier_flagged():
    sig = synth.modulated_carrier(sample_rate=SR)
    report = Pipeline().analyze(sig, SR)
    assert report.verdict in (SUSPICIOUS, HIGH_RISK)
    carrier = next(f for f in report.findings if f.name == "carrier_peak")
    assert carrier.risk >= 0.33


def test_low_bandwidth_reports_unassessable():
    # 16 kHz capture cannot see 18 kHz; ultrasonic detector must say so, not false-clear.
    sig = synth.benign_speechlike(sample_rate=16_000)
    det = OutOfBandEnergyDetector()
    finding = det.analyze(sig, 16_000)
    assert finding.assessable is False
    assert finding.risk == 0.0


def test_low_bandwidth_verdict_is_insufficient_not_clear():
    # A narrow capture with nothing flagged must NOT read CLEAR - we couldn't check.
    sig = synth.benign_speechlike(sample_rate=16_000)
    report = Pipeline().analyze(sig, 16_000)
    assert report.verdict == INSUFFICIENT_DATA


def test_spectral_profile_separates_speech_from_injection():
    speech = synth.benign_speechlike(sample_rate=SR)
    inject = synth.out_of_band(sample_rate=SR)
    det = SpectralProfileDetector()
    assert det.analyze(speech, SR).risk < det.analyze(inject, SR).risk


def test_report_serialises():
    sig = synth.out_of_band(sample_rate=SR)
    d = Pipeline().analyze(sig, SR).to_dict()
    assert d["verdict"] == HIGH_RISK
    assert "findings" in d and len(d["findings"]) == 3


# --- realistic synthetic attack generator (benchmark/synth_attacks.py) ---

def test_realistic_attack_flagged():
    """A physics-modelled captured attack (carrier + mic demodulation) is flagged."""
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "benchmark"))
    import synth_attacks as sa
    sig = sa.make_attack(sample_rate=96_000, carrier_hz=28_000.0, distance_m=0.5,
                         snr_db=25.0, seed=3)
    report = Pipeline().analyze(sig, 96_000)
    assert report.verdict in (HIGH_RISK, SUSPICIOUS)


def test_realistic_benign_is_clear():
    """A matched benign capture (same room/mic/noise, no carrier) stays CLEAR."""
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "benchmark"))
    import synth_attacks as sa
    for kind in ("speech", "music", "silence"):
        sig = sa.make_benign(sample_rate=96_000, kind=kind, snr_db=25.0, seed=5)
        report = Pipeline().analyze(sig, 96_000)
        assert report.verdict == CLEAR, f"{kind} -> {report.verdict}"


# --- short clips: the carrier threshold must scale with Welch variance ---

def test_short_white_noise_is_not_flagged():
    """Short broadband noise has a noisy spectrum whose largest bin stands a few dB
    above the median by chance; that must not read as a carrier."""
    rng = np.random.default_rng(0)
    pipe = Pipeline()
    for duration in (0.1, 0.2, 0.3, 0.5):
        flagged = 0
        for _ in range(20):
            sig = 0.2 * rng.standard_normal(int(duration * SR))
            flagged += pipe.analyze(sig, SR).verdict in (SUSPICIOUS, HIGH_RISK)
        assert flagged <= 1, f"{duration}s white noise flagged {flagged}/20"


def test_short_clip_carrier_still_flagged():
    """The CFAR threshold must not hide a real carrier in a short clip."""
    base = synth.benign_speechlike(duration=0.2, sample_rate=SR)
    carrier = synth.out_of_band(duration=0.2, sample_rate=SR, tone_hz=21_000.0, seed=9) - \
        synth.benign_speechlike(duration=0.2, sample_rate=SR, seed=9) * 0.0
    sig = base + 0.3 * carrier
    report = Pipeline().analyze(sig, SR)
    assert report.verdict in (SUSPICIOUS, HIGH_RISK)


def test_bare_tone_is_a_beacon_not_a_carrier():
    """An unmodulated 19 kHz tone (retail beacon, pilot, PSU whine) has no command sidebands."""
    n = SR
    t = np.arange(n) / SR
    rng = np.random.default_rng(0)
    sig = 0.02 * rng.standard_normal(n) + 0.5 * np.sin(2 * np.pi * 19_000.0 * t)
    report = Pipeline().analyze(sig, SR)
    carrier = next(f for f in report.findings if f.name == "carrier_peak")
    assert carrier.evidence["sideband_db"] < -60
    assert report.verdict == CLEAR
    assert "beacon" in carrier.detail


def test_noise_threshold_shrinks_with_more_segments():
    from echoguard.detectors.modulation import noise_prominence_threshold_db
    from echoguard.detectors._dsp import welch_dof
    short = noise_prominence_threshold_db(welch_dof(int(0.1 * SR), SR), 384)
    long = noise_prominence_threshold_db(welch_dof(int(2.0 * SR), SR), 384)
    assert short > long > 0


# --- the phase-2 target: detection on a device-realistic capture ---

def _synth_attacks():
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "benchmark"))
    import synth_attacks as sa
    return sa


def test_adc_capture_keeps_benign_clear():
    sa = _synth_attacks()
    for kind in ("speech", "music", "silence"):
        sig = sa.capture(sa.make_benign(sample_rate=192_000, kind=kind, seed=5), 192_000, 48_000)
        assert Pipeline().analyze(sig, 48_000).verdict == CLEAR


@pytest.mark.xfail(strict=True, reason="phase 2: the carrier is removed by the capture chain; "
                                       "needs the baseband detector (BENCHMARK_REPORT §6b)")
def test_adc_capture_attack_is_detected():
    sa = _synth_attacks()
    sig = sa.capture(sa.make_attack(sample_rate=192_000, carrier_hz=28_000.0, seed=3), 192_000, 48_000)
    assert Pipeline().analyze(sig, 48_000).verdict in (HIGH_RISK, SUSPICIOUS)
