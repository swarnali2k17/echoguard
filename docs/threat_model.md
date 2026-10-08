# EchoGuard threat model and detection coverage

**As-of:** 2026-10-08. This document answers one question: *beyond DolphinAttack,
what classes of voice-injection attack exist, and which of them can a software
detector that runs on an ordinary recording actually catch?* You cannot defend
what you cannot detect, and you cannot measure detection without test data — so
each class below is scored on (a) what survives a commodity capture, (b) whether
EchoGuard today sees it, and (c) whether any public real-attack data exists to
test it. The attack descriptions are at the level needed to state detection
requirements; this is a defensive document and contains no attack instructions.

## Two facts that organise everything

1. A microphone's analogue-to-digital converter sits behind an anti-aliasing
   low-pass filter. Anything above roughly 0.45 × the capture rate is removed
   **before** the audio is stored. So the band an attack occupies *in the air* is
   not the band a detector sees *in the file*. Our measurements
   (`BENCHMARK_REPORT.md` §6) show the current detectors key on the ultrasonic
   *carrier*, which is exactly what the filter removes on a phone.

2. For every *physical-injection* class the exploit is the **non-linearity of the
   MEMS microphone and pre-amp**, which self-demodulates the out-of-band carrier
   down into the **audible baseband (≈ 0–8 kHz)**. So the command itself reappears
   *in-band* and survives any ADC. The detectable residue for those classes is the
   **demodulation artefact, not the carrier** — which is why a baseband detector is
   possible *in principle*, and why §7 of the benchmark report set out to build
   one. That it could not yet be separated from benign audio on real recordings is
   a data problem (§7e), not a statement that the residue is absent.

The decisive column in the matrix is therefore "what survives into the stored
audio", because that — and only that — is what a detector on a normal recording
can use.

## Coverage matrix

Legend for **EchoGuard today**: ✅ detected on a capture that preserves the band ·
⚠️ detected only from a high-rate (≥ 96 kHz) capture, not a phone · ❌ not
detectable with current features · N/A out of the injection scope.

| # | Attack class | In-air band | What survives a 16/48 kHz capture | EchoGuard today | Public real-attack data |
| --- | --- | --- | --- | --- | --- |
| 1 | **Ultrasonic AM injection** (DolphinAttack family) | ~25–40 kHz carrier | Demodulated command in 0–8 kHz; carrier gone; sometimes device-specific aliased lines | ⚠️ carrier only, at ≥ 96 kHz | **Yes** — DolphinAttack set, 16 kHz, no matched benign |
| 2 | **Near-ultrasound in media** (NUIT) | ~16–22 kHz | On devices that keep ≥ 16 kHz: the near-US band itself (partly). On devices that low-pass < 16 kHz: little | ⚠️ partial — a modulated carrier ≥ 18 kHz is caught; 15–17.9 kHz only via sidebands | No public capture set found |
| 3 | **Solid-surface / guided-wave** (SurfingAttack, SUAD) | ~21–28 kHz through a surface | Same as class 1: demodulated baseband; carrier removed | ⚠️ high-rate only | No public capture set found |
| 4 | **Laser / light injection** (LightCommands) | Optical, not acoustic | The induced *audible* signal in the mic; no ultrasonic trace | ❌ looks like ordinary injected audio in-band | Artifacts released (see datasets) |
| 5 | **EM / power-line injection** (GhostTalk-style) | RF / conducted | Induced in-band audio; no acoustic HF trace | ❌ | No public capture set found |
| 6 | **Hidden / adversarial audible audio** (Hidden Voice Commands, CommanderSong, psychoacoustic) | 0–8 kHz, audible but obfuscated | Everything — it is in-band by design | ❌ not an out-of-band signature; needs an ASR-consistency or adversarial-perturbation detector | Some code/samples public |
| 7 | **Adversarial perturbation vs ASR / LLM voice agents** (2024–2026: VRIFLE, AudioHijack, speech-LLM jailbreaks) | 0–8 kHz (some ultrasonic-delivered) | In-band perturbation; ultrasonic-delivered variants reduce to class 1/2 | ❌ for the in-band form | Some benchmarks public (agent-layer) |
| 8 | **Hearable / earbud-generated ultrasound** | demodulated inside the earbud | Phantom audio already in the user's ear; attacker's signal may not reach a separate mic | ❌ wrong sensor location | No public capture set found |
| 9 | **Metamaterial-assisted ultrasound** | ultrasonic, longer range | Same residue as class 1 | ⚠️ high-rate only | No public capture set found |
| 10 | **Replay / voice-clone spoofing** | 0–8 kHz speech | In-band synthetic/replayed speech | N/A — `echoguard.spoof` (separate baseline, 16.9% EER) | **Yes** — ASVspoof 2019/2021/5 |

