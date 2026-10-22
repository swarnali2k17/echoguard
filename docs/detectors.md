# Signal-layer detectors (L1): what each one measures and how the verdict is formed

This is the reference for `echoguard.pipeline` and `echoguard/detectors/`. Every
threshold named here is a constant in the module cited, so the numbers can be
audited and tuned in one place. For the trust layer (L2–L4) see
[`trust_layer.md`](trust_layer.md); for what each attack class leaves in a
recording see [`threat_model.md`](threat_model.md).

## Pipeline at a glance

```
WAV ──► mono float ──► Welch PSD (Hann, 50 % overlap)
                          │
      ┌───────────────────┼─────────────────────┬──────────────────────┐
      ▼                   ▼                     ▼                      ▼
out_of_band_energy   carrier_peak         spectral_profile    near_ultrasonic_carrier
 (> 18 kHz share)    (narrow line +        (context only)      (15–18 kHz, uses the
                      sidebands)                                carrier finding + time)
      └────────┬──────────┘                                            │
               ▼                                                       │
  R_main = √(out_of_band × carrier)                                    │
               └─────────────── R = max(R_main, R_near) ◄──────────────┘
                                        │
             CLEAR < 0.33 ≤ SUSPICIOUS < 0.66 ≤ HIGH_RISK
             INSUFFICIENT_DATA when a detector could not assess and nothing is flagged
```

Every finding is always present in the output, with the same `evidence` keys on
every branch, so JSON consumers never meet a missing field.

## `out_of_band_energy` — `detectors/ultrasonic.py`

**Question:** how much of the clip's energy lies above 18 kHz, where speech has
almost none?

- `risk = clip01(share / SATURATION_RATIO)`, with `SATURATION_RATIO = 0.10`.
- The share counts only when the band reaches `MIN_BAND_LEVEL_DBFS = −60` dBFS.
  Below that, a near-silent tail of dither can be 100 % of a tiny total.

**When it refuses to clear (assessable = false).** A capture that never recorded
the band cannot be cleared:

| Condition | Typical cause |
| --- | --- |
| Nyquist ≤ 18 kHz | 16 kHz or 32 kHz capture |
| Band level < `ABSENT_BAND_DBFS = −110` dBFS | AAC/MP3 encode removed it; real microphones sit near −90 to −100 dBFS |
| A brick-wall low-pass below 18 kHz (`lowpass_cutoff_hz`) | 16 kHz audio inside a 48 kHz container; codec cut-off |

`lowpass_cutoff_hz` looks for the steepest fall of at least `CUTOFF_DROP_DB = 35`
dB across ±0.25–0.75 kHz of a candidate edge. It also needs everything above the
edge to stay at least 25 dB under the content. Natural roll-off of speech and
music is tens of dB per octave, not per kilohertz.

Evidence: `nyquist_hz`, `assessable`, `out_of_band_ratio`,
`out_of_band_level_dbfs`, `content_cutoff_hz`.

## `carrier_peak` — `detectors/modulation.py`

**Question:** is there a single narrow line high in the band, and does it carry
a command?

1. **Prominence (CFAR).** The strongest bin above the search floor is compared
   with the local median. The threshold scales with the Welch degrees of
   freedom (`noise_prominence_threshold_db`), so short noisy clips do not throw
   up chance peaks. The prominence beyond that threshold is `excess_db`.
2. **Narrowness.** The width of the line is checked: a carrier is a line, not a
   hump of noise.
3. **Sidebands** (`sideband_measures`). A command modulates the carrier and
   spreads energy beside it. A beacon, pilot tone or power-supply whine does
   not.
   - The plateau is measured on the **lower** side, 150 Hz–4 kHz below the
     carrier. It is compared against a background 4.5–7.5 kHz below. The
     upper side often lies above a codec cut-off, where silence would fake a
     plateau.
   - Both are **medians**, so the leakage skirt of a loud bare tone cannot move
     them.
   - The plateau must clear what noise alone reaches with probability
     `SIDEBAND_FALSE_ALARM_PROB = 1e-3`. The variance accounts for Hann-bin
     correlation (`HANN_SUM_VARIANCE = 1.56`).
   - A tone-modulated carrier shows mirrored *lines* rather than a plateau. A
     search over offsets ≥ 300 Hz finds them, with its own budget
     `SIDEBAND_LINE_FALSE_ALARM_PROB = 1e-4`.
   - Below these gates the peak is a **bare tone**: `sideband_db = null`,
     `modulated = false`, and its risk is scaled down by
     `BARE_TONE_RISK_SCALE = 0.05`.

