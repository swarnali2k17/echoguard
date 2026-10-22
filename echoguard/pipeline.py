"""The detection pipeline: validate input, run detectors, aggregate into a verdict.

Two entry points:

- `Pipeline.analyze(signal, sample_rate)` scores one clip as a whole.
- `Pipeline.analyze_windows(signal, sample_rate)` scores overlapping windows
  and returns the per-window reports plus a summary whose verdict is the
  worst window. Use this for anything longer than a few seconds: a short
  injection inside a long recording is averaged away by a single whole-clip
  spectrum, but not by a 1 s window.

`StreamAnalyzer` wraps `analyze_windows` for audio that arrives in chunks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Iterator, List, Optional

import numpy as np

from .audio import to_float_mono
from .detectors import default_detectors
from .detectors.base import Detector, Finding
from .detectors._dsp import band_power, compute_spectrum, power_to_dbfs, total_power
from .detectors.base import clip01

CLEAR = "CLEAR"
SUSPICIOUS = "SUSPICIOUS"
HIGH_RISK = "HIGH_RISK"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

VERDICTS = (CLEAR, SUSPICIOUS, HIGH_RISK, INSUFFICIENT_DATA)
_SEVERITY = {CLEAR: 0, INSUFFICIENT_DATA: 1, SUSPICIOUS: 2, HIGH_RISK: 3}

SUSPICIOUS_RISK = 0.33
HIGH_RISK_RISK = 0.66

# Fewer samples than this cannot produce a meaningful spectrum at any rate.
MIN_SAMPLES = 64


class InvalidInput(ValueError):
    """The audio cannot be analysed: empty, non-finite, or a nonsensical sample rate."""


@dataclass
class Report:
    """Aggregated result over all detectors."""

    overall_risk: float
    verdict: str
    findings: list[Finding] = field(default_factory=list)
    sample_rate: int = 0
    duration_sec: float = 0.0
    start_sec: float = 0.0

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "overall_risk": round(float(self.overall_risk), 3),
            "sample_rate": int(self.sample_rate),
            "duration_sec": round(float(self.duration_sec), 3),
            "start_sec": round(float(self.start_sec), 3),
            "findings": [
                {
                    "name": f.name,
                    "risk": round(float(f.risk), 3),
                    "severity": f.severity,
                    "assessable": bool(f.assessable),
                    "detail": f.detail,
                    "evidence": f.evidence,
                }
                for f in self.findings
            ],
        }


@dataclass
class WindowedReport:
    """Per-window reports over a clip, plus the worst window as the summary."""

    summary: Report
    windows: List[Report]
    window_sec: float
    hop_sec: float

    def to_dict(self) -> dict:
        d = self.summary.to_dict()
        d["window_sec"] = self.window_sec
        d["hop_sec"] = self.hop_sec
        d["windows"] = [
            {"start_sec": w.start_sec, "verdict": w.verdict, "overall_risk": round(float(w.overall_risk), 3)}
            for w in self.windows
        ]
        return d


def validate_input(signal, sample_rate) -> tuple[np.ndarray, int]:
    """Coerce to a 1-D float64 mono array and a positive int sample rate, or raise InvalidInput."""
    try:
        sr = int(sample_rate)
    except (TypeError, ValueError) as exc:
        raise InvalidInput(f"sample_rate must be a positive integer, got {sample_rate!r}") from exc
    if sr <= 0:
        raise InvalidInput(f"sample_rate must be positive, got {sr}")
    try:
        arr = np.asarray(signal)
    except Exception as exc:  # noqa: BLE001
        raise InvalidInput(f"signal is not array-like: {exc}") from exc
    if arr.dtype.kind not in "fiu":
        raise InvalidInput(f"signal must be numeric, got dtype {arr.dtype}")
    if arr.ndim == 0 or arr.size == 0:
        raise InvalidInput("signal is empty")
    if arr.ndim > 2:
        raise InvalidInput(f"signal must be 1-D (mono) or 2-D (samples x channels), got {arr.ndim}-D")
    mono = to_float_mono(arr).astype(np.float64)
    if not np.all(np.isfinite(mono)):
        raise InvalidInput("signal contains NaN or infinite samples")
    if mono.size < MIN_SAMPLES:
        raise InvalidInput(f"signal too short: {mono.size} samples (need at least {MIN_SAMPLES})")
    return mono, sr


# A carrier at 15-18 kHz sits below the 18 kHz out-of-band edge, so the
# out-of-band detector is blind to it by construction (R = sqrt(0 x 1) = 0).
# SWhisper's published hardware puts its carrier at ~16 kHz. This band also
# holds beacons, power-supply whine and codec artefacts, so the evidence here
# is stricter than above 18 kHz. All of it must hold:
#   - the carrier detector found a narrow, prominent peak at 15-18 kHz;
#   - its sideband plateau stands above the background (sideband_plateau_db);
#   - the sideband energy FLUCTUATES in time far more than the background
#     band's does: a spoken command modulates it syllable by syllable, while
#     noise - including noise that rises with frequency, which can lift a
#     plateau - is steady (variance of log band energy over ~20 ms frames
#     against the steady-noise variance, chi-square test).
# The energy concentrated around the carrier (from 12 kHz up, above any speech
# content) then stands in for the out-of-band share.
NEAR_CARRIER_LOW, NEAR_CARRIER_HIGH = 15_000.0, 18_000.0
NEAR_BAND_FLOOR_HZ = 12_000.0
NEAR_BAND_HALF_WIDTH = 4_000.0
FLUCTUATION_FALSE_ALARM_PROB = 1e-3
# A 15-18 kHz flag must hold for this many consecutive windows (1 s windows, 0.5 s hop: ~2 s).
# On 40 benign 60 s AAC-encoded tones at 15-18 kHz (white, pink, shaped dither) no run of flagged
# windows exceeded 2; SWhisper's carrier keeps 214 runs of 3+ windows (30% of its 58 minutes).
NEAR_PERSIST_WINDOWS = 3
MIN_FLUCTUATION_FRAMES = 16


def sideband_fluctuation(mono: np.ndarray, sr: int, fc: float) -> tuple[float, float] | None:
    """(ratio, critical ratio): variance in time of the sideband band's log energy, over what steady
    noise would give.

    The reference is theoretical, not another band: each frame's band energy is a sum of n
    Hann-windowed periodogram bins, whose log has variance ~ HANN_SUM_VARIANCE / n when the content
    is steady (noise, a beacon's leakage, dither however it is shaped). A command fluctuates syllable
    by syllable, far above that. A neighbouring band cannot serve as the reference: speech that is not
    band-limited before modulation spreads sideband tails into it, so it flickers with the command.
    Codec frame-gain flicker can also exceed the steady-noise variance; that is what the persistence
    rule in `analyze_windows` answers.
    """
    from scipy import stats
    from .detectors.modulation import HANN_SUM_VARIANCE
    nfft = int(2 ** round(np.log2(0.02 * sr)))
    k = len(mono) // nfft
    if k < MIN_FLUCTUATION_FRAMES:
        return None
    frames = mono[:k * nfft].reshape(k, nfft) * np.hanning(nfft)
    p = np.abs(np.fft.rfft(frames, axis=1)) ** 2
    f = np.fft.rfftfreq(nfft, 1.0 / sr)
    off = fc - f
    side = (off >= 150.0) & (off <= 4_000.0)
    n = int(np.count_nonzero(side))
    if n < 4:
        return None
    es = np.log(p[:, side].sum(axis=1) + 1e-30)
    steady = HANN_SUM_VARIANCE / n
    ratio = float(np.var(es, ddof=1)) / steady
    return ratio, float(stats.chi2.isf(FLUCTUATION_FALSE_ALARM_PROB, k - 1) / (k - 1))


def near_ultrasonic_finding(spectrum, findings, mono: np.ndarray | None = None) -> Finding:
    """Always returns a finding (stable JSON). Its risk is this path's own corroborated score."""
    from .detectors.modulation import PLATEAU_FLOOR_DB, PLATEAU_FULL_DB
    from .detectors.ultrasonic import MIN_BAND_LEVEL_DBFS, SATURATION_RATIO
    keys = {"assessable": True, "carrier_hz": None, "band_hz": None, "band_share": None,
            "band_level_dbfs": None, "sideband_plateau_db": None, "fluctuation_f": None, "fluctuation_f_crit": None}
    def result(risk: float, detail: str, **ev) -> Finding:
        return Finding(name="near_ultrasonic_carrier", risk=risk, detail=detail, evidence={**keys, **ev})
    none = "No modulated carrier between 15 and 18 kHz."
    carrier = next((f for f in findings if f.name == "carrier_peak"), None)
    if carrier is None or not carrier.assessable:
        return result(0.0, none)
    ev = carrier.evidence
    fc, plateau, prom = ev.get("peak_freq_hz"), ev.get("sideband_plateau_db"), ev.get("prominence_risk") or 0.0
    if fc is None or not (NEAR_CARRIER_LOW <= fc < NEAR_CARRIER_HIGH) or prom <= 0.0:
        return result(0.0, none)
    if plateau is None or plateau < PLATEAU_FLOOR_DB:
        return result(0.0, f"Tone at {fc/1000:.1f} kHz has no sideband plateau above the noise: a beacon, not a carrier.",
                      carrier_hz=float(fc), sideband_plateau_db=plateau)
    fl = sideband_fluctuation(mono, spectrum.sample_rate, fc) if mono is not None else None
    if fl is None:
        return result(0.0, f"Tone at {fc/1000:.1f} kHz: clip too short to test whether its sidebands carry a command.",
                      carrier_hz=float(fc), sideband_plateau_db=float(plateau))
    F, crit = fl
    if not F > crit:
        return result(0.0, (f"Tone at {fc/1000:.1f} kHz sits on a steady raised floor (sidebands do not fluctuate: "
                            f"F {F:.1f} < {crit:.1f}) - noise shaping or a codec, not a command."),
                      carrier_hz=float(fc), sideband_plateau_db=float(plateau), fluctuation_f=F, fluctuation_f_crit=crit)
    lo = max(NEAR_BAND_FLOOR_HZ, fc - NEAR_BAND_HALF_WIDTH)
    hi = min(spectrum.nyquist, fc + NEAR_BAND_HALF_WIDTH)
    share = band_power(spectrum.freqs, spectrum.psd, lo, hi) / total_power(spectrum.freqs, spectrum.psd)
    level = power_to_dbfs(band_power(spectrum.freqs, spectrum.psd, lo, hi))
    share_risk = 0.0 if level < MIN_BAND_LEVEL_DBFS else clip01(share / SATURATION_RATIO)
    mod_conf = clip01((plateau - PLATEAU_FLOOR_DB) / (PLATEAU_FULL_DB - PLATEAU_FLOOR_DB))
    risk = float((share_risk * mod_conf * prom) ** 0.5)
    return result(risk, (f"Speech-modulated carrier at {fc/1000:.1f} kHz, below the 18 kHz edge: sidebands "
                         f"{plateau:.0f} dB above the background and fluctuating like speech (F {F:.0f}); "
                         f"{share*100:.1f}% of the energy sits around it ({lo/1000:.0f}-{hi/1000:.0f} kHz)."),
                  carrier_hz=float(fc), band_hz=[float(lo), float(hi)], band_share=float(share),
                  band_level_dbfs=float(level), sideband_plateau_db=float(plateau), fluctuation_f=F, fluctuation_f_crit=crit)


class Pipeline:
    """Runs a set of detectors over a signal and aggregates their findings.

    The overall risk is a *corroborated* injection score: the geometric mean
    of the out-of-band-energy and carrier-peak risks, so both signatures must
    be present. See `_injection_score` for why. The spectral-profile detector
    is reported as context only.
    """

    def __init__(self, detectors: list[Detector] | None = None):
        self.detectors = detectors if detectors is not None else default_detectors()

    def analyze(self, signal, sample_rate) -> Report:
        mono, sr = validate_input(signal, sample_rate)
        report = self._analyze_clean(mono, sr)
        if self._near_only(report):
            # one spectrum cannot show persistence: confirm a 15-18 kHz flag inside the clip, or set it aside
            win, hop = sr, sr // 2
            windows = [self._analyze_clean(mono[s:s + win], sr, s / sr) for s in range(0, len(mono) - win + 1, hop)]
            if not any(self._near_only(r) for r in self._require_persistence(windows)):
                report = self._without_near(report)
        return report

    def _analyze_clean(self, mono: np.ndarray, sr: int, start_sec: float = 0.0) -> Report:
        spectrum = compute_spectrum(mono, sr)
        findings = [d.analyze(mono, sr, spectrum) for d in self.detectors]
        findings.append(near_ultrasonic_finding(spectrum, findings, mono))
        risks = {f.name: f.risk for f in findings}
        any_unassessable = any(not f.assessable for f in findings)

        overall = self._injection_score(risks)
        verdict = self._verdict(overall, any_unassessable)
        return Report(
            overall_risk=overall,
            verdict=verdict,
            findings=findings,
            sample_rate=sr,
            duration_sec=len(mono) / sr,
            start_sec=start_sec,
        )

    def analyze_windows(self, signal, sample_rate, window_sec: float = 1.0,
                        hop_sec: float = 0.5) -> WindowedReport:
        """Score overlapping windows; the summary verdict is the worst window's.

        A clip shorter than one window is scored as a single window.
        """
        mono, sr = validate_input(signal, sample_rate)
        if window_sec <= 0 or hop_sec <= 0:
            raise InvalidInput("window_sec and hop_sec must be positive")
        win = max(MIN_SAMPLES, int(round(window_sec * sr)))
        hop = max(1, int(round(hop_sec * sr)))
        if len(mono) <= win:
            reports = [self._analyze_clean(mono, sr)]
        else:
            starts = list(range(0, len(mono) - win + 1, hop))
            if starts[-1] + win < len(mono):
                starts.append(len(mono) - win)  # cover the tail
            reports = [self._analyze_clean(mono[s:s + win], sr, start_sec=s / sr) for s in starts]
            reports = self._require_persistence(reports)
        return WindowedReport(
            summary=self._summarise(reports, len(mono) / sr),
            windows=reports,
            window_sec=window_sec,
            hop_sec=hop_sec,
        )

    @staticmethod
    def _near_only(report: Report) -> bool:
        """Flagged only through the 15-18 kHz path (the established >18 kHz path is quiet)."""
        risks = {f.name: f.risk for f in report.findings}
        main = (risks.get("out_of_band_energy", 0.0) * risks.get("carrier_peak", 0.0)) ** 0.5
        return main < SUSPICIOUS_RISK <= risks.get("near_ultrasonic_carrier", 0.0)

    @classmethod
    def _without_near(cls, report: Report) -> Report:
        """The window's verdict with an unpersistent 15-18 kHz flag set aside, and saying so."""
        risks = {f.name: f.risk for f in report.findings}
        main = float((risks.get("out_of_band_energy", 0.0) * risks.get("carrier_peak", 0.0)) ** 0.5)
        findings = []
        for f in report.findings:
            if f.name == "near_ultrasonic_carrier":
                f = Finding(name=f.name, risk=0.0, assessable=f.assessable, evidence={**f.evidence},
                            detail=f.detail + " Not persistent (under ~2 s) - set aside: codec artefacts "
                                              "around a loud tone can mimic it for a moment; a command persists.")
            findings.append(f)
        return Report(overall_risk=main, verdict=cls._verdict(main, any(not f.assessable for f in findings)),
                      findings=findings, sample_rate=report.sample_rate, duration_sec=report.duration_sec,
                      start_sec=report.start_sec)

    @classmethod
    def _require_persistence(cls, reports: List[Report]) -> List[Report]:
        """A 15-18 kHz flag stands only within a run of NEAR_PERSIST_WINDOWS consecutive windows
        that all carry it (~2 s at the default 1 s / 0.5 s windows): codec artefacts around a loud
        tone mimic a modulated carrier for a moment; a command persists."""
        near = [cls._near_only(r) for r in reports]
        keep = [False] * len(reports)
        i = 0
        while i < len(reports):
            if near[i]:
                j = i
                while j < len(reports) and near[j]:
                    j += 1
                if j - i >= NEAR_PERSIST_WINDOWS:
                    keep[i:j] = [True] * (j - i)
                i = j
            else:
                i += 1
        return [cls._without_near(r) if near[k] and not keep[k] else r for k, r in enumerate(reports)]

    @staticmethod
    def _summarise(reports: List[Report], total_sec: float) -> Report:
        worst = max(reports, key=lambda r: (_SEVERITY[r.verdict], r.overall_risk))
        return Report(
            overall_risk=worst.overall_risk,
            verdict=worst.verdict,
            findings=worst.findings,
            sample_rate=worst.sample_rate,
            duration_sec=total_sec,
            start_sec=worst.start_sec,
        )

    @staticmethod
    def _injection_score(risks: dict) -> float:
        """Corroborated injection score.

        An ultrasonic/near-ultrasound injection shows up as BOTH significant
        out-of-band energy AND a narrowband high-frequency carrier. Benign
        audio trips at most one: broadband noise has out-of-band energy but no
        carrier; a tonal/edge artefact has a peak but no broadband out-of-band
        energy. The geometric mean of the two primary detectors requires both
        to be present, which collapses those benign false positives.

        The spectral-profile detector is deliberately NOT part of the score: on
        its own it fires on any music or noise. It stays in the findings as
        context, but it no longer drives the verdict.
        """
        oob = risks.get("out_of_band_energy", 0.0)
        carrier = risks.get("carrier_peak", 0.0)
        # A modulated carrier at 15-18 kHz puts no energy above 18 kHz; its own path
        # (near_ultrasonic_finding) is already a corroborated score.
        return float(max((oob * carrier) ** 0.5, risks.get("near_ultrasonic_carrier", 0.0)))

    @staticmethod
    def _verdict(risk: float, any_unassessable: bool = False) -> str:
        if risk >= HIGH_RISK_RISK:
            return HIGH_RISK
        if risk >= SUSPICIOUS_RISK:
            return SUSPICIOUS
        # Nothing flagged - but if a key detector couldn't even assess the
        # clip (e.g. bandwidth too low to see the ultrasonic band), we must
        # not pass it off as CLEAR. "Couldn't check" is not "checked and clean".
        if any_unassessable:
            return INSUFFICIENT_DATA
        return CLEAR


class StreamAnalyzer:
    """Feed audio in chunks; get a Report for every completed window.

    >>> s = StreamAnalyzer(48_000)
    >>> for chunk in chunks:            # any chunk sizes
    ...     for report in s.push(chunk):
    ...         ...
    >>> for report in s.flush(): ...    # score the partial tail, if any
    """

    def __init__(self, sample_rate: int, window_sec: float = 1.0, hop_sec: float = 0.5,
                 pipeline: Optional[Pipeline] = None):
        self.sr = int(sample_rate)
        if self.sr <= 0:
            raise InvalidInput("sample_rate must be positive")
        self.win = max(MIN_SAMPLES, int(round(window_sec * self.sr)))
        self.hop = max(1, int(round(hop_sec * self.sr)))
        self.pipeline = pipeline or Pipeline()
        self._buf = np.zeros(0, dtype=np.float64)
        self._consumed = 0  # samples before the start of _buf
        self._near_run = 0  # consecutive windows flagged only through the 15-18 kHz path

    def push(self, chunk) -> Iterator[Report]:
        arr = np.asarray(chunk)
        if arr.size == 0:
            return iter(())
        mono = to_float_mono(arr).astype(np.float64)
        if not np.all(np.isfinite(mono)):
            raise InvalidInput("chunk contains NaN or infinite samples")
        self._buf = np.concatenate([self._buf, mono])
        return self._drain(final=False)

    def flush(self) -> Iterator[Report]:
        return self._drain(final=True)

    def _drain(self, final: bool) -> Iterator[Report]:
        out: List[Report] = []
        while len(self._buf) >= self.win:
            start = self._consumed / self.sr
            r = self.pipeline._analyze_clean(self._buf[:self.win], self.sr, start_sec=start)
            # a 15-18 kHz flag needs the previous window to carry it too (see _require_persistence)
            near = Pipeline._near_only(r)
            self._near_run = self._near_run + 1 if near else 0
            if near and self._near_run < NEAR_PERSIST_WINDOWS:
                r = Pipeline._without_near(r)
            out.append(r)
            self._buf = self._buf[self.hop:]
            self._consumed += self.hop
        if final and len(self._buf) >= MIN_SAMPLES:
            out.append(self.pipeline._analyze_clean(self._buf, self.sr, start_sec=self._consumed / self.sr))
            self._consumed += len(self._buf)
            self._buf = np.zeros(0, dtype=np.float64)
        return iter(out)


def iter_windows(signal, sample_rate, window_sec: float = 1.0, hop_sec: float = 0.5) -> Iterable[Report]:
    """Convenience generator over `Pipeline.analyze_windows`."""
    return Pipeline().analyze_windows(signal, sample_rate, window_sec, hop_sec).windows
