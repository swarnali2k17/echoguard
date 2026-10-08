# EchoGuard capture tool

A defensive dataset-collection helper for `docs/recording_protocol.md`. It records
labelled clips, writes a JSON metadata sidecar per clip, maintains a `manifest.csv`
in the schema the benchmark harnesses read, and can run the detector live. It emits
no audio and ships no attack signal.

```bash
pip install -e ".[capture]"      # adds sounddevice for live mic input
```

`ingest` and `monitor --file` work without `sounddevice`; only live mic capture
needs it.

## Record from a mic

```bash
# Characterise a device first: benign ambient, must come back CLEAR / INSUFFICIENT_DATA
python tools/capture/record.py record --out corpus \
    --label benign --track B --device "Pixel 8 / Android 15" \
    --distance-cm 30 --room roomA --operator you --consent consent-2026-01

# A probe take (the test probe from the protocol, carrying no command)
python tools/capture/record.py record --out corpus \
    --label probe --track P --device "Pixel 8 / Android 15" \
    --distance-cm 60 --room roomA --seconds 3 --rate 48000
```

Each call writes `corpus/<device>__<track>__<dist>__<room>__NNN.wav`, a matching
`.json` sidecar (full metadata + the live verdict), and appends a `manifest.csv`
row. A clipped take warns; a benign clip that flags warns (likely a device
artefact to note in `SOURCES.md`).

## Label audio captured elsewhere

Copy recordings off a phone, then:

```bash
python tools/capture/record.py ingest phone_dump/*.wav --out corpus \
    --label probe --track P --device "iPhone 15 / iOS 18" --room roomB --distance-cm 100
```

## Live monitor (writes nothing)

```bash
python tools/capture/record.py monitor --rate 48000        # from the mic
python tools/capture/record.py monitor --file clip.wav     # score a file
```

## Build / evaluate the corpus

The manifest is consumed directly by the repo harness:

```bash
python benchmark/real_audio/evaluate_real.py corpus
```
