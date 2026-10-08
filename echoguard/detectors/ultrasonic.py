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

from .base import Detector, Finding, clip01
from ._dsp import Spectrum, band_power, total_power, power_to_dbfs

NEAR_ULTRASOUND_LOW = 18_000.0  # Hz
# Fraction of total energy above the threshold that we treat as fully suspicious.
SATURATION_RATIO = 0.10
# Absolute level (dBFS, mean-square re full scale) the 18 kHz+ band must reach
# before its ratio counts. On 307 real benign clips the 99th percentile of this
# level was -34 dBFS and the near-silent false flags sat at -79 to -51 dBFS.
MIN_BAND_LEVEL_DBFS = -60.0


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
                          "out_of_band_ratio": None, "out_of_band_level_dbfs": None},
                assessable=False,
            )

        freqs, psd = spectrum.freqs, spectrum.psd
        total = total_power(freqs, psd)
        oob = band_power(freqs, psd, NEAR_ULTRASOUND_LOW, nyquist)
        ratio = oob / total
        level_dbfs = power_to_dbfs(oob)
        evidence = {
            "nyquist_hz": float(nyquist),
            "assessable": True,
            "out_of_band_ratio": float(ratio),
            "out_of_band_level_dbfs": level_dbfs,
        }

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
