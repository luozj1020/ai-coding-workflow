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

Its fixed prompt is `你好`. Dispatch performs this minimal interaction in the exact execution worktree and resolved route before starting Builder. Workspace-trust, socket, and CLI failures stop immediately with a preflight result receipt instead of consuming the Builder window. It is read-only diagnostic evidence, not implementation or acceptance evidence, and it never changes configuration. Restricted-sandbox failure is inconclusive; a successful user-terminal interaction or dispatch is authoritative. Successful dispatches may persist only the proven `direct` or `inherit` route. `CLAUDE_CODE_STARTUP_PREFLIGHT_REQUIRED=0` is an explicit diagnostic override, not the normal workflow.

One failed Builder attempt is not takeover permission. Tighten and re-dispatch once. Two consecutive current-lineage counted rounds issue a hash-bound `*.takeover-receipt.json` containing only the permitted write scope and required validation. Transport, trust, approval, and sandbox failures never contribute. Useful on-plan diff remains salvageable; missing prose is an evidence gap, not automatic implementation failure.

When useful on-plan work has exactly one semantic blocker, `aiwf advisor-continuation` may prepare a one-call same-worktree continuation. It does not invoke a model or dispatch by itself. Bind request/evidence, state hash, allowed and forbidden paths, and one-call idempotency.

Worktree continuity and model memory are separate. Initial dispatch assigns an explicit Claude session UUID. Retry-in-place, reviewed continuation, and advisor continuation resume that UUID from the prior runtime receipt when valid; otherwise runtime records `unavailable-file-backed-fallback` and starts a new named session. `--bare` disables auto-memory/customization, not explicit conversation persistence. Never describe file-only continuation as restored model memory.

## Progress and Monitoring

Execution-only, batch, and test-writing Checker tasks default to a 120-second first durable-output deadline with stop action. Generic planning, acknowledgement, timestamps, and claimed command starts do not satisfy it; a worktree delta or valid owned report does. Validation-only Checker work retains the ordinary observation policy. The later active window remains 600 seconds and may receive one 300-second semantic-growth extension; the 1500-second hard cap always wins.

After the Claude child exits, finalization waits one bounded drain interval and
rechecks the worktree. A late change triggers one additional stability sample
before diff/status/result capture. `CLAUDE_CODE_TERMINAL_DRAIN_SECONDS=0` is a
diagnostic/test override.

Builder progress also carries `Execution Phase`, `Implementation Complete`, `Assigned Tail Work`, `Tail Work Complete`, and `Completion Ready`. After implementation, Claude may run only the bounded self-review, narrow validation, documentation, and reporting explicitly assigned by the card's Post-Implementation Contract. It then marks `Completion Ready: yes`, writes the final report/result, and exits voluntarily without waiting for acknowledgement. The monitor reports `finish_recommended=yes` while awaiting that normal exit; it never turns this marker into kill authority. A bounded self-review uses built-in Read/diff/search tools over changed files. No separate code-review plugin is assumed, and Claude's review never replaces Codex semantic review.

`Execution Phase: implementation` is an edit-readiness declaration, not durable progress. It is accepted only with `Context Acquisition Complete: yes` and a non-empty `Planned First Write`, meaning repository scanning, requirement understanding, and local planning are complete. The dispatcher grants a bounded edit-ready bridge (`CLAUDE_CODE_EDIT_READY_GRACE_SECONDS`, default 120) but refreshes the full active window only after product content changes or a valid owned report appears.

After the first product change, the dispatcher tracks the product-content digest rather than heartbeat timestamps. An unchanged digest for `CLAUDE_CODE_PRODUCT_IDLE_TIMEOUT_SECONDS` (default 180) becomes an idle candidate; `CLAUDE_CODE_PRODUCT_IDLE_CONFIRMATIONS` consecutive observations (default 2) stop the child as `product_idle_confirmed`. Explicit blocker evidence, active validation with a named/running command, and declared completion/tail work reset or exempt this counter.

`solution-planner` progress uses `context`, `planning`, `contract-validation`, and `complete`. It must never report `implementation`, because planning progress is not implementation evidence.

Relevant overrides are `CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS`, `CLAUDE_CODE_TIMEOUT_SECONDS` (active window), `CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS`, and `CLAUDE_CODE_HARD_TIMEOUT_SECONDS`.

Approval-blocked early convergence requires two stable heartbeats by default. `CLAUDE_CODE_APPROVAL_CONVERGENCE_HEARTBEATS` may lower or raise that count for unusually slow filesystem environments or deterministic tests; production defaults remain conservative.

Do not spend Codex turns polling unchanged heartbeats. The dispatcher is the
default single sampling owner and appends only `started`, `material-change`,
`child-exited`, and finalized `terminal` boundaries to
`<task-id>.monitor-events.log`. An agent must issue one blocking
`monitor-claude.sh wait <task-id> --until material|terminal` call; repeated
`ps`, `tail`, status, process-tree, or clock-only commands are forbidden. Read a bounded
decision/diff only after that wait returns.

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

## Reports

Seeded/fallback reports are not Claude-owned completion. Missing reports may be reconstructed when the diff matches the card and assigned checks pass. The dispatcher runs `verify-claude-report.py`; report claims must list changed files/count, optional symbols, and unexpected-file status. Mechanical agreement is review evidence, never semantic acceptance. Checker ALL GREEN supersedes an earlier Claude validation approval blocker in the aggregate status.
