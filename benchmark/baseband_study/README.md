# Baseband detection study (BENCHMARK_REPORT.md §7)

Can an inaudible-command injection be detected from what a phone actually stores — the demodulated residue in 0–8 kHz — after the capture chain has removed the carrier? Two studies, both run 2026-10-08.

## `synthetic/` — candidate features on ADC-captured synthetic clips

- `features.py`: 22 features in six groups: ghost (sub-20/100 Hz energy, 2–20 Hz vs speech band), aliased carrier line (prominence, frame stability, sideband ratio), envelope modulation spectrum, bicoherence, harmonicity (HNR, CPP, voiced fraction), spectral flatness/entropy and 2nd-harmonic distortion.
- `gen_dataset.py`: 600 attack + 600 condition-matched benign clips, synthesised at 192 kHz and captured at 48 and 16 kHz through the ADC model; carriers 18–40 kHz, AM/DSB-SC/SSB, mic a2 0.03–0.2, SNR 10–35 dB; real VCTK speech as room audio and, in one variant, as the modulator. Needs the 40 VCTK clips from `benchmark/real_audio/`.
- `run_eval.py`: per-feature AUC, logistic-regression and gradient-boosting cross-validation, cross-carrier and cross-a2 transfer.
- Results: `per_feature_auc.csv`, `per_feature_auc_tone.csv` (real-speech modulator), `feature_importance.csv`, `fig_feature_auc.png`, `fig_psd_0_300.png`, `fig_modspec.png`.
- `ghost_real.csv`, `line_real16k.csv`: the ghost and aliased-line features measured on the 307 real benign clips and the 2,934 real DolphinAttack captures. These are the numbers that show the synthetic separation does not transfer.

## `oneclass/` — benign-only anomaly model on real audio

NormDetect-style: Mahalanobis (Ledoit–Wolf), isolation forest and PCA-reconstruction models on 83 features (40 log-mel bands mean+std, ghost ratios, flatness; 1 s segments at 16 kHz), trained on real benign audio only, scored on the DolphinAttack captures per device.

- `features.py`, `model.py`, `ablation.py`; results in `results.csv`, `scores_mahalanobis_speech.png`, `scores_mahalanobis_all.png`.
- Needs `benchmark/real_audio/` corpus and the DolphinAttack set (`benchmark/dolphinattack/README.md`). Edit the paths at the top of each script.

## Finding

On the two DolphinAttack phones without an ADC tone artefact (Pixel, Reno), nothing separates real attack captures from real benign audio at a usable operating point: AUC 0.50–0.52 and 0% detection at 1% false alarms with a broad benign model. The data needed to go further is matched benign and attack recordings from the same devices.
