# DolphinAttack public recordings

The only public corpus of **real** inaudible-command attack captures we have found: 2,934 WAV files recorded by the victim phone's own microphone (so already demodulated by the device), released by USSLab / Zhejiang University with [Zhang et al., "DolphinAttack: Inaudible Voice Commands", ACM CCS 2017](https://github.com/USSLab/DolphinAttack).

| Item | Value |
| --- | --- |
| Files | 2,934 WAV + `annotation.txt` (phone, file, distance, command) |
| Format | **16 kHz**, mono, 16-bit PCM; 0.56–2.75 s |
| Devices | Huawei Mate 9, Huawei Nova 2, Oppo K3, Google Pixel, Oppo Reno |
| Distances | 10, 30, 60, 100 cm (three devices); 150, 200, 300 cm (all five) |
| Commands | 23 wake words and word pairs, English and Chinese |
| Benign recordings | none |
| Licence | **none stated**; treat as all-rights-reserved academic courtesy. Do not redistribute. |

## Obtain

The README of the repository above links one Google Drive file (87 MB zip). It downloads without authentication:

```bash
pip install gdown
gdown 1RgXlq4UuU2QKYHqwOcDaMESR-Z_2Rzmq -O dolphin_dataset.zip
unzip dolphin_dataset.zip -d dolphin   # -> dolphin/dolphin-dataset/*.wav
```

## Run

```bash
python benchmark/dolphinattack/run.py dolphin/dolphin-dataset --out benchmark/dolphinattack/results.csv
```

## Result on v0.2.0 (2026-10-08)

Every clip returns **INSUFFICIENT_DATA** (2,934/2,934 full clips, 6,502/6,502 half-second windows): at 16 kHz the Nyquist frequency is 8 kHz, below the 18 kHz band the current detectors need. This is the real-world form of the capture-chain result in `BENCHMARK_REPORT.md` §6b, and this set is the first real-attack benchmark for the baseband detector.

What a 16 kHz detector has to work with here: energy below 100 Hz is 1–27% of the clip (median 4%); the 0–8 kHz shape is strongly low-pass with 71% of energy under 1 kHz; the clips are quiet (median −45 dBFS). On the three Huawei/Oppo devices most clips carry a stationary device-specific tone at exactly 5, 6 or 7 kHz (median prominence 17–20 dB), absent on the Pixel and Reno. Because the set has no benign recordings from the same phones, these tones cannot yet be attributed to the attack rather than the device.
