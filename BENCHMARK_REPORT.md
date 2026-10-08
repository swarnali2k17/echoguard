# EchoGuard — Benchmark Report

**Version:** v0.2.0  **As-of:** 2026-10-08 (§1–5 measured 2026-10-06 on v0.1.0)  **Scope:** what has actually been measured, and what has not.

## Summary

EchoGuard has been benchmarked on two things that can be measured honestly without a capture lab: its **false-positive rate on benign audio** (real result) and its **detector sensitivity to a controlled synthetic probe** (characterization). Benign false positives are **0%** after a corroboration fix (down from 75%). The sensitivity analysis shows the detector flags a high-frequency carrier once it reaches ~5% of the signal level and covers 18 kHz and above, but is **blind to weak carriers (below ~3%) and to the 15–17 kHz band**. The **detection rate against real attacks has not been measured** — that requires the capture lab and is the main open item. No real-attack numbers are reported here because none have been produced; fabricating them would invalidate the work.

## 1. Benign false positives — measured, real

60 benign clips (synthetic: speech, bright music, pink noise, white noise), scored by the pipeline.

| Benign audio | FP before fix | FP after fix |
| --- | --- | --- |
| Speech | 0% | 0% |
| Bright music | 100% | 0% |
| Pink noise | 100% | 0% |
| White noise | 100% | 0% |
| **Overall (60 clips)** | **75%** | **0%** |

The original "flag if any detector fires" logic condemned any broadband/high-frequency audio. The fix scores an injection as the geometric mean of two detectors — out-of-band energy **and** a narrowband carrier, both required — which benign audio never satisfies (it trips at most one). *(Chart: `echoguard_fp_beforeafter.png`.)*

## 2. Synthetic sensitivity analysis — detector characterization

**What this is and is not.** These are controlled synthetic probes — a generic high-frequency carrier over a benign speech-band base — used to map the detector's response. They contain no voice command and drive no device. This is **not** a real-attack benchmark and makes **no** claim about real-world detection. Its purpose is to quantify the known blind spots of the corroboration rule. *(Chart: `echoguard_sensitivity.png`.)*

### A. Sensitivity to carrier strength

Carrier at 21 kHz, strength swept as a fraction of the base amplitude; 10 trials per point.

| Carrier strength (× base) | Detection |
| --- | --- |
| ≤ 0.023 | 0% |
| 0.034 | 10% |
| **0.051** | **100%** |
| ≥ 0.077 | 100% |

**Sensitivity floor ≈ 0.05.** A carrier at or above ~5% of the signal is reliably flagged; below ~3% it is missed. This is the weak-carrier blind spot, quantified: a real attack whose carrier is heavily attenuated by the time it reaches the mic could fall under this floor.

### B. Coverage across carrier frequency

Carrier strength fixed at 0.3; frequency swept.

| Carrier frequency | Detection |
| --- | --- |
| 15 kHz | 0% |
| 16 kHz | 0% |
| 17 kHz | 0% |
| 18 kHz | 100% |
| 19–23 kHz | 100% |

**Blind band: 15–17 kHz.** The out-of-band detector's threshold sits at 18 kHz, so a near-ultrasound carrier in 15–17 kHz is not corroborated and is not flagged. Lowering the threshold would extend coverage but would re-introduce false positives on bright music (which has genuine energy there) — a tuning trade-off to study on real data, not to guess at.

## 3. Not yet measured — real-attack detection

**The detection rate against real inaudible-injection attacks is unmeasured.** It requires:

- real attack captures (a controlled lab, or shared captures from the paper authors), and
- running them through the existing `benchmark/evaluate.py` harness, which is ready for exactly this.

Until that exists, EchoGuard's detection claim is limited to synthetic signals. This is stated plainly rather than papered over — see the Lab Plan for how to produce the real captures.

## 4. Limitations & recommendations

- The 0% benign FP is on synthetic benign audio; confirm on real recordings across devices.
- The corroboration rule trades sensitivity for precision: it will miss weak carriers (§2A) and the 15–17 kHz band (§2B). Both are tuning knobs to calibrate on real data.
- Recommended next step: capture a real labelled set (benign + attack), re-run both the FP and detection benchmarks, and sweep the out-of-band threshold and the corroboration weights to pick an operating point with measured, not assumed, trade-offs.

