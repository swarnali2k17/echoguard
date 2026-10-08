# Confirmation gate

A reference integration for an action-taking voice agent. It sits at one point
in the agent's loop — between *"the ASR produced a command"* and *"the agent
executes it"* — and decides, from EchoGuard's verdict on the captured audio and
how sensitive the action is, whether to **allow**, **confirm**, or **block**.

```python
from echoguard import ConfirmationGate, ActionSensitivity, GateDecision

gate = ConfirmationGate()
result = gate.evaluate(signal, sample_rate, ActionSensitivity.CRITICAL,
                       command="unlock the front door")

if result.decision is GateDecision.BLOCK:
    agent.refuse(result.reason)
elif result.decision is GateDecision.CONFIRM:
    agent.step_up_confirm(result.reason)   # out-of-band: app tap, PIN, re-prompt
else:
    agent.execute()
```

Or from the CLI:

```bash
echoguard gate command.wav --action critical --command "unlock the front door"
# exit 0 = ALLOW · 7 = CONFIRM · 8 = BLOCK  (3 read error · 5 invalid audio · 6 usage)
```

## The decision

Two inputs: **how sensitive the action is** (the integrator classifies it) and
**EchoGuard's verdict** on the captured audio.

| Action sensitivity | Examples |
| --- | --- |
| `ROUTINE` | reversible, low-impact — weather, a timer, play music, lights |
| `SENSITIVE` | outbound or stateful — send a message, place a call, change a setting |
| `CRITICAL` | irreversible / high-impact — unlock, pay, transfer, delete, disarm |

The default policy (`DEFAULT_POLICY`, fully overridable):

| verdict ↓ / action → | ROUTINE | SENSITIVE | CRITICAL |
| --- | --- | --- | --- |
| **CLEAR** | allow | allow | **confirm** |
| **INSUFFICIENT_DATA** | allow | **confirm** | **confirm** |
| **SUSPICIOUS** | allow | confirm | **block** |
| **HIGH_RISK** | confirm | **block** | **block** |

Rationale for the non-obvious cells:

- **CRITICAL is never silently executed**, even on a CLEAR verdict — confirming an
  unlock or a payment is good practice regardless, and EchoGuard is not a
  guarantee.
- **INSUFFICIENT_DATA** ("couldn't check" — the verdict on any 16 kHz phone
  capture, because the ultrasonic band is below Nyquist) is treated as
  *unverified*, not *safe*: a sensitive or critical action is confirmed, never
  executed silently. See the deployment note below.
- **HIGH_RISK** confirms even a routine action, because a clear injection
  signature means someone may be driving the device.

## Windowing

By default the gate scores the clip in 1 s windows (0.5 s hop) and the **worst
window** drives the decision, so a short injection burst anywhere inside the
captured command gates the action instead of being averaged away by a
single whole-clip spectrum. Pass `window_sec=None` to score the whole clip.

## Deploying it honestly

- **It is only as good as the verdict.** The gate raises the bar for the
  injection classes EchoGuard can see — ultrasonic / near-ultrasound carriers in
  a capture that preserves the band. It does **not** catch in-band
  hidden/adversarial commands, laser or EM injection, or an attack whose carrier
  the device already filtered out. See `docs/threat_model.md`.
- **Capture rate matters.** On a 16 kHz capture every verdict is
  INSUFFICIENT_DATA, so under the default policy every sensitive/critical action
  is confirmed. If your device can capture at **≥ 44.1 kHz**, the gate can
  actually assess the ultrasonic band and only confirm when there is a reason to.
  An integrator stuck at 16 kHz who finds the confirmations too frequent should
  raise the capture rate, not relax the INSUFFICIENT_DATA row — relaxing it means
  silently executing sensitive actions on audio you could not check.
- **Defence in depth.** Use the gate as one signal among several — speaker ID,
  liveness, rate-limiting, out-of-band confirmation for high-value actions — never
  as a sole control.

## Customising the policy

Pass your own matrix (any subset of sensitivities; each must map all four
verdicts):

```python
from echoguard.gate import GateDecision as D
from echoguard.pipeline import CLEAR, SUSPICIOUS, HIGH_RISK, INSUFFICIENT_DATA

policy = {
    ActionSensitivity.CRITICAL: {
        CLEAR: D.CONFIRM, INSUFFICIENT_DATA: D.BLOCK,   # stricter: block if we can't check
        SUSPICIOUS: D.BLOCK, HIGH_RISK: D.BLOCK,
    },
    # ... ROUTINE, SENSITIVE ...
}
gate = ConfirmationGate(policy=policy)
```

A worked mock agent is in `examples/voice_agent_gate.py`.
