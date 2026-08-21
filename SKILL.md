---
name: ai-coding-workflow
description: Durable Claude-owned Bookend workflow. Codex freezes intent, submits once, exits; returns only for semantic block or final review. Skip questions, read-only, tiny edits, and direct Skill maintenance.
---

# AI Coding Workflow

## Applicability Gate

Classify first: **bypass** for questions/read-only/tiny work (record
`workflow bypassed: <reason>`); **direct** for bounded edits and Skill
maintenance (`aiwf direct --reason ... --path ...`); **delegated** when durable
Claude ownership materially reduces Codex work; or **setup/update**.

## Bookend Contract

The production model is asynchronous:
`GROUND -> FREEZE -> SUBMIT -> CONVERGE -> PROJECT -> REVIEW`.

Codex freezes the goal, acceptance, invariants, forbidden boundaries,
validation authority, and concrete risk facts. Freeze ends when the contract is
executable.

Submit with:

```bash
python ai/aiwf.py submit TASK.json    # overnight: detached, Codex exits
python ai/aiwf.py balanced TASK.json  # balanced: foreground, dispatcher window
```

**Overnight** returns `bookend-state.json`; Codex ends its episode. A detached
supervisor owns Claude convergence. Codex returns only for `revision_pending`
or `semantic_blocked`. On `revise`, Claude continues in the same worktree.

**Balanced** keeps Codex in foreground. The dispatcher manages the execution
window. When the tool returns, Codex does one bounded review.

## Execution Profiles

| Profile | Command | Control | Codex calls |
|---|---|---|---|
| **overnight** | `aiwf submit TASK.json` | Detached supervisor | ~2 |
| **balanced** | `aiwf balanced TASK.json` | Foreground dispatcher window | ~3 |
| **interactive** | Codex + `aiwf_*` subagents | Native orchestration | Internal |

## Wakeup Boundary

Only these states schedule Codex:

- `revision_pending` — `DONE_CANDIDATE` proved; Codex performs one bounded
  review and returns `accept` or `revise`. On `revise`, Claude continues in
  the same worktree with a bounded Revision Delta.
- `semantic_blocked` — completing the frozen contract requires a new semantic
  decision. Codex returns one bounded contract delta, then ownership returns to
  Claude.

Read the hash-bound wake request with:

```bash
python ai/aiwf.py bookend review-input BOOKEND_STATE_OR_DIR
```

Compile/test failures, unknown code locations, timeout, session/transport/report
recovery, scope checks, and mechanical revisions never wake Codex.

## Interactive Profile

When using Codex-native subagents, delegate to `aiwf_*` agents:
`aiwf_explorer` (parallel read-only), `aiwf_worker` (implementation),
`aiwf_tester` (validation), `aiwf_debugger` (failure investigation),
`aiwf_build_fixer` (compilation), `aiwf_benchmarker` (performance),
`aiwf_reviewer` (code review, stronger model). Keep main thread as planner.

## Ownership and Runtime Safety

Logical Claude ownership persists across epochs; process/write authority does
not. Before another epoch, revoke the old grant, identity-stop its process tree,
prove no active writer, and freeze a stable state. Unknown visibility fails
closed. Hard timeout expires an epoch, not the task. Models never merge;
high-impact authority remains human.

## Evidence and Review Projection

Tools establish typed facts; models make typed claims. Every changed byte has
exactly one Review Projection class. Gaps/overlaps invalidate it and expand the
semantic frontier.

## Compatibility Paths

`aiwf run` is the foreground compatibility lifecycle. `aiwf loop` is the
legacy per-iteration Codex-review loop. Neither is the default agent path.

## Reference Router

Load only the reference for the current operation; do not load multiple
references speculatively.

| Operation | Reference |
|---|---|
| Bookend roles, states, evidence | `references/operating-model.md` |
| Contract and Task JSON | `references/task-card-policy.md` |
| Claude epochs, recovery, single writer | `references/claude-runtime.md` |
| Review Projection and Codex decisions | `references/review-policy.md` |
| Worktrees and continuation | `references/worktree-and-parallel.md` |
| Setup/update/doctor | `references/setup-policy.md` |

For command syntax, prefer this Skill package's `README.md`. If the repo has
been bootstrapped (`ai/aiwf.py` exists), use `ai/README.md` instead. The
absence of `ai/` in an unbootstrapped repo is expected, not an error.
Overnight/balanced require bootstrap; interactive/direct do not.
