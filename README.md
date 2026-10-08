# EchoGuard

**A baseline open-source detector for inaudible / injected voice-command attacks.**

Voice assistants and, increasingly, action-taking AI voice agents and wearables have been attacked for nearly a decade — DolphinAttack (2017), SurfingAttack (2020), NUIT (2023), audio prompt injection against AI agents (2026). The attack literature is deep. The **defense** side is thin, older, and — critically — lives almost entirely in research papers. There is no simple, deployable tool a developer or device maker can run to screen a captured audio clip for the fingerprints these attacks leave behind.

EchoGuard is a first step toward closing that gap. It is **defensive only**: it inspects audio and reports risk. It does not generate attacks and ships no attack payloads.

> Status: `v0.1.0` — baseline detector. This is a screening tool, not a guarantee. See [Limitations](#limitations).

## What it does

Given a WAV clip, EchoGuard runs three detectors and returns a risk verdict with reasons:

| Detector | Looks for | Catches (class) |
| --- | --- | --- |
| `out_of_band_energy` | Significant energy above 18 kHz | Ultrasonic / near-ultrasound injection (DolphinAttack, NUIT) |
| `carrier_peak` | A dominant narrowband tone high in the band | Modulated ultrasonic carriers |
| `spectral_profile` | Energy roll-off inconsistent with human speech | Context only — reported, never drives the verdict |

Each detector returns a risk in `[0, 1]` with the numbers behind its decision. The overall verdict requires **corroboration**: it is the geometric mean of `out_of_band_energy` and `carrier_peak`, so both an out-of-band energy signature *and* a narrowband carrier must be present to raise the score. `spectral_profile` is reported as context but deliberately does not drive the verdict, because on its own it fires on ordinary music and noise. See [Benchmark](#benchmark) for why.

## Install

```bash
pip install -e .
```

Requires Python 3.9+, numpy, scipy.

## Usage

```bash
# Scan a clip
echoguard scan recording.wav

# Machine-readable output
echoguard scan recording.wav --json
```

```bash
# Long recording: score 1 s windows every 0.5 s; the verdict is the worst window's
echoguard scan recording.wav --window 1 --json
```

Exit codes: `0` = CLEAR, `1` = SUSPICIOUS, `2` = HIGH_RISK, `3` = read error, `4` = INSUFFICIENT_DATA (capture too narrow to assess), `5` = audio invalid (NaN, empty, bad rate), `6` = usage error — so it drops into CI or a capture pipeline. JSON output is strict (no `NaN`) and every finding carries the same `evidence` keys regardless of the branch taken.

### As a library

```python
from echoguard import Pipeline, StreamAnalyzer

report = Pipeline().analyze(samples, sample_rate)          # one clip
windowed = Pipeline().analyze_windows(samples, sample_rate) # per-window + worst-window summary

stream = StreamAnalyzer(sample_rate, window_sec=1.0, hop_sec=0.5)
for chunk in audio_chunks:
    for report in stream.push(chunk):
        ...
```

Whole-clip analysis averages one spectrum over the entire file, so a 0.3 s injection inside a minute of speech is diluted away. Use windows for anything longer than a few seconds.

### Confirmation gate (for action-taking voice agents)

An agent that *acts* on spoken commands shouldn't execute silently on audio that looks injected. The gate turns a verdict plus an action's sensitivity into **allow / confirm / block**:

```python
from echoguard import ConfirmationGate, ActionSensitivity, GateDecision

result = ConfirmationGate().evaluate(samples, sample_rate, ActionSensitivity.CRITICAL,
                                     command="unlock the front door")
if result.decision is GateDecision.BLOCK:   agent.refuse(result.reason)
elif result.decision is GateDecision.CONFIRM: agent.step_up_confirm(result.reason)
else:                                        agent.execute()
```

```bash
echoguard gate command.wav --action critical   # exit 0 allow · 7 confirm · 8 block
```

Critical actions are never executed silently (confirmed even on CLEAR); a 16 kHz capture is INSUFFICIENT_DATA → treated as unverified, not safe. It is defence-in-depth, not a guarantee — see `docs/confirmation_gate.md` and `examples/voice_agent_gate.py`.

### Example

```
$ echoguard scan out_of_band.wav
EchoGuard scan: out_of_band.wav
========================================
Sample rate : 48000 Hz (Nyquist 24.0 kHz)
Duration    : 1.00 s
Verdict     : [ !! ] HIGH_RISK  (risk 1.00)

Detectors:
  - out_of_band_energy   high       risk 1.00
      97.2% of signal energy sits above 18 kHz - well beyond the human-voice band.
      Consistent with ultrasonic/near-ultrasound injection.
  - carrier_peak         high       risk 1.00
      Dominant narrowband tone at 20.0 kHz stands 119 dB above the local noise floor.
  - spectral_profile     high       risk 1.00
      99% of energy extends up to 20.0 kHz - inconsistent with a live speaker.
```

### Try it on sample fixtures

```bash
python examples/make_fixtures.py
echoguard scan fixtures/benign.wav            # -> CLEAR
echoguard scan fixtures/out_of_band.wav       # -> HIGH_RISK
echoguard scan fixtures/modulated_carrier.wav # -> HIGH_RISK
```

The fixtures are **synthetic test signals** with particular spectral shapes (band-limited noise, a high-frequency tone, an AM carrier). They contain no speech, no command, and nothing that can drive a device — they exist only to validate the detectors.

## Capture matters

Detecting ultrasonic and near-ultrasound energy requires a recording whose Nyquist frequency reaches into that band. A standard 16 kHz voice capture **cannot** see 18 kHz, and EchoGuard says so rather than returning a false "clear". For meaningful results, capture at **44.1 kHz or higher**.

## How it works

1. Load the WAV as a mono float signal at its true sample rate.
2. Estimate the power spectral density (Welch).
3. Each detector measures one property (out-of-band energy ratio, high-band peak prominence, 99% energy roll-off) and maps it to a risk score with a documented threshold.
4. Aggregate to a verdict.

Every threshold is a named constant in the detector module, so they are easy to audit and tune.

## Benchmark

EchoGuard ships with a benchmark harness (`benchmark/`) that runs the detectors over a labelled corpus and reports detection and false-positive rates.

```bash
python benchmark/corpus.py benchmark/corpus_benign   # generate a labelled benign corpus
python benchmark/evaluate.py benchmark/corpus_benign # report per-category verdicts + FP rate
```

Our first result exposed a real weakness and the fix for it. The original "flag if any detector fires" logic treated any high-frequency audio as suspicious, giving a **75% false-positive rate** on benign audio (clean only on speech). Scoring an injection as the *corroboration* of two detectors — out-of-band energy **and** a narrowband carrier, both required — dropped that to **0%** while still flagging the attack fixtures:

| Benign audio | FP before | FP after |
| --- | --- | --- |
| Speech | 0% | 0% |
| Bright music | 100% | 0% |
| Pink noise | 100% | 0% |
| White noise | 100% | 0% |
| **Overall (60 clips)** | **75%** | **0%** |

Measured against synthetic benign audio and synthetic attack fixtures. On **real audio** — 307 openly licensed 44.1/48 kHz recordings (VCTK speech, FSD50K, ESC-50, MUSDB18-HQ), scored as full clips and as 0.5 s and 0.2 s windows, 3,230 segments — the false-positive rate is **0%** after the absolute level floor and carrier-narrowness gate in v0.2.0 (it was 2.0% on full clips before). Measuring detection rate on real attacks is the open research step; see Limitations.

## Limitations

This is a **baseline screen**, and it is honest about what it is not:

- The 0% benign false-positive rate above is on **synthetic** audio. The corroboration rule requires both an out-of-band and a carrier signature, so a real attack with an attenuated carrier could be missed — real-capture calibration is pending.
- **It detects the carrier, not the attack.** Phones and smart speakers capture at 16–48 kHz behind an anti-aliasing filter, which removes an ultrasonic carrier before the audio is stored. On simulated attacks passed through such a chain (`benchmark/synth_attacks.py --evaluate`, now the default) the current detectors catch **4 of 12** at 48 kHz (only carriers in the filter's transition band), 1 of 12 at 44.1 kHz, and return INSUFFICIENT_DATA at 16 kHz; on the 2,934 real recordings of the public DolphinAttack set (all 16 kHz) every clip is INSUFFICIENT_DATA. A baseband detector working on what survives (the demodulated residue in 0–8 kHz) is the next milestone; see `BENCHMARK_REPORT.md` §6.
- Carriers between 15 and 17.9 kHz are mostly not flagged (below the 18 kHz out-of-band edge; only the sidebands of a near-18 kHz carrier reach the band, giving 17–42% detection). Steady benign ultrasound — 19 kHz retail beacons, 20–22 kHz power-supply whine — is recognised as an unmodulated tone and reported without raising the verdict.
- The baseband detector that a phone capture needs could not be built from available data: on the DolphinAttack phones without ADC artefacts, nothing separates real attack captures from real benign audio at a usable operating point, and the public set has no matched benign recordings. See `BENCHMARK_REPORT.md` §7.
- Replay and voice-clone spoofing are a separate problem. An experimental baseline lives in `echoguard.spoof` (`pip install echoguard[spoof]`; 16.9% EER on ASVspoof 2019 LA, see `ASVSPOOF_RESULT.md`); it is not wired into `scan`. Application-layer abuse (skill squatting) is out of scope.
- It works on recorded clips. Real-time, on-device deployment is future work.
- Thresholds are set against synthetic fixtures and need calibration on real-world captures across devices.

## Roadmap

- [ ] **Matched-device capture set** (the blocking prerequisite — see `docs/recording_protocol.md`) — benign + probe from the same phones, rooms and gain chain, so the baseband work has a benign class that is not confounded with the device
- [ ] Baseband detector for the demodulated residue (works on 16/48 kHz device captures) — blocked on the above (`BENCHMARK_REPORT.md` §7)
- [x] Replay & voice-clone (anti-spoofing) baseline module (`echoguard.spoof`, experimental)
- [x] Windowed / streaming mode (`analyze_windows`, `StreamAnalyzer`, `--window`)
- [x] Capture tool for corpus collection (`tools/capture/`)
- [x] Threat-model coverage matrix across the 11 attack classes (`docs/threat_model.md`)
- [ ] Evaluate on [AdvSV](https://advsv.github.io/) (matched over-the-air adversarial-vs-ASV) and ASVspoof 5 adversarial subset — the only public matched-device attack corpora
- [x] Reference integration for an action-taking voice agent's confirmation step (`echoguard.gate`, `docs/confirmation_gate.md`)

## Scope across attack classes

EchoGuard targets one family — inaudible/ultrasonic **injection** — and is honest
about the rest. `docs/threat_model.md` scores all eleven known classes on what
survives a commodity capture and whether a software detector can see it: the
physical-injection classes (DolphinAttack, SurfingAttack/SUAD, hearable, metamaterial)
share one in-band residue that is the real detection target; laser (LightCommands)
and EM/power-line (GhostTalk) leave **no** acoustic residue and are out of software
scope; the in-band adversarial classes (hidden/adversarial audio, LLM-agent prompt
injection) and replay/clone are separate detection problems. For most injection
classes **no public real-attack corpus exists**, which is itself a finding.

## Research context

EchoGuard is built on, and credits, a decade of prior work. See `docs/threat_model.md`
for the full cited coverage matrix and `docs/landscape.md` for the attack/defense
landscape. Key references: DolphinAttack (CCS 2017), SurfingAttack (NDSS 2020), NUIT
(USENIX 2023), NormDetect (USENIX 2023), MicGuard (USENIX 2024), EarArray (NDSS 2021),
and the ACM Computing Surveys *Voice Assistant Security* survey (2022).

## Contributing

Issues and PRs welcome — especially real-world captures and new detector modules. Run the tests with:

```bash
pip install -e ".[dev]"
pytest -q
```

## License

MIT — see [LICENSE](LICENSE).

---

*EchoGuard is a defensive research tool. Use it only on audio you are authorised to analyse.*