## What this says about where to invest

Reading the matrix by the "what survives" column, the classes split three ways.

**(a) Detectable in principle from a normal 48 kHz recording — but we can't yet.**
Classes 1–3 and 9 all leave the *same* trace once the carrier is gone: the
demodulated command in 0–8 kHz, plus device-dependent aliased lines. That residue
is the entire addressable surface for a phone-side detector, and `BENCHMARK_REPORT.md`
§7 shows we could not separate it from benign audio with the data we have. This is
the highest-value target because four attack classes collapse into one detection
problem — and it is blocked on matched benign+attack data, which
`docs/recording_protocol.md` is designed to produce.

**(b) Detectable only with a high-rate capture.** The carrier itself (classes 1–3,
9 at ≥ 96 kHz) is what EchoGuard catches today. This is real and useful for a
*capture appliance* — a dedicated mic running at 96–192 kHz at the point of
ingest — but not for software on a shipping phone. NUIT (class 2) is the partial
exception: on devices that retain 16–22 kHz, a modulated carrier in that band is
visible at 48 kHz, which our sideband work now catches above 18 kHz.

**(c) No out-of-band signature at all, or no sensor access.** Classes 4, 5, 6, 7
(in-band), and 8 cannot be caught by *any* out-of-band or carrier feature, because
there is nothing out of band in the stored audio. These need a different detector
entirely — ASR-consistency, adversarial-perturbation, or liveness/anti-spoofing
(class 10, which we already have a baseline for). They are out of scope for the
injection detector and should be stated as such to a partner, not promised.

## Public datasets with real attack recordings

