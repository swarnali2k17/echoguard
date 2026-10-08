# EchoGuard recording protocol

**Purpose.** Collect a labelled corpus of real device captures so a detector can be
trained and evaluated on what phones, speakers and wearables actually store — not
on synthetic signals. The §7 study in `BENCHMARK_REPORT.md` shows why this is the
blocking prerequisite: every cue that separates attacks from benign audio on
synthetic data either fails to transfer to real recordings or turns out to be a
device artefact, and the one public real-attack set (DolphinAttack) has **no
matched benign recordings from the same phones**. Matched benign + probe audio
from the same device, room and gain chain is the single most valuable thing this
protocol produces.

**Scope and safety.** This protocol collects audio for a *defensive* dataset. The
"probe" recordings below use a near-ultrasound carrier modulated by a **generic
speech-like envelope that carries no recoverable command** — exactly the
detector-validation signal in `benchmark/synth_attacks.py` (see its SAFETY note).
The point is to reproduce the *capture-chain fingerprint* a real injection would
leave, so the detector can learn it, without producing anything that can drive a
device. This document does **not** describe how to compromise an assistant, and
collecting recordings that would actually trigger a device is out of scope here;
evaluating detection against live command attacks is red-team work that belongs
with the device owner, under written authorisation, using their own controlled
tooling. Record only on equipment and premises you own or are authorised to use,
and only with the consent of anyone whose voice is captured.

---

## 1. What to record

Four tracks per device. The first two are the matched pair the study needs; the
third gives real voice content; the fourth is the hard negative set that caused
the beacon false alarms.

| Track | Label | Content | Why |
| --- | --- | --- | --- |
| **B. Benign ambient** | `benign` | Room tone, the device idle, typical household/office sound, no deliberate HF source | The missing matched-benign baseline; characterises each device's own noise floor and any ADC tones |
| **P. Probe** | `probe` | The near-ultrasound test probe (below) emitted toward the device from each distance | Reproduces the capture-chain fingerprint of an injection without a command payload |
| **S. Benign speech** | `benign` | A person reading neutral text, or played-back speech corpus audio, at normal volume | Real glottal/formant structure for the speech-plausibility features |
| **N. Negative HF** | `benign` | Ordinary narrowband ultrasound in the wild: a retail/presence beacon if present, a switching power supply, a CRT/LED driver whine, bright percussive music | The look-alikes the detector must *not* flag |

The probe track is what makes this a *detection* dataset rather than a benign-only
one. If you cannot emit the probe safely (no ultrasonic transducer, shared space,
etc.), still collect B, S and N: matched benign audio alone unblocks the
device-artefact question in §7c of the benchmark report.

### The probe signal (no command payload)

Generate it with the repository's own generator, which emits a carrier modulated
by a speech-*shaped* noise envelope — the time/bandwidth of a command, none of its
content:

```bash
# Writes probe_48k.wav etc.; --capture 0 keeps the raw carrier for emission.
python benchmark/synth_attacks.py --out probe --n 1 --capture 0
```

Play `probe/attack_00_*.wav` through an ultrasonic-capable emitter in a controlled
space. The emitted sound is inaudible but **is still acoustic energy above the
hearing range**: keep levels modest, keep people and pets out of the direct path,
and do not run it continuously. Its only role is to let the victim device's own
microphone non-linearity fold the carrier down, so the recording carries the same
baseband residue a real injection would — which is what the detector must learn.

---

## 2. Devices, distances, repetitions

Mirror the DolphinAttack layout so results compare directly, and add at least one
phone of each model family already in that set (so the 5/6/7 kHz ADC-line question
from §7c can be settled with matched benign audio).

- **Devices:** ≥ 3, ideally spanning capture behaviours — one that keeps 16–22 kHz
  energy (many budget Android), one that low-passes hard (recent iPhone), one
  smart speaker or wearable. Record each device's native capture rate **and** a
  high-rate reference capture (§3).
- **Distances:** 10, 30, 60, 100, 150, 200, 300 cm (probe track). Benign/speech
  tracks: one near (≈ 30 cm) and one room-distance (≈ 200 cm).
- **Repetitions:** ≥ 10 probe takes per (device × distance); ≥ 5 min continuous
  per benign/speech/negative track per device.
- **Rooms:** ≥ 2 acoustically different spaces per device (e.g. a soft furnished
  room and a bare/reflective one) so reverberation is not confounded with class.

Target size: 3 devices × 7 distances × 10 takes ≈ 210 probe clips, plus ~30 min
each of benign, speech and negative per device. This is enough to retrain the §7
models with a *matched* benign class and to re-check the device-artefact finding.

---

## 3. Capture settings

