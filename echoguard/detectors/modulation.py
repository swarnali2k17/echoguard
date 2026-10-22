"""High-frequency carrier detector.

Ultrasonic injection carries a command on a high-frequency carrier that the
microphone's non-linearity demodulates back down to audible baseband. A
recording made near such an attack tends to show a strong, narrow spectral
peak high in the band (often with modulation sidebands around it). Ordinary
speech and music do not contain a dominant narrowband tone above ~15 kHz.

This detector looks for a narrowband peak in the high band that stands far
above the local spectral floor. It does not attempt to demodulate or recover
any content - it only flags the structural signature.

The threshold is constant-false-alarm-rate (CFAR): even pure noise has a
largest bin that sits a few dB above the median, and that margin grows as the
clip gets shorter (fewer Welch segments, noisier spectrum). We compute the
prominence noise alone would reach with probability NOISE_FALSE_ALARM_PROB and
score only the prominence in excess of it. Without this, short noise clips
(~0.2 s at 48 kHz) were flagged almost every time.
"""

from __future__ import annotations

import numpy as np
from scipy import stats

from .base import Detector, Finding, clip01
from ._dsp import Spectrum, band_power, total_power

HIGH_BAND_LOW = 15_000.0  # Hz
# Prominence (in dB) above the noise threshold at which we treat the tone as fully suspicious.
PROMINENCE_DB_SATURATION = 30.0
# Probability that a noise-only high band produces a peak above the CFAR threshold.
NOISE_FALSE_ALARM_PROB = 1e-3
# The high band must hold at least this fraction of total energy before we
# even assess a peak - otherwise we'd be measuring the ratio of noise to noise
# inside an essentially silent band (a false positive on normal speech).
MIN_HIGH_BAND_FRACTION = 1e-3
# A carrier is narrow: a tone (and its slow speech-rate sidebands) sits in a
# couple of Welch bins. Narrowness = power within +-NARROW_INNER bins of the
# peak over power within +-NARROW_OUTER bins. Synthetic carriers (AM, DSB-SC,
# SSB, aliased) measure 1.00; a 19 kHz beacon under heavy noise 0.79; cymbal,
# scissor and lighter resonances on real recordings 0.17-0.61. The carrier
# risk is scaled down between NARROW_FLOOR and NARROW_FULL.
NARROW_INNER = 2
NARROW_OUTER = 20
NARROW_FLOOR = 0.60
NARROW_FULL = 0.85
# A carrier that injects a command is MODULATED by it, so it carries sidebands
# spanning the command's bandwidth (+-150 Hz .. 4 kHz around the line). An
# unmodulated tone - a retail beacon, a power-supply whine, a pilot - has
# none. Sideband ratio = energy above the local floor at |f - f_peak| in
# [SIDEBAND_LOW, SIDEBAND_HIGH] over energy within +-SIDEBAND_LINE bins of the
# peak, in dB. Below SIDEBAND_FLOOR_DB the peak is treated as a bare tone and
# the carrier risk is scaled down to BARE_TONE_RISK_SCALE (reported in the
# evidence and detail, but it no longer corroborates an injection).
# Measured: speech-bandwidth AM attacks -18 to -35 dB (depth 0.5-0.9, 15-35 dB
# SNR, through a 48 kHz ADC -4 dB); a 19 kHz beacon, a 20-22 kHz PSU whine and
# a pure probe tone -98 to -130 dB.
SIDEBAND_LOW = 150.0
SIDEBAND_HIGH = 4_000.0
SIDEBAND_LINE_BINS = 3
SIDEBAND_FLOOR_DB = -60.0
SIDEBAND_FULL_DB = -35.0
BARE_TONE_RISK_SCALE = 0.05
# Sideband plateau above the background, used by the near-ultrasonic path
# (pipeline.py) together with a fluctuation test: SWhisper's hardware measures
# 12.7-15.8 dB (5th-95th percentile over its 58-minute recording), synthetic AM
# attacks 23-25 dB, benign beacons in white noise at most 0.34 dB.
PLATEAU_FLOOR_DB = 2.0
PLATEAU_FULL_DB = 8.0
# Sidebands must stand out from the capture's own noise before they count as
# modulation: probability that noise alone clears the gate.
SIDEBAND_FALSE_ALARM_PROB = 1e-3
# The mirrored-line search tries every offset, so it gets a stricter budget of its own.
SIDEBAND_LINE_FALSE_ALARM_PROB = 1e-4
# Variance of a sum of Hann-windowed periodogram bins relative to independent
# bins: adjacent bins correlate ~0.25, bins two apart ~0.03, so 1 + 2(0.25 + 0.03).
HANN_SUM_VARIANCE = 1.56