| Dataset | Class(es) | Clips | Rate | Licence | Matched benign, same device? |
| --- | --- | --- | --- | --- | --- |
| [DolphinAttack set](https://github.com/USSLab/DolphinAttack) | 1 (ultrasonic AM) | 2,934 | 16 kHz | none stated | No |
| [AdvSV](https://advsv.github.io/) ([paper](https://arxiv.org/html/2310.05369v1)) | 8 (over-the-air adversarial vs ASV) | 314k (2.0: ~628k) | **16 kHz** (VoxCeleb1 lineage) | CC-BY-SA, **gated** | Yes — genuine + adversarial through 3 speakers × 3 phones; but see note below |
| [ASVspoof 2019](https://zenodo.org/records/6906306) | 11 (replay, TTS, VC) | ~121k+ | 16 kHz | ODC-By | Yes (bonafide + spoof) |
| [ASVspoof 2021](https://www.asvspoof.org/index2021.html) (LA/PA/DF) | 11 + codec/deepfake | large | 16 kHz | ODC-By | Yes; PA has real room recordings |
| [ASVspoof 5](https://zenodo.org/records/14498691) | 11 + 7 adversarial (partial 8) | 1,006,363 | 16 kHz | ODC-By | Yes (~2,000 speakers) |

The only public corpora with **matched benign + attack from the same device/room**
are AdvSV (adversarial-vs-ASV, over-the-air) and ASVspoof PA/5 (replay/deepfake,
in-band). **No public matched-device corpus exists for any ultrasonic, laser, EM or
solid-surface physical-injection class.** DolphinAttack is the one ultrasonic set
and has no matched benign. Explicit negatives (searched, none located as of
2026-10-08): NUIT (2), SurfingAttack/SUAD (3), GhostTalk (5), VRIFLE/AudioHijack/
Sirens' Whisper (8), UltrasonicWhisper+ (9), MetaAttack (10) — demo pages or videos
only; LightCommands (4) hardware demos only.

> **AdvSV does not fill our gap (assessed 2026-10-08).** It is **16 kHz** and
> **in-band**, so it is below the injection detector's capture-rate floor — every
> clip returns INSUFFICIENT_DATA, and it cannot serve as a real-audio
> false-positive test (which needs ≥ 36 kHz). It is also **gated** (HuggingFace
> `amphion/AdvSV2.0`, ~85 GB, accepted-gate + token; or a Google Form for v1.0),
> so it is not a drop-in download. Its one potential use is the experimental spoof
> module (genuine vs adversarial EER on condition-matched audio), but our coarse
> spectral features are not built for small PGD perturbations and would likely
> score near chance. Conclusion: not worth a standing benchmark; **ASVspoof
> remains the controlled anti-spoofing corpus.** The matched-data route for the
> injection detector is still self-collection (`docs/recording_protocol.md`).

**Coverage risk to state in any PoC:** our evaluation to date covers exactly one of
eleven classes (ultrasonic demodulation, via DolphinAttack) and the near-ultrasound
band (2) is structurally invisible to that 16 kHz set. The largest untested
surfaces are in-band adversarial (6, 7, 8) and NUIT's 16–20 kHz band (2).

## The data gap, stated plainly

For most inaudible-injection classes **no public corpus of real attack recordings
exists**. This is why "we won't be able to prevent them" is, today, literally true
for classes 2, 3, 5, 8-ultrasonic, 9: there is nothing to measure a detector
against. Two actions change this:

1. **Collect matched data ourselves** for classes 1–3 using
   `docs/recording_protocol.md` (the probe reproduces the class-1/3 capture
   fingerprint without a command payload). This unblocks the §7 baseband work.
2. **Request author captures** for NUIT, SUAD and the hearable work — several
   papers released demo videos but not audio; a direct request to the authors,
   under a data-use agreement, is the realistic route and costs only email.

Until (1) or (2) exists, every detection claim for classes 2–3 and 8–9 is
unvalidated, and the honest position for a PoC is: EchoGuard screens the
carrier-bearing classes on a high-rate capture, flags the NUIT band above 18 kHz
on devices that retain it, and declines (INSUFFICIENT_DATA) rather than guessing on
a narrowband phone capture.

## References (attack classes)

1. DolphinAttack — [arXiv 1708.09537](https://arxiv.org/pdf/1708.09537) (CCS 2017)
2. NUIT — [USENIX Security 2023](https://www.usenix.org/conference/usenixsecurity23/presentation/xia)
3. SurfingAttack — [NDSS 2020](https://www.ndss-symposium.org/wp-content/uploads/2020/02/24068.pdf); SUAD (fc = 21 kHz) — [arXiv 2508.02116](https://arxiv.org/html/2508.02116v1)
4. LightCommands — [USENIX Security 2020](https://www.usenix.org/conference/usenixsecurity20/presentation/sugawara)
5. GhostTalk — [NDSS 2022](https://ndss-symposium.org/wp-content/uploads/2022-254-paper.pdf)
6. Hidden Voice Commands (USENIX 2016); CommanderSong — [USENIX Security 2018](https://www.usenix.org/conference/usenixsecurity18/presentation/yuan-xuejing)
7. Psychoacoustic adversarial — [Schönherr et al., NDSS 2019](https://arxiv.org/abs/1808.05665)
8. VRIFLE — [NDSS 2024](https://www.ndss-symposium.org/wp-content/uploads/2024-30-paper.pdf); AudioHijack — [arXiv 2604.14604](https://arxiv.org/pdf/2604.14604); Sirens' Whisper — [arXiv 2603.13847](https://arxiv.org/abs/2603.13847)
9. UltrasonicWhisper+ — [ACM 10.1145/3789679](https://doi.org/10.1145/3789679)
10. MetaAttack — [arXiv 2501.15031](https://arxiv.org/html/2501.15031v2)
11. ASVspoof — [2019 overview](https://arxiv.org/abs/1911.01601); [ASVspoof 5](https://arxiv.org/abs/2408.08739)

The "what survives into stored audio" column is a DSP synthesis of each class's
physics, not a vendor claim. A class without a dataset URL means "no public capture
located as of 2026-10-08", not "none exists".
