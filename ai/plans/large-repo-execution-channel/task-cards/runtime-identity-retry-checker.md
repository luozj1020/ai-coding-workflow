# Task Card

## ID

large-repo-runtime-identity-retry-checker

## Task Type

normal

## Executor

Claude Code

## Task Mode

| Field | Value |
|---|---|
| Mode | checker-test |
| Builder scope | complete in commits `7582db3`, `859887a` |
| Checker/Test scope | add focused regression tests, run assigned validation, report; only tiny concrete implementation fixes if a test exposes one |
| Codex direction review required before checker/test? | yes, completed |
| Mixed implementation + test-writing allowed in one Claude dispatch? | no |

## Checker Reuse Risk Gate

| Risk Row | Value |
|---|---|
| Public API risk | no |
| Data model risk | no |
| Security risk | no |
| Migration risk | no |
| Permission risk | no |
| Concurrency risk | no |
| Cross-module risk | no |
| Production impact | no |

## Claude Context Packet

| Field | Value |
|---|---|
| CodeGraph status | not indexed |
| Target files/modules | `tests/test_dirty_source_guard.py`, `tests/test_install_workflow.py`; implementation only if a focused test exposes a tiny defect |
| Relevant behavior | runtime.json emission; managed worktree path resolution; fallback diagnostics; unique retry evidence; retry safety rejection; child-exit log |
| Do not read / do not modify | unrelated tests/docs/templates/scripts |
| Narrow validation commands | `python -m pytest -q tests/test_dirty_source_guard.py tests/test_install_workflow.py` |
| Context is sufficient for execution? | yes |

## Goal

Prove the runtime identity and retry-in-place contract with deterministic fake-Claude/temp-repository tests.

## Required Tests

- Fresh dispatch writes valid runtime JSON with actual worktree/source/base/branch/PID paths.
- Status and watch resolve a managed worktree via runtime JSON; missing/malformed/unsafe artifacts retain fallback with a diagnostic.
- Retry gets a new task id/artifact prefix and reuses the prior worktree without invoking worktree add/reset/clean/checkout.
- Retry accepts only known control files and rejects source diff, unknown untracked, stale source HEAD, live role PID, managed prior strategy, unsafe/missing runtime, and competing reservation. Use representative cases without duplicating every shell branch if setup cost is high.
- Progress log includes distinct child-exit-to-finalization transition.

## Validation Contract

```validation
python -m pytest -q tests/test_dirty_source_guard.py tests/test_install_workflow.py
```

Local validation allowed? yes.

## Acceptance Criteria

- Tests are deterministic and do not call real Claude/network.
- Existing focused suites pass.
- Checker report maps coverage and any remaining gaps.
- No broad implementation rewrite.
