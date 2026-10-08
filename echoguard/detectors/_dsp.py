"""Small shared DSP helpers used by the detectors."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal as sps

# np.trapz was removed in NumPy 2.0 in favour of np.trapezoid.
_trapz = getattr(np, "trapezoid", None) or getattr(np, "trapz")  # noqa: B009


@dataclass(frozen=True)
class Spectrum:
    """A Welch power spectral density of one clip, computed once and shared by all detectors."""

    freqs: np.ndarray
    psd: np.ndarray
    sample_rate: int
    n_samples: int

    @property
    def nyquist(self) -> float:
        return self.sample_rate / 2.0

    @property
    def dof(self) -> float:
        return welch_dof(self.n_samples, self.sample_rate)


def welch_nperseg(n: int, sample_rate: int) -> int:
    """Welch segment length: adapts to the clip so short fixtures still produce a usable spectrum."""
    return int(min(n, max(256, 2 ** int(np.floor(np.log2(max(sample_rate // 20, 256)))))))


def welch_psd(signal: np.ndarray, sample_rate: int):
    """Return (frequencies, power spectral density) via Welch's method."""
    n = len(signal)
    if n == 0:
        return np.array([0.0]), np.array([0.0])
    nperseg = welch_nperseg(n, sample_rate)
    freqs, psd = sps.welch(signal, fs=sample_rate, nperseg=nperseg)
    return freqs, psd


def compute_spectrum(signal: np.ndarray, sample_rate: int) -> Spectrum:
    freqs, psd = welch_psd(signal, sample_rate)
    return Spectrum(freqs=freqs, psd=psd, sample_rate=int(sample_rate), n_samples=len(signal))


def welch_dof(n: int, sample_rate: int) -> float:
    """Equivalent degrees of freedom of each bin of `welch_psd` for an n-sample clip.

    For a noise-like signal each Welch bin is distributed as S(f) * chi2(nu) / nu.
    With K Hann segments at 50% overlap, adjacent segments are correlated, so
    nu is a little below 2K (Welch 1967):

        nu = 2K / (1 + 2 * sum_{j=1}^{K-1} (1 - j/K) * rho(j)^2)

    where rho(j) is the window's normalised overlap correlation at lag j.
    Short clips have few segments, so their spectrum is noisier - this is what
    lets the carrier detector scale its threshold to the clip length.
    """
    if n <= 0:
        return 2.0
    nperseg = welch_nperseg(n, sample_rate)
    step = nperseg - nperseg // 2
    k = (n - nperseg) // step + 1
    w = sps.get_window("hann", nperseg)
    energy = float(np.sum(w ** 2))
    denom = 1.0
    for j in range(1, k):
        shift = j * step
        if shift >= nperseg:
            break
        rho = float(np.sum(w[: nperseg - shift] * w[shift:])) / energy
        denom += 2.0 * (1.0 - j / k) * rho ** 2
    return 2.0 * k / denom


def band_power(freqs: np.ndarray, psd: np.ndarray, low: float, high: float) -> float:
    """Integrate PSD over [low, high) Hz. Returns mean-square units (full scale = 1.0)."""
    mask = (freqs >= low) & (freqs < high)
    if np.count_nonzero(mask) < 2:
        return 0.0
    return float(_trapz(psd[mask], freqs[mask]))


def total_power(freqs: np.ndarray, psd: np.ndarray) -> float:
    total = float(_trapz(psd, freqs))
    return total if total > 0 else 1e-20


def power_to_dbfs(power: float) -> float:
    """Mean-square power -> dB relative to a full-scale square wave (mean square 1.0)."""
    return float(10.0 * np.log10(max(power, 1e-30)))


def rolloff_frequency(freqs: np.ndarray, psd: np.ndarray, fraction: float = 0.99) -> float:
    """Frequency below which `fraction` of the total spectral energy lies."""
    cumulative = np.cumsum(psd)
    if cumulative[-1] <= 0:
        return 0.0
    cumulative = cumulative / cumulative[-1]
    idx = int(np.searchsorted(cumulative, fraction))
    idx = min(idx, len(freqs) - 1)
    return float(freqs[idx])