_EMPTY_EVIDENCE = {
    "assessable": None,
    "high_band_fraction": None,
    "peak_freq_hz": None,
    "prominence_db": None,
    "noise_threshold_db": None,
    "excess_db": None,
    "narrowness": None,
    "sideband_db": None,
    "sideband_plateau_db": None,
    "modulated": None,
    "prominence_risk": None,
    "welch_dof": None,
}


def sideband_measures(freqs: np.ndarray, psd: np.ndarray, peak_freq: float,
                      dof: float | None = None) -> tuple[float, float]:
    """(ratio_db, plateau_db): how strongly `peak_freq` is modulated.

    ratio_db  - sideband power relative to the line itself, in dB.
    plateau_db - how far the sideband plateau stands above the background, in dB.

    The line is measured above its immediate neighbourhood (median within
    +-150 Hz). The sidebands are measured on the LOWER side of the carrier,
    against the background 4.5-7.5 kHz below it - outside any speech-band
    sideband. A command spreads its sidebands into a broad plateau; a floor taken
    from inside that plateau (an earlier version) measured it against itself and
    saw modulation only through noise excursions - the same excursions that made
    noisy beacons read as modulated (38% of unmodulated 19-22 kHz tones at
    20-40 dB SNR: benchmark/beacon_fp.py). Only the lower side is used because the
    upper side often lies above a codec's cut-off, where silence would drag a
    pooled background down and fake a plateau.

    The plateau level is a MEDIAN over the sideband region: the leakage skirt of
    a loud bare tone touches only the first few bins and cannot move it. With
    `dof`, it must also clear what noise alone would reach with probability
    SIDEBAND_FALSE_ALARM_PROB (each Welch bin ~ background * chi2(dof)/dof) - the
    CFAR idea of the peak threshold. Below that the peak is a bare tone (both
    measures -inf), however loud the room.

    The two measures answer different questions. A deeply modulated synthetic
    attack has sidebands only ~25 dB under its line; SWhisper's hardware drives a
    carrier ~94 dB above the floor, so its sidebands sit ~65 dB under the line yet
    ~14 dB above the background in every window. A beacon's plateau never clears
    the gate (at most 0.34 dB above background at 20-40 dB SNR).
    """
    df = float(freqs[1] - freqs[0]) if len(freqs) > 1 else 1.0
    off = peak_freq - freqs                                  # positive below the carrier
    d = np.abs(off)
    in_line = d <= SIDEBAND_LINE_BINS * df
    near = (d > SIDEBAND_LINE_BINS * df) & (d <= SIDEBAND_LOW)
    in_side = (off >= SIDEBAND_LOW) & (off <= SIDEBAND_HIGH)
    ref = (off >= SIDEBAND_HIGH + 500.0) & (off <= SIDEBAND_HIGH + 3_500.0) & (freqs >= 1_000.0)
    if np.count_nonzero(ref) < 8 or np.count_nonzero(in_side) < 8:
        return float("-inf"), float("-inf")    # nowhere to measure modulation against: a bare tone
    # the median of chi2(dof)/dof sits slightly below its mean; scale it so pure noise nets to zero
    unbias = (dof / stats.chi2.median(dof)) if dof else 1.0
    line_floor = float(np.median(psd[near])) * unbias if np.any(near) else 0.0
    background = float(np.median(psd[ref])) * unbias
    line = max(float((psd[in_line] - line_floor).sum()), 0.0)
    n_side, n_ref = int(np.count_nonzero(in_side)), int(np.count_nonzero(ref))
    plateau = float(np.median(psd[in_side])) * unbias
    level = plateau - background
    if dof:
        # Spread of the difference of two medians of noise-only bins: a median's standard error
        # is ~1.2533 sigma / sqrt(n), widened for the correlation of Hann-windowed bins.
        se = background * float(np.sqrt(2.0 / dof * HANN_SUM_VARIANCE)) * 1.2533 * float(np.sqrt(1.0 / n_side + 1.0 / n_ref))
        if level < stats.norm.isf(SIDEBAND_FALSE_ALARM_PROB) * se:
            level = 0.0
    # the lower side stands for both: double it so the ratio keeps its two-sided scale
    side = 2.0 * max(level, 0.0) * n_side
    # A carrier modulated by a tone (rather than by speech) shows discrete sideband LINES mirrored
    # either side of it instead of a plateau. Look for a mirrored pair, each above its own local
    # floor by a CFAR margin; offsets start at 300 Hz so a bare tone's leakage skirt - smooth, and
    # tracked by the local median - cannot qualify.
    if dof:
        side = max(side, _mirrored_line_power(freqs, psd, peak_freq, dof, unbias))
    if side <= 0.0:
        return float("-inf"), float("-inf")
    ratio_db = float(10.0 * np.log10(side / (line + 1e-30)))
    plateau_db = float(10.0 * np.log10(plateau / background)) if level > 0 and background > 0 else float("-inf")
    return ratio_db, plateau_db


