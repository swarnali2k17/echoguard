# Published attacks: SWhisper and MetaAttack

Recordings shared privately by the attack authors (SWhisper) or published as demos without a licence
(MetaAttack). **They are not in this repository and must not be added.** This file records only the method
and the numbers.

## The attacks

- **SWhisper** (Zijian et al.) injects jailbreak prompts into voice agents over a **~16 kHz** carrier.
  - Recording: iPhone 14 Pro at 1 m, AAC at 48 kHz, 58 minutes, 50 payloads.
  - The 16 kHz carrier sits in the 15–17.9 kHz band that the >18 kHz out-of-band test cannot see.
- **MetaAttack** (arXiv 2501.15031) focuses a **40 kHz** carrier through an acoustic metamaterial.
  - Six demo recordings: AAC at 48 kHz, 3 commands, each with and without the metamaterial.
  - The carrier cannot survive a 48 kHz capture, and the AAC encode removed everything above 18 kHz.

## What changed

| Layer | Change |
| --- | --- |
| L4 content | Jailbreak families (Wei et al. 2023): always-comply persona, refusal suppression, forced response prefix, ethics waiver, fictional framing |
| L1 carrier sidebands | Measured against the background 4.5–7.5 kHz below the carrier (not a ±150 Hz running median), by median, behind a CFAR gate; mirrored-line search for tone modulation |
| L1 near-ultrasonic | A modulated 15–18 kHz carrier is scored through its own path. All of these must hold: a narrow, prominent peak; a sideband plateau above the background; sideband energy fluctuating in time beyond steady-noise variance (chi-square, ~20 ms frames); and persistence for **3 consecutive windows** (~2 s) |
| L1 bandwidth | A capture whose 18 kHz+ band was never recorded returns INSUFFICIENT_DATA, not CLEAR. Two cases: the band sits below any microphone noise floor (< −110 dBFS), or a brick-wall cut-off lies below 18 kHz |

## Results

All runs use the windowed scan (1 s windows, 0.5 s hop).

| | before (`master` ae9c6e6) | after |
| --- | --- | --- |
| **SWhisper**, original 58 min | CLEAR (0 / 7,006 windows) | **HIGH_RISK**: 2,070 windows (30%) in 164 sustained events; the rest INSUFFICIENT_DATA (AAC cut-off) |
| **SWhisper** payload text, L4 | 2 / 50 | **50 / 50** |
| **SWhisper** "17 kHz" demo (transcoded, content to 7.5 kHz) | CLEAR | **INSUFFICIENT_DATA** |
| **MetaAttack**, 6 files | CLEAR (`assessable: true`) | **INSUFFICIENT_DATA** (content stops at 18.0 kHz) |
| **MetaAttack**, L2 anti-spoofing (Spectra-AASIST, already in the trust layer) | — | **43 / 45** 4 s chunks below the reject point (median logit −1.35 to −2.40) |

Controls for the anti-spoofing result: genuine LibriSpeech speech scored median +3.24; the same speech at
−50 / −58 dBFS over room noise scored +3.20 / +3.09 (0 / 24 rejected); room noise alone scored about −0.7.

Benign and regression checks (synthetic unless stated):

| | before | after |
| --- | --- | --- |
| Unmodulated 19–22 kHz beacons, 20–40 dB SNR (`benchmark/beacon_fp.py`) | 302 / 800 flagged | **0 / 800** |
| Benign tones at 15–18 kHz, 60 s, real AAC encode, white / pink / shaped dither | — (blind band) | **0 / 4,760 windows, 0 / 40 files** |
| Benign tones at 15–18 kHz, 1 s clips, with and without AAC | — | **0 / 640** |
| Real voice commands (SLURP 5,005) / deepset benign (271), L4 false alarms | 0 / 0 | **0 / 0** |
| Carrier-strength floor; 22 kHz carrier | α 0.0511; 90% | α 0.0511; **100%** |
| Field captures, 320 lab files (`captures/`) | 14 attack captures: 10 high + 4 suspicious | **12 high + 2 suspicious**; every other file's verdict unchanged |
| Synthetic chain: raw 192 kHz / 48 kHz converter | 12/12 / 3/12 | 12/12 / 3/12 |

## Limits

- **Benign controls are synthetic.** No matched benign recordings exist from the attack hardware. The 307-recording real benign corpus was not available to re-run; with the bandwidth rule, MP3- or AAC-derived clips in it will now read INSUFFICIENT_DATA rather than CLEAR.
- **Detection is window-level.** SWhisper is flagged per window. Per-payload rates need segment timestamps from the authors.
- **Short commands.** The near-ultrasonic path needs ~2 s of evidence; a 15–18 kHz command shorter than that is reported but not scored.
- **MetaAttack's 40 kHz carrier** is detectable by the signal layer only in a capture at ≥ 96 kHz. After a 48 kHz converter, the trust layer's anti-spoofing check is the defence. That result has no same-device genuine control.
- **deepset injections stay at 60 / 184.** These are mostly multilingual text-pipeline attacks, outside the voice threat model.
