# Routing and Spark Policy

Load this reference for pre-task-card ownership routing, Spark invocation, Spark failures, Spark diagnostics, or Spark source-writing modes.

## Pre-Card Route

Run the deterministic owner route before an execution artifact. `claude-first`
is the default profile: source-writing routes to Claude, while Codex freezes
intent and reviews bounded semantic evidence. `economy-first` restores the
strict positive delegation gate for users optimizing single-task latency or
total model usage. Invoke Spark `execution-cost-estimator` when structured
estimation can replace Codex analysis. `preflight-bundle` is diagnostic.

Run `aiwf route` before Spark when deterministic facts are already available.
Its `precard_estimator.spark_action=skip` means owner estimation is complete; it
does not require wasting an available Spark budget. For a non-Express Claude
route, use that call as `task-card-audit`. Express, zero-budget, explicit
`spark_gate=off`, unavailable Spark, or an audit already bound to the same card
hash may skip it.
Recovery always re-routes from fresh current facts and never inherits an earlier
estimate. An explicit deterministic owner may still skip Spark; otherwise a
concrete uncertain Claude candidate can request one bounded estimate.

For continuation, consume a granted Owner Lease before considering a switch.
Mechanical revision and test-fix leases favor the original Builder; a same-owner
new session is allowed only after recorded resume failure. `route-task.py`
accepts the lease's `selected_model` as `continuation_owner` only with
`continuation_eligible=true`.

Communication-aware routing may apply a Handoff Tax calibration only when its
source is `observed-calibration`, status is `calibrated`, and the configured
minimum sample count is met. Missing or model-produced estimates remain
`unknown`/`canary` and cannot override deterministic facts or explicit human
ownership.

Under `claude-first`, the economy record optimizes Codex work: single-task
elapsed time is advisory, the default Codex-work reduction target is 15%, and
missing estimates do not push implementation back to Codex. Under
`economy-first`, delegation still requires at least 15% cost saving, active time
at most 2.0x direct execution, and at least 30% less Codex work. Explicit human
ownership remains authoritative.

Claude-first work gets one initial execution and one useful-evidence continuation
before takeover review. Transport or approval failure does not consume the model
failure allowance and should reuse the same worktree when safe.

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

Size is not sufficient. Prefer Claude whenever it can own implementation,
revision, assigned tests, or long validation and return a compact evidence
boundary. Prefer Codex direct only for explicit human ownership, confirmed
high-risk core semantics, or a deterministic reviewer-owned correction already
fully in Codex context.

Record `expected_delegated_cost_ratio`, `expected_active_elapsed_ratio`,
`expected_codex_work_reduction_ratio`, `codex_review_scope=sampled|bounded|full`,
and `delegation_value=yes|no`. If review scope is `full` and no other reduction is
present, route to Codex even when the line estimate exceeds an ordinary small-task
gate, provided scope and solution remain bounded. Repository scale affects this
judgment because context reacquisition and worktree costs rise with project size.

Claude roles are `solution-planner`, `exploratory-builder`, `batch-builder`, and
`execution-builder`. Use one structured planner for a large open feature, freeze
the contract after one Codex review, then return its slices to Claude. Use
`exploratory-builder` when the goal and boundary are stable but the implementation
path is unclear; it must create source changes and evidence rather than prose.
A prose plan, repository summary, unverified bug list, or document summary is
not completion.

Default read-only routing to local tools, Spark, or Codex. A read-only Claude
call is eligible only when it creates a durable structured result and is expected
to remove at least 30% of Codex work. Bug scanning therefore needs a reproduction
test, patch candidate, or executable structured issue artifact; document parsing
needs a checked-in index/configuration/generated asset or equivalent downstream
input. Do not pay Claude merely to hand prose back to Codex.

