---
name: ai-coding-workflow
description: Durable Claude-owned Bookend workflow. Codex freezes intent, submits once, exits; returns only for semantic block, balanced checkpoint, or final review. Skip questions, read-only, tiny edits, and direct Skill maintenance.
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
executable; Codex does not need to discover implementation files or design the
implementation path.

Submit with:

```bash
python ai/aiwf.py submit TASK.json    # overnight: detached, Codex exits
python ai/aiwf.py balanced TASK.json  # balanced: foreground, dispatcher-managed window
```

**Overnight** returns `bookend-state.json`; Codex ends its episode. A detached
supervisor owns Claude convergence across multiple epochs. Codex returns only
for `review_ready` or `semantic_blocked`.

**Balanced** keeps Codex in foreground. The dispatcher manages the execution
window. When the tool returns, Codex does one bounded review.
Do not block on
`monitor-claude.sh`, poll Claude, perform direction review, dispatch Checker
through Codex, or interpret ordinary runtime transitions.

The control plane owns Claude exploration, implementation, assigned tests,
validation, revision, epochs, and sessions until convergence. These are
internal duties, not model handoffs.

## Wakeup Boundary

Only these states schedule Codex:

- `review_ready` — tools proved a stable `DONE_CANDIDATE` and constructed a
  coverage-preserving Review Projection. Codex performs one bounded final
  semantic review.
- `semantic_blocked` — completing the frozen contract requires a new semantic
  decision. Codex returns one bounded contract delta, then ownership returns to
  Claude.
- `checkpoint_ready` — (overnight balanced mode) review window expired; at most
  once per task. Codex returns `continue` / `continue_with_guidance` / `stop`.

Read the hash-bound wake request with:

```bash
python ai/aiwf.py bookend review-input BOOKEND_STATE_OR_DIR
```

Compile/test failures, unknown code locations, timeout, session/transport/report
recovery, scope checks, and mechanical revisions never wake Codex.
`runtime_blocked`, `authority_blocked`, `budget_exhausted`, and `cancelled` go
to the control plane or human.

## Ownership and Runtime Safety

Logical Claude ownership persists across epochs; process/write authority does
not. Before another epoch, revoke the old grant, identity-stop its process tree,
prove no active writer, and freeze a stable state. Unknown visibility fails
closed. Hard timeout expires an epoch, not the task. Models never merge;
high-impact authority remains human.

## Evidence and Review Projection

Tools establish typed facts; models make typed claims. Every changed byte has
exactly one Review Projection class. Gaps/overlaps invalidate it and expand the
semantic frontier. Codex reviews contract, semantic implications, and unresolved
risks—not routine runtime history.

## Compatibility Paths

`aiwf run` is the foreground compatibility lifecycle. `aiwf loop` /
`run-loop.sh` is the legacy per-iteration Codex-review loop. Neither is the
default agent path. `monitor-claude.sh` is an operator diagnostic, not a
synchronization mechanism.

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
| Retrieval and context budgets | `references/mcp-policy.md` |
| Setup/update/doctor | `references/setup-policy.md` |
| Metrics and pilots | `references/benchmark-policy.md` |

For installed command syntax, prefer `ai/README.md`.
