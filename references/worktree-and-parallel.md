# Worktree and Parallel Policy

Load this reference for dirty-source restoration, large-repository worktree performance, interrupted runs, checker reuse, or parallel dispatch.

## Worktree Safety

Dirty source or stale HEAD blocks reliable delegation but is not a Codex takeover trigger. Prefer a clean accepted base. When the current tracked/untracked working state is intentionally the execution baseline, use `bash ai/dispatch-to-claude.sh CARD --dirty-source-mode snapshot`. Authority for that baseline must be explicit, but may already be established by the user's instruction or a frozen reviewed task card; once recorded, do not request a second business-content confirmation for the same hash-bound snapshot. First-time host/sandbox execution authority remains a separate platform boundary. The stable CLI preserves the trusted launcher prefix across an authorized host retry; `CLAUDE_CODE_DIRTY_SOURCE_MODE=snapshot` remains compatibility-only. The dispatcher builds a commit from a temporary Git index, never changes source index/HEAD, excludes untracked task/control inputs, creates the fresh isolated worktree from that commit, and writes `*.dirty-snapshot.json` with base/tree/commit/path hashes. Snapshot mode is limited to one fresh non-DAG dispatch and never authorizes merge. Legacy `CLAUDE_CODE_ALLOW_DIRTY_SOURCE=1` retains hash comparison through `dispatch-preflight.py` and still blocks missing/different task-relevant paths. Reading dirty source by absolute path while writing to a stale fresh worktree remains forbidden.

Runtime identity keeps `source_base_commit` separate from
`execution_base_commit`. They are equal for a clean fresh run; a dirty snapshot
uses the original source HEAD for the former and the synthetic snapshot commit
for the latter. Retry-in-place and advisor continuation require the current
source HEAD to match `source_base_commit`, require worktree HEAD to match
`execution_base_commit`, and inherit that execution baseline without creating a
second snapshot. Source working-copy dirty state after the initial snapshot is not
silently imported into the continuation.