Select `solution-planner` only for a multi-phase, multi-module, or large-repository
feature whose goal is clear but implementation path remains open. It must write a
validated `solution-contract.draft.json`, not a prose summary, and the expected
reduction in Codex planning work must be at least 30%. Codex performs exactly one
adversarial planning review. The deterministic solution-contract helper freezes
the accepted end state, invariants, acceptance IDs, and slices. After freeze,
implementation routing runs separately for each slice; neither model may reopen
the whole plan unless new evidence invalidates a blocking invariant.

## Codex Quota Hotspots

The scarce-cost path is duplicated Codex semantic context, not downstream-model volume:

1. broad repository discovery or loading several policy references;
2. reading a monolithic template and authoring a long card;
3. reviewing full progress/status/log tails during execution;
4. rereading a large Claude diff semantically;
5. writing another full revision card and repeating Claude context acquisition;
6. dispatching Checker when deterministic evidence already closes acceptance.

Avoid those costs with bounded locators, one on-demand reference, component card
composition, local persistent monitoring, delta-only same-worktree continuation,
and conditional Checker dispatch. In `claude-first`, accept slower productive
execution when it removes Codex planning, editing, polling, or full-diff rereview.
Run separate projects in separate user terminals; the Skill does not orchestrate
portfolio parallelism.

The lower-level efficient control plane defaults to 45 seconds, 24 KiB for the
composed task card, 64 KiB for a standalone Context Packet, and 80 KiB combined.
The integrated runner instead inlines bounded context into its single short card.
Exceeding a bound
writes `recompose-before-dispatch` and makes no Claude call. Override these only
with a reviewed `control_plane_policy`; human approval wait is not control-plane
execution time.

## Spark Roles

Prefer explicit read-only modes when the role is known: `observe-synthesizer`, `task-card-drafter`, `context-packet-builder`, `task-card-audit`, `plan-splitter`, `validation-planner`, `failure-triage`, `direction-precheck`, `acceptance-matrix`, `postflight-bundle`, `revision-drafter`, `lesson-extractor`, `evidence-checker`, `parallel-planner`, or `monitor-triage`. `monitor-triage` accepts only the deterministic compact monitor JSON, returns a bounded advisory decision, and cannot authorize interruption. `auto` is value-triggered and may skip with `skip.no_expected_decision_value`.

Spark is advisory. It cannot satisfy acceptance, approve final review, authorize
merge, or silently fall back to a stronger model. Use it for structured route,
card-field, monitoring, and terminal-evidence compression when that avoids a
Codex read. Every revised card starts with deterministic ROUTE; invoke Spark only
when its structured answer replaces work Codex would otherwise perform. The
default available-quota policy performs one `task-card-audit` for each
non-Express initial or revision card. Pass only a bounded advisory to Claude;
it cannot alter scope, acceptance, ownership, or authority.
Observed Handoff Tax and a valid Owner Lease make continuity deterministic, so
Spark is skipped even when requested. With insufficient history, Spark may
advise task shape only; it is never an authoritative Handoff Tax source.

Treat routing, Claude monitoring, and failure triage as one
structured control plane. `spark_control_protocol.py` normalizes legacy
`key=value` output into `spark-route-decision-v1`,
`spark-monitor-decision-v1`, `spark-failure-decision-v1`, or
`spark-parallel-decision-v1`. Every object is capped, evidence-hashed, marked
`advisory_only=true`, and validated locally. Monitor decisions always force
`interrupt_authorized=false`; failure decisions cannot authorize takeover;
parallel decisions remain legacy within-repository compatibility artifacts and
never coordinate user terminals. Invalid output degrades to local/Codex review rather than prose
reinterpretation. Downstream helpers consume the normalized object directly and
avoid persisting successful advisory prose.

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
  Control-plane modes also append `spark_decision_json=<compact JSON>`.
  `aiwf spark-output` validates this field and exposes it as
  `structured_decision`; use `aiwf spark-decision KIND [FILE] --compact` to
  normalize a legacy result explicitly.
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
