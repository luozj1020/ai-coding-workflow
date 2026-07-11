# Spec

## Title

dispatch latency and evidence convergence

## Problem

The workflow is reliable but wastes time on WSL/Windows filesystems and can publish contradictory evidence. Checker tasks create fresh worktrees by default, Claude may wait until timeout after a validation command is blocked by approval even though implementation/report evidence is complete, monitor tools conflate wrapper and Claude process state, plain watch output can omit a useful terminal machine snapshot, and final summaries may retain Claude's blocked validation even after the deterministic checker helper passes. Worktree creation is noisy and accumulated runtime artifacts lack actionable preview-only cleanup guidance.

## Desired Behavior

1. Low-risk `checker-test` task cards default to the managed reusable worktree when the caller did not explicitly choose a worktree strategy. Builder, mixed, high-risk, parallel, and explicit strategy runs retain their existing behavior. Reuse must preserve per-run evidence outside the managed worktree and require a clean/reset-safe managed path.
2. During the heartbeat loop, the dispatcher may end Claude early and enter the checker helper only when all of these are observed: task mode is `checker-test`; implementation/report work is complete; a valid Claude-owned report exists; the exact validation command is recorded as blocked specifically by approval/permission; no risky or unexplained source changes exist; the condition is stable for two heartbeats. The event is recorded as `approval_blocked_early_convergence`, not timeout or successful Claude validation.
3. PID artifacts distinguish dispatcher, Claude child/wrapper, and checker helper processes. Status/watch report each independently and derive overall running state without claiming a dead Claude child is running merely because the dispatcher is finalizing.
4. `watch-claude.sh --plain` always emits a final `machine:` line with `monitor_level`, `action`, `evidence_state`, `quiet_seconds`, and `suspect_count`, including fast exits and unchanged terminal snapshots.
5. Final aggregation exposes a validation timeline and authoritative final status. A passed checker helper supersedes Claude's earlier approval-blocked state while preserving both facts, e.g. `claude=blocked_by_approval`, `checker=ALL GREEN`, `final=passed`.
6. Worktree creation progress is quiet by default and reports a compact duration/file-count/result summary. A verbose environment switch may retain raw Git progress.
7. Doctor reports runtime entry count, approximate disk use, oldest entry, and preview-only cleanup suggestions grouped by age/task where practical. It never deletes by default.
8. Spark reports preserve both requested and resolved modes accurately.

## Non-Goals

- No automatic deletion or destructive reset of source repositories.
- No reuse for concurrent/parallel dispatches or high-risk Builder work.
- No broad inference that any validation failure is an approval blocker.
- No automatic acceptance or merge based on checker success.
- No model-tier routing or strong-model fallback.

## Acceptance Surface

- [ ] Checker default-reuse tests cover eligible, explicit override, high-risk, parallel, dirty-managed, and evidence isolation cases.
- [ ] Early-convergence tests cover positive two-heartbeat detection and negative cases: Builder mode, invalid/seeded report, incomplete work, generic failure, risky changes, and one transient heartbeat.
- [ ] PID/status/watch tests distinguish process roles and guarantee a terminal plain machine line.
- [ ] Summary tests prove checker helper success becomes authoritative without erasing Claude's blocked state.
- [ ] Worktree output tests prove default quiet compact output and opt-in verbose behavior.
- [ ] Doctor tests prove preview-only runtime inventory and no deletion.
- [ ] Spark requested/resolved mode regression passes.
- [ ] Installer propagation and focused/full regressions pass.

## Constraints

- Existing environment overrides remain authoritative.
- Reuse applies only when task-card risk gates make eligibility deterministic.
- Early convergence must be opt-out configurable and default conservatively.
- Runtime parsing treats Claude-authored text as untrusted input; no `eval`/`source`.
- Evidence artifacts remain per-task even when the execution worktree is reused.
- Shell scripts remain Bash-compatible with current Linux/WSL support.

## Alternatives Considered

1. Keep fresh worktrees and only shorten timeouts: rejected because filesystem creation remains the dominant Checker cost.
2. Reuse worktrees for every task: rejected because it weakens isolation and conflicts with parallel/high-risk execution.
3. Detect approval text once and terminate immediately: rejected because a transient report update could be mistaken for completion. Two stable heartbeats plus valid evidence is safer.
4. Overwrite Claude's report after checker success: rejected; aggregation should preserve provenance and compute a separate authoritative final status.

## Risks and Unknowns

- Exact approval-blocked phrases emitted by Claude vary; detection should prioritize structured report/progress fields and a bounded allowlist of phrases.
- Reusing one managed Checker worktree serializes eligible Checker dispatches. Parallel/DAG runs must stay fresh.
- File count for compact creation output may be expensive in very large repositories; allow `unknown` or use already-available Git metadata.

## Plan Derivation

<!-- Link generated plan/task-card artifacts when available. -->

| Artifact | Path |
|----------|------|
| Plan | `ai/plans/dispatch-latency-evidence/task_plan.md` |
| Task cards | `ai/plans/dispatch-latency-evidence/task-cards/` |

## Implementation record

- Spark `plan-splitter` auto-disabled because local app-server initialization required unavailable helper write access; no strong-model fallback was used.
- Three independent Builders ran through the reviewed parallel plan. Summary required a semantic revision; doctor first attempt produced no useful implementation and succeeded after a tightened retry; dispatcher was split after the first card proved too broad.
- Summary Checker made no implementation progress twice because Python execution required approval. Codex scoped takeover was limited to validation-state tests and the missing blocked-state semantics; focused pytest passed.
- Dispatcher Checker timed out with a useful but oversized test diff. Codex salvaged the accepted parser/output direction, replaced non-asserting tests with bounded integration tests, and preserved timeout evidence.
- Documentation Builder and tightened revision both missed one stale task-card sentence; Codex takeover changed only that sentence after the repeated-miss threshold.
- Final implementation keeps automatic merge disabled and preserves human review/merge ownership.
