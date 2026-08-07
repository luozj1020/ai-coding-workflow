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

A standard Builder card that already contains the complete write boundary,
acceptance, validation, stop-condition, and report contracts automatically uses
a bounded **bootstrap capsule** by default. This does not change Builder mode,
tool profile, or authority: it removes only Codex-only/audit sections and proves
that every present hard-contract section survived byte-identically. Incomplete
or exploratory cards keep their legacy execution view rather than guessing
missing requirements. Set `CLAUDE_CODE_AUTO_BOOTSTRAP_CAPSULE=0` only for a
compatibility diagnosis.

Builder implements and reports direction. Codex reviews direction. Checker/Test writes or runs assigned tests only after direction acceptance. Do not mix implementation, test writing, and broad validation unless the task card explicitly records `mixed-exception`. Non-blocking acknowledgement with `proceed` must continue editing in the same run.

## Failure Attribution

Run `ai/classify-claude-attempt.py` before retry/takeover accounting. Transport failure before acknowledgement/diff/report/progress is `transient-transport`: preserve the worktree, retry in place at most once, and do not count it toward takeover. Runtime metadata records the lineage root and retry ordinal. Ordinal one exhausts the same-worktree transport retry; a second transport failure must return `fallback-local-or-reroute` instead of recommending another retry. Approval/sandbox blockers, including an untrusted Claude workspace, also do not count. Acknowledgement-only, clean exit without progress, confirmed direction deviation, and a report/product role mismatch with zero product delta count. A successful interaction followed by context timeout and zero durable product output is model no-progress even when retry budget is exhausted.

Two counted receipts can form a takeover candidate only across one explicit
`retry-in-place` edge. The builder binds both classifications to the same
lineage root, source/execution base, source repository, physical worktree,
task-card hash, and Claude session UUID. A reviewed/advisor continuation,
Context Lease, changed card/base/worktree, or any fresh session resets the
failure count. This includes the automatic fresh-session fallback after
`session-not-found`: it may preserve the task and worktree, but it cannot
inherit a prior conversation's takeover count.

Before classifying zero usable output as model no-progress, run one fixed interaction diagnostic in the same resolved route:

```bash
python ai/claude-healthcheck.py --interaction-route auto --timeout 60
```

Its fixed prompt is `你好`. The default `adaptive` mode performs this minimal
interaction only when no recent success is available for the same repository,
resolved route, probe environment, and Claude executable. A success is cached
for 24 hours by default (`CLAUDE_CODE_API_AVAILABILITY_TTL_SECONDS`) and useful
model-owned dispatch evidence refreshes it. The probe uses Claude's stream-init
event to record the actual runtime tool inventory, bound to the requested tool
profile. For an explicit non-default profile, this comparison occurs during the
early connectivity probe, before a full worktree is created. A mismatch writes
complete result/outcome receipts with `builder_started=false` and
`worktree_created=false`. A missing required tool fails before Builder execution; when Bash and
exact-write enforcement are both present, missing Edit/Write may resolve only
to the receipt-validated exact-writer fallback. Later zero output, socket/transport
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
`sandbox_permissions=require_escalated`) using the stable CLI form:

```bash
bash ai/dispatch-to-claude.sh <task-card> \
  --execution-env host \
  --dirty-source-mode snapshot \
  --tool-profile minimal-builder \
  --preflight-task-id <task-id>
```

Include `--dirty-source-mode snapshot` only when the handoff receipt names
snapshot mode. `--preflight-task-id` preserves identity when the early probe
stops before any worktree or session exists. A later transport handoff from an
existing worktree continues to use `--retry-in-place-task-id`; reviewed
continuation uses `--reviewed-continuation <approval-path>`. A selected fixed
capability set is preserved with `--tool-profile`; do not prepend
`CLAUDE_CODE_TOOL_PROFILE`. Legacy
`CLAUDE_CODE_HOST_AUTHORITY=1` and
continuation/dirty-source selector environment variables remain compatible, but the CLI
shape is preferred because it matches the narrow persistent launcher approval.
The receipt marks `host_retry_args_authoritative=true` and the environment map
as legacy; outer orchestration must reconstruct the command from the CLI args.
This preserves every identity that already exists without manufacturing a
worktree solely for a failed connectivity probe. The authorized dispatcher forces the probe environment to `host`
and removes the inherited sandbox marker; this
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

