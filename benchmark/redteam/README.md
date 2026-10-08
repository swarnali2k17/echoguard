# Red-team experiments

Adversarial evaluation of the ultrasonic detectors (BENCHMARK_REPORT.md §6b, §6d).

```bash
PYTHONPATH=.:benchmark/redteam python benchmark/redteam/experiments.py A   # realistic ADC capture chain
PYTHONPATH=.:benchmark/redteam python benchmark/redteam/experiments.py all # A-F
```

`rt_common.adc_capture()` models a device ADC: 8th-order Butterworth anti-alias at 0.45 x target rate, then polyphase resampling. `A_baseband_48k.png` shows what remains of a 28 kHz attack after capture at 48 kHz.
