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
from .detectors._dsp import compute_spectrum

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
        return self._analyze_clean(mono, sr)

    def _analyze_clean(self, mono: np.ndarray, sr: int, start_sec: float = 0.0) -> Report:
        spectrum = compute_spectrum(mono, sr)
        findings = [d.analyze(mono, sr, spectrum) for d in self.detectors]
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
        return WindowedReport(
            summary=self._summarise(reports, len(mono) / sr),
            windows=reports,
            window_sec=window_sec,
            hop_sec=hop_sec,
        )

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
        return float((oob * carrier) ** 0.5)

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
            out.append(self.pipeline._analyze_clean(self._buf[:self.win], self.sr, start_sec=start))
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
