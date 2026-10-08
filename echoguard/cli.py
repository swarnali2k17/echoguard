"""Command-line interface: `echoguard scan file.wav` and `echoguard gate file.wav`.

Exit codes (stable; safe to branch on in CI):
  scan: 0 CLEAR · 1 SUSPICIOUS · 2 HIGH_RISK · 4 INSUFFICIENT_DATA
  gate: 0 ALLOW · 7 CONFIRM · 8 BLOCK
  both: 3 file could not be read · 5 audio invalid · 6 usage error
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings

from .audio import load_wav
from .gate import ActionSensitivity, ConfirmationGate, GateDecision
from .pipeline import (
    Pipeline, InvalidInput, CLEAR, SUSPICIOUS, HIGH_RISK, INSUFFICIENT_DATA,
)

EXIT_CODE = {CLEAR: 0, SUSPICIOUS: 1, HIGH_RISK: 2, INSUFFICIENT_DATA: 4}
EXIT_READ_ERROR = 3
EXIT_INVALID_INPUT = 5
EXIT_USAGE = 6
GATE_EXIT_CODE = {GateDecision.ALLOW: 0, GateDecision.CONFIRM: 7, GateDecision.BLOCK: 8}


class _Parser(argparse.ArgumentParser):
    """argparse exits 2 on usage errors, which collides with HIGH_RISK. Use 6."""

    def error(self, message: str) -> None:  # type: ignore[override]
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="echoguard",
        description="Scan a WAV clip for signs of inaudible / injected voice-command attacks.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    scan = sub.add_parser("scan", help="scan a WAV file")
    scan.add_argument("path", help="path to a .wav file")
    scan.add_argument("--json", action="store_true", help="emit JSON instead of text")
    scan.add_argument(
        "--window", type=float, default=None, metavar="SEC",
        help="score overlapping windows of SEC seconds (default: whole clip); "
             "the verdict is the worst window's",
    )
    scan.add_argument(
        "--hop", type=float, default=0.5, metavar="SEC",
        help="hop between windows when --window is given (default 0.5)",
    )

    gate = sub.add_parser(
        "gate", help="decide allow/confirm/block for an action given the captured audio")
    gate.add_argument("path", help="path to a .wav file of the captured command")
    gate.add_argument(
        "--action", required=True, choices=[s.value for s in ActionSensitivity],
        help="how sensitive the action the agent is about to take is",
    )
    gate.add_argument("--command", default=None, help="the parsed command text, for the log")
    gate.add_argument("--json", action="store_true", help="emit JSON instead of text")
    gate.add_argument(
        "--window", type=float, default=1.0, metavar="SEC",
        help="windowed scan length; the worst window drives the decision (default 1.0; 0 = whole clip)",
    )
    gate.add_argument("--hop", type=float, default=0.5, metavar="SEC", help="hop between windows")
    return parser


def _load(path: str):
    """Load a WAV, printing any loader warnings cleanly. Returns (signal, rate) or None."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        signal, sample_rate = load_wav(path)
    for w in caught:
        print(f"warning: {path}: {w.message}", file=sys.stderr)
    return signal, sample_rate


def _run_scan(args) -> int:
    try:
        signal, sample_rate = _load(args.path)
    except Exception as exc:  # noqa: BLE001 - surface a clean CLI error
        print(f"error: could not read '{args.path}': {exc}", file=sys.stderr)
        return EXIT_READ_ERROR
    try:
        if args.window:
            windowed = Pipeline().analyze_windows(signal, sample_rate, args.window, args.hop)
            report, payload = windowed.summary, windowed.to_dict()
        else:
            report = Pipeline().analyze(signal, sample_rate)
            payload = report.to_dict()
    except InvalidInput as exc:
        print(f"error: '{args.path}' cannot be analysed: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT

    if args.json:
        print(json.dumps(payload, indent=2, allow_nan=False))
    else:
        from .report import render_text
        print(render_text(report, source=args.path))
    return EXIT_CODE.get(report.verdict, 0)


def _run_gate(args) -> int:
    try:
        signal, sample_rate = _load(args.path)
    except Exception as exc:  # noqa: BLE001
        print(f"error: could not read '{args.path}': {exc}", file=sys.stderr)
        return EXIT_READ_ERROR
    gate = ConfirmationGate(window_sec=(args.window or None), hop_sec=args.hop)
    try:
        result = gate.evaluate(signal, sample_rate,
                               ActionSensitivity(args.action), command=args.command)
    except InvalidInput as exc:
        # Fail safe: unusable audio before a sensitive action is not "allow".
        print(f"error: '{args.path}' cannot be analysed: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, allow_nan=False))
    else:
        cmd = f' for "{result.command}"' if result.command else ""
        print(f"EchoGuard gate{cmd}")
        print(f"  action    : {result.sensitivity.value}")
        print(f"  verdict   : {result.verdict} (risk {result.risk:.2f})")
        print(f"  decision  : {result.decision.value.upper()}")
        print(f"  reason    : {result.reason}")
    return GATE_EXIT_CODE[result.decision]


def run(argv: list[str] | None = None) -> int:
    """Run the CLI and return its exit code without calling sys.exit."""
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    if args.cmd == "scan":
        return _run_scan(args)
    if args.cmd == "gate":
        return _run_gate(args)
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
