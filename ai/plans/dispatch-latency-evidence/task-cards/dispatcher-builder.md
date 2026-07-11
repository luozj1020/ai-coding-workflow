# Task: dispatcher latency convergence Builder

## Task Mode

| Field | Value |
|---|---|
| Mode | builder |
| Local validation allowed? | yes, `bash -n scripts/dispatch-to-claude.sh` only |

## Goal

Implement spec items 1, 2, and 6 in `scripts/dispatch-to-claude.sh` only.

## Scope

- Parse task mode and explicit risk rows before choosing defaults.
- When no explicit `CLAUDE_CODE_WORKTREE_STRATEGY` is supplied, select `reuse-managed` only for serial low-risk `checker-test` cards. Parallel/DAG, Builder/mixed/control-plane, missing/ambiguous mode, or any public API/data/security/migration/permission/concurrency/cross-module/production risk stays `fresh`.
- Preserve caller override. Reuse must keep per-task evidence under `.worktrees/` and must not silently reset an existing managed worktree unless the existing explicit reset contract permits it.
- Add conservative opt-out-configurable approval-blocked early convergence. Require checker-test mode, valid non-seeded Claude report, completion evidence, an allowlisted approval/permission validation blocker, safe scoped changes, and the same qualifying fingerprint for two heartbeats before terminating Claude and continuing to checker helper.
- Record `approval_blocked_early_convergence` distinctly from timeout/success.
- Quiet Git worktree progress by default and emit compact duration plus result/path; add an opt-in verbose environment flag.

## Boundaries

- Edit only `scripts/dispatch-to-claude.sh`.
- Do not change tests, docs, status/watch, checker helper, or summarizer.
- Do not execute broad tests.
- Do not weaken dirty-source, managed-reuse reset, parallel isolation, or evidence rules.

## Acceptance

- Existing explicit environment overrides retain priority.
- No model-provided text is evaluated as shell.
- Positive convergence needs two stable heartbeats; all missing/ambiguous evidence fails closed.
- `bash -n scripts/dispatch-to-claude.sh` passes.
