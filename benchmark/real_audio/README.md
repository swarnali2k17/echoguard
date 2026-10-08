# Real-audio false-positive benchmark

307 openly licensed recordings at 44.1/48 kHz (`manifest.csv`: file, category, source, licence, rate, duration, labels), scored as full clips and 0.5 s / 0.2 s windows.

```bash
pip install -e ".[bench]"
python benchmark/real_audio/select_fsd50k.py      # pick FSD50K clips (CC0 / CC-BY only)
python benchmark/real_audio/build_corpus.py       # download + convert into ./corpus
python benchmark/real_audio/evaluate_real.py corpus   # -> results.csv, clipinfo.csv
```

`results_v0.2.0.csv` is the published run (0 flagged of 3,230 segments). Sources: FSD50K (CC0/CC-BY-3.0 per clip), VCTK (CC-BY-4.0), ESC-50 (CC-BY-NC-3.0), MUSDB18-HQ (CC-BY-NC-SA-4.0). The audio itself is not committed.