`prominence_risk = clip01(excess_db / 30) × narrow_factor`;
`risk = prominence_risk × mod_factor`.

Evidence: `assessable`, `peak_freq_hz`, `prominence_db`, `noise_threshold_db`,
`excess_db`, `narrowness`, `sideband_db`, `sideband_plateau_db`, `modulated`,
`prominence_risk`, `welch_dof`.

## `near_ultrasonic_carrier` — `pipeline.py`

**Question:** is there a speech-modulated carrier at 15–18 kHz? Such a carrier
puts nothing above 18 kHz, so the main score cannot see it
(√(0 × carrier) = 0). SWhisper's hardware carrier is at ~16 kHz.

This band also holds beacons, TV pilot tones, power-supply whine and codec
artefacts, so the evidence required is stricter than above 18 kHz. **All** of
the following must hold:

| Test | Rule | What it rejects |
| --- | --- | --- |
| Carrier | `carrier_peak` found a narrow, prominent line at 15–18 kHz (`prominence_risk > 0`) | broadband noise |
| Sideband plateau | `sideband_plateau_db ≥ PLATEAU_FLOOR_DB = 2` dB (full confidence at 8 dB) | unmodulated beacons (≤ 0.34 dB measured) |
| Fluctuation in time | Variance of log sideband energy over ~20 ms frames, against steady-noise variance `1.56 / n`; chi-square test at `FLUCTUATION_FALSE_ALARM_PROB = 1e-3`; needs ≥ 16 frames | noise shaping, dither and steady raised floors, which lift a plateau but do not flicker syllable by syllable |
| Persistence | Flag held for `NEAR_PERSIST_WINDOWS = 3` consecutive windows (~2 s at 1 s / 0.5 s) | codec frame-gain flicker around a loud tone; benign runs never exceeded 2 windows |

When all hold, the score is `√(share_risk × mod_conf × prominence_risk)`:

- `share_risk` is the energy share in [max(12 kHz, fc − 4 kHz), fc + 4 kHz],
  scaled like the out-of-band share. It must reach −60 dBFS.
- `mod_conf` ramps the plateau from 2 to 8 dB.

**Persistence in each entry point:**

- `analyze_windows`: runs shorter than 3 windows are set aside, and so is a clip
  shorter than one window.
- `analyze`: one spectrum cannot show persistence. A 15–18 kHz flag is confirmed
  by scanning the clip internally in 1 s / 0.5 s windows; if it does not
  persist, it is set aside.
- `StreamAnalyzer`: a stream cannot look ahead. The flag appears from the third
  consecutive window of a run.

A flag set aside keeps its evidence. Its detail then says "Not persistent (under
~2 s) – set aside".

Evidence: `assessable`, `carrier_hz`, `band_hz`, `band_share`,
`band_level_dbfs`, `sideband_plateau_db`, `fluctuation_f`,
`fluctuation_f_crit`.

## `spectral_profile` — `detectors/spectral.py`

Reports the 99 % energy roll-off. It is **context only**. On its own it fires on
music and noise, so it never drives the verdict.

## Windowing

Whole-clip analysis averages one spectrum over the whole file, so a short
injection inside a long recording is diluted away. For anything longer than a
few seconds use `analyze_windows` (or `echoguard scan --window 1`). The summary
verdict is the worst window's.

## Validation

- [`../BENCHMARK_REPORT.md`](../BENCHMARK_REPORT.md): synthetic, real benign and
  DolphinAttack evaluations.
- [`../benchmark/published_attacks/RESULTS.md`](../benchmark/published_attacks/RESULTS.md):
  SWhisper and MetaAttack recordings, before and after.
- `benchmark/beacon_fp.py`: unmodulated 19–22 kHz beacons; 0 / 800 flagged.
- `tests/test_published_attacks.py`: synthetic signals for each rule above.
