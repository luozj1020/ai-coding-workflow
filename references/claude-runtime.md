# Claude Runtime Policy

Load this reference for dispatch, execution-only mode, connection diagnosis, zero progress, timeouts, monitoring, retries, takeover, or report recovery.

## Dispatch Contract

Local routing facts and task-card components own the full audit card. Codex
reviews only material goal/boundary/acceptance fields. `balanced` dispatch renders
a compact Claude card while preserving `TASK_CARD_FULL.md`; use `safe` for
ambiguous work. Builder `auto` resolves to `execution-only` only when context is
sufficient; otherwise use `exploratory` for a bounded unclear implementation
path. Prefer one responsibility and measurable acceptance, but do not require
Codex to discover every exact file before Claude starts.

Builder implements and reports direction. Codex reviews direction. Checker/Test writes or runs assigned tests only after direction acceptance. Do not mix implementation, test writing, and broad validation unless the task card explicitly records `mixed-exception`. Non-blocking acknowledgement with `proceed` must continue editing in the same run.

## Failure Attribution

Run `ai/classify-claude-attempt.py` before retry/takeover accounting. Transport failure before acknowledgement/diff/report/progress is `transient-transport`: preserve the worktree, retry in place at most once, and do not count it toward takeover. Runtime metadata records the lineage root and retry ordinal. Ordinal one exhausts the same-worktree transport retry; a second transport failure must return `fallback-local-or-reroute` instead of recommending another retry. Approval/sandbox blockers, including an untrusted Claude workspace, also do not count. Acknowledgement-only, clean exit without progress, and confirmed direction deviation count.

Before classifying zero usable output as model no-progress, run one fixed interaction diagnostic in the same resolved route:

```bash
python ai/claude-healthcheck.py --interaction-route auto --timeout 60
```

Its fixed prompt is `你好`. The default `adaptive` mode performs this minimal
interaction only when no recent success is available for the same repository,
resolved route, probe environment, and Claude executable. A success is cached
for 24 hours by default (`CLAUDE_CODE_API_AVAILABILITY_TTL_SECONDS`) and useful
model-owned dispatch evidence refreshes it. Later zero output, socket/transport
symptoms, an inconclusive probe, or a changed execution context invalidates or
bypasses the cache and triggers a live probe. `always` remains an explicit
diagnostic mode; `failure-only` defers probing until suspicious terminal
evidence. Workspace-trust, socket, and CLI failures discovered by a live probe
stop before consuming the Builder window. Cached availability is attribution
evidence, not implementation or acceptance evidence. Restricted-sandbox
failure is inconclusive; a successful user-terminal interaction or dispatch is
authoritative. `CLAUDE_CODE_STARTUP_PREFLIGHT_REQUIRED=0` is an explicit
diagnostic override, not the normal workflow.

A restricted-sandbox interaction failure emits exit 75 plus
`needs_host_execution=true` before Builder execution. The outer Codex caller
must immediately replay the identical dispatcher command once through its
host-execution permission surface (for example,
`sandbox_permissions=require_escalated`) with
`CLAUDE_CODE_HOST_AUTHORITY=1` and the emitted
`CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID`. This preserves the same task card,
worktree, retry lineage, and session identity. The authorized dispatcher forces
the probe environment to `host` and removes the inherited sandbox marker; this
is only an assertion of an already-crossed boundary and never grants authority
by itself. Do not classify Claude as unavailable until this single host attempt
fails.

One failed Builder attempt is not takeover permission. Tighten and re-dispatch
once. Two consecutive current-lineage counted rounds issue a hash-bound
`*.takeover-receipt.json` candidate containing only the permitted write scope
and required validation. It is not write authority. Run `aiwf prepare-takeover`
to revoke (or explicitly declare absent) the Owner Lease, terminate and
identity-confirm every recorded Claude/dispatcher/checker process tree, sample a
stable worktree content hash, and issue the single-writer Codex grant. Unknown
process visibility fails closed. Transport, trust, approval, and sandbox
failures never contribute.

