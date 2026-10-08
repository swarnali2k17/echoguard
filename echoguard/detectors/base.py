"""Detector base classes and the Finding result type."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ._dsp import Spectrum, compute_spectrum


@dataclass
class Finding:
    """One detector's verdict on a signal.

    risk is a float in [0, 1]. severity is a coarse label derived from risk.
    detail is a short human-readable explanation. evidence holds the numbers
    behind the decision so a reviewer can audit it; values are plain Python
    floats/bools/None so the report serialises as strict JSON.
    """

    name: str
    risk: float
    detail: str
    evidence: dict = field(default_factory=dict)
    assessable: bool = True  # False when the detector could not evaluate (e.g. too little bandwidth)

    @property
    def severity(self) -> str:
        if not self.assessable:
            return "n/a"
        if self.risk >= 0.66:
            return "high"
        if self.risk >= 0.33:
            return "suspicious"
        return "clear"


class Detector:
    """Base class. A detector inspects a signal and returns a Finding.

    `spectrum` is the clip's shared Welch PSD; the pipeline computes it once
    and passes it to every detector. Calling a detector on its own without a
    spectrum still works: it computes its own.
    """

    name: str = "detector"

    def analyze(self, signal: np.ndarray, sample_rate: int,
                spectrum: Optional[Spectrum] = None) -> Finding:
        if spectrum is None:
            spectrum = compute_spectrum(np.asarray(signal, dtype=np.float64), int(sample_rate))
        return self.analyze_spectrum(spectrum)

    def analyze_spectrum(self, spectrum: Spectrum) -> Finding:  # pragma: no cover
        raise NotImplementedError


def clip01(x: float) -> float:
    """Clamp a value to [0, 1]. Non-finite input clamps to 0 (no evidence, no risk)."""
    x = float(x)
    if not math.isfinite(x):
        return 0.0
    return max(0.0, min(1.0, x))
