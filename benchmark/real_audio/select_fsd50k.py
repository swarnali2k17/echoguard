"""Pick FSD50K dev clips per category, filtered by licence (CC0 / CC-BY only) and size (2-12 s)."""
import csv
import json
import os
import random
import sys
from huggingface_hub import HfApi, hf_hub_download

HERE = os.path.dirname(os.path.abspath(__file__))
DL = os.path.join(HERE, "dl")
random.seed(1234)

info = json.load(open(os.path.join(DL, "Fhrozen__FSD50k/metadata/dev_clips_info_FSD50K.json")))
labels = {r["fname"]: r["labels"].split(",") for r in csv.DictReader(open(os.path.join(DL, "Fhrozen__FSD50k/labels/dev.csv")))}
api = HfApi()
sizes = {s.rfilename: (s.size or 0) for s in api.dataset_info("Fhrozen/FSD50k", files_metadata=True).siblings}

OK_LIC = {"http://creativecommons.org/licenses/by/3.0/": "CC-BY-3.0",
          "http://creativecommons.org/publicdomain/zero/1.0/": "CC0-1.0"}
BPS = 44100 * 2  # 16-bit mono -> bytes/sec

# category -> (required labels (any), excluded labels (any), n)
CATS = {
    "speech_noisy":      (["Speech", "Male_speech_and_man_speaking", "Female_speech_and_woman_speaking", "Conversation"],
                          ["Music", "Musical_instrument", "Singing"], 20, ["Crowd", "Chatter", "Traffic_noise_and_roadway_noise", "Wind", "Rain", "Vehicle", "Human_group_actions", "Walk_and_footsteps", "Engine", "Water", "Bus", "Train", "Subway_and_metro_and_underground"]),
    "music_guitar_piano": (["Electric_guitar", "Acoustic_guitar", "Piano", "Organ", "Keyboard_(musical)", "Accordion", "Bass_guitar", "Harp"], ["Cymbal", "Hi-hat"], 25, None),
    "music_percussive":  (["Cymbal", "Hi-hat", "Crash_cymbal", "Tambourine", "Glockenspiel", "Snare_drum", "Drum_kit", "Marimba_and_xylophone", "Cowbell", "Rattle_(instrument)"], [], 30, None),
    "music_wind_brass_vocal": (["Trumpet", "Brass_instrument", "Wind_instrument_and_woodwind_instrument", "Harmonica", "Bowed_string_instrument", "Male_singing", "Female_singing"], [], 15, None),
    "hf_natural":        (["Bird_vocalization_and_bird_call_and_bird_song", "Chirp_and_tweet", "Cricket", "Insect", "Keys_jangling", "Wind_chime", "Chink_and_clink", "Glass", "Coin_(dropping)", "Scissors", "Zipper_(clothing)", "Cutlery_and_silverware"], ["Music", "Speech"], 30, None),
    "environmental":     (["Rain", "Stream", "Ocean", "Waves_and_surf", "Traffic_noise_and_roadway_noise", "Wind", "Fire", "Crackle", "Walk_and_footsteps", "Thunderstorm", "Boiling", "Water_tap_and_faucet"], ["Music", "Speech", "Musical_instrument"], 25, None),
    "electronic_device": (["Alarm", "Ringtone", "Telephone", "Microwave_oven", "Printer", "Computer_keyboard", "Typing", "Mechanical_fan", "Speech_synthesizer", "Tick", "Camera", "Doorbell", "Buzz", "Hiss", "Mechanisms"], ["Music", "Speech", "Musical_instrument"], 30, None),
}

chosen = {}
used = set()
for cat, (req, exc, n, must_also) in CATS.items():
    pool = []
    for fname, labs in labels.items():
        if fname in used:
            continue
        if not any(r in labs for r in req) or any(e in labs for e in exc):
            continue
        if must_also and not any(m in labs for m in must_also):
            continue
        lic = info[fname]["license"]
        if lic not in OK_LIC:
            continue
        sz = sizes.get(f"clips/dev/{fname}.wav", 0)
        dur = (sz - 44) / BPS
        if not (2.0 <= dur <= 12.0):
            continue
        pool.append(fname)
    random.shuffle(pool)
    pick = pool[:n]
    used.update(pick)
    chosen[cat] = pick
    print(cat, "pool", len(pool), "picked", len(pick), file=sys.stderr)

out = os.path.join(DL, "fsd50k_selection.csv")
with open(out, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["category", "fname", "labels", "license", "title"])
    for _cat, picks in chosen.items():
        for fname in picks:
            w.writerow([cat, fname, "|".join(labels[fname]), OK_LIC[info[fname]["license"]], info[fname]["title"]])

if "--download" in sys.argv:
    tgt = os.path.join(DL, "Fhrozen__FSD50k")
    for _cat, picks in chosen.items():
        for fname in picks:
            hf_hub_download("Fhrozen/FSD50k", f"clips/dev/{fname}.wav", repo_type="dataset", local_dir=tgt)
    print("downloaded", sum(len(p) for p in chosen.values()), file=sys.stderr)
