# The trust layer: should a voice agent act on this command?

`echoguard.trust` generalises the confirmation gate from one verdict to a
**trust tuple**. Four layers of checks each answer one question; the gate
summarises them into a trust level and, with the action's sensitivity, returns
`allow`, `confirm` or `block`.

| Layer | Question | Check | Needs |
| --- | --- | --- | --- |
| L1 signal integrity | Is the audio physically genuine — no ultrasonic carrier, no out-of-band energy? | `SignalIntegrityCheck` (the EchoGuard pipeline) | audio at ≥ 44.1 kHz; below that it reports *could not check* |
| L2 speaker identity | Is this the enrolled user? Is it a live human voice rather than synthesis, conversion or playback? | `SpeakerVerificationCheck` (ECAPA-TDNN embeddings, cosine to the enrolled profile); `AntiSpoofCheck` (Spectra-AASIST bonafide score) | `pip install "echoguard[trust]"`, a `SpeakerProfile` for verification, ≥ 0.8 s of speech; ~1.5 GB of weights on first run for anti-spoofing |
| L3 source attribution | Did the wake word and the command come from the same voice? | `SameSpeakerCheck` (embedding similarity between the two segments) | the wake-word segment; no enrolment needed |
| L4 content safety | Is the transcript a spoken prompt injection? Do two decoders agree? | `ContentSafetyCheck` (pattern families: override, role change, forcing, exfiltration, authority claim, tool abuse, concealment, jailbreak), `TranscriptConsistencyCheck` (word error rate between decoders) | the transcript; optionally a second transcript |

Every check returns a `TrustSignal` with `trust` in [0, 1] (1 = fully consistent
with the genuine user), an `assessable` flag, a sentence of detail and the
numbers behind it. The bands are shared: below 0.34 the check is *hostile*,
below 0.67 *suspect*, otherwise *clean*; not assessable is *unassessed*.

## From tuple to decision

```
level = HOSTILE     if any assessable check < 0.34
      = SUSPECT     if any assessable check < 0.67
      = UNVERIFIED  if a required layer could not assess   (default required: L1, L2, L4)
      = TRUSTED     otherwise
decision = policy[sensitivity][level]
```

| sensitivity \ level | trusted | unverified | suspect | hostile |
| --- | --- | --- | --- | --- |
| routine | allow | allow | confirm | block |
| sensitive | allow | confirm | confirm | block |
| critical | confirm | confirm | block | block |

*Unverified* is "could not check" — a 16 kHz capture, no enrolment, the model
not installed. It is never treated as safe for anything that matters, and a
`confirm` must go through a channel the attacker cannot reach: a screen tap, a
second device, a spoken passphrase. The reason names the weakest check.

## Use

```python
from echoguard.gate import ActionSensitivity
from echoguard.trust import TrustGate, TrustContext, SpeakerProfile

profile = SpeakerProfile.enrol("sanket", [(utt1, sr), (utt2, sr), (utt3, sr)])
profile.save("sanket.json")

gate = TrustGate()
ctx = TrustContext(audio=command, sample_rate=sr, transcript=asr_text,
                   wake_audio=wake_segment, speaker_profile=profile)
result = gate.evaluate(ctx, ActionSensitivity.CRITICAL, command="unlock the door")
result.decision        # GateDecision.BLOCK / CONFIRM / ALLOW
result.level           # TrustLevel
result.reason          # "Do not execute: a critical action with a hostile command — content safety: …"
result.to_dict()       # decision, level, every signal with its band and evidence, audit record
```

As a tool an LLM agent calls before acting (OpenAI / Anthropic function shape):

```python
from echoguard.trust import TOOL_SCHEMA, handle_tool_call

tools = [TOOL_SCHEMA]                       # name: check_voice_command
result = handle_tool_call(call.arguments)   # {"decision": "confirm", "reason": …, "next_step": …}
```

Drop a layer from `required_layers` if you cannot provide it (no enrolment →
`required_layers=(Layer.SIGNAL, Layer.CONTENT)`), and the tuple is TRUSTED on
clean L1 + L4 alone. Add your own check by subclassing `Check` and returning
`self.signal(...)` or `self.unassessable(...)`.

## The corpus runner

`benchmark/trust/run_scenarios.py` takes a matched-device corpus in the manifest
format of `docs/corpus_manifest.md`, enrols each user, runs the full gate on every
command with its wake word and the owner's profile, and reports the two numbers an
integrator needs — unauthorised actions executed, needless confirmations or
blocks — per threat class T1–T6, with the check that caught each attack.
`--reverb` adds the experimental `ReverbConsistencyCheck` (Λ = T60(wake) −
T60(command); paper §VIII), which is not in `default_checks()` until the corpus
calibrates its estimator. `benchmark/trust/corpus/make_smoke.py` builds a
public-proxy corpus in the same format so the whole path runs today.

## Status and calibration

- L1 is the benchmarked EchoGuard detector (see `BENCHMARK_REPORT.md`).
- L2 and L3 use `speechbrain/spkrec-ecapa-voxceleb`. Thresholds come from
  LibriSpeech dev-clean trials (`benchmark/trust/RESULTS.md`): verification
  EER 0.8 % with 3 enrolment utterances (`reject_below=0.15`,
  `accept_from=0.50`); same-speaker with a 1 s wake segment EER 4.3 %
  (`different_below=0.10`, `same_from=0.36`). That is clean read speech, so
  they remain constructor arguments for per-device re-calibration on the
  Phase 0 corpus.
- L4's rule set scores 0 false alarms on 5,005 real voice-assistant commands
  (SLURP) and on 63 verb-matched benign action commands; on the spoken-injection
  seed corpus (`benchmark/trust/corpus`, 72 phrasings × slot fills ≈ 390
  utterances across seven mechanism families) it detects about 86 % at text
  level. That corpus was written by the rule authors, so the detection figure is
  an upper bound until other people's phrasings are added in Phase 0; the
  learned model (Phase 2) is measured against it.
- `AntiSpoofCheck` uses `lab260/Spectra-AASIST` (MIT; wav2vec2-XLS-R + AASIST).
  It separates synthetic and converted speech from live voices; replay through a
  loudspeaker is only partly covered (ASVspoof physical access is a different
  task) and is also attacked from the source side (L3). On a stratified
  ASVspoof2019 LA evaluation sample (500 bonafide, 1,040 spoofs over the 13
  unseen attacks) the model separates the classes with no overlap — EER 0 %,
  bonafide logits above 3.6, spoofs below −2.3 — and the trust mapping
  (`spoof_below=-2.0`, `bonafide_from=2.5`) sits in that gap. That is the
  model's own benchmark domain; the margin will shrink on real device
  captures and must be re-measured on the Phase 0 corpus.
- L4 is a transparent rule baseline with eight families: override, role
  change, forcing, exfiltration, authority claim, tool abuse (recited
  destinations and secrets), concealment, and jailbreak. The jailbreak family
  covers always-comply personas, refusal suppression, forced response prefixes,
  ethics waivers and fictional framing (Wei et al. 2023). It raised SWhisper's 50
  spoken jailbreak payloads from 2 to 50 detected, with no new false alarms on
  SLURP (`benchmark/published_attacks/RESULTS.md`). Every match is returned in the
  evidence so a reviewer sees exactly why a transcript scored.
- Multi-microphone direction-of-arrival (L3) waits on raw-channel captures.

The tool call is stateless. Audio is scored in memory; the audit record is
returned to the caller and stored nowhere by the library.
