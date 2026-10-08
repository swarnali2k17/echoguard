"""Confirmation gate: policy matrix, worst-window behaviour, CLI, fail-safe."""

from __future__ import annotations

import json

import numpy as np
import pytest
from scipy.io import wavfile

from echoguard import (
    ActionSensitivity, ConfirmationGate, GateDecision, InvalidInput,
)
from echoguard.gate import DEFAULT_POLICY
from echoguard.pipeline import CLEAR, SUSPICIOUS, HIGH_RISK, INSUFFICIENT_DATA
from echoguard.cli import run
from tests import synth

SR = 48_000
A = ActionSensitivity
D = GateDecision


# --- the default policy matrix -----------------------------------------------

@pytest.mark.parametrize("sensitivity,verdict,expected", [
    (A.ROUTINE, CLEAR, D.ALLOW),
    (A.ROUTINE, INSUFFICIENT_DATA, D.ALLOW),
    (A.ROUTINE, SUSPICIOUS, D.ALLOW),
    (A.ROUTINE, HIGH_RISK, D.CONFIRM),
    (A.SENSITIVE, CLEAR, D.ALLOW),
    (A.SENSITIVE, INSUFFICIENT_DATA, D.CONFIRM),
    (A.SENSITIVE, SUSPICIOUS, D.CONFIRM),
    (A.SENSITIVE, HIGH_RISK, D.BLOCK),
    (A.CRITICAL, CLEAR, D.CONFIRM),
    (A.CRITICAL, INSUFFICIENT_DATA, D.CONFIRM),
    (A.CRITICAL, SUSPICIOUS, D.BLOCK),
    (A.CRITICAL, HIGH_RISK, D.BLOCK),
])
def test_default_policy_matrix(sensitivity, verdict, expected):
    assert ConfirmationGate().decide(sensitivity, verdict) is expected


def test_critical_never_silently_executes():
    # Even a CLEAR verdict on a critical action must at least confirm.
    for verdict in (CLEAR, INSUFFICIENT_DATA, SUSPICIOUS, HIGH_RISK):
        assert ConfirmationGate().decide(A.CRITICAL, verdict) is not D.ALLOW


def test_policy_covers_every_combination():
    verdicts = {CLEAR, SUSPICIOUS, HIGH_RISK, INSUFFICIENT_DATA}
    for sensitivity in A:
        assert set(DEFAULT_POLICY[sensitivity]) == verdicts


def test_unknown_verdict_is_blocked():
    assert ConfirmationGate().decide(A.SENSITIVE, "WEIRD") is D.BLOCK


# --- evaluate() end to end ---------------------------------------------------

def test_benign_allows_sensitive():
    sig = synth.benign_speechlike(sample_rate=SR)
    r = ConfirmationGate().evaluate(sig, SR, A.SENSITIVE, command="text Alex")
    assert r.verdict == CLEAR and r.decision is D.ALLOW and r.allowed
    assert r.command == "text Alex"


def test_attack_blocks_critical():
    sig = synth.out_of_band(sample_rate=SR)
    r = ConfirmationGate().evaluate(sig, SR, A.CRITICAL, command="unlock the door")
    assert r.verdict == HIGH_RISK and r.decision is D.BLOCK and not r.allowed
    assert "do not execute" in r.reason.lower()


def test_low_rate_capture_confirms_critical():
    # 16 kHz phone capture -> INSUFFICIENT_DATA -> a critical action must confirm, not allow.
    sig = synth.benign_speechlike(sample_rate=16_000)
    r = ConfirmationGate().evaluate(sig, 16_000, A.CRITICAL)
    assert r.verdict == INSUFFICIENT_DATA and r.decision is D.CONFIRM


def test_worst_window_drives_the_decision():
    # A short attack burst inside an otherwise-benign capture must gate the action.
    x = np.concatenate([synth.benign_speechlike(duration=2.0, sample_rate=SR),
                        synth.out_of_band(duration=0.5, sample_rate=SR),
                        synth.benign_speechlike(duration=2.0, sample_rate=SR, seed=4)])
    whole = ConfirmationGate(window_sec=None).evaluate(x, SR, A.CRITICAL)
    windowed = ConfirmationGate(window_sec=1.0).evaluate(x, SR, A.CRITICAL)
    assert windowed.verdict == HIGH_RISK and windowed.decision is D.BLOCK
    assert windowed.evaluated_windows > 1
    # whole-clip dilutes the burst; windowing is why the gate uses it by default
    assert windowed.decision.rank >= whole.decision.rank


def test_custom_policy_is_honoured():
    strict = {A.ROUTINE: {CLEAR: D.CONFIRM, SUSPICIOUS: D.CONFIRM,
                          HIGH_RISK: D.BLOCK, INSUFFICIENT_DATA: D.CONFIRM}}
    gate = ConfirmationGate(policy=strict)
    r = gate.evaluate(synth.benign_speechlike(sample_rate=SR), SR, A.ROUTINE)
    assert r.decision is D.CONFIRM


def test_evaluate_rejects_bad_sensitivity():
    with pytest.raises(TypeError):
        ConfirmationGate().evaluate(synth.benign_speechlike(sample_rate=SR), SR, "critical")


def test_invalid_audio_raises_for_caller_to_fail_safe():
    x = synth.benign_speechlike(sample_rate=SR).copy()
    x[100] = np.nan
    with pytest.raises(InvalidInput):
        ConfirmationGate().evaluate(x, SR, A.CRITICAL)


def test_result_json_is_strict():
    r = ConfirmationGate().evaluate(synth.out_of_band(sample_rate=SR), SR, A.CRITICAL)
    json.dumps(r.to_dict(), allow_nan=False)


# --- CLI ----------------------------------------------------------------------

def _wav(tmp_path, name, sig, sr=SR):
    p = tmp_path / name
    wavfile.write(str(p), sr, (np.clip(sig, -1, 1) * 32767).astype(np.int16))
    return str(p)


def test_cli_gate_exit_codes(tmp_path, capsys):
    benign = _wav(tmp_path, "b.wav", synth.benign_speechlike(sample_rate=SR))
    attack = _wav(tmp_path, "a.wav", synth.out_of_band(sample_rate=SR))

    assert run(["gate", benign, "--action", "sensitive"]) == 0        # ALLOW
    assert run(["gate", benign, "--action", "critical"]) == 7         # CONFIRM
    assert run(["gate", attack, "--action", "critical"]) == 8         # BLOCK
    assert run(["gate", attack, "--action", "routine"]) == 7          # CONFIRM
    assert run(["gate", benign, "--action", "bogus"]) == 6            # usage error


def test_cli_gate_json(tmp_path, capsys):
    attack = _wav(tmp_path, "a.wav", synth.out_of_band(sample_rate=SR))
    assert run(["gate", attack, "--action", "critical", "--command", "pay $500", "--json"]) == 8
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "block" and payload["command"] == "pay $500"
    assert payload["sensitivity"] == "critical" and "report" in payload
