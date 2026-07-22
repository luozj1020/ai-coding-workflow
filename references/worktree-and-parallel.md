# Worktree and Parallel Policy

Load this reference for dirty-source restoration, large-repository worktree performance, interrupted runs, checker reuse, or parallel dispatch.

## Worktree Safety

Dirty source or stale HEAD blocks reliable delegation but is not a Codex takeover trigger. Prefer a clean accepted base. When the current tracked/untracked working state is intentionally the execution baseline, explicitly set `CLAUDE_CODE_DIRTY_SOURCE_MODE=snapshot`. The dispatcher builds a commit from a temporary Git index, never changes source index/HEAD, excludes untracked task/control inputs, creates the fresh isolated worktree from that commit, and writes `*.dirty-snapshot.json` with base/tree/commit/path hashes. Snapshot mode is limited to one fresh non-DAG dispatch and never authorizes merge. Legacy `CLAUDE_CODE_ALLOW_DIRTY_SOURCE=1` retains hash comparison through `dispatch-preflight.py` and still blocks missing/different task-relevant paths. Reading dirty source by absolute path while writing to a stale fresh worktree remains forbidden.

Default to complete evidence. For large repositories, explicitly select `fast-large-repo`, `reuse-managed`, `CLAUDE_CODE_LARGE_REPO_MODE=1`, or summary evidence only after recording the tradeoff. `reuse-managed` may reuse only `.worktrees/reuse/claude-managed`; reset it only with `CLAUDE_CODE_REUSE_WORKTREE_RESET=1` after preserving or reviewing evidence. Never reset the source repository.

CodeGraph indexes and results from the source worktree do not automatically transfer to a fresh execution worktree. After worktree creation the dispatcher writes a CodeGraph identity receipt. A mismatch or pending index defaults to deterministic local fallback and graph output is excluded from execution evidence. Set `CLAUDE_CODE_CODEGRAPH_POLICY=repair` only when explicitly accepting the index/sync cost; continuation may reuse a `ready` index in the same worktree.

After interruption, use `python ai/clean_runtime.py --task-id ...` to preview or remove only that run's stopped artifacts. Preserve useful dirty isolated worktrees for same-worktree continuation or review. Ensure `.worktrees/*` is ignored while `.worktrees/.gitkeep` remains trackable; local-only installs may use `.git/info/exclude`.

Before source dirty-state classification, recognized untracked root control
files are hash-snapshotted under `.worktrees/control-archive/<task-id>/` and
excluded from the dirty blocker. Originals are retained. Arbitrary task cards,
nested files, tracked controls, and user files are never silently ignored or moved.

Retry and cleanup liveness checks consume `*.process.json` identity receipts and match PID, process start time, command-line hash, PID namespace, task ID, and role. `kill -0 <pid>` alone is accepted only for legacy runs without an identity receipt; it cannot distinguish PID reuse or a host/container namespace collision.

Before opening a fresh same-owner session, run the ownership selector. A lease
with `session.mode=resume-required` is not execution authority; first attempt to
resume the recorded session. Only `resume_status=failed` permits a new
same-owner session. Switching owners inherently creates a new session and must
carry the lease's explicit switch reason.

The dispatcher records `claude_session_id`, session mode, prior task, and resume
status in runtime evidence. Same worktree alone preserves files/diff only; it
does not prove conversation memory. A valid `--resume <uuid>` invocation is the
model-session continuity evidence.

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
