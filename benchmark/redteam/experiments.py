"""Red-team experiments A-F against EchoGuard."""
from __future__ import annotations
import json
import os
import sys
import numpy as np
from scipy import signal as sps

from rt_common import analyze, summarize, adc_capture, band_energy, norm
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import synth_attacks as sa

SEEDS = list(range(1, 13))   # 12 seeds
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
RESULTS = {}


def run_rows(make, seeds=SEEDS):
    return [analyze(*make(s)) for s in seeds]


# --------------------------------------------------------------------------- #
# Baseline: unmodified make_attack at 96 kHz (no ADC), as the detector was tuned
# --------------------------------------------------------------------------- #
def exp_baseline():
    rows96 = run_rows(lambda s: (sa.make_attack(sample_rate=96_000, seed=s), 96_000))
    RESULTS["baseline_96k_noADC"] = summarize(rows96)


# --------------------------------------------------------------------------- #
# A. Realistic capture chain: synth at 192 kHz, ADC anti-alias + resample
# --------------------------------------------------------------------------- #
def exp_A():
    FS_HI = 192_000
    for fs_t in (48_000, 44_100, 16_000):
        rows = []
        for s in SEEDS:
            hi = sa.make_attack(sample_rate=FS_HI, carrier_hz=28_000.0, seed=s)
            cap = norm(adc_capture(hi, FS_HI, fs_t))
            rows.append(analyze(cap, fs_t))
        RESULTS[f"A_capture_{fs_t//1000}k"] = summarize(rows)

    # Also: ADC starting from the native 96 kHz make_attack output
    rows = []
    for s in SEEDS:
        hi = sa.make_attack(sample_rate=96_000, carrier_hz=28_000.0, seed=s)
        cap = norm(adc_capture(hi, 96_000, 48_000))
        rows.append(analyze(cap, 48_000))
    RESULTS["A_capture_48k_from96k"] = summarize(rows)

    # Baseband demod characterisation (seed 1, 48 kHz) + PNG
    _exp_A_baseband_png()


def _exp_A_baseband_png():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    FS_HI, fs_t, s = 192_000, 48_000, 1
    np.random.default_rng(s)
    n = int(1.5 * FS_HI)
    t = np.arange(n) / FS_HI
    # reconstruct components using the fork's own internals (no fork edits)
    env = sa._speechlike_envelope(n, FS_HI, np.random.default_rng(s))
    carrier = np.sin(2 * np.pi * 28_000.0 * t)
    ultra = (1.0 + 0.9 * (env - env.mean())) * carrier
    room = 0.05 * sa._voiceband_content(n, FS_HI, np.random.default_rng(s + 10_000))
    emitted = sa._speaker_and_air(ultra, FS_HI, 0.5, np.random.default_rng(s))
    # full capture vs room-only capture (carrier removed) -> isolate demod term
    full = sa._mic_nonlinearity(emitted + room, 0.12, 0.03)
    roomcap = sa._mic_nonlinearity(room, 0.12, 0.03)
    full_adc = adc_capture(full, FS_HI, fs_t)
    room_adc = adc_capture(roomcap, FS_HI, fs_t)
    demod = full_adc - room_adc           # the term the mic nonlinearity folded down

    e_demod = band_energy(demod, fs_t, 0, 8000)
    e_room = band_energy(room_adc, fs_t, 150, 4000)
    RESULTS["A_baseband"] = {
        "demod_vs_room_db": float(10 * np.log10((e_demod + 1e-20) / (e_room + 1e-20))),
        "demod_0_8k_energy": e_demod,
        "room_150_4k_energy": e_room,
        "demod_energy_0_100Hz_frac": float(
            band_energy(demod, fs_t, 0, 100) / (band_energy(demod, fs_t, 0, 8000) + 1e-20)),
    }

    fig, ax = plt.subplots(2, 1, figsize=(8, 7))
    for sig, lab in [(full_adc, "full capture @48k"), (demod, "isolated demod term"),
                     (room_adc, "room-only @48k")]:
        f, p = sps.welch(sig, fs=fs_t, nperseg=4096)
        ax[0].semilogy(f[f <= 8000], p[f <= 8000] + 1e-20, label=lab)
    ax[0].set_title("A: PSD 0-8 kHz after 48 kHz anti-alias ADC (carrier at 28 kHz removed)")
    ax[0].set_xlabel("Hz"); ax[0].set_ylabel("PSD"); ax[0].legend(fontsize=8)
    f2, tt, Sxx = sps.spectrogram(full_adc, fs=fs_t, nperseg=1024)
    ax[1].pcolormesh(tt, f2 / 1000, 10 * np.log10(Sxx + 1e-20), shading="gouraud")
    ax[1].set_title("Spectrogram of full 48 kHz capture (0-24 kHz)")
    ax[1].set_xlabel("s"); ax[1].set_ylabel("kHz")
    fig.tight_layout(); fig.savefig(f"{OUT}/A_baseband_48k.png", dpi=110); plt.close(fig)