For `model-no-progress`, `acknowledgement-only`, or
`report-evidence-mismatch`, Codex may first freeze a revised card with an
explicit `Revision Delta` or `Required Revisions`, then dispatch it with:

```bash
bash ai/dispatch-to-claude.sh REVISED_CARD.md \
  --recovery-classification PRIOR_ATTEMPT_CLASSIFICATION.json
```

The dispatcher creates a receipt-bound `Bounded Recovery Delta` containing only
the safe failure class, route, current-card hash, and no-replay boundary. It
does not copy model text, logs, source, diffs, or change requests; the revised
Task Card remains authoritative. Transport failures must use exact
`--retry-in-place-task-id` instead, while direction/authority failures require
review rather than a generated recovery delta.

When useful on-plan work has exactly one semantic blocker, `aiwf advisor-continuation` may prepare a one-call same-worktree continuation. It does not invoke a model or dispatch by itself. Bind request/evidence, state hash, allowed and forbidden paths, and one-call idempotency.

Worktree continuity and model memory are separate. Initial dispatch assigns an explicit Claude session UUID. Retry-in-place, reviewed continuation, and advisor continuation resume that UUID from the prior runtime receipt when valid. If Claude rejects that UUID with a conversation/session-not-found result, the dispatcher writes a hash-bound `session-resume-failure.json` receipt with `counts_as_model_failure=false`, then makes exactly one fresh-session attempt with the same owner, task, worktree, and write scope. The runtime receipt is atomically updated with the replacement UUID and generation, so that attempt starts a new takeover-accounting scope. Other resume failures remain terminal. When no resumable UUID exists, runtime records `unavailable-file-backed-fallback` and starts a new named session. `--bare` disables auto-memory/customization, not explicit conversation persistence. Never describe file-only continuation as restored model memory.

Sequential slices under one frozen solution contract may use a one-use Context
Lease. Create it with `ai/context-lease.py create` only after Codex accepts the
current dirty state. Dispatch with `--context-lease PATH --continuation-kind
next-slice|revision|checker-followup`. The lease extends reviewed continuation:
it binds the exact worktree/card plus solution-contract, session, role,
tool-profile, and visible model/provider identities. Every accepted slice gets
a newly hash-bound lease; pass `--parent-lease` when continuing an existing
lineage. Do not treat a lease as persistent write authority.

The default warm-call limit is three. On the next compatible call, dispatch
automatically creates a bounded deterministic checkpoint under the common
`.worktrees/` root and starts a fresh Claude session with a delta capsule.
The checkpoint is hash-bound to the lease, the approved state, and the next
Task Card; it contains accepted paths/state digests and unresolved findings,
never source, diffs, or transcript text. A caller may instead provide a
compatibility `--rehydrate-from` checkpoint (recorded as legacy-unbound) or explicitly use
`--force-fresh-session`. Each call still starts a fresh restricted process so
sandbox mounts and exact write paths are rebound. Long-lived stream-json
daemons are not part of this contract.

Because dispatch uses `claude --bare`, project `CLAUDE.md`/`AGENTS.md` are not
the model-facing startup cost. Execution-only and Context Lease calls use
`build-execution-capsule.py` to render a bounded bootstrap or delta card;
`TASK_CARD_FULL.md` remains the audit source and is not appended to the prompt.

The deterministic skill-context packet adds only registry-approved procedural,
retrieval, validation, or output-contract cues. Each cue has source/hash
provenance, an applicability reason, polarity (`positive` or `negative`), and
an optional conflict group. Conflicting cues fail before dispatch; hard
authority, write-scope, acceptance, validation, and stop rules remain in the
frozen card and cannot be supplied by the registry.

