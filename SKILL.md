---
name: ai-coding-workflow
description: Run or maintain a durable Claude-owned Bookend coding workflow in which Codex freezes intent, submits once, exits, and returns only for a semantic block or final review. Skip questions, read-only work, tiny/urgent edits, and direct Skill maintenance unless durable delegation is explicitly requested.
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
python ai/aiwf.py submit TASK.json
```

The command returns `bookend-state.json`; Codex then ends the current episode.
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
closed.

Hard timeout expires an epoch, not the task. Continue only with a deterministic
`continuation_safe=true` receipt. Budget exhaustion is not semantic. Models
never merge; high-impact authority remains human.

## Evidence and Review Projection

Tools establish typed facts; models make typed claims. Claude is not the source
of truth for hashes, paths, commands, exit codes, scope, or validation.

Every changed byte has exactly one Review Projection class. Gaps/overlaps
invalidate it and expand the semantic frontier. Codex reviews contract,
semantic implications, and unresolved risks—not routine runtime history.

## Compatibility Paths

`aiwf run` is the foreground compatibility lifecycle. `aiwf loop` /
`run-loop.sh` is the legacy per-iteration Codex-review loop. Neither is the
default agent path and neither should be selected merely because it already
exists. `monitor-claude.sh` remains an operator diagnostic for standalone
legacy dispatches; it is not a Codex synchronization mechanism.

## Reference Router

Load only the reference for the current operation; do not load multiple
references speculatively.

| Operation | Reference |
|---|---|
| Bookend roles, states, evidence | `references/operating-model.md` |
| Contract and Task JSON | `references/task-card-policy.md` |
| Claude epochs, recovery, single writer | `references/claude-runtime.md` |
| Review Projection and Codex decisions | `references/review-policy.md` |
| Compatibility synchronous loop | `references/loop-model.md` |
| Worktrees and continuation | `references/worktree-and-parallel.md` |
| Retrieval and context budgets | `references/mcp-policy.md` |
| Setup/update/doctor | `references/setup-policy.md` |
| Metrics and pilots | `references/benchmark-policy.md` |
| User-triggered feedback | `references/feedback-policy.md` |

For installed command syntax, prefer `ai/README.md`.