When useful on-plan work has exactly one semantic blocker, `aiwf advisor-continuation` may prepare a one-call same-worktree continuation. It does not invoke a model or dispatch by itself. Bind request/evidence, state hash, allowed and forbidden paths, and one-call idempotency.

Worktree continuity and model memory are separate. Initial dispatch assigns an explicit Claude session UUID. Retry-in-place, reviewed continuation, and advisor continuation resume that UUID from the prior runtime receipt when valid; otherwise runtime records `unavailable-file-backed-fallback` and starts a new named session. `--bare` disables auto-memory/customization, not explicit conversation persistence. Never describe file-only continuation as restored model memory.

## Progress and Monitoring

Execution-only, batch, and test-writing Checker tasks default to a 120-second first durable-output deadline with stop action. Generic planning, acknowledgement, timestamps, and claimed command starts do not satisfy it; a worktree delta or valid owned report does. Validation-only Checker work retains the ordinary observation policy. The later active window remains 600 seconds and may receive one 300-second semantic-growth extension; the 1500-second hard cap always wins.

After the Claude child exits, finalization waits one bounded drain interval and
rechecks the worktree. A late change triggers one additional stability sample
before diff/status/result capture. `CLAUDE_CODE_TERMINAL_DRAIN_SECONDS=0` is a
diagnostic/test override.

Builder progress also carries `Execution Phase`, `Implementation Complete`, `Assigned Tail Work`, `Tail Work Complete`, and `Completion Ready`. After implementation, Claude may run only the bounded self-review, narrow validation, documentation, and reporting explicitly assigned by the card's Post-Implementation Contract. It then marks `Completion Ready: yes`, writes the final report/result, and exits voluntarily without waiting for acknowledgement. The monitor reports `finish_recommended=yes` while awaiting that normal exit. A bounded self-review uses built-in Read/diff/search tools over changed files. No separate code-review plugin is assumed, and Claude's review never replaces Codex semantic review.

The dispatcher has a role-specific completion convergence path. When
`Completion Ready: yes` is paired with a valid owned report, no blocker, and
durable evidence (`solution-contract.draft.json` for Planner; a test diff or
validation-start evidence for Checker; both `Implementation Complete: yes` and
a product delta for Builder), the dispatcher grants
`CLAUDE_CODE_COMPLETION_READY_TIMEOUT_SECONDS` (default 20) for final output
flush, then identity-stops the child and records `completion_ready_converged`.
This is dispatch completion only, not validation success or semantic acceptance.
The standalone monitor still has no kill authority.
When the dispatcher stops a direct child, it freezes the remaining authenticated
process tree before terminating any leaf, preventing a parent shell from
resuming for one last write. Brokered calls instead cancel through the broker,
which terminates the model process group and records a terminal `cancelled`
ledger transition before the wrapper is reaped.

`Implementation Complete: yes` starts an independent tail/report window.
`CLAUDE_CODE_TAIL_TIMEOUT_SECONDS` defaults to 90; expiry stops the lingering
child, preserves and drains evidence, and records `tail-timeout`. A useful diff
with missing prose produces `<task-id>.recovered-completion.json` for bounded
Codex review rather than being discarded.

`Execution Phase: implementation` is an edit-readiness declaration, not durable progress. It is accepted only with `Context Acquisition Complete: yes` and a non-empty `Planned First Write`, meaning repository scanning, requirement understanding, and local planning are complete. The dispatcher grants a bounded edit-ready bridge (`CLAUDE_CODE_EDIT_READY_GRACE_SECONDS`, default 120) but refreshes the full active window only after product content changes or a valid owned report appears.

Before launch, the dispatcher freezes a full product-content baseline. Existing
dirty content in a reviewed continuation never counts as first progress; only a
content digest different from that approved baseline does. First progress and
product idle use the same full content hash. An unchanged digest for
`CLAUDE_CODE_PRODUCT_IDLE_TIMEOUT_SECONDS` (default 180) becomes an idle
candidate; `CLAUDE_CODE_PRODUCT_IDLE_CONFIRMATIONS` consecutive observations
(default 2) stop the child as `product_idle_confirmed`.

