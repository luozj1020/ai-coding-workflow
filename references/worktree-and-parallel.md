# Worktree and Parallel Policy

Load this reference for dirty-source restoration, large-repository worktree performance, interrupted runs, checker reuse, or parallel dispatch.

## Worktree Safety

Dirty source or stale HEAD blocks reliable delegation but is not a Codex takeover trigger. Restore a clean current base by committing an accepted phase, stashing/patching source changes, refreshing workflow files, re-dispatching from current HEAD, requesting explicit dirty-source authority, or stopping for human input.

Default to complete evidence. For large repositories, explicitly select `fast-large-repo`, `reuse-managed`, `CLAUDE_CODE_LARGE_REPO_MODE=1`, or summary evidence only after recording the tradeoff. `reuse-managed` may reuse only `.worktrees/reuse/claude-managed`; reset it only with `CLAUDE_CODE_REUSE_WORKTREE_RESET=1` after preserving or reviewing evidence. Never reset the source repository.

After interruption, use `python ai/clean_runtime.py --task-id ...` to preview or remove only that run's stopped artifacts. Preserve useful dirty isolated worktrees for same-worktree continuation or review. Ensure `.worktrees/*` is ignored while `.worktrees/.gitkeep` remains trackable; local-only installs may use `.git/info/exclude`.

When the dirty diff is useful and Codex has reviewed and accepted its direction, prefer an explicit reviewed continuation over another fresh checkout. Run `aiwf reviewed-continuation prepare` with the prior task id, exact accepted-existing paths, next-role card, and allow-new-write paths; then dispatch with `CLAUDE_CODE_REVIEWED_CONTINUATION=<approval.json>`. The approval binds source/base/worktree HEAD, full worktree state, path content/mode, and next-card hash. The dispatcher consumes it once, reuses the exact worktree without reset/clean/checkout, archives prior control files, and enforces new-write boundaries after execution. Builder→Builder and Builder→Checker are supported; Checker→Builder, managed/advisor/retry/parallel origins, state drift, live prior PIDs, and replay fail closed. This is an explicit Codex review path, never an automatic dirty-worktree reuse policy.

Checker worktree reuse requires every Checker Reuse Risk Gate row to be explicit `no`. Missing/unknown/high risk, DAG, parallel, or shared-contract work stays fresh. Environment overrides remain explicit.

## Parallel Dispatch

Parallel execution is a legacy within-repository compatibility tool, not a
portfolio feature or default route. The Skill never coordinates projects or
terminals; the user runs one repository workflow per terminal. Invoke
`assess-parallel-opportunity.py` only for an explicit same-repository experiment.

Before execution, review and save the strict schema-v1 DAG. Every card must declare the same real Base commit matching current `HEAD`, non-overlapping write scopes, independent owned contracts, and validation ownership. Shared API, data model, migration, security, permission, global configuration, or overlapping paths require serial work or explicit human-approved reconciliation.

Use maximum concurrency 2 by default. The scheduler starts only dependency-ready tasks, skips transitive dependents after prerequisite failure, and lets unrelated branches continue. Review every diff and evidence packet serially; merge remains human-controlled. `--allow-overlap` is a manual-reconcile escape hatch, not permission to bypass base, contract, or validation checks.

Default dispatch is progressive: run one ready canary alone, execute its declared narrow validation with the local checker helper, and release the remaining ready units only after that gate passes. Every later unit is also helper-validated; dispatcher exit zero without an available worktree or passing validation is incomplete, not success. `--no-ramp-up` and `--no-unit-validation` are diagnostic overrides and must be recorded in benchmark evidence.