- **Native-rate track:** record at the device's own microphone rate (whatever the
  OS delivers — often 48 kHz on phones, 16 kHz on voice pipelines). This is the
  operating condition a deployed detector sees.
- **High-rate reference:** where the hardware allows (external USB mic, audio
  interface, or a device that exposes ≥ 96 kHz), capture the *same* emission at
  ≥ 96 kHz. This lets you confirm the probe really did carry an ultrasonic
  carrier and measure how much of it the native path removed.
- **Format:** WAV, PCM 16- or 24-bit, mono. No AGC, no noise suppression, no voice
  "enhancement" — disable every DSP the OS will let you (these destroy exactly the
  residue we study). Note any that cannot be disabled in the metadata.
- **Levels:** keep peaks below −6 dBFS; discard clipped takes. Do **not**
  normalise at capture time; the detector is gain-invariant and the true level is
  itself a feature.

The `tools/capture/record.py` helper (below) captures at a chosen rate, writes the
WAV, and writes the metadata sidecar in the schema §4 expects.

---

## 4. Metadata and file naming

Every clip gets a row in `manifest.csv` and (from the capture tool) a sidecar
`<clip>.json`. The manifest schema is the one the existing harnesses read
(`benchmark/real_audio/evaluate_real.py`, `benchmark/dolphinattack/run.py`):

```
file,label,category,device,capture_rate,distance_cm,room,track,operator,consent,notes
```

- `label` ∈ {`benign`, `probe`} — the detection ground truth.
- `category` — free text for slicing (`benign_speech`, `negative_psu`, `probe`, …).
- `track` ∈ {B, P, S, N}; `room` a short id; `consent` the consent reference for any
  voice recorded.

File name encodes the key facts so a clip is self-describing even detached from the
manifest, echoing the DolphinAttack convention:

```
<device>__<track>__<distance_cm>cm__<room>__<nnn>.wav
pixel8__P__060cm__roomA__003.wav
pixel8__B__030cm__roomA__001.wav
```

Keep a `SOURCES.md` in the corpus recording device make/model/OS version, mic,
emitter, interface, and which DSP could not be disabled — the §7 study shows these
determine the result as much as the audio does.

---

## 5. Procedure (per device)

1. **Consent & authorisation.** Record written consent for any voice captured;
   confirm you are authorised for the premises and the emitter. Log both.
2. **Characterise the device first.** Capture track B (benign ambient) at both
   distances and both rooms *before* any probe. Run `echoguard scan` on these:
   they must come back CLEAR or INSUFFICIENT_DATA. Any flag here is a device
   artefact to note, not an attack.
3. **Speech track S.** Record/playback neutral speech at both distances.
4. **Negative track N.** Capture whatever ordinary ultrasound the environment has
   (beacons, PSU/LED whine) and bright percussive music. These must stay CLEAR.
5. **Probe track P.** For each distance, position the emitter, play the probe,
   capture ≥ 10 takes on the native path and, if available, simultaneously on the
   high-rate reference. Keep emission brief.
6. **Label and log** every clip into `manifest.csv` as you go; do not rely on
   memory. Spot-check with the capture tool's live verdict.
7. **Verify the pair.** For at least one distance, confirm the high-rate reference
   shows the carrier (e.g. `echoguard scan ref.wav` → HIGH_RISK) while the native
   capture does or does not — that difference is the headline measurement.

---

## 6. Acceptance / sanity checks before the corpus is "done"

Run these and record the result in the corpus README:

- Every **benign** and **negative** clip → CLEAR or INSUFFICIENT_DATA under
  `echoguard scan` (full clip and `--window 1`). Any flag is a documented device
  artefact, not a detector failure.
- Every **high-rate probe** reference → SUSPICIOUS/HIGH_RISK (confirms the probe
  carried a real carrier; if not, the emission or capture failed).
- Native-rate probe clips: tabulate the verdict distribution. This is the real
  number — how much of the attack the deployed detector actually sees — and it is
  the input to phase-2 of the roadmap.
- Build the manifest and run `benchmark/real_audio/evaluate_real.py <corpus>` to
  get the per-category table in the repo's standard format.

---

## 7. What this unblocks

With a matched benign + probe set from the same devices:

- The §7c question — are the 5/6/7 kHz lines attack traces or device ADC artefacts?
  — is answerable directly (they will appear in the matched *benign* audio too, or
  they will not).
- The §7d anomaly models can be retrained with a benign class drawn from the *same*
  channel as the probes, removing the device confound that currently makes
  attack-vs-benign separation meaningless.
- The phase-2 detection gate (`test_adc_capture_attack_is_detected`) can be
  re-measured on real captures instead of the simulator.

See `docs/threat_model.md` for which attack classes this capture setup can and
cannot exercise, and what capture rate each class requires.