## 5. Short-clip false positives — found and fixed

The 0% benign FP in §1 was measured on 1.5 s clips. On shorter white noise at 48 kHz the carrier detector misfired: a Welch spectrum built from few segments is noisy, so its largest high-band bin stands several dB above the median by chance, and the old fixed `prominence / 30 dB` scale counted that as a carrier.

**Fix:** a constant-false-alarm-rate threshold. Each Welch bin of noise is ~ χ²(ν)/ν, with ν the equivalent degrees of freedom of the segment average (Hann, 50% overlap). The detector computes the peak-to-median ratio that noise alone exceeds with probability 10⁻³ over the high band, and scores only the prominence in excess of it. The threshold is 8.4 dB at 0.1 s, 2.7 dB at 1 s and 2.0 dB at 2 s.

| White noise, 50 clips | 0.1 s | 0.2 s | 0.3 s | 0.5 s | ≥ 1 s |
| --- | --- | --- | --- | --- | --- |
| Flagged before | 100% | 96% | 48% | 2% | 0% |
| Flagged after | 0% | 0% | 0% | 0% | 0% |

Everything else is unchanged: benign FP 0/60, sensitivity floor α ≈ 0.051, 15–17 kHz blind band, realistic synthetic attacks 12/12 with 0/12 false alarms. Regression tests: `tests/test_detectors.py::test_short_*`.

## 6. v0.2.0 — real audio, realistic capture chains, and what the detector cannot see

**As-of:** 2026-10-08. Four measurements made against this version; scripts and raw outputs are being moved into `benchmark/` and are available on request until then.

### 6a. Real benign audio: 2.0% → 0% false positives

307 openly licensed real recordings at 44.1/48 kHz (VCTK clean speech, FSD50K speech-with-noise / music / environmental / foley, ESC-50, MUSDB18-HQ mixes), scored as full clips and as 0.5 s and 0.2 s windows (3,230 segments).

| Window | FP before v0.2.0 | FP after |
| --- | --- | --- |
| Full clip | 2.0% (6/307, 1 HIGH_RISK) | **0%** |
| 0.5 s | 1.1% | **0%** |
| 0.2 s | 0.7% | **0%** |

Speech was 0% at every window length before and after. The 13 flagged clips were close-miked hi-hats, a ticking stopwatch, scissors, a lighter, shattering glass (genuinely 10–18% of energy above 18 kHz, with a 12 dB resonance in a flat spectrum) and three synth-sample tails at −51 to −73 dBFS flagged on dither alone. Two changes cleared them without touching any attack benchmark:

- **Absolute level floor** (`MIN_BAND_LEVEL_DBFS = −60`): the 18 kHz+ band must reach −60 dBFS before its ratio counts. The 99th percentile of that level across benign segments is −34 dBFS.
- **Carrier narrowness** (`peak_narrowness`): power within ±2 Welch bins of the peak over power within ±20 bins. Synthetic carriers (AM, DSB-SC, SSB, and a 25 kHz carrier aliased through a 48 kHz ADC) measure 1.00; a 19 kHz beacon under heavy noise 0.79; the benign resonances 0.17–0.61. Carrier risk is scaled down between 0.60 and 0.85.

Unchanged after both: sensitivity floor α = 0.051, 15–17 kHz blind band, synthetic attacks 12/12 with 0/12 false alarms, synthetic benign 0/60. One red-team case changed: a *spread-spectrum* "carrier" (band-limited noise 20–23.5 kHz) was previously flagged 12/12 and is now 0/12, because it is not narrow. No published attack uses one; a wideband carrier does not demodulate to an intelligible command.

### 6b. Realistic capture chains: 0% detection

