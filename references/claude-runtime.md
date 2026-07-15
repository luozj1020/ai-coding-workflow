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

Default execution-only first progress is 60 seconds; base timeout is 600 seconds with one 300-second active-progress extension. Progress growth, useful diff, or a valid report favors waiting. Interrupt only after evidence indicates deviation or useful progress is unlikely.

Do not spend Codex turns polling unchanged heartbeats. Start `ai/monitor-claude.sh`, then read its compact event tail at a review or terminal boundary. Escalate L0 heartbeat → L1 partial diff → L2 status/details → L3 network/status/diff corroboration → L4 explicit kill. If a restricted sandbox cannot see PIDs while no terminal marker exists, report `visibility-unknown` and check from the dispatch environment; do not launch a second Builder in the same worktree.

## Reports

Seeded/fallback reports are not Claude-owned completion. Missing reports may be reconstructed when the diff matches the card and assigned checks pass. The dispatcher runs `verify-claude-report.py`; report claims must list changed files/count, optional symbols, and unexpected-file status. Mechanical agreement is review evidence, never semantic acceptance. Checker ALL GREEN supersedes an earlier Claude validation approval blocker in the aggregate status.