`solution-planner` progress uses `context`, `planning`, `contract-validation`, and `complete`. It must never report `implementation`, because planning progress is not implementation evidence.

Relevant overrides are `CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS`, `CLAUDE_CODE_TIMEOUT_SECONDS` (active window), `CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS`, and `CLAUDE_CODE_HARD_TIMEOUT_SECONDS`.

Approval-blocked early convergence requires two stable heartbeats by default. `CLAUDE_CODE_APPROVAL_CONVERGENCE_HEARTBEATS` may lower or raise that count for unusually slow filesystem environments or deterministic tests; production defaults remain conservative.

Do not spend Codex turns polling unchanged heartbeats. The dispatcher is the
default single sampling owner and appends only `started`, `material-change`,
`child-exited`, and finalized `terminal` boundaries to
`<task-id>.monitor-events.log`. An agent must issue one blocking
`monitor-claude.sh wait <task-id> --until terminal` call; repeated
`watch`, `ps`, `tail`, status, process-tree, or clock-only commands are forbidden. Read a bounded
decision/diff only after that wait returns.

Installed monitor helpers are versioned with the rest of the runtime. If an
older project copy does not recognize `wait --until`, run
`python ai/doctor_workflow.py`; it reports `ai/monitor-claude.sh` as outdated,
then refresh with the displayed `--update-workflow-files` command.

Do not start a detached monitoring supervisor. It duplicates dispatcher
sampling and is not part of the installed workflow. When `wait` reaches a
material or terminal boundary, it appends one compact local decision and may
invoke Spark `monitor-triage` to compress ambiguous evidence only when the local decision is `inspect` or
`interrupt-candidate`; Spark receives compact JSON rather than raw process
listings, logs, network tails, or diffs. Its diagnostic summary is capped at 240 characters and explicitly distinguishes edit readiness, durable writes, and confirmed product-idle duration. Codex receives that summary plus fixed decision fields; raw evidence remains file-backed. Stable `continue`, `terminal`, and
`visibility-unknown` states use no model call. `monitor-claude.sh decision`
provides the same one-shot path manually. Neither local monitoring nor Spark
authorizes interruption. Use
`status-claude.sh --details` only for exceptional diagnosis. If a restricted
sandbox cannot see PIDs without a terminal event, report `visibility-unknown`
from the dispatch environment and never launch a duplicate Builder.

Each dispatch writes `<task-id>.phase-metrics.json` with approximate heartbeat-observed context acquisition, implementation, validation, tail, and completion-ready timing. Use it to identify context reacquisition or post-implementation tail waste; do not treat sampled boundaries as provider billing timestamps.
It also writes `<task-id>.phase-events.jsonl` when the normalized phase or
current validation command changes. Phases are `exploring`, `editing`,
`validating`, and `reporting`; these Claude-authored signals are advisory and
never satisfy completion by themselves.

## Reports

Seeded/fallback reports are not Claude-owned completion. Missing reports may be reconstructed when the diff matches the card and assigned checks pass. The dispatcher runs `verify-claude-report.py`; changed-file/count/cleanliness claims are mandatory. Assigned tests additionally require a test diff and a claimed count that matches detected added test declarations. Assigned validation requires its exact command and exit code, but model-authored claims remain `claimed-unverified` until a deterministic receipt exists. A revision `RESOLVED` claim binds finding ID, changed file, symbol, and exact test name. Prose-only, missing, or contradictory claims produce `needs-review`.

Treat `<task-id>.outcome.json` as the terminal control-plane summary. Keep
`dispatch_success`, `artifact_valid`, `validation_success`, and
`semantic_acceptance` separate. A normal process exit can have
`completion_state=needs-review`; only Codex review can change semantic acceptance.
Checker ALL GREEN may supersede an earlier validation approval blocker, but it
never substitutes for semantic review.
`<task-id>.acceptance-bundle.json` is the compact review entry point for changed
paths, scope/report/validation gates, environment-failure classification, and a
recommended next decision. It is evidence-summary-only, always records
`merge_authorized=false`, and never replaces the underlying receipts.
