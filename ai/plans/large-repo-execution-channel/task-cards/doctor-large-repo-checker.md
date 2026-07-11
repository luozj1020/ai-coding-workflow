# Task Card

## ID

doctor-large-repo-execution-diagnostics-checker

## Task Mode

| Field | Value |
|---|---|
| Mode | checker-test |

## Claude Context Packet

| Field | Value |
|---|---|
| Target files/modules | `tests/test_doctor_workflow.py` only |
| Relevant behavior | missing documented helpers, `--hash-path` bounds/path validation/hash mismatch, read-only wording, risk-gated large-repo messages |
| Do not read / do not modify | implementation and all other files |
| Narrow validation commands | `python -m pytest -q tests/test_doctor_workflow.py` |

## Goal

Add focused deterministic tests for doctor diagnostics. Do not modify implementation.

## Required Tests

- positional repo invocation remains compatible and repeated `--hash-path` works;
- absolute/traversal/missing/directory/outside-symlink and >20 paths reject;
- matching hash reports target-only match; mocked or controlled mismatch with empty status reports `possible stat-cache/index mismatch` and never invokes mutating git commands;
- missing runtime helpers include dispatcher/status/watch/checker/Spark/parallel;
- large repo messaging is conditional, uses correct execution-only/retry variables, and says target-only does not prove global cleanliness.

## Validation Contract

```validation
python -m pytest -q tests/test_doctor_workflow.py
```

Local validation allowed? yes.

## Acceptance Criteria

- Only doctor tests change.
- No network or real Claude.
- Focused suite passes or reports exact failure evidence.
