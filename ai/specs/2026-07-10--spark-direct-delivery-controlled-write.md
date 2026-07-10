# Spark Direct Delivery and Controlled Write Spec

## Problem

Read-only Spark helpers currently persist prompt, result, stderr, status, empty diff, task-card copy, manifest, and a report for every call. Most downstream consumers need only the result. Conversely, source writes are limited to `micro-builder`, with no exact-path permission profile for a specifically authorized small task.

## Desired Behavior

### Result delivery

- Add `--result-mode direct|minimal|full` and `CODEX_SPARK_RESULT_MODE`.
- Default to `direct` for read-only/advisory modes:
  - Spark stdout is returned unchanged to the caller/downstream process.
  - transient prompt/stderr/initialization files use a temporary directory and are removed on exit.
  - no permanent Spark artifact directory is created on a successful read-only call.
- `minimal` returns the result on stdout and persists only a compact `codex-spark.report.md` plus metadata needed for metrics/failure diagnosis; it does not duplicate the full prompt or raw result.
- `full` preserves the current artifact-rich behavior for audits and debugging.
- `--output` implies at least `minimal`, unless an explicit incompatible mode is rejected.
- Auto-disabled or failed calls may persist a compact report only in `minimal`/`full`; direct mode reports the reason on stderr.
- Source-writing modes always force `full` because their diff and boundary evidence must be reviewable.

### Controlled source permission

- Add explicit `controlled-builder` mode.
- It requires:
  - `--sandbox workspace-write`;
  - one to three repeated `--allow-write <repo-relative-path>` arguments;
  - `--max-diff-lines <n>`, with `1 <= n <= 200`;
  - task-card authorization for controlled-builder and source edits;
  - task-card evidence that public API, data model, security, migration, permission, concurrency, and cross-module contract risks are all excluded;
  - an existing-pattern/source-of-truth reference;
  - exact narrow validation.
- It always runs in a helper-created isolated worktree and forces `full` artifacts.
- After execution, the helper checks all changed/untracked paths against the exact allowlist, file count, and diff-line cap.
- Boundary violations return non-zero and are never applied to the source repository. The isolated worktree and evidence remain for review.
- Controlled-builder does not authorize merge, satisfy acceptance, or replace Codex review.

## Non-goals

- No Sol/Terra/Luna or other strong-model tier routing.
- No open-ended Spark implementation.
- No shared API, data model, security, permission, migration, concurrency, or cross-module changes.
- No automatic merge or source-worktree application.
- No daemon or cross-process quota state.

## Compatibility

- Existing `micro-builder` remains supported and artifact-rich.
- Explicit `--result-mode full` preserves current artifact names and report structure.
- Metrics parsers continue to read reports when reports are requested; direct calls without reports are intentionally observable only to their downstream caller.

## Acceptance

- Direct read-only invocation creates no permanent `.worktrees/codex-spark-*` directory and emits the Spark result to stdout.
- Minimal invocation writes only compact report/metadata and emits the result to stdout.
- Full invocation retains legacy prompt/result/report/evidence artifacts.
- Controlled-builder rejects missing authorization, missing allowlist, invalid caps, dirty source without override, changed paths outside allowlist, more than three changed files, and over-cap diffs.
- Controlled-builder accepts an in-bound isolated change while leaving the source repository untouched.
- Focused tests, shell syntax, installer propagation, and documentation checks pass.