The default compilation strategy is `coverage`: it combines top-down
preset/gate/continuation candidates with bottom-up language/task/section
candidates, retains active anchors, and rescues only cues with new required
coverage. The receipt records the candidate routes, marginal coverage,
zero-marginal exclusions, and source-heading span hashes when available.
`--context-compile-strategy anchors-only` exists solely for a paired benchmark
ablation; it must not become an ordinary lower-context production default.
The runtime stores the selected strategy and phase metrics expose coverage and
candidate counts without storing prompt bodies.

Required exact-path write enforcement uses writable staging sources outside the
worktree and binds only those sources over their declared destinations inside
the read-only sandbox. Before starting Claude, the dispatcher runs the actual
receipt-bound writer through the final sandbox command against a control file
and one declared product file when available. It synchronizes only
receipt-listed paths back to the worktree and fails as
`write-sandbox-approved-writer-unavailable` when the effective writer cannot
open those bindings. Required enforcement never degrades to post-run-only auditing;
`editor-only` removes Bash rather than merely discouraging it. Claude's
`~/.claude/session-env` is separately mapped to a task-scoped temporary
directory so Bash initialization does not need a writable home. The lineage's
`~/.claude/projects` transcript store is mapped to
`.worktrees/.session-store/<lineage>/projects`, allowing a real same-session
resume without opening the rest of the home directory. Both mounts receive a
write probe before launch. Because built-in Edit may require a neighboring temporary file, the
prompt supplies `write-approved-file.py` plus a task-local
`.aiwf-write-staging/` input directory whose writable parent supports built-in
Edit/Write atomic replacement. Fixed source filenames feed complete bytes or
old/new fragments to the helper, which reads the immutable receipt internally
and writes only its external staged binding. The commands need no shell
expansion or per-task path in `allowedTools`; base64 arguments remain a fallback
when both Edit and Write are absent. Fragment
replacement fails without writing unless the old bytes occur exactly once.
Complete replacement is allowed by default only for new files; an existing file
must use unique-fragment replacement unless its exact path appears under `Full
file replacement paths`. Empty mount-only placeholders are removed during
synchronization and do not survive a preflight-only failure.

The writer validates the complete candidate before changing the mounted staged
file. Python candidates must parse and compile, retain valid dataclass default
field ordering, and must not introduce duplicate top-level definitions or
imports or remove an import that remains globally referenced. JSON and TOML
candidates must parse. A failed check leaves the prior
checkpoint bytes intact. For existing files of at least 4 KiB, a unique
fragment covering more than 75% of the file is rejected unless the card
explicitly authorizes that exact full-file replacement. These deterministic
micro-gates run inside the fixed writer command and require no additional Bash
approval; they do not replace assigned tests or Codex semantic review.

Model-facing helpers never resolve from the execution worktree's historical
`ai/` or `scripts/` files. Before any connectivity/model probe, the dispatcher
verifies its sibling helper protocol. It then snapshots the exact-writer and
validation runner into a task bundle before tool negotiation, records the
protocol and helper hashes in
`*.managed-runtime-bundle.json`, and read-only mounts that bundle at the fixed
`.aiwf-runtime/` path. Reviewed continuations therefore reuse product state and
session lineage without combining a new dispatcher with old worktree helpers.
Missing or mismatched bundle components stop before Builder execution as
`workflow-runtime-mismatch` and do not consume a model round.

## Progress and Monitoring

