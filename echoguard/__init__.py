"""EchoGuard - a baseline open-source detector for inaudible / injected voice-command attacks.

EchoGuard is a *defensive* tool. It inspects a captured audio clip for the
acoustic fingerprints that known voice-assistant attacks leave behind
(out-of-band / near-ultrasound energy, a modulated high-frequency carrier,
and a frequency profile inconsistent with human speech) and returns a risk
verdict with human-readable reasons.

It does not generate attacks and contains no attack payloads. See README.md
for scope, limitations, and the research context.
"""

from .pipeline import Pipeline, Report, WindowedReport, StreamAnalyzer, InvalidInput
from .detectors.base import Detector, Finding

__all__ = ["Pipeline", "Report", "WindowedReport", "StreamAnalyzer", "InvalidInput",
           "Detector", "Finding"]
__version__ = "0.2.0"
