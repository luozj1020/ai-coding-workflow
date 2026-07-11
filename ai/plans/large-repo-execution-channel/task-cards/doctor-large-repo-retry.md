# Task Card

## ID

doctor-large-repo-execution-diagnostics-retry

## Task Mode

| Field | Value |
|---|---|
| Mode | builder |

## Claude Context Packet

| Field | Value |
|---|---|
| Target files/modules | `scripts/doctor_workflow.py` only |
| Relevant symbols/functions | `WORKFLOW_REQUIRED_FILES`, `run_doctor`, large-repo findings, `main` |
| Do not read / do not modify | every other file including tests/assets |
| Narrow validation commands | `python -m py_compile scripts/doctor_workflow.py` |

## Goal

Implement the doctor core only. Start editing after reading the named sections; do not restate a plan.

## Required Changes

- Report missing documented runtime helpers distinctly and reuse workflow refresh guidance.
- Preserve positional repo argument and add repeatable `--hash-path` (max 20), rejecting absolute/traversal/missing/directory paths.
- For only those paths compare filesystem `git hash-object --no-filters`, index `git rev-parse :path`, and scoped porcelain status. Warn when hashes differ while status is empty. Never mutate; label target-only scope and say renormalize is never automatic.
- Change tracked-count messaging so fast-large-repo/reuse is conditional on low risk, exact targets, serial safety, and accepted evidence reduction; otherwise fresh/full. Mention execution-only and retry-in-place eligibility.

## Acceptance Criteria

- Only `scripts/doctor_workflow.py` changes.
- Standard-library/read-only behavior and old invocation remain compatible.
- `python -m py_compile scripts/doctor_workflow.py` passes.

## Testing Responsibility

Builder runs py_compile only; Checker will add tests.

## Required Report

Return acceptance mapping and any unimplemented item. Edit immediately.
