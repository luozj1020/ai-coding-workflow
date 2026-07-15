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

For `large` and `giant` repositories, a separate full-rereview economy gate
defaults to 800 calibrated lines / 8 files. It applies only to bounded
`core-semantic` work with high clarity and concentration, medium/high context
reacquisition, mandatory full Codex rereview, medium/high estimator confidence,
an explicit `codex-fast-path` economic recommendation, and delegated total work
not lower than direct work. Override the bounded ceiling with
`CODEX_FULL_REREVIEW_FAST_PATH_MAX_DIFF_LINES` (100–2000) and
`CODEX_FULL_REREVIEW_FAST_PATH_MAX_FILES` (1–10). Auxiliary and mixed work never
uses this expansion.

Classify tightly coupled behavior/architecture as `core-semantic`; classify tests/checker work, mechanical batches, long validation/log/evidence work, and independent support units as `auxiliary`. In large/giant repositories, auxiliary work above one file or 50 calibrated lines favors Claude. Risk changes rigor and may bias only toward Codex; it must never push ownership from Codex to Claude.

## Delegation Value Gate

Size is not sufficient. Prefer Codex direct work whenever Codex already holds the
exact implementation context and delegation would still require full semantic
rereview. Delegate only when at least one expected saving is explicit: Claude
avoids substantial Codex context acquisition, performs a mechanical/independent
batch, owns assigned test creation, runs long validation/evidence processing, or
allows Codex to review a bounded interface/sample instead of the full edit.

Record `expected_codex_work_reduction`, `codex_review_scope=sampled|bounded|full`,
and `delegation_value=yes|no`. If review scope is `full` and no other reduction is
present, route to Codex even when the line estimate exceeds an ordinary small-task
gate, provided scope and solution remain bounded. Repository scale affects this
judgment because context reacquisition and worktree costs rise with project size.

## Codex Quota Hotspots

The costly path is usually duplicated semantic context, not the edit itself:

1. broad repository discovery or loading several policy references;
2. reading a monolithic template and authoring a long card;
3. reviewing full progress/status/log tails during execution;
4. rereading a large Claude diff semantically;
5. writing another full revision card and repeating Claude context acquisition;
6. dispatching Checker when deterministic evidence already closes acceptance.

Avoid those costs with bounded locators, one on-demand reference, component card
composition, local persistent monitoring, the full-rereview economy route,
delta-only revision cards, and conditional Checker dispatch. Delegation has value
only when it removes more Codex context/review work than its control plane adds.

## Spark Roles

Prefer explicit read-only modes when the role is known: `observe-synthesizer`, `task-card-drafter`, `context-packet-builder`, `task-card-audit`, `plan-splitter`, `validation-planner`, `failure-triage`, `direction-precheck`, `acceptance-matrix`, `postflight-bundle`, `revision-drafter`, `lesson-extractor`, `evidence-checker`, `parallel-planner`, or `monitor-triage`. `monitor-triage` accepts only the deterministic compact monitor JSON, returns a bounded advisory decision, and cannot authorize interruption. `auto` is value-triggered and may skip with `skip.no_expected_decision_value`.

Spark is advisory. It cannot replace Claude ownership implicitly, satisfy acceptance, approve final review, authorize merge, or silently fall back to a stronger model. Recommend at most three short helpers per task, excluding mandatory later pre-card re-estimates.

## Results and Failure

- `direct`: bounded stdout only; no permanent successful artifact. Results up
  to `CODEX_SPARK_STDOUT_MAX_BYTES` (default 32768) pass through. Oversized
  estimator-family results are reduced to recognized machine fields; other
  modes retain a bounded head/tail. The wrapper appends
  `spark_output_truncated` and byte-count fields.
  Direct stdout is an `aiwf-spark-stdout-v1` envelope: it emits
  `spark_status=started` before the model call and ends with one terminal status
  (`success`, `failed`, or `unavailable`) plus `spark_protocol_end`. Parse it
  with `aiwf spark-output [FILE] --require-terminal`. A started-only envelope
  is not a usable Spark result.
- `minimal`: stdout plus compact report.
- `full`: prompt, result, stderr, status, diff, task card, and manifest.

Source-writing modes force `full`. Diagnostics default to `failure`, persisting only a compact redacted record for unusable direct results; unique diagnostic directories prevent same-second evidence overwrite. `off` persists nothing and `full` preserves reproduction evidence. Every optional direct failure also emits `spark_status=unavailable` and related machine fields on stdout, so exit 0 can never mean silent absence. Missing CLI/model/auth/network/quota or helper initialization auto-disables optional Spark without strong-model fallback. `--require-spark` makes failure hard.

When `CODEX_SANDBOX_NETWORK_DISABLED=1` is inherited, `--execution-env auto` emits a machine-readable `needs_host_execution` handoff before a model call. Do not probe. Direct callers use `--execution-env host` only through an already-authorized outside-sandbox boundary. `dispatch-efficient.py --host-authority` (or `CODEX_SPARK_HOST_AUTHORITY=1`) lets that already-authorized outer caller retry the identical preflight exactly once; bound it with `--host-retry-timeout` / `CODEX_SPARK_HOST_RETRY_TIMEOUT`. The dispatcher records both attempts in `spark-dispatch.json`, terminates the retry process tree on timeout, and continues without a stronger-model fallback. Without explicit authority it records the pending handoff and continues. Merely unsetting the marker inside a restricted sandbox is not a bypass.

The model-call broker owns the process timeout. The default
`CODEX_SPARK_CALL_TIMEOUT_SECONDS=75` terminates the child process group,
records a failed terminal ledger transition, and lets the wrapper emit its
terminal envelope. Outer timeouts should be longer than this internal bound.

## Controlled Writing

Use `micro-builder` only for explicitly authorized tiny isolated work. Use `controlled-builder` only with 1–3 exact `--allow-write` paths, required `--max-diff-lines` of 1–200, an existing source-of-truth pattern, exact narrow validation, and explicit exclusion of public API, data, security, migration, permission, concurrency, and cross-module contract risks. Boundary violations remain isolated, exit non-zero, and never modify the source repository, merge, or satisfy acceptance.