The current source worktree and the workflow runtime root are also separate
identities. The dispatcher resolves the runtime root from Git's common dir and
stores every fresh execution worktree and adjacent receipt as a direct child of
that one top-level `.worktrees/`. Dispatching while the shell is inside an
accepted linked worktree must therefore create a sibling, never
`source/.worktrees/child`. Fresh creation fails closed unless its target is the
expected direct child. A task card may be read from outside the current source
worktree (for example, the main worktree's reviewed `ai/plans/` copy); the
dispatcher hashes and copies it into the execution worktree without adding it
to the accepted source baseline. Runtime identity records both
`source_repository` and `runtime_repository_root`.

Default to complete evidence. For large repositories, explicitly select `fast-large-repo`, `reuse-managed`, `CLAUDE_CODE_LARGE_REPO_MODE=1`, or summary evidence only after recording the tradeoff. `reuse-managed` may reuse only `.worktrees/reuse/claude-managed`; reset it only with `CLAUDE_CODE_REUSE_WORKTREE_RESET=1` after preserving or reviewing evidence. Never reset the source repository.

CodeGraph indexes and results from the source worktree do not automatically transfer to a fresh execution worktree. After worktree creation the dispatcher writes a CodeGraph identity receipt. A mismatch or pending index defaults to deterministic local fallback and graph output is excluded from execution evidence. Set `CLAUDE_CODE_CODEGRAPH_POLICY=repair` only when explicitly accepting the index/sync cost; continuation may reuse a `ready` index in the same worktree.

After interruption, use `python ai/clean_runtime.py --task-id ...` to preview or remove only that run's stopped artifacts. `--mark-cleanup-eligible` writes a state-bound receipt only when the run is terminal, its worktree HEAD is merged into current HEAD, and no modified or untracked product path remains. Apply recomputes the worktree status, repository/worktree HEADs, terminal-receipt hash, and process identity; a missing or stale receipt preserves the complete task bundle rather than deleting adjacent recovery evidence. `.session-store`, archived evidence, and control snapshots are lifecycle-managed stores and never generic cleanup candidates. A lineage session store is removed only after an eligible worktree removal proves no other lineage worktree remains. Use `--json` for a machine-readable preview. Preserve useful dirty isolated worktrees for same-worktree continuation or review. Ensure `.worktrees/*` is ignored while `.worktrees/.gitkeep` remains trackable; local-only installs may use `.git/info/exclude`.

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

When the dirty diff is useful and Codex has reviewed and accepted its direction,
prefer an explicit reviewed continuation over another fresh checkout. `Mode =
revision` is natively treated as a Builder continuation, including a reviewed
Builder-to-Checker transition; the runtime receipt and task-card fallback use
the same role normalization. A narrow Checker-to-Checker continuation is also
supported when the runtime records a valid Claude session UUID; it must resume
that same session and remain in the Checker role. Checker-to-Builder remains
fail closed. The continuation helper reads raw Task JSON, rendered
`aiwf-execution-card-v1` metadata, or legacy Markdown; role and Builder mode do
not depend on a Markdown table. It inherits the prior Builder mode, verified
tool profile, and Context Lease lineage unless the new machine-readable card
requires a compatible transition. By default it writes the one-use approval
under the common `.worktrees/continuations/` control directory and returns a
copyable `dispatch_command`; an approval path inside the product worktree is
rejected because it would invalidate the approved state hash. The approval
binds the baseline content hash, prior role and session, and exact new Write
paths. On supported hosts the
dispatcher runs Claude inside a read-only-root sandbox with writable binds only
for those exact paths and control reports, so Edit/Write/Bash cannot create
forbidden siblings. Required enforcement fails closed when paths are globbed or
the sandbox capability is unavailable; post-run checking remains secondary
evidence. A Codex takeover marker permanently blocks later Claude continuation
on that worktree.
After each reviewed continuation, a later continuation may be prepared again
from the latest runtime only after Codex reviews the new state. Every approval
is one-use and rebinds the current content hash, task-card hash, role/session,
and exact paths; runtime `reuse_count` increases across the lineage.

When an Acceptance Graph is available, prepare continuation with a validated
revision `--delta-review-packet`, bounded `--unresolved-finding` values, and
immutable `--new-validation-ref sha256:...` evidence. The approval records only
the baseline hash, selected acceptance IDs, new diff/test refs, unresolved
findings, and new validation refs; it explicitly records that the full prior
task card was not repeated. Packet hash, worktree state, and next-card drift
continue to fail closed.

Every productive dispatch also attempts to emit `<task-id>.scoped.patch` and
`<task-id>.scoped-handoff.json` beside the worktree. The patch is computed from
`execution_base_commit` and contains only receipt-approved product paths,
including new files. Unexpected paths block the handoff. In dirty-snapshot mode
the manifest records both source and execution baselines and explicitly warns
that the whole worktree cannot be merged; the human may run only the listed
`git apply --check` and `git apply` commands after reviewing the patch. No model
is authorized to apply or merge it.

Checker worktree reuse requires every Checker Reuse Risk Gate row to be explicit `no`. Missing/unknown/high risk, DAG, parallel, or shared-contract work stays fresh. Environment overrides remain explicit.

## Parallel Dispatch

Parallel execution is a legacy within-repository compatibility tool, not a
portfolio feature or default route. The Skill never coordinates projects or
terminals; the user runs one repository workflow per terminal. Invoke
`assess-parallel-opportunity.py` only for an explicit same-repository experiment.

Before execution, review and save the strict schema-v1 DAG. Every card must declare the same real Base commit matching current `HEAD`, non-overlapping write scopes, independent owned contracts, and validation ownership. Shared API, data model, migration, security, permission, global configuration, or overlapping paths require serial work or explicit human-approved reconciliation.

Use maximum concurrency 2 by default. The scheduler starts only dependency-ready tasks, skips transitive dependents after prerequisite failure, and lets unrelated branches continue. Review every diff and evidence packet serially; merge remains human-controlled. `--allow-overlap` is a manual-reconcile escape hatch, not permission to bypass base, contract, or validation checks.

Default dispatch is progressive: run one ready canary alone, execute its declared narrow validation with the local checker helper, and release the remaining ready units only after that gate passes. Every later unit is also helper-validated; dispatcher exit zero without an available worktree or passing validation is incomplete, not success. `--no-ramp-up` and `--no-unit-validation` are diagnostic overrides and must be recorded in benchmark evidence.
