"""Per-clip baseband features for post-ADC ultrasonic-injection detection.

All features operate on a captured clip at `fs` (48 kHz or 16 kHz); nothing
above Nyquist is assumed to exist.

Feature groups
  a) ghost:      sub-100 Hz energy fraction; 20-100 / 100-300 Hz ratio
  b) line:       strongest narrow spectral line in the top band, prominence over
                 local median, and its frame-to-frame stability
  c) modulation: 300-3400 Hz envelope modulation spectrum ratio (0.5-4 / 4-16 Hz),
                 envelope-vs-envelope^2 correlation, envelope kurtosis
  d) bicoherence summary (mean / max / frac>0.5) over 0-4 kHz
  e) harmonicity: autocorr HNR, cepstral peak prominence, voiced fraction
  f) spectral flatness / entropy 0-4 kHz; 2nd-harmonic distortion of LF peaks
"""
from __future__ import annotations

import numpy as np
from scipy import signal as sps
from scipy.ndimage import median_filter

FEATURE_NAMES = [
    "a_sub20_frac", "a_sub100_frac", "a_ratio_20_100_vs_100_300", "a_lf2_20_vs_speech_db",
    "a_lf_rate_2_20_vs_0p5_2_db", "a_lf2_20_dbfs",
    "b_line_prom_db", "b_line_stability", "b_line_freq_norm", "b_line_sideband_db",
    "c_mod_ratio_lo_hi", "c_env_sq_corr", "c_env_kurt",
    "d_bicoh_mean", "d_bicoh_max", "d_bicoh_frac05", "d_bicoh_lf_speech",
    "e_hnr_db", "e_cpp_db", "e_voiced_frac",
    "f_flatness_0_4k", "f_entropy_0_4k", "f_h2_distortion_db",
]


def _welch(x, fs, nperseg):
    f, p = sps.welch(x, fs=fs, nperseg=min(nperseg, len(x)), noverlap=None)
    return f, p + 1e-20


def _band(f, p, lo, hi):
    m = (f >= lo) & (f < hi)
    return float(np.trapezoid(p[m], f[m])) if m.sum() > 1 else 0.0