Until v0.2.0 the simulator wrote 96 kHz audio with no anti-alias filter, so the carrier itself survived into the file. Real devices do not do that. `synth_attacks.py` now synthesises at 192 kHz (so the microphone's 2·f_c product is represented rather than folded) and, by default, captures through a device ADC model: 8th-order Butterworth anti-alias filter at 0.45 × f_s, then a polyphase resampler. `--capture 0` keeps the raw audio.

| `--capture` | Detected | False alarms | Note |
| --- | --- | --- | --- |
| 0 (raw 192 kHz, no filter) | 12/12 | 0/12 | the old benchmark; carrier present in the file |
| 48000 | **4/12** | 0/12 | only the 24–25 kHz carriers: they sit in the filter's transition band and alias to 23–24 kHz. 28–38 kHz: 0/8 |
| 44100 | **1/12** | 0/12 | |
| 16000 | 0/12 | 0/12 | INSUFFICIENT_DATA on all 24 clips |

The red-team run with a 28 kHz carrier alone gave 0/12 at both 48 and 44.1 kHz. After the chain the demodulated command sits in 0–8 kHz at the same level as the room audio (−0.08 dB relative), and nothing remains above 18 kHz (`benchmark/redteam/A_baseband_48k.png`). The current detectors measure the carrier, and the carrier is gone. The transition-band leak that saves the 24–25 kHz cases is real (MicGuard, USENIX 2024, builds a detector on it) but depends on the device's filter; a sigma-delta decimation filter is far steeper than this model. `tests/test_detectors.py::test_adc_capture_attack_is_detected` is marked xfail until a detector passes it.

### 6c. Real attack recordings: DolphinAttack public set

The DolphinAttack authors' demo dataset (USSLab, 2,934 WAV files, 5 phones × 7 distances × 23 commands, no licence stated) is recorded by the victim phone's own microphone and stored at **16 kHz**. Every clip returns INSUFFICIENT_DATA. This set is the first real-attack benchmark for the baseband detector; it contains no benign recordings, so matched benign captures from the same devices are still needed.

### 6d. Other evasions and look-alikes (48 kHz, no filter)

| Case | Result |
| --- | --- |
| Carrier at 15, 16, 17, 17.9 kHz | 0/12 detected: below the 18 kHz out-of-band edge, so the geometric mean is zero |
| Carrier at 18.1 kHz | 12/12 |
| Energy above 18 kHz held under 1.09% | 0/12 (costs the attacker ≈ 6 dB of carrier level) |
| 19 kHz pilot tone (retail beacon) | **12/12 false alarms** |
| 20–22 kHz power-supply whine | **12/12 false alarms** |
| Harmonic-rich synth to 22 kHz; keyboard clicks | 0/12 (correctly cleared) |

### 6e. Conclusion

EchoGuard v0.2.0 is a clean, fast, quiet screen for high-rate lab captures that still contain the carrier. On a phone it detects nothing, and current near-ultrasound attacks (16–22 kHz) fall in or beside its blind band. The next milestone is a detector for the demodulated residue in 0–8 kHz, corroborated so that benign ultrasound no longer scores. Section 7 reports what was found when that was attempted.

## 7. Baseband detection: what the real data says

**As-of:** 2026-10-08. Scripts and raw outputs in `benchmark/baseband_study/`.

The plan was a detector for what survives a phone's capture chain: the demodulated residue in 0–8 kHz. Two studies were run. The first measured candidate features on ADC-captured synthetic clips (attack vs condition-matched benign, at 48 and 16 kHz; carriers 18–40 kHz; AM, DSB-SC and SSB; real VCTK speech as room audio and, in one variant, as the modulator). The second asked whether any of it holds on real recordings. The scripts are in `benchmark/baseband_study/synthetic/`.

### 7a. On synthetic data, the apparent cue is largely a simulator artefact

A first pass reported a "ghost" feature (2–20 Hz energy vs the speech band) at AUC 0.92 — a near-perfect separator. It did not survive scrutiny. Four properties of the simulator, not of the attack, were producing it, and each was fixed:

1. **Reverb gain.** The generator's reverb tail is unnormalised noise × decay; at 192 kHz it ran ~18 dB hotter than the direct path and, convolved with the x² DC offset of a strong carrier, produced a large low-frequency onset ramp — a fake ghost. Fixed to a DRR-scaled tail, with the first 0.5 s (reverb transient) discarded.
2. **Noise floor.** The generator scales noise to the audible content, so a tone-only clip was unphysically clean. A fixed device self-noise floor (−60 dBFS white + −55 dBFS rumble) was added to every clip.
3. **AC coupling.** A 10 Hz DC blocker was added after the ADC, as a real codec has, and a genuine "quiet room" benign kind (the generator's "silence" is normalised to full scale).
4. **Level.** Because the generator normalises the mic input to full scale with the carrier present, the attack baseband lands ~20 dB below benign content. The absolute-level feature scored AUC 0.14 (0.86 inverted) — a gain/AGC artefact — and is excluded.

With those fixed, **no single physics feature exceeds AUC 0.77**, and for attacks whose modulator is real speech nothing exceeds 0.67 (`per_feature_auc.csv`):

| Feature | 48 kHz (env / speech mod) | 16 kHz (env / speech mod) |
| --- | --- | --- |
| Ghost: sub-20 Hz energy fraction | 0.50 / 0.39 | **0.81** / 0.64 |
| Ghost: 2–20 Hz vs 300–3400 Hz | **0.76** / 0.56 | **0.77** / 0.60 |
| Aliased line prominence / stability | 0.72 / 0.67 | 0.44 / 0.47 (none) |
| Modulation spectrum, envelope², bicoherence, harmonicity, flatness, H2 | 0.40–0.56 | 0.40–0.58 |

The ghost is real only for the generator's *envelope-only* attack (0.76–0.81); for a real-speech modulator (0.56–0.64) the residue is the envelope-squared term, which benign speech produces through the same microphone non-linearity anyway. It also sits where any AC-coupled codec high-pass removes it.

A combined gradient-boosted model over all features reaches a high headline AUC but no usable operating point (`results.json`):

| Rate / model | CV AUC | EER | Benign FP at 95% TPR | Look-alike FP (tone / PSU / all) |
| --- | --- | --- | --- | --- |
| 48 kHz gradient boosting | 0.959 | 11.3% | 23.5% | 100% / 77% / 74% |
| 16 kHz gradient boosting | 0.936 | 15.5% | 25.8% | 93% / 17% / 42% |

At a 95%-detection threshold the model still flags **33% of real VCTK speech and 53% of speech-in-noise** (music, quiet room and synthetic speech < 13%), and almost every steady 19 kHz tone. And it does not transfer: a 48 kHz model trained on 25–40 kHz carriers is at **chance (AUC 0.50)** on 18–22 kHz, because at 48 kHz it learns the directly-in-band 18–21.6 kHz carrier line rather than any demodulation residue. The high AUC is mostly a floor/level separation of "quiet, LF-heavy, speech-poor" clips from loud speech — exactly what real AGC would erase.

### 7b. On real recordings, even the surviving cue does not transfer

The same ghost feature on the 307 real benign clips and the 2,934 real DolphinAttack captures (`ghost_real.csv`):

| Set | 2–20 Hz vs speech band, median | 95th percentile |
| --- | --- | --- |
| Real benign, 307 clips | −23.4 dB | +4.7 dB |
| Real benign, 1 s windows | −14.2 dB | +9.4 dB |
| DolphinAttack, Pixel | −32.3 dB | −4.7 dB |
| DolphinAttack, Huawei Mate 9 / Nova 2 / Oppo K3 | −11 to −14 dB | −0.8 to −4 dB |

Real recordings carry wind, handling noise and rumble below 20 Hz that the simulator never produced; a threshold that catches 80% of the real attacks flags 20–60% of real benign audio by category. The feature's AUC on the Pixel is 0.085 — it points the wrong way, because phones high-pass harder than the benign corpus.

### 7c. The aliased-line cue is real but belongs to particular phones

A stable line at exactly 5, 6 or 7 kHz (prominence ≥ 10 dB, within 10 Hz of a round kilohertz) appears in 99.7% of Mate 9, 98.7% of Nova 2 and 65.5% of Oppo K3 captures, in 0–1% of real benign clips (11% of guitar/piano), and in **0%** of Pixel and 4% of Oppo Reno captures (`line_real16k.csv`). This is MicGuard's "carrier trace" (USENIX 2024): an ADC artefact of specific devices. It is a strong cue where it exists and absent where it does not, so it cannot be the detector.

### 7d. A benign-only anomaly model finds the channel, not the attack

NormDetect-style one-class models (Mahalanobis, isolation forest, PCA reconstruction; 83 features: 40 log-mel bands mean and std, ghost ratios, flatness; 1 s segments at 16 kHz; split by clip) trained on real benign audio only, scored on DolphinAttack (`benchmark/baseband_study/oneclass/`):

| Training set | Honest test (Pixel, Reno) | Devices with the ADC tone |
| --- | --- | --- |
| Benign speech only | AUC 0.75–0.83; **4–6% detected at 1% false alarms**; benign music/environment flagged 13–39% by the same model | 39–48% detected, driven by the 6–7 kHz tone |
| All benign categories | **AUC 0.50–0.52; 0% detected at 1% false alarms** | 0–1% |

What the speech-only model keys on in the Pixel clips is low-band temporal variability and reduced high-band variability — recording-channel and noise-floor properties — not sub-100 Hz energy (single-feature AUC 0.56 on the Pixel). In this feature space a real inaudible-attack capture at 16 kHz looks like noisy speech from an unseen phone.

### 7e. Conclusion and what it means for the roadmap

No baseband detector can be built or validated from the data that exists. The synthetic corpus separates for a reason that real audio does not reproduce; the one real attack set has no benign recordings from the same phones, so any model trained to separate it from other benign audio learns the device. This is the same confound the anti-spoofing pilot hit and documented. The phase-2 gate is therefore not passed, and `test_adc_capture_attack_is_detected` stays xfail.

The prerequisite is matched data: benign and attack recordings from the same devices, same rooms, same gain chain — NormDetect's protocol (7 distances, ~24 devices) is the reference, and even a few minutes of matched benign audio from the DolphinAttack phones would let the ADC-tone question be settled. `docs/recording_protocol.md` and `tools/capture/` exist to collect exactly this. A real-data study should also, per §7a: (1) use a device's *measured* gradual anti-alias/decimation response rather than a steep synthetic filter; (2) record through real AGC; (3) put loud steady tones and PSU whine in the *benign* class; and (4) treat the envelope-only attack as an upper bound, not a representative attack. Until then, the ghost and aliased-line cues are kept as *reported evidence* in `benchmark/baseband_study/synthetic/features.py` for scoring a partner's captures, not as verdict inputs.

### 7f. What phase 2 did deliver: the beacon problem

An injected command modulates its carrier, so the carrier has sidebands spanning the command's bandwidth. A retail beacon, a pilot tone or a power-supply whine has none. The carrier detector now measures sideband energy (|f − f_peak| in 150 Hz–4 kHz, net of the local floor) relative to the line; below −60 dB the peak is reported as a bare tone and its risk scaled to 5%.

| Case (48 kHz, 12 seeds) | Before | After |
| --- | --- | --- |
| 19 kHz pilot tone over room tone | 12/12 false alarms | **0/12** (R = 0.22, tone reported in evidence) |
| 20–22 kHz PSU whine | 12/12 false alarms | **0/12** |
| Speech-bandwidth AM attacks, raw 192 kHz | 12/12 | 12/12 |
| Real benign audio, 3,230 segments | 0 | 0 |
| Carrier at 15 / 16 / 17 / 17.9 kHz (modulated) | 0% each | 25 / 17 / 25 / 42% — the sidebands of a near-18 kHz carrier reach above 18 kHz |

Two consequences. The simulator's attacks are now modulated by a speech-bandwidth signal (`modulator="voiceband"`, the default) rather than by a bare syllable envelope, because a real command has 150 Hz–4 kHz of content and its sidebands show it; the old envelope-only modulator is kept for reference. And the sensitivity sweep's probe tone is modulated the same way; its floor is unchanged at α = 0.051. A beacon under very loud broadband hiss (noise amplitude 0.3 full scale against a 0.5 tone) can still read as modulated; at ordinary noise levels it does not.

## Reproduce

```bash
python benchmark/corpus.py benchmark/corpus_benign
python benchmark/evaluate.py benchmark/corpus_benign   # benign false positives
python benchmark/sensitivity.py                        # synthetic sensitivity sweeps
```

---

*This report describes only measurements actually performed. Real-attack detection figures are intentionally absent until the capture lab produces them.*
