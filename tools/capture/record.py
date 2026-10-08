"""EchoGuard capture tool: record labelled clips and build a corpus manifest.

A defensive dataset-collection helper for the recording protocol in
`docs/recording_protocol.md`. It records audio from an input device (or ingests
WAV files captured elsewhere, e.g. copied off a phone), writes each clip plus a
JSON metadata sidecar, appends a row to the corpus `manifest.csv` in the schema
the repo's benchmark harnesses read, and can run the detector live so the
operator sees a verdict as they capture.

It never emits audio and ships no attack signal; it only records and labels.

Commands
  list-devices                 show input devices and their max sample rate
  record  --label L --out DIR  record one clip, write WAV + sidecar + manifest row
  ingest  FILES... --out DIR   label already-captured WAVs into the corpus
  monitor --rate R             live verdicts from the mic, writing nothing

Audio input needs `sounddevice` (pip install sounddevice). `ingest`, `monitor`
of a file, and manifest building work without it.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from typing import Optional

import numpy as np
from scipy.io import wavfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from echoguard.audio import load_wav, to_float_mono  # noqa: E402
from echoguard.pipeline import Pipeline, InvalidInput  # noqa: E402

MANIFEST = "manifest.csv"
FIELDS = ["file", "label", "category", "device", "capture_rate", "distance_cm",
          "room", "track", "operator", "consent", "notes"]


@dataclass
class ClipMeta:
    file: str
    label: str = "benign"            # ground truth: benign | probe
    category: str = ""               # free text for slicing
    device: str = ""                 # make/model/OS of the CAPTURING device
    capture_rate: int = 0
    distance_cm: str = ""
    room: str = ""
    track: str = ""                  # B | P | S | N (see the protocol)
    operator: str = ""
    consent: str = ""                # consent reference for any recorded voice
    notes: str = ""
    recorded_utc: str = field(default_factory=lambda: _dt.datetime.now(_dt.timezone.utc).isoformat())

    def manifest_row(self) -> dict:
        return {k: getattr(self, k) for k in FIELDS}


# --------------------------------------------------------------------------- #
# writing a clip + sidecar + manifest row
# --------------------------------------------------------------------------- #
def _write_clip(out_dir: str, name: str, signal: np.ndarray, rate: int, meta: ClipMeta,
                verdict: Optional[dict] = None) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name)
    peak = float(np.max(np.abs(signal))) if signal.size else 0.0
    if peak >= 0.999:
        print(f"  WARNING: {name} is clipped (peak {peak:.3f}); discard and re-take.", file=sys.stderr)
    pcm = np.clip(signal, -1.0, 1.0)
    wavfile.write(path, rate, (pcm * 32767.0).astype(np.int16))
    sidecar = {**asdict(meta)}
    if verdict is not None:
        sidecar["echoguard_verdict"] = verdict
    with open(path + ".json", "w") as fh:
        json.dump(sidecar, fh, indent=2)
    _append_manifest(out_dir, meta)
    return path


def _append_manifest(out_dir: str, meta: ClipMeta) -> None:
    mpath = os.path.join(out_dir, MANIFEST)
    exists = os.path.exists(mpath)
    with open(mpath, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if not exists:
            w.writeheader()
        w.writerow(meta.manifest_row())


def _next_index(out_dir: str, stem: str) -> int:
    if not os.path.isdir(out_dir):
        return 1
    n = 0
    for f in os.listdir(out_dir):
        if f.startswith(stem) and f.endswith(".wav"):
            n += 1
    return n + 1


def _clip_name(meta: ClipMeta, out_dir: str) -> str:
    # device slug: the part before "/" (make/model), spaces removed -> "Pixel 8 / ..." = "Pixel8"
    make = meta.device.split("/")[0] if meta.device else "dev"
    dev = "".join(make.split()) or "dev"
    track = meta.track or meta.label[0].upper()
    dist = f"{int(meta.distance_cm):03d}cm" if str(meta.distance_cm).isdigit() else "____"
    room = meta.room or "room"
    stem = f"{dev}__{track}__{dist}__{room}__"
    return f"{stem}{_next_index(out_dir, stem):03d}.wav"


def _run_detector(signal: np.ndarray, rate: int) -> dict:
    try:
        wr = Pipeline().analyze_windows(signal, rate, window_sec=1.0, hop_sec=0.5)
        return {"verdict": wr.summary.verdict, "overall_risk": round(wr.summary.overall_risk, 3),
                "worst_window_start_s": round(wr.summary.start_sec, 2)}
    except InvalidInput as exc:
        return {"verdict": "INVALID_INPUT", "error": str(exc)}


# --------------------------------------------------------------------------- #
# audio input (sounddevice, imported lazily)
# --------------------------------------------------------------------------- #
def _require_sounddevice():
    try:
        import sounddevice as sd  # noqa: PLC0415
    except ImportError:
        sys.exit("this command needs sounddevice: pip install sounddevice")
    return sd


def cmd_list_devices(_args) -> int:
    sd = _require_sounddevice()
    print(f"{'idx':>3}  {'max in':>6}  {'default rate':>12}  name")
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            print(f"{i:>3}  {d['max_input_channels']:>6}  {int(d['default_samplerate']):>12}  {d['name']}")
    return 0


def _record(rate: int, seconds: float, device: Optional[int]) -> np.ndarray:
    sd = _require_sounddevice()
    print(f"  recording {seconds:.1f}s at {rate} Hz ...", file=sys.stderr)
    frames = sd.rec(int(seconds * rate), samplerate=rate, channels=1, dtype="float32", device=device)
    sd.wait()
    return to_float_mono(frames).astype(np.float64)


def cmd_record(args) -> int:
    meta = ClipMeta(file="", label=args.label, category=args.category, device=args.device,
                    capture_rate=args.rate, distance_cm=args.distance_cm, room=args.room,
                    track=args.track, operator=args.operator, consent=args.consent, notes=args.notes)
    signal = _record(args.rate, args.seconds, args.device_index)
    name = _clip_name(meta, args.out)
    meta.file = name
    verdict = _run_detector(signal, args.rate) if not args.no_detect else None
    path = _write_clip(args.out, name, signal, args.rate, meta, verdict)
    print(f"wrote {path}" + (f"   verdict: {verdict['verdict']} (risk {verdict.get('overall_risk')})" if verdict else ""))
    if verdict and meta.label == "benign" and verdict["verdict"] in ("SUSPICIOUS", "HIGH_RISK"):
        print("  NOTE: a benign clip flagged — inspect; likely a device artefact to record in SOURCES.md.",
              file=sys.stderr)
    return 0


def cmd_ingest(args) -> int:
    base = ClipMeta(file="", label=args.label, category=args.category, device=args.device,
                    distance_cm=args.distance_cm, room=args.room, track=args.track,
                    operator=args.operator, consent=args.consent, notes=args.notes)
    n = 0
    for src in args.files:
        try:
            signal, rate = load_wav(src)
        except Exception as exc:  # noqa: BLE001
            print(f"  skip {src}: {exc}", file=sys.stderr)
            continue
        meta = ClipMeta(**{**asdict(base), "file": "", "capture_rate": rate,
                           "recorded_utc": base.recorded_utc})
        name = _clip_name(meta, args.out)
        meta.file = name
        verdict = _run_detector(to_float_mono(np.asarray(signal)).astype(np.float64), rate) if not args.no_detect else None
        _write_clip(args.out, name, np.asarray(signal, dtype=np.float64), rate, meta, verdict)
        n += 1
        print(f"  {src} -> {name}" + (f"   {verdict['verdict']}" if verdict else ""))
    print(f"ingested {n} file(s) into {args.out}")
    return 0


def cmd_monitor(args) -> int:
    if args.file:
        signal, rate = load_wav(args.file)
        wr = Pipeline().analyze_windows(signal, rate, window_sec=1.0, hop_sec=args.hop)
        for w in wr.windows:
            print(f"  t={w.start_sec:6.1f}s  {w.verdict:18s} risk {w.overall_risk:.2f}")
        print(f"summary: {wr.summary.verdict} (worst window at {wr.summary.start_sec:.1f}s)")
        return 0
    sd = _require_sounddevice()
    from echoguard.pipeline import StreamAnalyzer  # noqa: PLC0415
    stream = StreamAnalyzer(args.rate, window_sec=1.0, hop_sec=args.hop)
    print(f"monitoring at {args.rate} Hz (Ctrl-C to stop)", file=sys.stderr)
    try:
        with sd.InputStream(samplerate=args.rate, channels=1, dtype="float32", device=args.device_index) as s:
            while True:
                block, _ = s.read(int(args.hop * args.rate))
                for rep in stream.push(to_float_mono(block).astype(np.float64)):
                    tag = "" if rep.verdict in ("CLEAR", "INSUFFICIENT_DATA") else "  <-- flag"
                    print(f"  t={rep.start_sec:6.1f}s  {rep.verdict:18s} risk {rep.overall_risk:.2f}{tag}")
    except KeyboardInterrupt:
        print("\nstopped", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="echoguard-capture", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list-devices", help="list input devices").set_defaults(func=cmd_list_devices)

    def common(sp):
        sp.add_argument("--out", required=True, help="corpus directory")
        sp.add_argument("--label", default="benign", choices=["benign", "probe"])
        sp.add_argument("--category", default="")
        sp.add_argument("--device", default="", help="make/model/OS of the capturing device")
        sp.add_argument("--distance-cm", dest="distance_cm", default="")
        sp.add_argument("--room", default="")
        sp.add_argument("--track", default="", choices=["", "B", "P", "S", "N"])
        sp.add_argument("--operator", default="")
        sp.add_argument("--consent", default="")
        sp.add_argument("--notes", default="")
        sp.add_argument("--no-detect", action="store_true", help="skip the live verdict")

    r = sub.add_parser("record", help="record one clip from the mic")
    common(r)
    r.add_argument("--rate", type=int, default=48000)
    r.add_argument("--seconds", type=float, default=3.0)
    r.add_argument("--device-index", type=int, default=None)
    r.set_defaults(func=cmd_record)

    g = sub.add_parser("ingest", help="label already-captured WAV files")
    common(g)
    g.add_argument("files", nargs="+")
    g.set_defaults(func=cmd_ingest)

    m = sub.add_parser("monitor", help="live verdicts (writes nothing)")
    m.add_argument("--rate", type=int, default=48000)
    m.add_argument("--hop", type=float, default=0.5)
    m.add_argument("--device-index", type=int, default=None)
    m.add_argument("--file", default=None, help="score a WAV instead of the mic")
    m.set_defaults(func=cmd_monitor)
    return p


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
