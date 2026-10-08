"""Confirmation gate: a reference integration for an action-taking voice agent.

A voice agent that *acts* on spoken commands — unlocks a door, sends money,
sends a message — should not execute silently on audio that looks injected.
`ConfirmationGate` sits between "the ASR produced a command" and "the agent
executes it": it scores the captured audio with EchoGuard and returns one of
`ALLOW`, `CONFIRM` (require an explicit, ideally out-of-band confirmation) or
`BLOCK`, based on the verdict **and** how sensitive the action is.

The decision is a policy over (action sensitivity x verdict). The default
policy is deliberately conservative for high-impact actions and permissive for
reversible ones; it is fully overridable.

    from echoguard.gate import ConfirmationGate, ActionSensitivity, GateDecision

    gate = ConfirmationGate()
    result = gate.evaluate(signal, sample_rate, ActionSensitivity.CRITICAL,
                           command="unlock the front door")
    if result.decision is GateDecision.BLOCK:
        agent.refuse(result.reason)
    elif result.decision is GateDecision.CONFIRM:
        agent.ask_user_to_confirm(result.reason)   # step-up / out-of-band
    else:
        agent.execute()

**What this protects against, and what it does not.** The gate is only as good
as EchoGuard's verdict. It raises the bar for the injection classes EchoGuard
can see — ultrasonic / near-ultrasound carriers in a capture that preserves the
band (see BENCHMARK_REPORT.md). It does **not** detect in-band hidden/adversarial
commands, laser or EM injection, or an attack whose carrier the device already
filtered out. On a 16 kHz phone capture the verdict is INSUFFICIENT_DATA, and the
default policy treats "could not check" as "do not silently execute a sensitive
action" — not as proof of safety. Treat the gate as defence-in-depth, one signal
among several (speaker ID, liveness, rate-limiting), never a guarantee.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .pipeline import (
    Pipeline, Report, InvalidInput,
    CLEAR, SUSPICIOUS, HIGH_RISK, INSUFFICIENT_DATA,
)


class ActionSensitivity(Enum):
    """How much a wrongly-executed action would cost.

    The integrator classifies each of their actions into one of these. The
    examples are a guide, not a rule.
    """

    ROUTINE = "routine"      # reversible, low-impact: weather, a timer, play music, lights
    SENSITIVE = "sensitive"  # outbound or stateful: send a message, call, change a setting
    CRITICAL = "critical"    # irreversible / high-impact: unlock, pay, transfer, delete, disarm


class GateDecision(Enum):
    ALLOW = "allow"          # execute normally
    CONFIRM = "confirm"      # require explicit, ideally out-of-band, confirmation before executing
    BLOCK = "block"          # do not execute; surface that the audio looks injected

    @property
    def rank(self) -> int:
        return {"allow": 0, "confirm": 1, "block": 2}[self.value]


# Default policy: (sensitivity -> verdict -> decision). Rationale in docs/confirmation_gate.md.
#  - CRITICAL always at least CONFIRM, even on a CLEAR verdict: confirming an
#    unlock or a payment is good practice regardless, and EchoGuard is not a
#    guarantee.
#  - INSUFFICIENT_DATA ("couldn't check", the norm on a 16 kHz capture) is
#    treated as unverified: confirm anything sensitive, never silently execute
#    a critical action on audio we could not assess.
#  - SUSPICIOUS blocks critical, confirms sensitive. HIGH_RISK blocks both and
#    confirms even routine, because a clear injection signature means someone
#    may be driving the device.
DEFAULT_POLICY: dict = {
    ActionSensitivity.ROUTINE: {
        CLEAR: GateDecision.ALLOW,
        INSUFFICIENT_DATA: GateDecision.ALLOW,
        SUSPICIOUS: GateDecision.ALLOW,
        HIGH_RISK: GateDecision.CONFIRM,
    },
    ActionSensitivity.SENSITIVE: {
        CLEAR: GateDecision.ALLOW,
        INSUFFICIENT_DATA: GateDecision.CONFIRM,
        SUSPICIOUS: GateDecision.CONFIRM,
        HIGH_RISK: GateDecision.BLOCK,
    },
    ActionSensitivity.CRITICAL: {
        CLEAR: GateDecision.CONFIRM,
        INSUFFICIENT_DATA: GateDecision.CONFIRM,
        SUSPICIOUS: GateDecision.BLOCK,
        HIGH_RISK: GateDecision.BLOCK,
    },
}

_VERDICT_CAUSE = {
    CLEAR: "the captured audio shows no injection signature",
    INSUFFICIENT_DATA: "the capture bandwidth was too low to check for an injection "
                       "(capture at >= 44.1 kHz to enable the check)",
    SUSPICIOUS: "the captured audio shows a possible injection signature",
    HIGH_RISK: "the captured audio shows a strong ultrasonic-injection signature",
}

_DECISION_VERB = {
    GateDecision.ALLOW: "proceed",
    GateDecision.CONFIRM: "require explicit confirmation before proceeding",
    GateDecision.BLOCK: "do not execute",
}


@dataclass
class GateResult:
    decision: GateDecision
    sensitivity: ActionSensitivity
    verdict: str
    risk: float
    reason: str
    report: Report
    command: Optional[str] = None
    evaluated_windows: int = 1

    @property
    def allowed(self) -> bool:
        return self.decision is GateDecision.ALLOW

    def to_dict(self) -> dict:
        return {
            "decision": self.decision.value,
            "sensitivity": self.sensitivity.value,
            "verdict": self.verdict,
            "risk": round(float(self.risk), 3),
            "reason": self.reason,
            "command": self.command,
            "evaluated_windows": self.evaluated_windows,
            "report": self.report.to_dict(),
        }


class ConfirmationGate:
    """Decide whether a voice agent may act on a captured command.

    `window_sec`/`hop_sec` control the windowed scan: the *worst* window's
    verdict drives the decision, so a short injection burst anywhere inside the
    captured command gates the action rather than being averaged away. Pass
    `window_sec=None` to score the clip as a whole.
    """

    def __init__(self, policy: Optional[dict] = None, pipeline: Optional[Pipeline] = None,
                 window_sec: Optional[float] = 1.0, hop_sec: float = 0.5):
        self.policy = policy if policy is not None else DEFAULT_POLICY
        self.pipeline = pipeline or Pipeline()
        self.window_sec = window_sec
        self.hop_sec = hop_sec

    def decide(self, sensitivity: ActionSensitivity, verdict: str) -> GateDecision:
        """The policy lookup alone, exposed for testing and for custom flows."""
        by_verdict = self.policy.get(sensitivity)
        if by_verdict is None:
            raise ValueError(f"no policy for sensitivity {sensitivity!r}")
        # An unknown verdict is treated as the most cautious mapping present.
        return by_verdict.get(verdict, GateDecision.BLOCK)

    def evaluate(self, signal, sample_rate, sensitivity: ActionSensitivity,
                 command: Optional[str] = None) -> GateResult:
        """Score the captured audio and return the gate decision.

        Raises `InvalidInput` (from the pipeline) on unusable audio; a caller
        that would rather fail safe can catch it and treat it as CONFIRM/BLOCK.
        """
        if not isinstance(sensitivity, ActionSensitivity):
            raise TypeError("sensitivity must be an ActionSensitivity")

        if self.window_sec:
            windowed = self.pipeline.analyze_windows(
                signal, sample_rate, window_sec=self.window_sec, hop_sec=self.hop_sec)
            report = windowed.summary
            n_windows = len(windowed.windows)
        else:
            report = self.pipeline.analyze(signal, sample_rate)
            n_windows = 1

        decision = self.decide(sensitivity, report.verdict)
        reason = (
            f"{_DECISION_VERB[decision].capitalize()}: a {sensitivity.value} action with a "
            f"{report.verdict} verdict (risk {report.overall_risk:.2f}) — "
            f"{_VERDICT_CAUSE.get(report.verdict, 'unrecognised verdict')}."
        )
        return GateResult(
            decision=decision,
            sensitivity=sensitivity,
            verdict=report.verdict,
            risk=report.overall_risk,
            reason=reason,
            report=report,
            command=command,
            evaluated_windows=n_windows,
        )


__all__ = [
    "ActionSensitivity", "GateDecision", "GateResult", "ConfirmationGate",
    "DEFAULT_POLICY", "InvalidInput",
]