# ---------------------------------------------------------------- a) ghost
def ghost_features(x, fs):
    f, p = _welch(x, fs, 8192 if fs >= 32000 else 4096)   # ~5.9 Hz / 3.9 Hz bins
    r = _band(f, p, 20, 100) / (_band(f, p, 100, 300) + 1e-20)
    # time-domain LF band powers on a 400 Hz decimated copy (well-conditioned filters;
    # a sub-Hz amplitude drift of a strong tone must not leak into the 2-20 Hz band)
    fl = 400
    xl = sps.resample_poly(x, 1, int(fs // fl))
    def bp(lo, hi, order=4):
        sos = sps.butter(order, [lo, hi], btype="bandpass", fs=fl, output="sos")
        return float(np.mean(sps.sosfiltfilt(sos, xl) ** 2))
    e2_20 = bp(2.0, 20.0)
    e2_100 = bp(2.0, 99.0)
    e_lo = bp(0.5, 2.0, order=2)
    sos = sps.butter(4, [300.0, 3400.0], btype="bandpass", fs=fs, output="sos")
    e_sp = float(np.mean(sps.sosfiltfilt(sos, x) ** 2))
    tot = float(np.mean(x ** 2)) + 1e-20
    return [e2_20 / tot, e2_100 / tot, 10 * np.log10(r + 1e-12),
            10 * np.log10((e2_20 + 1e-20) / (e_sp + 1e-20)),
            10 * np.log10((e2_20 + 1e-20) / (e_lo + 1e-20)),
            10 * np.log10(e2_20 + 1e-20)]


# ---------------------------------------------------------------- b) line
def line_features(x, fs):
    # top band: 8 kHz..Nyquist at 48k; the analogous upper half-band at 16k
    lo = 8000.0 if fs >= 32000 else 0.5 * fs / 2
    nfft = 16384 if fs >= 32000 else 8192
    f, p = _welch(x, fs, nfft)
    pdb = 10 * np.log10(p)
    df = f[1] - f[0]
    w = int(max(3, round(300.0 / df)))        # +/-150 Hz local median window
    med = median_filter(pdb, size=2 * w + 1, mode="nearest")
    m = (f >= lo) & (f < fs / 2 * 0.98)
    prom = pdb[m] - med[m]
    k = int(np.argmax(prom))
    peak_f = f[m][k]
    prom_db = float(prom[k])
    # stability: STFT frames, is the per-frame peak within +/-2 bins of peak_f?
    nper = 4096 if fs >= 32000 else 2048
    fS, _, Z = sps.stft(x, fs=fs, nperseg=nper, noverlap=nper // 2, padded=False)
    P = 10 * np.log10(np.abs(Z) ** 2 + 1e-20)
    mS = (fS >= lo) & (fS < fs / 2 * 0.98)
    dfS = fS[1] - fS[0]
    wS = int(max(3, round(300.0 / dfS)))
    medS = median_filter(P, size=(2 * wS + 1, 1), mode="nearest")
    promS = (P - medS)[mS]                  # (bins, frames)
    fb = fS[mS]
    target = int(np.argmin(np.abs(fb - peak_f)))
    pk = np.argmax(promS, axis=0)
    hit = (np.abs(pk - target) <= 2) & (promS[pk, np.arange(promS.shape[1])] > 6.0)
    # sideband energy (|f-peak| in 100..4000 Hz) relative to the line itself (+/-3 bins),
    # both above the local median floor: an AM/SSB carrier has sidebands, a steady tone does not
    lin = p - 10 ** (med / 10)
    lin = np.clip(lin, 0, None)
    d = np.abs(f - peak_f)
    line_e = lin[d <= 3 * df].sum()
    sb_e = lin[(d > 100) & (d <= 4000)].sum()
    sideband_db = 10 * np.log10((sb_e + 1e-20) / (line_e + 1e-20))
    return [prom_db, float(hit.mean()), float(peak_f / (fs / 2)), float(sideband_db)]


# ---------------------------------------------------------------- c) modulation
def envelope_features(x, fs):
    sos = sps.butter(4, [300.0, 3400.0], btype="bandpass", fs=fs, output="sos")
    xb = sps.sosfiltfilt(sos, x)
    env = np.abs(sps.hilbert(xb))
    sos2 = sps.butter(2, 50.0, btype="low", fs=fs, output="sos")
    env = sps.sosfiltfilt(sos2, env)
    dec = int(fs // 200)
    trim = int(0.05 * fs)
    env = env[trim:-trim:dec]
    fe = fs / dec
    e = env - env.mean()
    f, p = sps.welch(e, fs=fe, nperseg=min(len(e), 128))
    lo = _band(f, p, 0.5, 4.0)
    hi = _band(f, p, 4.0, 16.0)
    ratio = 10 * np.log10((lo + 1e-20) / (hi + 1e-20))
    sq = e ** 2
    sq = sq - sq.mean()
    corr = float(np.dot(e, sq) / (np.linalg.norm(e) * np.linalg.norm(sq) + 1e-20))
    s = e.std() + 1e-20
    kurt = float(np.mean(e ** 4) / s ** 4)
    return [float(ratio), corr, kurt]


# ---------------------------------------------------------------- d) bicoherence
def bicoherence_features(x, fs, fmax=4000.0):
    nfft = 1024 if fs >= 32000 else 512
    f, _, Z = sps.stft(x, fs=fs, nperseg=nfft, noverlap=nfft // 2, padded=False)
    K = int(np.searchsorted(f, fmax))
    Zb = Z[:K, :]                                  # (K, T)
    # restrict to bins >= 2 (skip DC) and f1 <= f2, f1+f2 < K
    i1 = np.arange(1, K // 2)
    X1 = Zb[i1][:, None, :]                        # (K1,1,T)
    X2 = Zb[None, :, :]                            # (1,K,T)
    idx = i1[:, None] + np.arange(K)[None, :]      # (K1,K)
    valid = (idx < K) & (np.arange(K)[None, :] >= i1[:, None])
    idx_c = np.minimum(idx, K - 1)
    X3 = np.conj(Zb[idx_c])                        # (K1,K,T)
    prod = X1 * X2 * X3
    num = np.abs(prod.sum(axis=-1)) ** 2
    den = (np.abs(X1 * X2) ** 2).sum(axis=-1) * (np.abs(X3) ** 2).sum(axis=-1) + 1e-30
    b = (num / den)[valid]
    return [float(b.mean()), float(b.max()), float((b > 0.5).mean()), bicoh_lf_speech(x, fs)]


def bicoh_lf_speech(x, fs):
    """Quadratic phase coupling between the 2-50 Hz 'ghost' and the 300-3400 Hz band.

    The x^2 demodulation product s^2 contains difference tones f1-f2 (the LF ghost)
    phase-locked to the speech-band components that produced them.
    """
    nfft = 4096 if fs >= 32000 else 2048
    f, _, Z = sps.stft(x, fs=fs, nperseg=nfft, noverlap=3 * nfft // 4, padded=False)
    i1 = np.arange(1, int(np.searchsorted(f, 50.0)))        # LF bins (skip DC)
    i2 = np.arange(int(np.searchsorted(f, 300.0)), int(np.searchsorted(f, 3400.0)))
    X1 = Z[i1][:, None, :]
    X2 = Z[i2][None, :, :]
    X3 = np.conj(Z[i1[:, None] + i2[None, :]])
    num = np.abs((X1 * X2 * X3).sum(-1)) ** 2
    den = (np.abs(X1 * X2) ** 2).sum(-1) * (np.abs(X3) ** 2).sum(-1) + 1e-30
    return float((num / den).mean())


# ---------------------------------------------------------------- e) harmonicity
def harmonicity_features(x, fs):
    sos = sps.butter(4, [60.0, 4000.0], btype="bandpass", fs=fs, output="sos")
    x = sps.sosfiltfilt(sos, x)
    fl = int(0.03 * fs)
    hop = int(0.01 * fs)
    lag_lo, lag_hi = int(fs / 400), int(fs / 60)
    win = np.hanning(fl)
    nq = fl
    qlo, qhi = int(0.0025 * fs), int(fs / 60)
    hnr, cpp, f0s, voiced = [], [], [], []
    energies = []
    frames = [x[i:i + fl] for i in range(0, len(x) - fl, hop)]
    for fr in frames:
        energies.append(np.mean(fr ** 2))
    thr = 0.1 * (np.median(energies) + 1e-12)
    for fr, en in zip(frames, energies):
        if en < thr:
            f0s.append(0.0)
            continue
        w = (fr - fr.mean()) * win
        ac = sps.correlate(w, w, mode="full")[fl - 1:]
        ac = ac / (ac[0] + 1e-20)
        seg = ac[lag_lo:lag_hi]
        k = int(np.argmax(seg)) + lag_lo
        r = float(np.clip(ac[k], 1e-4, 1 - 1e-4))
        hnr.append(10 * np.log10(r / (1 - r)))
        f0s.append(fs / k if r > 0.5 else 0.0)
        # cepstral peak prominence
        spec = np.log(np.abs(np.fft.rfft(w, 2 * nq)) + 1e-9)
        cep = np.fft.irfft(spec)[:nq]
        q = np.arange(qlo, qhi)
        c = cep[qlo:qhi]
        A = np.vstack([q, np.ones_like(q)]).T
        coef, *_ = np.linalg.lstsq(A, c, rcond=None)
        line = A @ coef
        cpp.append(float(20 * (c - line).max() / np.log(10)))
    f0s = np.array(f0s)
    v = 0
    for i in range(1, len(f0s)):
        if f0s[i] > 0 and f0s[i - 1] > 0 and abs(f0s[i] - f0s[i - 1]) / f0s[i] < 0.15:
            v += 1
    vf = v / max(1, len(f0s) - 1)
    return [float(np.mean(hnr)) if hnr else -20.0, float(np.mean(cpp)) if cpp else 0.0, float(vf)]


# ---------------------------------------------------------------- f) flatness / H2
def spectral_features(x, fs):
    f, p = _welch(x, fs, 2048 if fs >= 32000 else 1024)
    m = (f >= 50) & (f < 4000)
    q = p[m]
    flat = float(np.exp(np.mean(np.log(q))) / np.mean(q))
    pn = q / q.sum()
    ent = float(-(pn * np.log(pn)).sum() / np.log(len(pn)))
    # 2nd-harmonic distortion: strongest 3 LF peaks in 100-1500 Hz, energy at 2f
    f2, p2 = _welch(x, fs, 8192 if fs >= 32000 else 4096)
    df = f2[1] - f2[0]
    m2 = (f2 >= 100) & (f2 < 1500)
    idx = np.where(m2)[0]
    pk, props = sps.find_peaks(10 * np.log10(p2[idx]), prominence=6, distance=int(40 / df))
    if len(pk) == 0:
        return [flat, ent, -40.0]
    top = idx[pk[np.argsort(props["prominences"])[-3:]]]
    num = den = 0.0
    for i in top:
        j = int(round(2 * f2[i] / df))
        if j + 1 < len(p2):
            num += p2[j - 1:j + 2].sum()
            den += p2[i - 1:i + 2].sum()
    return [flat, ent, float(10 * np.log10((num + 1e-20) / (den + 1e-20)))]


def extract(x, fs):
    x = np.asarray(x, dtype=np.float64)
    x = x - x.mean()
    feats = (ghost_features(x, fs) + line_features(x, fs) + envelope_features(x, fs)
             + bicoherence_features(x, fs) + harmonicity_features(x, fs)
             + spectral_features(x, fs))
    return np.array(feats, dtype=np.float64)
