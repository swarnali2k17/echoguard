"""Realistic synthetic attack generator for validating EchoGuard.

WHAT THIS IS
------------
A physics-aware generator of *recordings* that look like what a microphone would
capture during an inaudible (ultrasonic) voice-injection attack -- and matched
benign recordings that look like ordinary captured audio. It exists to stress-
test and tune EchoGuard's detectors without hardware, and to produce a labelled
corpus for the benchmark.

WHY IT'S MORE THAN A CLEAN TONE
-------------------------------
A naive synthetic attack is just a pure carrier -- trivially detectable and
unrealistic. Real captured attacks are messy because the signal travels through
physical stages before the detector ever sees it. This generator models those
stages:

  1. Baseband envelope   - a SPEECH-LIKE amplitude envelope (formant-ish band
                           energy + syllable rhythm). It is NOT speech and carries
                           NO recoverable command -- only the time/'spectral shape
                           a real command's envelope would have.
  2. Ultrasonic carrier  - the envelope is amplitude-modulated onto a carrier
                           above human hearing (configurable, e.g. 22-40 kHz).
  3. Speaker response    - an ultrasonic transducer is not flat; high end rolls
                           off and ripples.
  4. Air / distance      - higher frequencies attenuate more with range; farther
                           attacks arrive weaker and duller.
  5. Microphone NON-LINEARITY  - the crux. A real MEMS mic is slightly non-linear
                           (y ~= x + a2*x^2 + a3*x^3). The x^2 term DEMODULATES the
                           carrier, folding the hidden envelope back down into the
                           audible band -- which is exactly why these attacks work,
                           and exactly the fingerprint (audible ghost + residual
                           ultrasonic) a defender must catch.
  6. Room reverb         - a short decaying impulse response.
  7. Background noise    - additive noise at a configurable SNR.

Benign clips go through the same room + noise + mic (minus the carrier), so the
benchmark is fair: attack vs. benign differ by the attack, not by recording
conditions. This is the trap the earlier deepfake pilot fell into; here we avoid
it on purpose.

SAFETY
------
These are DETECTOR-VALIDATION FIXTURES. The modulating envelope is generic
speech-like noise, not a spoken command; nothing here can control a device.
The point is to make a *defensive* tool more trustworthy.

  8. Device ADC          - anti-alias low-pass + resample to the capture rate
                           (default 48 kHz). THIS IS THE STAGE THAT MATTERS:
                           a real recording never contains the carrier, only
                           what the mic non-linearity folded into baseband.
                           --capture 0 keeps the raw synthesis-rate audio.

Run:
    python benchmark/synth_attacks.py --out fixtures/synth --sr 96000 --n 12
    python benchmark/synth_attacks.py --evaluate               # captured at 48 kHz (device-realistic)
    python benchmark/synth_attacks.py --evaluate --capture 0   # raw 96 kHz (carrier still present)
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
from scipy import signal as sps
from scipy.io import wavfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# --------------------------------------------------------------------------- #
# Stage 1: a speech-like baseband envelope (no real content)
# --------------------------------------------------------------------------- #
def _speechlike_envelope(n: int, sample_rate: int, rng: np.random.Generator) -> np.ndarray:
    """A positive, slowly-varying envelope with syllable-rate rhythm.

    Models the *shape* of a spoken command's loudness over time: bursts of
    energy (syllables) at 2-7 Hz with short gaps. No phonetic content.
    """
    t = np.arange(n) / sample_rate
    env = np.zeros(n)
    # a few overlapping syllable bursts
    n_syll = rng.integers(4, 9)
    for _ in range(n_syll):
        center = rng.uniform(0.05, t[-1] - 0.05)
        width = rng.uniform(0.04, 0.12)
        amp = rng.uniform(0.5, 1.0)
        env += amp * np.exp(-0.5 * ((t - center) / width) ** 2)
    # gentle 3-6 Hz tremolo so it isn't a smooth hump
    env *= 0.6 + 0.4 * (0.5 * (1 + np.sin(2 * np.pi * rng.uniform(3, 6) * t)))
    env = np.clip(env, 0.0, None)
    peak = env.max() or 1.0
    return env / peak


def _voiceband_content(n: int, sample_rate: int, rng: np.random.Generator) -> np.ndarray:
    """Band-limited noise shaped to sit in the voice band (~150 Hz - 4 kHz)."""
    noise = rng.standard_normal(n)
    sos = sps.butter(6, [150.0, 4000.0], btype="bandpass", fs=sample_rate, output="sos")
    return sps.sosfilt(sos, noise)


# --------------------------------------------------------------------------- #
# Stage 3-4: speaker colouration + distance attenuation
# --------------------------------------------------------------------------- #
def _speaker_and_air(sig: np.ndarray, sample_rate: int, distance_m: float,
                     rng: np.random.Generator) -> np.ndarray:
    """Roll off and ripple the high end (transducer) and attenuate HF with range."""
    # transducer: mild high-shelf droop above ~30 kHz + small ripple
    freqs = np.fft.rfftfreq(len(sig), 1.0 / sample_rate)
    spec = np.fft.rfft(sig)
    droop = 1.0 / (1.0 + (freqs / 35_000.0) ** 2)            # gentle LP ~35 kHz
    ripple = 1.0 + 0.15 * np.sin(2 * np.pi * freqs / 8_000.0)  # transducer ripple
    # air absorption: ~ exp(-alpha * f^2 * distance), normalised constant
    air = np.exp(-(freqs ** 2) * 1.0e-10 * max(distance_m, 0.1))
    spec = spec * droop * ripple * air
    return np.fft.irfft(spec, n=len(sig))


# --------------------------------------------------------------------------- #
# Stage 5: microphone non-linearity (the demodulation that makes it work)
# --------------------------------------------------------------------------- #
def _mic_nonlinearity(sig: np.ndarray, a2: float, a3: float) -> np.ndarray:
    """y = x + a2*x^2 + a3*x^3 -- the quadratic term demodulates the carrier."""
    x = sig / (np.max(np.abs(sig)) or 1.0)
    return x + a2 * x ** 2 + a3 * x ** 3


# --------------------------------------------------------------------------- #
# Stage 6-7: room reverb + background noise
# --------------------------------------------------------------------------- #
def _reverb(sig: np.ndarray, sample_rate: int, rt60: float, rng: np.random.Generator) -> np.ndarray:
    if rt60 <= 0:
        return sig
    n_ir = int(rt60 * sample_rate)
    if n_ir < 2:
        return sig
    t = np.arange(n_ir) / sample_rate
    ir = rng.standard_normal(n_ir) * np.exp(-6.9 * t / rt60)  # -60 dB over rt60
    ir[0] = 1.0
    wet = sps.fftconvolve(sig, ir, mode="full")[: len(sig)]
    return 0.7 * sig + 0.3 * wet


def _add_noise(sig: np.ndarray, snr_db: float, sample_rate: int,
               rng: np.random.Generator) -> np.ndarray:
    """Add pink-ish background noise at the requested SNR (over the audible band)."""
    aud = _bandpass(sig, sample_rate, 100.0, 8000.0)
    sig_pow = np.mean(aud ** 2) or 1e-12
    noise = rng.standard_normal(len(sig))
    noise = _bandpass(noise, sample_rate, 50.0, min(8000.0, sample_rate / 2 - 100))
    noise_pow = np.mean(noise ** 2) or 1e-12
    target = sig_pow / (10 ** (snr_db / 10.0))
    noise *= np.sqrt(target / noise_pow)
    return sig + noise


def _bandpass(sig, sample_rate, low, high):
    high = min(high, sample_rate / 2 - 1)
    if low >= high:
        return sig
    sos = sps.butter(4, [low, high], btype="bandpass", fs=sample_rate, output="sos")
    return sps.sosfilt(sos, sig)


# --------------------------------------------------------------------------- #
# Full pipelines
# --------------------------------------------------------------------------- #
def _modulating_signal(modulator, n: int, sample_rate: int, rng: np.random.Generator) -> np.ndarray:
    """The baseband 'command' that rides on the carrier, zero-mean, peak 1.

    "voiceband" (default): band-limited noise 150 Hz-4 kHz shaped by a
        syllable-rate envelope. No phonetic content, but the BANDWIDTH of a
        spoken command, so the carrier's sidebands span +-150 Hz..4 kHz as a
        real attack's do. A detector can tell this from an unmodulated tone.
    "envelope": the syllable-rate envelope alone (the pre-v0.2.0 behaviour).
        Its sidebands sit within +-20 Hz of the carrier, which no real
        command produces; kept for the sensitivity sweeps.
    an array: a real speech recording at `sample_rate` (resampled/padded here),
        for experiments with genuine voice structure.
    """
    if isinstance(modulator, np.ndarray):
        m = np.asarray(modulator, dtype=np.float64)
        if len(m) < n:
            m = np.pad(m, (0, n - len(m)))
        m = m[:n] - np.mean(m[:n])
    elif modulator == "envelope":
        env = _speechlike_envelope(n, sample_rate, rng)
        m = env - env.mean()
    elif modulator == "voiceband":
        env = _speechlike_envelope(n, sample_rate, rng)
        m = _voiceband_content(n, sample_rate, rng) * env
    else:
        raise ValueError(f"unknown modulator {modulator!r}")
    peak = float(np.max(np.abs(m))) or 1.0
    return m / peak


def make_attack(duration=1.5, sample_rate=96_000, carrier_hz=28_000.0, mod_depth=0.9,
                distance_m=0.5, snr_db=25.0, rt60=0.25, mic_a2=0.12, mic_a3=0.03,
                seed=0, modulator="voiceband") -> np.ndarray:
    """One realistic captured ULTRASONIC-INJECTION recording."""
    rng = np.random.default_rng(seed)
    n = int(duration * sample_rate)
    t = np.arange(n) / sample_rate

    # AM modulate the (inaudible) carrier with the command-like baseband signal
    carrier = np.sin(2 * np.pi * carrier_hz * t)
    ultrasonic = (1.0 + mod_depth * _modulating_signal(modulator, n, sample_rate, rng)) * carrier

    # some faint real room audio also present (TV/voices) so it's not pure carrier
    room = 0.05 * _voiceband_content(n, sample_rate, rng)

    emitted = _speaker_and_air(ultrasonic, sample_rate, distance_m, rng)
    captured = _mic_nonlinearity(emitted + room, mic_a2, mic_a3)
    captured = _reverb(captured, sample_rate, rt60, rng)
    captured = _add_noise(captured, snr_db, sample_rate, rng)
    return _normalise(captured)


def make_benign(duration=1.5, sample_rate=96_000, distance_m=0.5, snr_db=25.0,
                rt60=0.25, mic_a2=0.12, mic_a3=0.03, kind="speech", seed=0) -> np.ndarray:
    """A matched benign recording: same room/mic/noise, NO carrier."""
    rng = np.random.default_rng(seed)
    n = int(duration * sample_rate)
    t = np.arange(n) / sample_rate

    if kind == "speech":
        env = _speechlike_envelope(n, sample_rate, rng)
        content = _voiceband_content(n, sample_rate, rng) * (0.3 + 0.7 * env)
    elif kind == "music":
        # a few harmonic partials + wideband content up to ~12 kHz
        content = np.zeros(n)
        f0 = rng.uniform(180, 440)
        for k in range(1, 7):
            content += (1.0 / k) * np.sin(2 * np.pi * f0 * k * t + rng.uniform(0, 6.28))
        content += 0.2 * _bandpass(rng.standard_normal(n), sample_rate, 2000, 12000)
    elif kind == "silence":
        # Real room tone / mic self-noise is NOT flat white noise: it is
        # dominated by low-frequency rumble and rolls off well before the
        # ultrasonic band. Flat white noise would (wrongly) put most of its
        # energy above 18 kHz. Model it as low-passed, low-level noise.
        raw = rng.standard_normal(n)
        sos = sps.butter(4, 6000.0, btype="low", fs=sample_rate, output="sos")
        content = 0.02 * sps.sosfilt(sos, raw)
    else:
        content = _voiceband_content(n, sample_rate, rng)

    captured = _mic_nonlinearity(content, mic_a2, mic_a3)
    captured = _reverb(captured, sample_rate, rt60, rng)
    captured = _add_noise(captured, snr_db, sample_rate, rng)
    return _normalise(captured)


def _normalise(sig: np.ndarray) -> np.ndarray:
    """Scale DOWN to a 0.9 ceiling, but never boost a quiet clip up to full scale.

    Boosting near-silence would turn a quiet room into full-scale broadband
    noise -- an artefact, not a real recording. Quiet stays quiet.
    """
    peak = float(np.max(np.abs(sig))) if sig.size else 0.0
    gain = min(1.0, 0.9 / peak) if peak > 1e-9 else 1.0
    return (sig * gain).astype(np.float32)


def _write(path: str, sig: np.ndarray, sample_rate: int) -> None:
    pcm = (np.clip(sig, -1.0, 1.0) * 32767.0).astype(np.int16)
    wavfile.write(path, sample_rate, pcm)


# --------------------------------------------------------------------------- #
# Corpus builder -- sweeps realistic conditions
# --------------------------------------------------------------------------- #
# (carrier_hz, distance_m, snr_db) conditions spanning easy -> hard attacks
_ATTACK_CONDITIONS = [
    (25_000.0, 0.2, 35.0),   # near, strong, low carrier
    (28_000.0, 0.5, 25.0),   # typical
    (32_000.0, 1.0, 20.0),   # farther, higher carrier
    (38_000.0, 1.5, 15.0),   # far, weak, high carrier (hard)
    (24_000.0, 0.3, 30.0),
    (30_000.0, 0.8, 22.0),
]
_BENIGN_KINDS = ["speech", "music", "silence", "speech", "music", "speech"]


# --------------------------------------------------------------------------- #
# Stage 8: the device ADC (what a real recording keeps)
# --------------------------------------------------------------------------- #
def capture(sig: np.ndarray, sample_rate: int, capture_rate: int, order: int = 8) -> np.ndarray:
    """Model a real device capture chain: anti-alias low-pass, then resample.

    Phones, laptops and smart speakers store audio at 16-48 kHz behind an
    anti-aliasing filter. An ultrasonic carrier does not survive this; only
    the baseband residue the mic non-linearity folded down does. 8th-order
    Butterworth (zero-phase) at 0.45 x capture_rate, then polyphase resample.
    capture_rate <= 0 returns the input unchanged (the raw high-rate case).
    """
    if capture_rate <= 0 or capture_rate == sample_rate:
        return sig
    if capture_rate > sample_rate:
        raise ValueError("capture_rate must not exceed the synthesis rate")
    wc = 0.45 * capture_rate
    sos = sps.butter(order, wc / (sample_rate / 2.0), btype="low", output="sos")
    filtered = sps.sosfiltfilt(sos, sig)
    g = np.gcd(int(sample_rate), int(capture_rate))
    return sps.resample_poly(filtered, int(capture_rate) // g, int(sample_rate) // g).astype(np.float32)


def build_corpus(out_dir: str, sample_rate: int = 192_000, n: int = 12, seed: int = 0,
                 capture_rate: int = 48_000):
    """Write n attack + n benign recordings under out_dir, return a manifest list.

    Signals are synthesised at `sample_rate` and then captured at `capture_rate`
    through the ADC model; pass capture_rate=0 to keep the raw high-rate audio.
    """
    os.makedirs(out_dir, exist_ok=True)
    rng = np.random.default_rng(seed)
    out_rate = capture_rate if capture_rate > 0 else sample_rate
    manifest = []
    for i in range(n):
        c_hz, dist, snr = _ATTACK_CONDITIONS[i % len(_ATTACK_CONDITIONS)]
        s = int(rng.integers(0, 1_000_000))
        sig = make_attack(sample_rate=sample_rate, carrier_hz=c_hz, distance_m=dist,
                          snr_db=snr, seed=s)
        sig = capture(sig, sample_rate, capture_rate)
        p = os.path.join(out_dir, f"attack_{i:02d}_c{int(c_hz/1000)}k_d{dist}m_{int(snr)}db.wav")
        _write(p, sig, out_rate)
        manifest.append((p, "attack"))
    for i in range(n):
        kind = _BENIGN_KINDS[i % len(_BENIGN_KINDS)]
        dist = _ATTACK_CONDITIONS[i % len(_ATTACK_CONDITIONS)][1]
        snr = _ATTACK_CONDITIONS[i % len(_ATTACK_CONDITIONS)][2]
        s = int(rng.integers(0, 1_000_000))
        sig = make_benign(sample_rate=sample_rate, distance_m=dist, snr_db=snr,
                          kind=kind, seed=s)
        sig = capture(sig, sample_rate, capture_rate)
        p = os.path.join(out_dir, f"benign_{i:02d}_{kind}.wav")
        _write(p, sig, out_rate)
        manifest.append((p, "benign"))
    return manifest


# --------------------------------------------------------------------------- #
# Optional: build + score with EchoGuard
# --------------------------------------------------------------------------- #
def evaluate(out_dir: str, sample_rate: int = 192_000, n: int = 12, seed: int = 0,
             capture_rate: int = 48_000):
    from echoguard.pipeline import Pipeline, HIGH_RISK, SUSPICIOUS, INSUFFICIENT_DATA

    manifest = build_corpus(out_dir, sample_rate, n, seed, capture_rate)
    guard = Pipeline()
    rows, tp, fp, insuf = [], 0, 0, 0
    for path, truth in manifest:
        sr, data = wavfile.read(path)
        x = data.astype(np.float64) / 32768.0
        result = guard.analyze(x, sr)
        v = result.verdict
        flagged = v in (HIGH_RISK, SUSPICIOUS)
        if truth == "attack" and flagged:
            tp += 1
        if truth == "benign" and flagged:
            fp += 1
        if v == INSUFFICIENT_DATA:
            insuf += 1
        rows.append((os.path.basename(path), truth, v, result.overall_risk))

    n_atk = sum(1 for _, t in manifest if t == "attack")
    n_ben = sum(1 for _, t in manifest if t == "benign")
    chain = f"captured at {capture_rate} Hz through the ADC model" if capture_rate > 0 else "raw, no ADC"
    print(f"\nRealistic synthetic corpus  (synth {sample_rate} Hz, {chain}; {n_atk} attack / {n_ben} benign)\n")
    print(f"  {'file':42s} {'truth':8s} {'verdict':18s} risk")
    print("  " + "-" * 78)
    for name, truth, v, risk in rows:
        mark = "" if ((truth == "attack") == (v in (HIGH_RISK, SUSPICIOUS))) else "  <-- MISS"
        print(f"  {name:42s} {truth:8s} {v:18s} {risk:.2f}{mark}")
    print("  " + "-" * 78)
    print(f"  detection (attacks flagged): {tp}/{n_atk}   "
          f"false alarms (benign flagged): {fp}/{n_ben}   "
          f"insufficient data: {insuf}/{n_atk + n_ben}")
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="fixtures/synth", help="output directory")
    ap.add_argument("--sr", type=int, default=192_000,
                    help="synthesis sample rate; must exceed 4x the highest carrier so the mic's 2fc "
                         "product is represented rather than folded (default 192000)")
    ap.add_argument("--capture", type=int, default=48_000,
                    help="device capture rate through the ADC model (anti-alias + resample); "
                         "0 = keep the raw synthesis-rate audio (default 48000)")
    ap.add_argument("--n", type=int, default=12, help="clips per class")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--evaluate", action="store_true", help="also score with EchoGuard")
    a = ap.parse_args()
    if a.evaluate:
        evaluate(a.out, a.sr, a.n, a.seed, a.capture)
    else:
        m = build_corpus(a.out, a.sr, a.n, a.seed, a.capture)
        print(f"wrote {len(m)} clips to {a.out}")


if __name__ == "__main__":
    main()
