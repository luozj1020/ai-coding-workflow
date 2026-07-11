# Task Card

## ID

large-repo-runtime-identity-retry-builder

## Task Type

normal

## Executor

Claude Code

## Task Mode

| Field | Value |
|---|---|
| Mode | builder |
| Builder scope | implementation only; do not add or edit tests |
| Checker/Test scope | separate later task |
| Codex direction review required before checker/test? | yes |
| Mixed implementation + test-writing allowed in one Claude dispatch? | no |

## Spec Gate

| Field | Value |
|---|---|
| Spec required? | yes |
| Spec path | `ai/specs/2026-07-11--large-repository-execution-channel-optimization.md` |
| Spec status | reviewed by Codex |

## Phase Responsibility Matrix

| Phase | Owner |
|---|---|
| OBSERVE / PLAN | Codex |
| BUILDER EXECUTE | Claude |
| DIRECTION / FINAL REVIEW | Codex |
| TEST WRITING / VALIDATION | later Checker Claude |
| MERGE | human |

## Worktree / Large Repo Strategy Gate

| Field | Value |
|---|---|
| Repository size concern? | yes; runtime inventory is large, but this repository itself is small enough for one fresh Builder worktree |
| Worktree strategy | fresh |
| Large repo read mode | off |
| Evidence tradeoff accepted? | no; preserve full diff |
| Safety boundary | never reset or clean source repository |
| Worktree progress mode | quiet |

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
| Required before editing? | no; execution contract is complete |
| Blocking Codex approval required? | no |
| Maximum acknowledgement rounds | 0 |
| Stop if implementation boundary unclear? | yes; write blocker to `CLAUDE_PROGRESS.md` and `CLAUDE_REPORT.md` |

## Claude Context Packet

| Field | Value |
|---|---|
| CodeGraph status | not indexed |
| Target files/modules | `scripts/dispatch-to-claude.sh`, `scripts/status-claude.sh`, `scripts/watch-claude.sh` |
| Relevant symbols/functions | dispatcher artifact path declarations and worktree creation; status/watch `PREFIX` and `WORKTREE_DIR` resolution; Claude child heartbeat/wait/finalization |
| Reference examples / source of truth | role PID artifacts already emitted by dispatcher; spec above |
| Do not read / do not modify | tests, templates, docs, installer assets, unrelated scripts |
| Known constraints | Linux/WSL/Git-for-Windows shell compatibility; old runs without new artifact must still work |
| Narrow validation commands | `bash -n scripts/dispatch-to-claude.sh scripts/status-claude.sh scripts/watch-claude.sh` (Builder may run this syntax check only) |
| Context is sufficient for execution? | yes |
| Escalate before broad search if | required behavior needs files outside the three targets |

## Goal

Make runtime worktree identity explicit, make monitor helpers consume it, and add a safe opt-in retry-in-place path for the exact prior task worktree.

## Handoff Contract

Implement only these behaviors:

1. Dispatcher writes `${WORKTREE_ROOT}/${TASK_ID}.runtime.json` (or an equally simple machine-readable identity artifact) as soon as actual runtime identity is known. Include schema version, task id, actual absolute worktree path, strategy, branch, base commit, source repository, and role PID artifact paths. Write atomically when practical.
2. `status-claude.sh` and `watch-claude.sh` resolve live report/progress/worktree evidence from this artifact. Validate that the recorded worktree is inside the same repository's `.worktrees/` boundary. If artifact is missing, malformed, or unsafe, retain the existing task-id fallback and emit a compact diagnostic field.
3. Add explicit retry-in-place input, preferably `CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID=<prior-task-id>`. It may reuse only that prior run's recorded fresh worktree (not arbitrary paths and not the shared managed worktree) when: no dispatcher/Claude/checker PID is live; recorded source repository matches; recorded base commit equals current source HEAD; worktree HEAD equals the recorded base; and tracked/staged/untracked implementation state is clean apart from known dispatcher control files. On success do not call `git worktree add`, `git reset`, `git clean`, or checkout. On any ambiguity fail closed with an actionable error.
4. Log child exit detection and immediate transition to finalization distinctly. Do not introduce extra waiting after the Claude child is not running. Preserve checker execution and dispatcher-only finalization semantics.
5. Record retry provenance and runtime identity path in source status and final printed artifact paths.

## Acceptance Criteria

- Managed reuse monitor state uses the actual managed worktree path.
- Fresh runs remain backward compatible.
- Retry-in-place never resets/cleans and rejects dirty, stale, live, mismatched, missing, or managed prior runs.
- A dead Claude child moves directly into finalization; dispatcher/checker may still legitimately remain running.
- No tests/docs/templates are modified in this Builder phase.
- `bash -n` passes for the three scripts.

## Testing Responsibility

Builder may run only the listed shell syntax check. A separate Checker task will add focused regression tests and run them.

## Required Report

Update `CLAUDE_PROGRESS.md` after each completed behavior. Return `CLAUDE_REPORT.md` with touched files, acceptance mapping, syntax check, deviations, and remaining risks. Continue directly to edits; do not spend a round restating the plan.