Execution-only, batch, and test-writing Checker tasks use a first durable-output boundary at the 600-second context-acquisition window. Narrow, retry, revision, and split-child routing reduce task scope but never shorten this response window, and natural-language task-card prose never changes scheduler policy. Generic planning, acknowledgement, timestamps, and claimed command starts do not satisfy durable progress; a canonical product delta does. Validation-only Checker work retains the ordinary observation policy. Shortly before the initial context boundary and every later active deadline, the dispatcher starts one bounded Spark `monitor-triage` evaluation while Claude continues running. At the context boundary, a hash-current `continue` may extend orientation, while a high-confidence `interrupt-candidate` may stop only while the product digest still equals the approved baseline. After the first product delta, a fresh 600-second active window begins, and every later canonical product-content change refreshes that complete window. At an active boundary, a high-confidence stop candidate additionally requires deterministic product-idle corroboration. The capsule contains the frozen contract, product-state summary, redacted recent assistant output, and normalized tool events. Missing, late, low-confidence, or invalid Spark output never stops Claude; the 1500-second hard cap remains absolute.

The active deadline therefore enters `extension-pending`, not immediate termination. If Claude produces a canonical product-content change while Spark is running or while its snapshot is pending, the dispatcher cancels the task-scoped Spark process group and invalidates the stale judgment; the ordinary product-growth rule has already refreshed a complete active window from that change. Spark results are accepted only when their bound product digest still matches. When the digest is quiet but Spark confirms useful on-plan activity, further extensions use `CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS` and `CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS`; report, progress, terminal text, token use, and control-file growth never refresh a product window.

The single terminal monitor wait streams `extension-evaluation-started`,
`extension-evaluation-pending`, and `extension-evaluation-result` notices as
continuing boundaries. These notices never end the wait or authorize Codex to
poll or stop either process; they make the dispatcher-owned state transition
visible until the next product-window or terminal event.

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
Every refreshed dispatch path is process-group reclaimable. Brokered calls
cancel through the broker, which terminates the model process group and records
a terminal `cancelled` ledger transition before the wrapper is reaped.
Brokerless compatibility calls use their own session/process group. Automatic
timeout, catchable dispatcher-signal cleanup, takeover, and `kill-claude.sh`
all use the same identity-bound TERM, bounded wait, KILL, and final identity
confirmation. PID-only termination fails closed. Once inactivity is confirmed,
transient PID hints are removed while process-identity and termination receipts
remain as durable evidence. A catchable abnormal dispatcher exit writes a
terminal outcome; SIGKILL cannot run cleanup and therefore leaves identity
evidence for fail-closed takeover.

`Implementation Complete: yes` starts an independent tail/report window.
`CLAUDE_CODE_TAIL_TIMEOUT_SECONDS` defaults to 90; expiry stops the lingering
child, preserves and drains evidence, and records `tail-timeout`. A useful diff
with missing prose produces `<task-id>.recovered-completion.json` for bounded
Codex review rather than being discarded. That receipt says
`evidence_usability=recoverable` and `direct_acceptance_eligible=false`; only a
subsequent Codex Review Decision may identify adopted files or symbols.

`Execution Phase: implementation` is an edit-readiness declaration, not durable progress. It is accepted only with `Context Acquisition Complete: yes` and a non-empty `Planned First Write`, meaning repository scanning, requirement understanding, and local planning are complete. The dispatcher grants a bounded edit-ready bridge (`CLAUDE_CODE_EDIT_READY_GRACE_SECONDS`, default 120) but refreshes a Builder's full active window only after product content changes. A report without a Builder product delta never refreshes that implementation window.

Before launch, the dispatcher freezes a full product-content baseline. Existing
dirty content in a reviewed continuation never counts as first progress; only a
content digest different from that approved baseline does. First progress and
product idle use the same full content hash. Each heartbeat synchronizes
receipt-listed external staging before computing that digest, so a successful
approved-writer call becomes a product delta and refreshes the active window on
the next sample. Control files and writer-input scratch never do. An unchanged digest for
`CLAUDE_CODE_PRODUCT_IDLE_TIMEOUT_SECONDS` (default 600) becomes an idle
candidate; `CLAUDE_CODE_PRODUCT_IDLE_CONFIRMATIONS` consecutive observations
(default 2) make that candidate eligible as local corroboration for the timeout
advisor. With the advisor enabled, product-idle evidence alone does not stop
Claude before an actionable Spark judgment. Explicit `CLAUDE_CODE_TIMEOUT_ADVISOR=off`
retains the compatibility stop behavior.

