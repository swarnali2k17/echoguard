"""Assemble the real-audio corpus under realaudio/corpus/ with manifest.csv."""
import csv
import io
import os
import random
import glob
import numpy as np
import soundfile as sf
import pyarrow.parquet as pq
from scipy.signal import resample_poly

HERE = os.path.dirname(os.path.abspath(__file__))
DL = os.path.join(HERE, "dl")
CORPUS = os.path.join(HERE, "corpus")
random.seed(7)
rows = []


def add(cat, name, data, sr, source, licence, labels):
    d = os.path.join(CORPUS, cat)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name + ".wav")
    if data.ndim > 1:
        data = data.mean(axis=1)
    sf.write(path, data.astype(np.float32), sr, subtype="PCM_16")
    rows.append(dict(file=os.path.relpath(path, CORPUS), category=cat, source=source, license=licence,
                     sample_rate=sr, duration_s=round(len(data) / sr, 3), labels=labels))
    return data


# ---- FSD50K individual clips ----
sel = list(csv.DictReader(open(os.path.join(DL, "fsd50k_selection.csv"))))
for r in sel:
    p = os.path.join(DL, "Fhrozen__FSD50k", "clips", "dev", r["fname"] + ".wav")
    data, sr = sf.read(p, dtype="float32")
    add(r["category"], "fsd" + r["fname"], data, sr, "FSD50K (Freesound) via HF Fhrozen/FSD50k", r["license"], r["labels"])

# ---- VCTK clean speech (48 kHz, CC-BY-4.0) ----
pf = pq.ParquetFile(glob.glob(os.path.join(DL, "jspaulsen__vctk/data/*.parquet"))[0])
t = pf.read(columns=["id", "speaker_id", "mic_id", "audio"])
ids = [i for i, m in enumerate(t["mic_id"].to_pylist()) if m == "mic1"]
random.shuffle(ids)
vctk = []
for i in ids:
    row = t.slice(i, 1).to_pylist()[0]
    data, sr = sf.read(io.BytesIO(row["audio"]["bytes"]), dtype="float32")
    if len(data) / sr < 2.0:
        continue
    vctk.append((row["id"], data, sr))
    if len(vctk) >= 40:
        break
for cid, data, sr in vctk:
    add("speech_clean", "vctk_" + cid, data, sr, "VCTK via HF jspaulsen/vctk", "CC-BY-4.0", "speech,studio")

# ---- ESC-50 environmental (44.1 kHz, CC-BY-NC) ----
pf = pq.ParquetFile(glob.glob(os.path.join(DL, "ashraq__esc50/data/*.parquet"))[0])
t = pf.read(columns=["filename", "category", "audio"])
bycat = {}
for i, c in enumerate(t["category"].to_pylist()):
    bycat.setdefault(c, []).append(i)
ESC_ENV = ["rain", "sea_waves", "crackling_fire", "wind", "footsteps", "thunderstorm", "crickets", "chirping_birds",
           "water_drops", "pouring_water", "engine", "train", "helicopter", "chainsaw", "airplane", "church_bells",
           "clock_tick", "keyboard_typing", "mouse_click", "vacuum_cleaner", "washing_machine", "glass_breaking",
           "door_wood_knock", "can_opening", "clock_alarm", "hen", "insects", "frog", "dog", "cat"]
esc_noise_pool = []
for c in ESC_ENV:
    idxs = bycat.get(c, [])
    random.shuffle(idxs)
    for i in idxs[:2]:
        row = t.slice(i, 1).to_pylist()[0]
        data, sr = sf.read(io.BytesIO(row["audio"]["bytes"]), dtype="float32")
        cat = "electronic_device" if c in ("keyboard_typing", "mouse_click", "clock_alarm", "clock_tick") else \
              "hf_natural" if c in ("crickets", "chirping_birds", "insects", "glass_breaking", "church_bells") else "environmental"
        add(cat, "esc_" + row["filename"].replace(".wav", ""), data, sr, "ESC-50 via HF ashraq/esc50", "CC-BY-NC-3.0", c)
        if c in ("rain", "sea_waves", "wind", "engine", "train", "vacuum_cleaner", "crackling_fire", "airplane"):
            esc_noise_pool.append((c, data, sr))

# ---- MUSDB18-HQ mixtures (44.1 kHz stereo, CC-BY-NC-SA) : 10 s segments ----
pf = pq.ParquetFile(glob.glob(os.path.join(DL, "roro128__musdb18-hq-flac/data/*.parquet"))[0])
for rg in range(pf.metadata.num_row_groups):
    row = pf.read_row_group(rg).to_pylist()[0]
    if row["instrument"] != "mixture":
        continue
    data, sr = sf.read(io.BytesIO(row["audio"]["bytes"]), dtype="float32")
    track = row["path"].split("/")[-2].replace(" ", "_").replace("-", "")
    n = len(data)
    for k, start in enumerate([int(n * 0.3), int(n * 0.6)]):
        seg = data[start:start + 10 * sr]
        add("music_full_mix", f"musdb_{track}_{k}", seg, sr, "MUSDB18-HQ via HF roro128/musdb18-hq-flac", "CC-BY-NC-SA-4.0", "mixture:" + row["path"].split("/")[-2])

# ---- speech + real noise mixes (VCTK 48k -> 44.1k, + ESC-50 noise @ 10 dB SNR) ----
for j, (cid, data, _sr) in enumerate(vctk[:20]):
    noisec, noise, nsr = esc_noise_pool[j % len(esc_noise_pool)]
    sp = resample_poly(data, 147, 160).astype(np.float32)
    nz = noise[: len(sp)] if len(noise) >= len(sp) else np.resize(noise, len(sp))
    ps, pn = np.mean(sp ** 2), np.mean(nz ** 2) + 1e-12
    g = np.sqrt(ps / (pn * 10 ** (10 / 10)))
    mix = sp + g * nz
    mix = mix / max(1.0, np.max(np.abs(mix)) * 1.05)
    add("speech_noisy_mix", f"mix_{cid}_{noisec}", mix, 44100, "VCTK speech + ESC-50 noise (mixed here, 10 dB SNR)", "CC-BY-4.0 + CC-BY-NC-3.0", f"speech+{noisec}")

with open(os.path.join(CORPUS, "manifest.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
from collections import Counter  # noqa: E402
print(Counter(r["category"] for r in rows), len(rows))