def sideband_ratio_db(freqs: np.ndarray, psd: np.ndarray, peak_freq: float,
                      dof: float | None = None) -> float:
    """Sideband power relative to the line, in dB (see `sideband_measures`)."""
    return sideband_measures(freqs, psd, peak_freq, dof)[0]


def _mirrored_line_power(freqs: np.ndarray, psd: np.ndarray, peak_freq: float, dof: float,
                         unbias: float) -> float:
    """Power of the strongest mirrored sideband pair at |f - peak| in [300 Hz, SIDEBAND_HIGH], or 0."""
    df = float(freqs[1] - freqs[0])
    k0 = int(np.argmin(np.abs(freqs - peak_freq)))
    half = max(3, int(round(150.0 / df)))
    offsets = np.arange(int(np.ceil(300.0 / df)), int(SIDEBAND_HIGH / df) + 1)
    offsets = offsets[(k0 - offsets >= half) & (k0 + offsets < len(psd) - half)]
    if offsets.size == 0:
        return 0.0
    def local_floor(i: int) -> float:
        w = np.r_[psd[i - half:i - 1], psd[i + 2:i + half + 1]]    # excludes the bin and its neighbours
        return float(np.median(w)) * unbias
    # each noise bin ~ floor * chi2(dof)/dof; both bins of a pair must clear t, and the search covers
    # offsets.size pairs, so the per-bin tail probability is sqrt(SIDEBAND_LINE_FALSE_ALARM_PROB / n)
    t = float(stats.chi2.isf(np.sqrt(SIDEBAND_LINE_FALSE_ALARM_PROB / offsets.size), dof) / dof)
    best = 0.0
    for k in offsets:
        lo, hi = k0 - int(k), k0 + int(k)
        flo, fhi = local_floor(lo), local_floor(hi)
        if psd[lo] > t * flo and psd[hi] > t * fhi:
            best = max(best, float(psd[lo] - flo + psd[hi] - fhi))
    return best


def peak_narrowness(band: np.ndarray, k: int) -> float:
    lo_i, hi_i = max(0, k - NARROW_INNER), min(len(band), k + NARROW_INNER + 1)
    lo_o, hi_o = max(0, k - NARROW_OUTER), min(len(band), k + NARROW_OUTER + 1)
    outer = float(band[lo_o:hi_o].sum())
    return float(band[lo_i:hi_i].sum() / outer) if outer > 0 else 0.0


def noise_prominence_threshold_db(dof: float, n_bins: int,
                                  false_alarm_prob: float = NOISE_FALSE_ALARM_PROB) -> float:
    """Peak-to-median ratio (dB) that a flat noise band exceeds with probability `false_alarm_prob`.

    Each bin ~ chi2(dof)/dof. The max of n_bins independent bins exceeds t with
    probability 1 - F(t)^n_bins; solve for t, then divide by the median bin.
    """
    per_bin = -np.expm1(np.log1p(-false_alarm_prob) / max(n_bins, 1))
    peak = stats.chi2.isf(per_bin, dof)
    median = stats.chi2.median(dof)
    return float(10.0 * np.log10(peak / median))


