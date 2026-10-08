"""Baseband-residue dataset generator.

Synthesises attack / benign / look-alike clips at 192 kHz through the fork's
physics chain (transducer+air -> mic nonlinearity -> reverb -> noise), then
through the red-team ADC model (8th-order Butterworth anti-alias + polyphase
resample) to 48 kHz AND 16 kHz, followed by a gentle 1st-order 10 Hz DC blocker
(a real codec is AC coupled; the x^2 term otherwise leaves a large DC offset).

Attack and benign draw their condition parameters (SNR, distance, a2, a3, rt60)
from the SAME sampler so the classes are condition-matched.

Usage:
    PYTHONPATH=<fork>:<redteam> python gen_dataset.py --out dataset.npz --n 480 --n_look 60
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

import numpy as np
from scipy import signal as sps
from scipy.io import wavfile

from rt_common import adc_capture
from benchmark.synth_attacks import (
    _speechlike_envelope, _voiceband_content, _speaker_and_air,
    _mic_nonlinearity, _reverb, _add_noise, _normalise, _bandpass,
)

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(os.path.dirname(HERE), "realaudio", "corpus")
FS = 192_000
DUR = 1.5
PRE = 0.5                    # extra lead-in synthesised then discarded (steady-state reverb/DC)
N = int(FS * (DUR + PRE))
N_OUT = int(FS * DUR)


# ----------------------------------------------------------------------------
# real-audio helpers
# ----------------------------------------------------------------------------
def _load_corpus(categories):
    rows = []
    with open(os.path.join(CORPUS, "manifest.csv")) as f:
        for r in csv.DictReader(f):
            if r["category"] in categories:
                rows.append(r)
    return rows


_CACHE = {}


def _load_wav_192k(relpath):
    """Load a corpus wav, mono, resampled to 192 kHz, peak-normalised."""
    if relpath in _CACHE:
        return _CACHE[relpath]
    sr, d = wavfile.read(os.path.join(CORPUS, relpath))
    x = d.astype(np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    x /= 32768.0
    g = np.gcd(sr, FS)
    x = sps.resample_poly(x, FS // g, sr // g)
    x /= (np.max(np.abs(x)) or 1.0)
    _CACHE[relpath] = x
    return x


def _crop(x, rng, n=N, tries=6):
    """Random 1.5 s crop, preferring energetic segments (VCTK has leading silence)."""
    if len(x) <= n:
        return np.pad(x, (0, n - len(x)))
    best, best_rms = None, -1.0
    for _ in range(tries):
        s = int(rng.integers(0, len(x) - n))
        seg = x[s:s + n]
        r = float(np.sqrt(np.mean(seg ** 2)))
        if r > best_rms:
            best, best_rms = seg, r
    return best.copy()


def real_speech(rng, pool):
    r = pool[int(rng.integers(len(pool)))]
    return _crop(_load_wav_192k(r["file"]), rng), r["file"]


# ----------------------------------------------------------------------------
# condition sampler (shared by every class)
# ----------------------------------------------------------------------------
A2_GRID = [0.03, 0.05, 0.08, 0.12, 0.16, 0.20]


def sample_conditions(rng):
    return dict(
        snr_db=float(rng.uniform(10, 35)),
        distance_m=float(rng.uniform(0.2, 2.0)),
        mic_a2=float(A2_GRID[int(rng.integers(len(A2_GRID)))]),
        mic_a3=float(rng.uniform(0.0, 0.05)),
        rt60=float(rng.uniform(0.1, 0.5)),
        drr_db=float(rng.uniform(0.0, 15.0)),   # direct-to-reverberant ratio
    )


def _reverb_drr(sig, fs, rt60, drr_db, rng):
    """Fork-style exponentially decaying noise tail, but with the wet path scaled to a
    physical direct-to-reverberant ratio.  (The fork's 0.7 dry + 0.3 wet uses an
    unnormalised white tail whose gain is ~18x the direct path at 192 kHz, i.e. DRR ~ -25 dB,
    which turns the x^2 DC offset of any strong tone into a large LF onset ramp.)"""
    n_ir = int(rt60 * fs)
    t = np.arange(n_ir) / fs
    tail = rng.standard_normal(n_ir) * np.exp(-6.9 * t / rt60)
    tail[0] = 0.0
    tail *= np.sqrt(10 ** (-drr_db / 10.0) / (np.sum(tail ** 2) + 1e-20))
    wet = sps.fftconvolve(sig, tail, mode="full")[: len(sig)]
    return sig + wet


def capture_chain(x, cond, rng, level=1.0):
    """Mic nonlinearity -> reverb -> noise -> normalise (the fork's stages 5-7) -> self-noise."""
    y = _mic_nonlinearity(x, cond["mic_a2"], cond["mic_a3"])
    y = _reverb_drr(y, FS, cond["rt60"], cond["drr_db"], rng)
    y = _add_noise(y, cond["snr_db"], FS, rng)
    y = _normalise(y).astype(np.float64) * level
    # fixed device self-noise floor (independent of content level): white at -60 dBFS RMS
    # plus 1/f-ish LF rumble (2nd-order LP at 150 Hz) at -55 dBFS RMS.  The fork scales its
    # noise to the audible content, which leaves near-silent clips unphysically clean.
    w = rng.standard_normal(len(y))
    r = sps.sosfilt(sps.butter(2, 150.0, btype="low", fs=FS, output="sos"), rng.standard_normal(len(y)))
    r /= (np.sqrt(np.mean(r ** 2)) + 1e-12)
    y = y + 1e-3 * w + 10 ** (-55 / 20) * r
    return y[-N_OUT:]            # drop the lead-in: carrier/tone already on, reverb in steady state


def adc_both(y):
    out = {}
    for fs_t in (48_000, 16_000):
        z = adc_capture(y, FS, fs_t)
        z = z - z.mean()   # steady-state DC (carrier was already on): avoid a start-up step artefact
        sos = sps.butter(1, 10.0, btype="high", fs=fs_t, output="sos")  # AC-coupled codec
        z = sps.sosfilt(sos, z)
        out[fs_t] = z
    return out


# ----------------------------------------------------------------------------
# attack
# ----------------------------------------------------------------------------
def make_attack_192k(rng, cond, speech_pool, carrier_hz, scheme, modulator, mod_depth,
                     room_level):
    t = np.arange(N) / FS
    if modulator == "env":
        s = _speechlike_envelope(N, FS, rng)          # positive 0..1 (fork model)
        s = s - s.mean()
    else:  # real speech waveform as the modulating command
        s, _ = real_speech(rng, speech_pool)
        s = _bandpass(s, FS, 50.0, 4000.0)   # keep f0: attackers low-pass the command, they do not high-pass it
        s /= (np.max(np.abs(s)) or 1.0)

    c = np.cos(2 * np.pi * carrier_hz * t)
    if scheme == "am":
        ultra = (1.0 + mod_depth * s) * c
    elif scheme == "dsbsc":
        ultra = mod_depth * s * c
    else:  # ssb (upper sideband) with carrier
        sh = np.imag(sps.hilbert(s))
        ultra = (1.0 + mod_depth * s) * c - mod_depth * sh * np.sin(2 * np.pi * carrier_hz * t)
    ultra /= (np.max(np.abs(ultra)) or 1.0)

    emitted = _speaker_and_air(ultra, FS, cond["distance_m"], rng)
    emitted /= (np.max(np.abs(emitted)) or 1.0)

    room = np.zeros(N)
    room_kind = "none"
    if room_level > 0:
        u = rng.uniform()
        if u < 0.7:
            room, _ = real_speech(rng, speech_pool)
            room_kind = "real_speech"
        else:
            room = _voiceband_content(N, FS, rng)
            room /= (np.max(np.abs(room)) or 1.0)
            room_kind = "synth_voiceband"
        room = room_level * room
    return capture_chain(emitted + room, cond, rng), room_kind


# ----------------------------------------------------------------------------
# benign
# ----------------------------------------------------------------------------
def make_benign_192k(rng, cond, kind, pools):
    t = np.arange(N) / FS
    src = ""
    if kind == "real_speech":
        x, src = real_speech(rng, pools["speech"])
    elif kind == "speech_noise":
        x, src = real_speech(rng, pools["speech"])
        bg, _ = real_speech(rng, pools["bg"])
        bg_snr = rng.uniform(0, 10)
        g = np.sqrt(np.mean(x ** 2) / (np.mean(bg ** 2) + 1e-12) / 10 ** (bg_snr / 10))
        x = x + g * bg
    elif kind == "synth_speech":
        env = _speechlike_envelope(N, FS, rng)
        x = _voiceband_content(N, FS, rng) * (0.3 + 0.7 * env)
    elif kind == "music":
        if rng.uniform() < 0.7:
            x, src = real_speech(rng, pools["music"])
        else:
            x = np.zeros(N)
            f0 = rng.uniform(180, 440)
            for k in range(1, 7):
                x += (1.0 / k) * np.sin(2 * np.pi * f0 * k * t + rng.uniform(0, 6.28))
            x += 0.2 * _bandpass(rng.standard_normal(N), FS, 2000, 12000)
    elif kind == "silence":
        sos = sps.butter(4, 6000.0, btype="low", fs=FS, output="sos")
        x = 0.02 * sps.sosfilt(sos, rng.standard_normal(N))
    elif kind == "quiet":
        # genuinely quiet room: low-passed room tone (+ sometimes very faint distant speech)
        # captured at -50..-25 dBFS so the device self-noise floor matters
        sos = sps.butter(4, 2000.0, btype="low", fs=FS, output="sos")
        x = sps.sosfilt(sos, rng.standard_normal(N))
        if rng.uniform() < 0.5:
            sp, src = real_speech(rng, pools["speech"])
            x = x / (np.max(np.abs(x)) or 1) + rng.uniform(0.3, 1.0) * sp
        y = capture_chain(x, cond, rng, level=10 ** (rng.uniform(-50, -25) / 20))
        return y, src
    elif kind == "tone_hf":
        # steady in-band high tone (CRT/backlight/driver whine 15-21.5 kHz), unmodulated
        f = rng.uniform(15_000, 21_500)
        x = rng.uniform(0.3, 1.0) * np.cos(2 * np.pi * f * t + rng.uniform(0, 6.28))
        if rng.uniform() < 0.6:
            room, src = real_speech(rng, pools["speech"])
            x = x + rng.uniform(0.05, 0.5) * room
    else:
        raise ValueError(kind)
    return capture_chain(x, cond, rng), src


# ----------------------------------------------------------------------------
# look-alikes (must NOT be flagged)
# ----------------------------------------------------------------------------
def make_lookalike_192k(rng, cond, kind, pools):
    t = np.arange(N) / FS
    if kind == "tone19k":
        f = 19_000.0 + rng.uniform(-50, 50)
        tone = np.cos(2 * np.pi * f * t + rng.uniform(0, 6.28))
        # slow amplitude drift (not speech-rate), plus faint real room audio
        tone *= 1.0 + 0.03 * np.sin(2 * np.pi * rng.uniform(0.05, 0.3) * t)
        room, _ = real_speech(rng, pools["speech"]) if rng.uniform() < 0.5 else (np.zeros(N), "")
        x = tone + rng.uniform(0.0, 0.15) * room
    elif kind == "psu_whine":
        f = rng.uniform(20_000, 22_000)
        # slight frequency jitter + a few % mains-ripple AM (100/120 Hz) + weak harmonic
        jitter = np.cumsum(rng.standard_normal(N)) / np.sqrt(N)
        ph = 2 * np.pi * f * t + 2 * np.pi * rng.uniform(5, 30) * jitter
        mains = rng.choice([100.0, 120.0])
        am = 1.0 + rng.uniform(0.01, 0.05) * np.sin(2 * np.pi * mains * t)
        x = am * np.cos(ph) + 0.1 * np.cos(2 * ph)
        room, _ = real_speech(rng, pools["speech"]) if rng.uniform() < 0.5 else (np.zeros(N), "")
        x = x + rng.uniform(0.0, 0.15) * room
    elif kind == "percussive":
        x, _ = real_speech(rng, pools["perc"])
        if rng.uniform() < 0.3:  # add synthetic bright clicks
            clicks = np.zeros(N)
            for _ in range(int(rng.integers(3, 10))):
                i = int(rng.integers(0, N - 2000))
                clicks[i:i + 2000] += rng.standard_normal(2000) * np.exp(-np.arange(2000) / 300)
            x = x + 0.5 * clicks / (np.max(np.abs(clicks)) or 1)
    else:
        raise ValueError(kind)
    return capture_chain(x, cond, rng)


# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "dataset.npz"))
    ap.add_argument("--n", type=int, default=480, help="clips per class")
    ap.add_argument("--n_look", type=int, default=60, help="look-alike clips per kind")
    ap.add_argument("--n_tone", type=int, default=60, help="extra benign steady-HF-tone clips")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)

    pools = dict(
        speech=_load_corpus({"speech_clean"}),
        bg=_load_corpus({"environmental", "electronic_device"}),
        music=_load_corpus({"music_full_mix", "music_guitar_piano", "music_wind_brass_vocal"}),
        perc=_load_corpus({"music_percussive", "hf_natural"}),
    )
    print({k: len(v) for k, v in pools.items()})

    X48, X16, meta = [], [], []

    def push(y192, m):
        z = adc_both(y192)
        X48.append(z[48_000].astype(np.float32))
        X16.append(z[16_000].astype(np.float32))
        meta.append(m)

    for i in range(a.n):
        cond = sample_conditions(rng)
        near = rng.uniform() < 0.4
        carrier = float(rng.uniform(18_000, 22_000) if near else rng.uniform(25_000, 40_000))
        scheme = str(rng.choice(["am", "am", "dsbsc", "ssb"]))
        modulator = str(rng.choice(["env", "speech"]))
        depth = float(rng.uniform(0.5, 1.0))
        room_level = 0.0 if rng.uniform() < 0.1 else float(rng.uniform(0.02, 0.15))
        y, room_kind = make_attack_192k(rng, cond, pools["speech"], carrier, scheme,
                                        modulator, depth, room_level)
        push(y, dict(label=1, cls="attack", kind=f"{scheme}_{modulator}", carrier_hz=carrier,
                     scheme=scheme, modulator=modulator, mod_depth=depth,
                     room_level=room_level, room_kind=room_kind, **cond))
        if i % 50 == 0:
            print("attack", i)

    kinds = ["real_speech"] * 7 + ["speech_noise"] * 3 + ["synth_speech"] * 2 + ["music"] * 4 + ["silence"] * 2 + ["quiet"] * 2
    for i in range(a.n):
        cond = sample_conditions(rng)
        kind = kinds[i % len(kinds)]
        y, src = make_benign_192k(rng, cond, kind, pools)
        push(y, dict(label=0, cls="benign", kind=kind, carrier_hz=0.0, scheme="", modulator="",
                     mod_depth=0.0, room_level=0.0, room_kind=src, **cond))
        if i % 50 == 0:
            print("benign", i)

    for i in range(a.n_tone):
        cond = sample_conditions(rng)
        y, src = make_benign_192k(rng, cond, "tone_hf", pools)
        push(y, dict(label=0, cls="benign", kind="tone_hf", carrier_hz=0.0, scheme="", modulator="",
                     mod_depth=0.0, room_level=0.0, room_kind=src, **cond))
    print("benign tone_hf")

    for kind in ["tone19k", "psu_whine", "percussive"]:
        for i in range(a.n_look):
            cond = sample_conditions(rng)
            y = make_lookalike_192k(rng, cond, kind, pools)
            push(y, dict(label=0, cls="lookalike", kind=kind, carrier_hz=0.0, scheme="",
                         modulator="", mod_depth=0.0, room_level=0.0, room_kind="", **cond))
        print("lookalike", kind)

    keys = list(meta[0].keys())
    np.savez_compressed(
        a.out,
        x48=(np.clip(np.stack(X48), -1, 1) * 32767).astype(np.int16),
        x16=(np.clip(np.stack(X16), -1, 1) * 32767).astype(np.int16),
        meta_keys=np.array(keys),
        meta=np.array([[str(m[k]) for k in keys] for m in meta]),
    )
    with open(a.out.replace(".npz", "_meta.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(meta)
    print("wrote", a.out, len(meta), "clips")


if __name__ == "__main__":
    main()
