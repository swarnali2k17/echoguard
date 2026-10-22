"""Out-of-band / near-ultrasound energy detector.

Many inaudible-command attacks (DolphinAttack-class ultrasonic injection, NUIT
near-ultrasound in media) deposit energy above the human-voice band. Genuine
speech has almost no energy above ~8 kHz and essentially none above ~16 kHz.
A clip with significant energy in the 18 kHz+ region is therefore a strong
signal of injection or near-ultrasound carriage.

This detector needs bandwidth: if the capture's Nyquist frequency does not
exceed 18 kHz (sample rate at or below 36 kHz) it cannot assess the band and
says so rather than returning a false "clear".

It also needs signal: the out-of-band *ratio* is meaningless when the band
holds almost nothing in absolute terms. On real recordings the only content
above 18 kHz in a near-silent tail is dither or mic self-noise at -70 dBFS,
which can be 100% of a tiny total. An absolute level floor rejects that.
"""

from __future__ import annotations

import numpy as np

from .base import Detector, Finding, clip01
from ._dsp import Spectrum, band_power, total_power, power_to_dbfs

NEAR_ULTRASOUND_LOW = 18_000.0  # Hz
# Fraction of total energy above the threshold that we treat as fully suspicious.
SATURATION_RATIO = 0.10
# Absolute level (dBFS, mean-square re full scale) the 18 kHz+ band must reach
# before its ratio counts. On 307 real benign clips the 99th percentile of this
# level was -34 dBFS and the near-silent false flags sat at -79 to -51 dBFS.
MIN_BAND_LEVEL_DBFS = -60.0
# A capture whose 18 kHz+ band sits below any real microphone or converter noise
# floor never recorded that band: a codec (AAC, MP3) or resampler removed it.
# MetaAttack's published demos measure -119 to -129 dBFS there; live 48 kHz
# captures sit near -90 to -100 dBFS. Such a capture cannot be cleared.
ABSENT_BAND_DBFS = -110.0
# A converter or codec low-pass is a cliff: the spectrum falls by this much
# within about a kilohertz onto a flat noise floor. Natural roll-off of speech
# and music is gradual (tens of dB per octave, not per kilohertz).
CUTOFF_DROP_DB = 35.0


def lowpass_cutoff_hz(freqs: np.ndarray, psd: np.ndarray) -> float | None:
    """Frequency of a brick-wall low-pass in the capture, or None.

    Looks for the steepest fall between the 0.25-0.75 kHz just below a candidate
    edge and the 0.25-0.75 kHz just above it, from 3 kHz up to 1 kHz short of
    Nyquist, and requires everything above the edge to stay at least 25 dB under
    the content below it (resampler images and codec residue allowed). A
    "48 kHz" file whose content stops at 7.5 kHz (audio once captured at 16 kHz)
    has one; so does an AAC encode that cuts at 18 kHz.
    """
    if len(freqs) < 16:
        return None
    df = float(freqs[1] - freqs[0])
    db = 10.0 * np.log10(psd + 1e-30)
    lo_w, hi_w = int(round(250.0 / df)), int(round(750.0 / df))
    best_drop, best_i = 0.0, None
    for i in range(int(3000.0 / df), len(freqs) - int(1000.0 / df)):
        below = np.median(db[max(0, i - hi_w):i - lo_w + 1])
        above = np.median(db[i + lo_w:i + hi_w + 1])
        if below - above > best_drop:
            best_drop, best_i = float(below - above), i
    if best_i is None or best_drop < CUTOFF_DROP_DB:
        return None
    content = float(np.median(db[max(0, best_i - hi_w):best_i - lo_w + 1]))
    tail = db[best_i + lo_w:]
    if np.percentile(tail, 95) > content - 25.0:
        return None    # something above the edge comes close to the content: it continues
    return float(freqs[best_i])


class OutOfBandEnergyDetector(Detector):
    name = "out_of_band_energy"

    def analyze_spectrum(self, spectrum: Spectrum) -> Finding:
        nyquist = spectrum.nyquist
        if nyquist <= NEAR_ULTRASOUND_LOW:
            return Finding(
                name=self.name,
                risk=0.0,
                detail=(
                    f"Capture bandwidth too low to assess ultrasonic bands "
                    f"(Nyquist {nyquist/1000:.1f} kHz <= 18 kHz). "
                    f"Re-capture at >= 44.1 kHz to enable this check."
                ),
                evidence={"nyquist_hz": float(nyquist), "assessable": False,
                          "out_of_band_ratio": None, "out_of_band_level_dbfs": None,
                          "content_cutoff_hz": None},
                assessable=False,
            )

        freqs, psd = spectrum.freqs, spectrum.psd
        total = total_power(freqs, psd)
        oob = band_power(freqs, psd, NEAR_ULTRASOUND_LOW, nyquist)
        ratio = oob / total
        level_dbfs = power_to_dbfs(oob)
        cutoff = lowpass_cutoff_hz(freqs, psd)
        evidence = {
            "nyquist_hz": float(nyquist),
            "assessable": True,
            "out_of_band_ratio": float(ratio),
            "out_of_band_level_dbfs": level_dbfs,
            "content_cutoff_hz": cutoff,
        }

        # The container may claim 48 kHz while the audio inside never held the band: judge the
        # content, not the sample rate. "Not recorded" is not "clean".
        if level_dbfs < ABSENT_BAND_DBFS or (cutoff is not None and cutoff < NEAR_ULTRASOUND_LOW):
            where = (f"content stops at {cutoff/1000:.1f} kHz" if cutoff is not None
                     else f"the band above 18 kHz is at {level_dbfs:.0f} dBFS, below any microphone noise floor")
            return Finding(
                name=self.name,
                risk=0.0,
                detail=(f"The ultrasonic band was not captured: {where} (a codec or converter low-pass). "
                        f"It cannot be checked, so it cannot be cleared."),
                evidence={**evidence, "assessable": False},
                assessable=False,
            )

        if level_dbfs < MIN_BAND_LEVEL_DBFS:
            return Finding(
                name=self.name,
                risk=0.0,
                detail=(
                    f"Energy above 18 kHz is {level_dbfs:.0f} dBFS - below the "
                    f"{MIN_BAND_LEVEL_DBFS:.0f} dBFS floor; too little to assess a ratio against."
                ),
                evidence=evidence,
            )

        risk = clip01(ratio / SATURATION_RATIO)
        if risk >= 0.66:
            detail = (
                f"{ratio*100:.1f}% of signal energy sits above 18 kHz - well beyond "
                f"the human-voice band. Consistent with ultrasonic/near-ultrasound injection."
            )
        elif risk >= 0.33:
            detail = (
                f"{ratio*100:.1f}% of signal energy is above 18 kHz - higher than "
                f"expected for speech; worth a closer look."
            )
        else:
            detail = f"Negligible energy above 18 kHz ({ratio*100:.2f}%); consistent with normal audio."

        return Finding(name=self.name, risk=risk, detail=detail, evidence=evidence)