# --------------------------------------------------------------------------- #
# B. Evasion by carrier placement (48 kHz, NO anti-alias)
# --------------------------------------------------------------------------- #
def exp_B():
    tbl = {}
    for fc in (15_000., 16_000., 17_000., 17_900., 18_100., 19_000.):
        rows = run_rows(lambda s, fc=fc: (
            sa.make_attack(sample_rate=48_000, carrier_hz=fc, mod_depth=1.0, seed=s), 48_000))
        tbl[f"{fc/1000:.1f}kHz"] = summarize(rows)
    RESULTS["B_carrier_placement_48k"] = tbl


# --------------------------------------------------------------------------- #
# C. Evasion by modulation type (DSB-SC, SSB) at 25 kHz
# --------------------------------------------------------------------------- #
def _mod_attack(kind, fc, fs, seed):
    """Build an attack whose modulation is DSB-SC or SSB, reusing fork physics."""
    np.random.default_rng(seed)
    n = int(1.5 * fs)
    t = np.arange(n) / fs
    env = sa._speechlike_envelope(n, fs, np.random.default_rng(seed))
    m = env - env.mean()                      # zero-mean modulating signal
    m = m / (np.max(np.abs(m)) or 1.0)
    wc = 2 * np.pi * fc
    if kind == "dsbsc":
        ultra = m * np.cos(wc * t)
    elif kind == "ssb":
        analytic = sps.hilbert(m)
        ultra = (np.real(analytic) * np.cos(wc * t)
                 - np.imag(analytic) * np.sin(wc * t))   # upper sideband
    else:
        raise ValueError(kind)
    room = 0.05 * sa._voiceband_content(n, fs, np.random.default_rng(seed + 10_000))
    emitted = sa._speaker_and_air(ultra, fs, 0.5, np.random.default_rng(seed))
    cap = sa._mic_nonlinearity(emitted + room, 0.12, 0.03)
    cap = sa._reverb(cap, fs, 0.25, np.random.default_rng(seed))
    cap = sa._add_noise(cap, 25.0, fs, np.random.default_rng(seed))
    return sa._normalise(cap)


def exp_C():
    tbl = {}
    for kind in ("dsbsc", "ssb"):
        rows96 = run_rows(lambda s, k=kind: (_mod_attack(k, 25_000., 96_000, s), 96_000))
        tbl[f"{kind}_96k_noADC"] = summarize(rows96)
        rows48 = []
        for s in SEEDS:
            hi = _mod_attack(kind, 25_000., 192_000, s)
            cap = norm(adc_capture(hi, 192_000, 48_000))
            rows48.append(analyze(cap, 48_000))
        tbl[f"{kind}_48k_ADC"] = summarize(rows48)
        # same modulation but carrier moved to 40 kHz (fully above anti-alias)
        rows40 = []
        for s in SEEDS:
            hi = _mod_attack(kind, 40_000., 192_000, s)
            cap = norm(adc_capture(hi, 192_000, 48_000))
            rows40.append(analyze(cap, 48_000))
        tbl[f"{kind}_40kHz_48k_ADC"] = summarize(rows40)
    RESULTS["C_modulation_type"] = tbl


# --------------------------------------------------------------------------- #
# D. Evasion by level: set carrier amplitude so r hits target fractions
# --------------------------------------------------------------------------- #
def _probe_level(alpha, fc, fs, seed):
    """Speech base + alpha*carrier (mirrors sensitivity.py probe), 1.0 s.

    The carrier is AM-modulated (depth 0.9) by speech-bandwidth noise, as an
    injected command would modulate it; an unmodulated tone is a beacon.
    """
    rng = np.random.default_rng(seed)
    n = int(1.0 * fs)
    sos = sps.butter(6, [150, 3800], btype="bandpass", fs=fs, output="sos")
    base = sps.sosfilt(sos, rng.standard_normal(n))
    base = base / (np.max(np.abs(base)) or 1.0)
    mod = sps.sosfilt(sos, rng.standard_normal(n))
    mod = mod / (np.max(np.abs(mod)) or 1.0)
    t = np.arange(n) / fs
    sig = base + alpha * (1.0 + 0.9 * mod) * np.sin(2 * np.pi * fc * t)
    return norm(sig)


def exp_D():
    fs, fc = 48_000, 21_000.0
    # map alpha -> measured r (energy fraction above 18 kHz), find alpha for targets
    alphas = np.logspace(-2.3, 0.3, 40)
    curve = []
    for a in alphas:
        rs = [analyze(_probe_level(a, fc, fs, s), fs) for s in range(1, 6)]
        curve.append((float(a), float(np.mean([x["r_oob"] for x in rs]))))
    tbl = {}
    for target in (0.005, 0.01, 0.02, 0.05):
        # pick alpha whose mean r is closest to target
        a = min(curve, key=lambda c: abs(c[1] - target))[0]
        rows = run_rows(lambda s, a=a: (_probe_level(a, fc, fs, s), fs))
        tbl[f"r~{target*100:.1f}%"] = {"alpha": a, **summarize(rows)}
    RESULTS["D_level"] = tbl
    # analytic thresholds (rho_B assumed saturated for a clean strong carrier)
    RESULTS["D_analytic"] = {
        "susp_r_pct": 0.33**2 * 0.10 * 100,
        "highrisk_r_pct": 0.66**2 * 0.10 * 100,
    }


