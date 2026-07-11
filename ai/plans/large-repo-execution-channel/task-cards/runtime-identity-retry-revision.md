# Task Card

## ID

large-repo-runtime-identity-retry-revision

## Task Type

normal

## Executor

Claude Code

## Task Mode

| Field | Value |
|---|---|
| Mode | builder |
| Builder scope | revise `scripts/dispatch-to-claude.sh` only; no tests/docs/templates |
| Checker/Test scope | separate later task |
| Codex direction review required before checker/test? | yes |
| Mixed implementation + test-writing allowed in one Claude dispatch? | no |

## Worktree / Large Repo Strategy Gate

| Field | Value |
|---|---|
| Repository size concern? | no for this workflow repository |
| Worktree strategy | fresh |
| Large repo read mode | off |
| Evidence tradeoff accepted? | no |
| Safety boundary | never reset/clean source or retry target |

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

## Direction / Boundary Acknowledgement

| Field | Value |
|---|---|
| Required before editing? | no |
| Blocking Codex approval required? | no |
| Maximum acknowledgement rounds | 0 |
| Stop if implementation boundary unclear? | yes, report blocker |

## Claude Context Packet

| Field | Value |
|---|---|
| CodeGraph status | not indexed |
| Target files/modules | `scripts/dispatch-to-claude.sh` only |
| Relevant symbols/functions | `validate_retry_in_place`, runtime/task identity setup, artifact declarations, final cleanup |
| Reference examples / source of truth | commit `7582db3`; spec `ai/specs/2026-07-11--large-repository-execution-channel-optimization.md` |
| Do not read / do not modify | status/watch scripts, tests, templates, docs, other scripts |
| Known constraints | set -u; old evidence must never be overwritten; retry target has dispatcher control files |
| Narrow validation commands | `bash -n scripts/dispatch-to-claude.sh` |
| Context is sufficient for execution? | yes |

## Goal

Correct the reviewed defects in the retry-in-place implementation without expanding scope.

## Required Revisions

1. Compute current source HEAD before retry validation. Do not reference an unset `BASE_COMMIT`.
2. A retry must receive a new unique `TASK_ID` and therefore new result/status/PID/runtime evidence paths. `CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID` identifies only the prior runtime/worktree. Never overwrite prior artifacts.
3. Clean-state validation must allow only the known dispatcher control files at the retry worktree root (`TASK_CARD.md`, `TASK_CARD_FULL.md`, `CLAUDE_TASK_CARD.md`, `CLAUDE_PROMPT.md`, seeded/owned `CLAUDE_REPORT.md`, `CLAUDE_PROGRESS.md` as applicable). Reject every other untracked file. Tracked/staged source changes remain a hard rejection.
4. Prevent two new dispatchers from concurrently claiming the same retry target. Use an atomic, task-scoped reservation under `.worktrees/` or equivalent. Release it on normal exit and trapped error/signal. A live/existing reservation must fail closed; do not delete another process's reservation.
5. Preserve prior branch/worktree HEAD. Do not run worktree add/reset/clean/checkout for retry-in-place.
6. Runtime JSON for the new run records strategy `retry-in-place`, new task id, actual prior worktree path, base commit, and `retry_of` prior task id. Source status and final output show the same provenance.
7. Keep child-exit log behavior and existing fresh/managed behavior intact.

## Acceptance Criteria

- Shell syntax passes.
- No artifact path for the new run equals the prior run's artifact prefix.
- A legitimate no-diff prior run with only known control files is eligible.
- Any implementation diff, unknown untracked file, stale HEAD, live PID, managed strategy, unsafe path, or competing reservation is rejected.
- Only `scripts/dispatch-to-claude.sh` changes.

## Testing Responsibility

Builder runs shell syntax only. Checker will add regression tests.

## Required Report

Start editing after the bounded read. Update `CLAUDE_PROGRESS.md`; return acceptance mapping and deviations in `CLAUDE_REPORT.md`.
