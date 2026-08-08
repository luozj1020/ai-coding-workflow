# Agents

Project-specific text outside the managed block is preserved by the installer.

<!-- AI-CODING-WORKFLOW:BEGIN managed -->
## AI Coding Workflow Core

**Minimize scarce Codex work while preserving correctness.** Codex freezes
intent and performs bounded semantic review; Claude owns implementation,
revision, assigned tests, and long validation; Spark gives bounded advice;
humans own merge and destructive or high-impact approval.

Use `OBSERVE -> ROUTE -> PLAN -> DISPATCH -> EXECUTE -> VERIFY -> REVIEW`.
For indexed-code symbols, use one bounded CodeGraph query first;
`ai/locate-code.py` for behavior/files; lexical search for Shell/config/text.
Record result/skip before broad reads. Do not browse the web unless asked. Ordinary-risk
work proceeds after deterministic checks without a second business confirmation.

Classify before workflow references/cards: `bypass` for questions/read-only/
tiny/urgent work; `direct` for bounded local Codex edits (including workflow
maintenance); and `delegated` only when durable Claude output will materially
reduce Codex work. Bypass records `workflow bypassed: <reason>`. Direct work
records `python ai/aiwf.py direct --reason ...
--path ...` and uses only the files/checks needed for the edit—no Task Card,
Spark, or Claude call merely to audit it. Setup/update loads setup policy only.

## Claude-First Ownership

ROUTE every initial, revision, narrow, retry, split-child, and next-phase action.
`ownership_profile=claude-first` is default: Claude writes source unless the human selects Codex,
confirmed high-risk core semantics favor Codex, or Codex applies a reviewed
deterministic correction. Unknown risk increases review, not ownership changes.

For JSON-backed delegation, Codex freezes and reviews Task JSON; `aiwf run`
renders its execution card deterministically. Never hand-edit JSON, rendered
cards, or receipts. Helpers create workflow state deterministically.
Claude `solution-planner` is never inferred and requires
`solution_planner_opt_in=true`. Prefer one Claude execution round; additional
roles must remove material Codex work, not merely save model tokens.

When quota allows, run one advisory `task-card-audit` before non-Express
delegation; use `execution-cost-estimator` only for unresolved ownership. Spark
cannot expand scope or replace Codex review. Network failure exits 75, then
requires one identical authorized host retry. Reuse the stable launcher/host
preference, never environment prefixes. `implementation` aliases `next-phase`;
`preflight-bundle` is diagnostic-only. Load routing policy for exact rules.

Use `execution-builder` for frozen solutions, `batch-builder` for mechanical
work, and `exploratory-builder` for bounded new features. Large work uses a
short Codex plan; the solution planner remains explicit opt-in.
Route every frozen implementation slice independently. Compatible sequential
slices may reuse one hash-bound Context Lease.

The component composer is for explicit legacy Markdown cards only; `aiwf run`
renders JSON-backed execution cards directly.

## Dispatch and Validation

- Builder Claude receives one responsibility, exact paths/symbols, a
  source-of-truth example, forbidden paths, measurable acceptance, and narrow
  validation. `execution-only` requires explicit readiness markers.
- Builder does not own acceptance tests or broad suites unless assigned a mixed
  exception or narrow sanity check.
- Checker/Test Claude is conditional, not automatic. Use it only when assigned
  test writing, long validation, or evidence processing materially reduces
  Codex work. Otherwise record
  `checker skipped: deterministic evidence sufficient`.
- Codex reviews Builder direction before Checker dispatch and owns final
  semantic acceptance. Model reports remain claims until diff, receipts, and
  deterministic tests agree. Missing or contradictory evidence is
  `needs-review`.
- Models never merge. Do not automatically coordinate portfolio concurrency;
  repository-local parallel helpers remain explicit compatibility tools.

## Runtime and Recovery

Use stable CLI flags for host execution, dirty snapshots, tool profiles,
retry-in-place, and reviewed continuation. If a launcher lacks them or Spark's
exit-75 handoff, refresh the bootstrapped workflow before any model call.

Do not poll or use PID/clock-only liveness. Use one blocking
`monitor-claude.sh wait` and inspect only boundary evidence. Product content
alone refreshes the active window. Spark sees redacted, session-bound activity:
`task-directed` may extend; `unproductive` may advise interruption, never stop
Claude. Mtimes, control files, reports, and tokens never prove work.
Product-idle corroboration and the hard cap apply.

Classify failures before retry or takeover. Pre-interaction transport,
approval/sandbox blockers, dirty source, and stale HEAD are not model failures.
One acknowledgement-only/no-progress round gets one tighter retry; only an
explicit same-session `retry-in-place` pair with matching card, baseline, and
worktree bindings may permit scoped takeover. Prior-session failures, including
a fresh session after resume failure, do not transfer automatically.

Keep useful on-plan evidence. Prefer a reviewed same-worktree continuation after
direction acceptance. Resume the leased Builder before opening a new same-owner
session. Missing prose is an evidence gap; matching diff plus deterministic
checks may recover it. Seeded/fallback reports never count as completion.
A fresh route may use a reviewer-owned correction only for a deterministic
local delta.

Resolve runtime state from Git's common directory. Fresh execution worktrees
are siblings directly under its top-level `.worktrees/`, never recursively
nested. An external reviewed card may be passed by absolute path without being
copied into the product baseline.

Takeover is an atomic single-writer transfer: revoke or prove absence of the
Owner Lease, identity-stop the old task process tree, freeze a stable baseline,
then issue the Codex grant. Unknown visibility fails closed. PID-only kills and
unapproved write paths fail closed; `editor-only` removes Bash.

## Context and Safety

- Use CodeGraph once for indexed-code questions when `codegraph status . -j`
  matches the clean worktree; record result/skip reason. Delegated graph use
  needs a ready receipt.
- Keep artifacts under `.worktrees/` or `ai/plans/<task-id>/`; return compact summaries and paths,
  not logs, full diffs, or repeated file bodies.
- Skill feedback is user-triggered and read-only: use the conversation and
  minimum receipts; do not persist telemetry or start remediation implicitly.
- Spark is advisory and cannot satisfy acceptance, interrupt Claude, approve a
  review, or authorize merge. External MCP/plugins are default-off and do not widen Bash/Edit authority.
- Destructive actions, deletion, migration, auth/permission, billing,
  deployment, public API, secrets, and production-data changes require explicit
  human authority.

## On-Demand References

Load only the single reference for the current operation; load another only
after the phase changes or the first is insufficient.

| Need | Reference |
|---|---|
| roles/default loop/evidence | `references/operating-model.md` |
| ownership/Spark/results | `references/routing-and-spark.md` |
| task cards/specs/context | `references/task-card-policy.md` |
| Claude dispatch/monitor/recovery | `references/claude-runtime.md` |
| review/Checker/takeover | `references/review-policy.md` |
| worktrees/continuation | `references/worktree-and-parallel.md` |
| retrieval/context budgets | `references/mcp-policy.md` |
| setup/update/doctor | `references/setup-policy.md` |
| metrics/regressions | `references/benchmark-policy.md` |
| user-triggered feedback | `references/feedback-policy.md` |
<!-- AI-CODING-WORKFLOW:END managed -->
