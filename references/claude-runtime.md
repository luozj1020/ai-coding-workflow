# Claude Runtime Policy

Load this reference for dispatch, execution-only mode, connection diagnosis, zero progress, timeouts, monitoring, retries, takeover, or report recovery.

## Dispatch Contract

Codex owns the full planning card. `balanced` dispatch renders a compact Claude card while preserving `TASK_CARD_FULL.md`; use `safe` for ambiguous work. Builder `auto` resolves to `execution-only` only when both `Context is sufficient for execution? = yes` and `Execution-only eligible? = yes`. Validate dense packets with `ai/validate-claude-context.py`. Prefer one responsibility, 1–5 exact files, named symbols, one reference implementation, forbidden paths, measurable acceptance, and narrow validation.

Builder implements and reports direction. Codex reviews direction. Checker/Test writes or runs assigned tests only after direction acceptance. Do not mix implementation, test writing, and broad validation unless the task card explicitly records `mixed-exception`. Non-blocking acknowledgement with `proceed` must continue editing in the same run.

## Failure Attribution

Run `ai/classify-claude-attempt.py` before retry/takeover accounting. Transport failure before acknowledgement/diff/report/progress is `transient-transport`: preserve the worktree, retry in place at most once, and do not count it toward takeover. Approval/sandbox blockers also do not count. Acknowledgement-only, clean exit without progress, and confirmed direction deviation count.

Before classifying zero usable output as model no-progress, run one fixed interaction diagnostic in the same resolved route:

```bash
python ai/claude-healthcheck.py --interaction-route auto --timeout 60
```

Its fixed prompt is `你好`. It is read-only diagnostic evidence, not implementation or acceptance evidence, and it never changes configuration. Restricted-sandbox failure is inconclusive; a successful user-terminal interaction or dispatch is authoritative. Successful dispatches may persist only the proven `direct` or `inherit` route.

One failed Builder attempt is not takeover permission. Tighten and re-dispatch once. Two current-task acknowledgement/no-progress attempts permit scoped Codex takeover after recording both artifacts. Prior-session failures do not. Useful on-plan diff remains salvageable; missing prose is an evidence gap, not automatic implementation failure.

When useful on-plan work has exactly one semantic blocker, `aiwf advisor-continuation` may prepare a one-call same-worktree continuation. It does not invoke a model or dispatch by itself. Bind request/evidence, state hash, allowed and forbidden paths, and one-call idempotency.

## Progress and Monitoring

Default execution-only first-progress observation is 60 seconds. The execution clock has three boundaries: context acquisition (default 600 seconds, or 420 seconds for `fast-large-repo`), an active execution window (default 600 seconds) refreshed exactly once by the first role-specific substantive signal, and a hard cap (default 1500 seconds). Builder substantive signals are an implementation diff, an explicit editing/implementation phase, or a valid Claude-owned report; generic plans, acknowledgement, seeded progress, and blockers do not refresh. Checker substantive signals additionally include an explicit validation/test command or process start. After the refreshed active window, recent artifact growth may grant one 300-second extension; there is no second extension and the hard cap always wins. Progress growth and useful on-plan diff favor waiting. Interrupt only after evidence indicates deviation or useful progress is unlikely. Long Bazel/git/helper work should be managed or evidenced separately when possible rather than silently consuming the model window.

Relevant overrides are `CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS`, `CLAUDE_CODE_TIMEOUT_SECONDS` (active window), `CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS`, and `CLAUDE_CODE_HARD_TIMEOUT_SECONDS`.

Do not spend Codex turns polling unchanged heartbeats. Start `ai/monitor-claude.sh start <task-id>` in the dispatch environment. It runs `watch-claude.sh --machine` locally and persists only material transitions. At a review boundary run `ai/monitor-claude.sh decision <task-id>`: a deterministic helper first reduces PID, timing, artifact growth, progress fields, errors, and bounded changed paths to a compact decision packet. `continue`, `terminal`, and `visibility-unknown` normally require no model call; only `inspect` or `interrupt-candidate` may be sent to Spark `monitor-triage`. Spark sees the compact JSON, not raw `ps`, full progress/status logs, network tails, or source diffs. Codex receives the bounded result only when review remains necessary. Neither the local helper nor Spark authorizes interruption; L4 kill remains an explicit Codex/human action. Use `status-claude.sh --details` only for exceptional manual diagnosis. If a restricted sandbox cannot see PIDs while no terminal marker exists, report `visibility-unknown` and check from the dispatch environment; do not launch a second Builder in the same worktree.

## Reports

Seeded/fallback reports are not Claude-owned completion. Missing reports may be reconstructed when the diff matches the card and assigned checks pass. The dispatcher runs `verify-claude-report.py`; report claims must list changed files/count, optional symbols, and unexpected-file status. Mechanical agreement is review evidence, never semantic acceptance. Checker ALL GREEN supersedes an earlier Claude validation approval blocker in the aggregate status.