# --------------------------------------------------------------------------- #
# E. Spread-spectrum carrier: band-limited noise 20-24 kHz (no single peak)
# --------------------------------------------------------------------------- #
def _spread_attack(fs, seed, level=0.6):
    rng = np.random.default_rng(seed)
    n = int(1.5 * fs)
    sos = sps.butter(8, [20_000., 23_500.], btype="bandpass", fs=fs, output="sos")
    spread = sps.sosfilt(sos, rng.standard_normal(n))
    spread = spread / (np.max(np.abs(spread)) or 1.0)
    env = sa._speechlike_envelope(n, fs, np.random.default_rng(seed))
    ultra = (0.5 + 0.5 * (env - env.mean())) * spread * level
    room = 0.05 * sa._voiceband_content(n, fs, np.random.default_rng(seed + 10_000))
    cap = sa._mic_nonlinearity(ultra + room, 0.12, 0.03)
    cap = sa._reverb(cap, fs, 0.25, np.random.default_rng(seed))
    cap = sa._add_noise(cap, 25.0, fs, np.random.default_rng(seed))
    return sa._normalise(cap)


def exp_E():
    rows = run_rows(lambda s: (_spread_attack(48_000, s), 48_000))
    RESULTS["E_spread_spectrum_48k"] = summarize(rows)


# --------------------------------------------------------------------------- #
# F. Benign look-alikes at 48 kHz (no anti-alias) -> false-positive rates
# --------------------------------------------------------------------------- #
def _room_tone(n, fs, rng, level=0.02):
    sos = sps.butter(4, 6000.0, btype="low", fs=fs, output="sos")
    return level * sps.sosfilt(sos, rng.standard_normal(n))


def _benign_pilot(fs, seed, f_tone=19_000.0, amp=0.5):
    rng = np.random.default_rng(seed)
    n = int(1.5 * fs); t = np.arange(n) / fs
    sig = _room_tone(n, fs, rng) + amp * np.sin(2 * np.pi * f_tone * t)
    return norm(sig)


def _benign_smps(fs, seed):
    rng = np.random.default_rng(seed)
    n = int(1.5 * fs); t = np.arange(n) / fs
    f = rng.uniform(20_500, 21_500)
    whine = 0.4 * np.sin(2 * np.pi * f * t)
    whine += 0.15 * np.sin(2 * np.pi * 2 * f * t) if 2 * f < fs / 2 else 0.0
    return norm(_room_tone(n, fs, rng) + whine)


def _benign_sawtooth(fs, seed):
    rng = np.random.default_rng(seed)
    n = int(1.5 * fs); t = np.arange(n) / fs
    f0 = 440.0
    sig = np.zeros(n)
    k = 1
    while f0 * k < 22_000.0:
        sig += (1.0 / k) * np.sin(2 * np.pi * f0 * k * t + rng.uniform(0, 6.28))
        k += 1
    return norm(sig + 0.01 * rng.standard_normal(n))


def _benign_clicks(fs, seed):
    rng = np.random.default_rng(seed)
    n = int(1.5 * fs)
    sig = _room_tone(n, fs, rng)
    for _ in range(rng.integers(6, 12)):
        i = rng.integers(0, n - 200)
        imp = np.zeros(n)
        imp[i] = 1.0
        sos = sps.butter(2, [800., 20_000.], btype="bandpass", fs=fs, output="sos")
        click = sps.sosfilt(sos, imp) * rng.uniform(0.5, 1.0)
        sig += click
    return norm(sig)


def exp_F():
    tbl = {}
    tbl["pilot_19kHz"] = summarize(run_rows(lambda s: (_benign_pilot(48_000, s), 48_000)))
    tbl["smps_whine_20-22kHz"] = summarize(run_rows(lambda s: (_benign_smps(48_000, s), 48_000)))
    tbl["sawtooth_440Hz_to22k"] = summarize(run_rows(lambda s: (_benign_sawtooth(48_000, s), 48_000)))
    tbl["keyboard_clicks"] = summarize(run_rows(lambda s: (_benign_clicks(48_000, s), 48_000)))
    RESULTS["F_benign_lookalikes_48k"] = tbl


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    funcs = {"baseline": exp_baseline, "A": exp_A, "B": exp_B, "C": exp_C,
             "D": exp_D, "E": exp_E, "F": exp_F}
    if which == "all":
        for f in funcs.values():
            f()
    else:
        funcs[which]()
    print(json.dumps(RESULTS, indent=2, default=float))
