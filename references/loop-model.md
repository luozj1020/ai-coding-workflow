# Legacy Synchronous Loop

This reference describes compatibility behavior only. The production workflow
is the asynchronous Bookend model in `references/operating-model.md`.

## Why This Is Not the Default

The historical loop bound runtime transitions to model handoffs:

```text
Builder -> Codex Direction Review -> optional Checker -> Codex Final Review
```

That shape repeatedly serializes context, wakes scarce Codex inference for
execution events, and binds Claude wall time to one Codex response lifetime.
It remains available for controlled A/B experiments and old Markdown task
cards, but new tasks must not enter it merely because `run-loop.sh` exists.

## Compatibility State Machine

```text
OBSERVE -> ROUTE -> PLAN/DIRECT -> EXECUTE -> VERIFY -> REVIEW
                                                     |
                                                     +-- accept
                                                     +-- revise
                                                     +-- split
                                                     `-- reject
```

`aiwf loop` invokes this flow and may call Codex after each Claude iteration.
Those calls must be labelled as legacy synchronization in telemetry.

## Production Replacement

Use:

```bash
python ai/aiwf.py submit TASK.json
```

The submitter freezes the contract and exits. The durable control plane owns
Claude exploration, implementation, tests, revisions, validation, runtime
recovery, and execution epochs. It schedules a new Codex episode only for
`review_ready` or strict `semantic_blocked`.

Foreground diagnostics may still use one blocking `monitor-claude.sh wait`,
but a Codex agent must not do so after Bookend submission. Runtime state
transitions are not model synchronization points.

## Experimental Use

When comparing this loop with Bookend, report production Codex calls/tokens
separately from shadow audit calls. Do not infer counterfactual savings merely
from stage labels; follow `codex-wakeup-audit-protocol-v1.md` and retain
`indeterminate` classifications.