`solution-planner` progress uses `context`, `planning`, `contract-validation`, and `complete`. It must never report `implementation`, because planning progress is not implementation evidence.

Relevant overrides are `CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS`, `CLAUDE_CODE_TIMEOUT_SECONDS` (active window), `CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS` (first extension), `CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS` (product-growth renewals), and `CLAUDE_CODE_HARD_TIMEOUT_SECONDS`. Timeout-advisor controls are `CLAUDE_CODE_TIMEOUT_ADVISOR=auto|on|off`, `CLAUDE_CODE_TIMEOUT_ADVISOR_LEAD_SECONDS` (default 60), `CLAUDE_CODE_TIMEOUT_ADVISOR_CALL_TIMEOUT_SECONDS` (default 90), `CLAUDE_CODE_TIMEOUT_ADVISOR_MAX_ATTEMPTS` (default 2), and `CLAUDE_CODE_TIMEOUT_ADVISOR_RETRY_SECONDS` (default 30). Context acquisition defaults to the 600-second active window for every execution profile. Execution-only, batch, and test-writing Checker first-progress stops inherit that same context-acquisition timeout; routing shape does not alter it. The legacy first-progress environment override remains compatibility-only and is never derived from task narrowing.

Approval-blocked early convergence requires two stable heartbeats by default. `CLAUDE_CODE_APPROVAL_CONVERGENCE_HEARTBEATS` may lower or raise that count for unusually slow filesystem environments or deterministic tests; production defaults remain conservative.

Do not spend Codex turns polling unchanged heartbeats. The dispatcher is the
default single sampling owner and appends only `started`, `material-change`,
`active-window-refreshed`, `active-window-extended`, `child-exited`, and
finalized `terminal` boundaries to
`<task-id>.monitor-events.log`. An agent must issue one blocking
`monitor-claude.sh wait <task-id> --until terminal` call; repeated
`watch`, `ps`, `tail`, status, process-tree, or clock-only commands are forbidden. Read a bounded
decision/diff only after that wait returns.

The dispatcher continuously writes `<task-id>.activity-observation.json` from
bounded filesystem metadata only. It records session-store, control, and
product activity ages plus remaining active/hard windows; this ordinary sample
does not read transcript content and keeps `model_tool_split_available=false`.
Only near an active timeout, `claude-extension-capsule.py` may read the current
session UUID's recent JSONL tail. It persists no chain-of-thought or successful
tool-result payloads. Assistant text is redacted, bounded, and marked untrusted;
tool activity is reduced to names, target hints, and error state. The resulting
`<task-id>.extension-capsule.json` is advisory evidence only and never refreshes
the product window by itself.

The terminal wait remains one process, but it must stream each structured
execution-window refresh/extension and timeout-advisor state transition as soon
as the dispatcher records it. Window notices include the progress signal,
elapsed time, and new active/hard deadlines; advisor notices expose only the
evaluation identity, state, bounded decision fields, and whether Claude keeps
running. Every notice states that the same wait is continuing toward terminal.
Codex must consume these notices instead of polling or inferring state from
elapsed wall time.
`--until material` also returns immediately when a refresh already exists or
arrives after the wait starts.

The human-readable progress log likewise emits running detail only after a
phase/file/result/report change or when a timeout threshold is near. Unchanged
30-second heartbeat text is suppressed; `CLAUDE_CODE_WORKTREE_PROGRESS=verbose`
is the explicit diagnostic override.

Installed monitor helpers are versioned with the rest of the runtime. If an
older project copy does not recognize `wait --until`, run
`python ai/doctor_workflow.py`; it reports `ai/monitor-claude.sh` as outdated,
then refresh with the displayed `--update-workflow-files` command.

