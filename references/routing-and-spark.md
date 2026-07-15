# Routing and Spark Policy

Load this reference for pre-task-card ownership routing, Spark invocation, Spark failures, Spark diagnostics, or Spark source-writing modes.

## Pre-Card Route

Before every full initial, revision, narrow, retry, re-dispatch, split-child, or next-phase task card, send Spark a current 20–40 line brief with `execution-cost-estimator` or `preflight-bundle`. Identify the event with `--routing-event`. An earlier estimate is context, not a reusable owner decision. Deterministic Express/tiny work may skip with `skip.sized_tiny_fastpath`; record all disable, budget, and availability skips.

Calibrate Spark's upper line estimate by 1.5 normally and 2.0 for tests/fixtures, shell/process orchestration, or cross-platform work. Actual edits may exceed the estimate while scope, solution, and required context remain stable.

Use `repository-scale.py` gates:

| Scale | Ordinary Codex | Concentrated core semantics |
|---|---:|---:|
| small | 100 lines / 2 files | no expansion |
| medium | 100 / 2 | 250 / 3 |
| large | 150 / 3 | 500 / 5 |
| giant | 200 / 3 | 500 / 5 |

Historical median worktree setup of at least 120 seconds promotes the routing profile one level. Concentrated routing additionally requires local/bounded context, high solution clarity and semantic concentration, high Claude reacquisition cost, full Codex rereview, delegated/direct work ratio at least 1.5, and complete estimator fields.

Classify tightly coupled behavior/architecture as `core-semantic`; classify tests/checker work, mechanical batches, long validation/log/evidence work, and independent support units as `auxiliary`. In large/giant repositories, auxiliary work above one file or 50 calibrated lines favors Claude. Risk changes rigor and may bias only toward Codex; it must never push ownership from Codex to Claude.

## Spark Roles

Prefer explicit read-only modes when the role is known: `observe-synthesizer`, `task-card-drafter`, `context-packet-builder`, `task-card-audit`, `plan-splitter`, `validation-planner`, `failure-triage`, `direction-precheck`, `acceptance-matrix`, `postflight-bundle`, `revision-drafter`, `lesson-extractor`, `evidence-checker`, or `parallel-planner`. `auto` is value-triggered and may skip with `skip.no_expected_decision_value`.

Spark is advisory. It cannot replace Claude ownership implicitly, satisfy acceptance, approve final review, authorize merge, or silently fall back to a stronger model. Recommend at most three short helpers per task, excluding mandatory later pre-card re-estimates.

## Results and Failure

- `direct`: stdout only; no permanent successful artifact.
- `minimal`: stdout plus compact report.
- `full`: prompt, result, stderr, status, diff, task card, and manifest.

Source-writing modes force `full`. Diagnostics default to `failure`, persisting only a compact redacted record for unusable direct results; `off` persists nothing and `full` preserves reproduction evidence. Missing CLI/model/auth/network/quota or helper initialization auto-disables optional Spark without strong-model fallback. `--require-spark` makes failure hard.

When `CODEX_SANDBOX_NETWORK_DISABLED=1` is inherited, `--execution-env auto` fails before a model call. Do not probe. Use `--execution-env host` only through an already-authorized outside-sandbox boundary; merely unsetting the marker inside a restricted sandbox is not a bypass.

## Controlled Writing

Use `micro-builder` only for explicitly authorized tiny isolated work. Use `controlled-builder` only with 1–3 exact `--allow-write` paths, required `--max-diff-lines` of 1–200, an existing source-of-truth pattern, exact narrow validation, and explicit exclusion of public API, data, security, migration, permission, concurrency, and cross-module contract risks. Boundary violations remain isolated, exit non-zero, and never modify the source repository, merge, or satisfy acceptance.