class CarrierPeakDetector(Detector):
    name = "carrier_peak"

    def analyze_spectrum(self, spectrum: Spectrum) -> Finding:
        nyquist = spectrum.nyquist
        if nyquist <= HIGH_BAND_LOW:
            return Finding(
                name=self.name,
                risk=0.0,
                detail=(
                    f"Capture bandwidth too low to assess high-band carriers "
                    f"(Nyquist {nyquist/1000:.1f} kHz <= 15 kHz)."
                ),
                evidence={**_EMPTY_EVIDENCE, "assessable": False},
                assessable=False,
            )

        freqs, psd = spectrum.freqs, spectrum.psd
        mask = freqs >= HIGH_BAND_LOW
        if np.count_nonzero(mask) < 4:
            return Finding(
                name=self.name,
                risk=0.0,
                detail="Not enough high-band spectral resolution to assess.",
                evidence={**_EMPTY_EVIDENCE, "assessable": False},
                assessable=False,
            )

        # Energy gate: ignore peaks in a band that carries no real energy.
        high_fraction = band_power(freqs, psd, HIGH_BAND_LOW, nyquist) / total_power(freqs, psd)
        if high_fraction < MIN_HIGH_BAND_FRACTION:
            return Finding(
                name=self.name,
                risk=0.0,
                detail="No significant energy in the high band; no carrier to assess.",
                evidence={**_EMPTY_EVIDENCE, "assessable": True,
                          "high_band_fraction": float(high_fraction)},
            )

        band = psd[mask]
        band_freqs = freqs[mask]
        floor = float(np.median(band)) or 1e-20
        k = int(np.argmax(band))
        peak = float(band[k])
        peak_freq = float(band_freqs[k])
        prominence_db = float(10.0 * np.log10(peak / floor)) if floor > 0 else 0.0
        dof = spectrum.dof
        threshold_db = noise_prominence_threshold_db(dof, len(band))
        excess_db = prominence_db - threshold_db
        narrowness = peak_narrowness(band, k)
        narrow_factor = clip01((narrowness - NARROW_FLOOR) / (NARROW_FULL - NARROW_FLOOR))
        sideband_db, plateau_db = sideband_measures(freqs, psd, peak_freq, dof)
        # Modulation relative to the line decides the carrier's own risk. The plateau's height above
        # the background is reported too; on its own it is not trusted (noise that rises with
        # frequency lifts it), and the near-ultrasonic path pairs it with a test in time.
        modulation = clip01((sideband_db - SIDEBAND_FLOOR_DB) / (SIDEBAND_FULL_DB - SIDEBAND_FLOOR_DB))
        mod_factor = BARE_TONE_RISK_SCALE + (1.0 - BARE_TONE_RISK_SCALE) * modulation
        bare_tone = modulation <= 0.0

        prominence_risk = clip01(excess_db / PROMINENCE_DB_SATURATION) * narrow_factor
        risk = prominence_risk * mod_factor
        if risk >= 0.66:
            detail = (
                f"Dominant narrowband tone at {peak_freq/1000:.1f} kHz stands "
                f"{prominence_db:.0f} dB above the local noise floor with modulation "
                f"sidebands ({sideband_db:.0f} dB) - the signature of a modulated "
                f"ultrasonic carrier, not speech or music."
            )
        elif risk >= 0.33:
            detail = (
                f"Elevated narrowband energy at {peak_freq/1000:.1f} kHz "
                f"({prominence_db:.0f} dB above floor); unusual for speech."
            )
        elif bare_tone and excess_db > 0 and narrow_factor > 0:
            detail = (
                f"Steady unmodulated tone at {peak_freq/1000:.1f} kHz "
                f"({prominence_db:.0f} dB above floor, no sidebands above the noise): "
                f"a beacon, pilot or power-supply whine rather than a command carrier."
            )
        elif excess_db > 0 and narrow_factor < 1.0:
            detail = (
                f"High-band peak at {peak_freq/1000:.1f} kHz is broad (narrowness "
                f"{narrowness:.2f}) - a resonance or broadband content, not a carrier."
            )
        else:
            detail = "No dominant high-band carrier tone detected."

        return Finding(
            name=self.name,
            risk=risk,
            detail=detail,
            evidence={
                "assessable": True,
                "high_band_fraction": float(high_fraction),
                "peak_freq_hz": peak_freq,
                "prominence_db": prominence_db,
                "noise_threshold_db": float(threshold_db),
                "excess_db": float(excess_db),
                "narrowness": narrowness,
                "sideband_db": sideband_db if np.isfinite(sideband_db) else None,
                "sideband_plateau_db": plateau_db if np.isfinite(plateau_db) else None,
                "modulated": bool(not bare_tone),
                "prominence_risk": float(prominence_risk),
                "welch_dof": float(dof),
            },
        )
