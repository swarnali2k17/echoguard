"""Reference: wiring the confirmation gate into an action-taking voice agent.

This is a *mock* agent — it does not do real ASR and takes no real action. It
shows the one integration point that matters: between "the ASR produced a
command" and "the agent executes it", the captured audio goes through
`ConfirmationGate`, and the decision (allow / confirm / block) controls whether
the action runs.

Run it on the sample fixtures:

    python examples/make_fixtures.py
    python examples/voice_agent_gate.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from echoguard.audio import load_wav                                        # noqa: E402
from echoguard.gate import ActionSensitivity, ConfirmationGate, GateDecision, InvalidInput  # noqa: E402


class MockVoiceAgent:
    """A stand-in for an action-taking agent. Real ASR / actuation omitted."""

    def __init__(self):
        self.gate = ConfirmationGate()

    def handle_utterance(self, wav_path: str, command: str, sensitivity: ActionSensitivity):
        """Capture -> (ASR, omitted) -> gate -> act / confirm / refuse."""
        try:
            signal, sr = load_wav(wav_path)
        except Exception as exc:  # noqa: BLE001
            print(f"[{command!r}] could not read audio: {exc}")
            return

        try:
            result = self.gate.evaluate(signal, sr, sensitivity, command=command)
        except InvalidInput as exc:
            # Fail safe: unusable audio is never an allow for a real action.
            print(f"[{command!r}] audio unusable ({exc}) -> refusing to act")
            return

        tag = f"{result.verdict}/{result.risk:.2f}"
        if result.decision is GateDecision.ALLOW:
            print(f"[{command!r}] {sensitivity.value:9s} {tag:20s} -> EXECUTE")
            self._execute(command)
        elif result.decision is GateDecision.CONFIRM:
            print(f"[{command!r}] {sensitivity.value:9s} {tag:20s} -> CONFIRM first")
            print(f"            ask the user out-of-band: {result.reason}")
        else:
            print(f"[{command!r}] {sensitivity.value:9s} {tag:20s} -> BLOCK")
            print(f"            {result.reason}")

    @staticmethod
    def _execute(command: str):
        print(f"            ...(agent performs: {command})")


def main() -> int:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fx = os.path.join(here, "fixtures")
    if not os.path.isdir(fx):
        print("run `python examples/make_fixtures.py` first", file=sys.stderr)
        return 1

    agent = MockVoiceAgent()
    benign = os.path.join(fx, "benign.wav")
    attack = os.path.join(fx, "out_of_band.wav")

    print("=== benign capture ===")
    agent.handle_utterance(benign, "what's the weather", ActionSensitivity.ROUTINE)
    agent.handle_utterance(benign, "text Alex I'm running late", ActionSensitivity.SENSITIVE)
    agent.handle_utterance(benign, "unlock the front door", ActionSensitivity.CRITICAL)

    print("\n=== capture with an injection signature ===")
    agent.handle_utterance(attack, "what's the weather", ActionSensitivity.ROUTINE)
    agent.handle_utterance(attack, "text Alex I'm running late", ActionSensitivity.SENSITIVE)
    agent.handle_utterance(attack, "unlock the front door", ActionSensitivity.CRITICAL)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