Do not start a detached monitoring supervisor. It duplicates dispatcher
sampling and is not part of the installed workflow. When `wait` reaches a
material or terminal boundary, it appends one compact local decision and may
invoke Spark `monitor-triage` to compress ambiguous evidence only when the local decision is `inspect` or
`interrupt-candidate`; active-window extension evaluation also uses this mode
with the privacy-limited capsule above. Spark receives compact JSON rather than
raw process listings, full logs, network tails, source diffs, thinking content,
or tool-result payloads. Its diagnostic summary is capped at 240 characters and explicitly distinguishes edit readiness, durable writes, and confirmed product-idle duration. Codex receives that summary plus fixed decision fields; raw evidence remains file-backed. Stable `continue`, `terminal`, and
`visibility-unknown` states use no model call. `monitor-claude.sh decision`
provides the same one-shot path manually. Neither local monitoring nor Spark
authorizes interruption. Use
`status-claude.sh --details` only for exceptional diagnosis. If a restricted
sandbox cannot see PIDs without a terminal event, report `visibility-unknown`
from the dispatch environment and never launch a duplicate Builder.

Use `python ai/aiwf.py status snapshot --task-id <id> --format text` for the
single operator-facing result. It reports startup/lifecycle states, changed-path
counts, deterministic gates, and `usable`. `usable=yes` requires a terminal
receipt, no active writer, successful dispatch/artifact/specified validation,
Codex semantic acceptance, and no evidence conflicts; it never authorizes
merge.

Each dispatch writes `<task-id>.phase-metrics.json` with approximate heartbeat-observed context acquisition, implementation, validation, tail, and completion-ready timing. Use it to identify context reacquisition or post-implementation tail waste; do not treat sampled boundaries as provider billing timestamps.
It also writes `<task-id>.phase-events.jsonl` when the normalized phase or
current validation command changes. Phases are `exploring`, `editing`,
`validating`, and `reporting`; these Claude-authored signals are advisory and
never satisfy completion by themselves.

## Stable Tool Contract

Task-card validation commands are audited before launch but are not embedded
individually in Claude's `allowedTools` schema. When at least one command is
accepted, Claude receives the fixed
`python3 .aiwf-runtime/run-approved-validation.py run` entry
point. The helper re-reads `CLAUDE_TASK_CARD.md`, rejects unsafe composition,
length overflow, or command overflow, splits each command into an argv, and
executes it without a shell. A rejected command prevents the dispatch and the
runner from executing any assigned validation.
Run `python ai/compose_task_card.py --lint-card CARD` while filling the card to
surface rejected shell composition before dispatch.

Exact-write entries use the fixed repository-relative writer path and fixed
`.aiwf-write-staging/` input filenames, with base64 content arguments as a
fallback. The helper reads the dispatcher-bound receipt internally; the Claude
command contains neither an environment expansion nor a per-task absolute path.
Bubblewrap and the receipt still enforce exact declared targets.
This keeps the tool schema stable without broadening write authority. Cache attribution hashes
the final allowed-tools contract after both runner and writer entries exist.

The prompt is emitted as a stable static core followed by dynamic worktree
evidence and the task suffix. Phase metrics record the respective byte counts
and `static-core-v1` layout identifier; they diagnose prefix stability but do
not claim a provider cache hit.

## Reports

Seeded/fallback reports are not Claude-owned completion. Before progress or completion use, `validate-claude-report.py` requires the standard title and report sections and rejects seeded/progress markers, progress/report role swaps, oversized reports, and source-dominated bodies. Missing reports may be reconstructed when the diff matches the card and assigned checks pass. The dispatcher then runs `verify-claude-report.py`; changed-file/count/cleanliness claims are mandatory. Assigned tests additionally require a test diff and a claimed count that matches detected added test declarations. Assigned validation requires its exact command and exit code, but model-authored claims remain `claimed-unverified` until a deterministic receipt exists. A revision `RESOLVED` claim binds finding ID, changed file, symbol, and exact test name. Prose-only, missing, or contradictory claims produce `needs-review`.

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
