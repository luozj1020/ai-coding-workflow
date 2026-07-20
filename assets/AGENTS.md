# Agents

Project-specific text outside the managed block is preserved by the installer.

<!-- AI-CODING-WORKFLOW:BEGIN managed -->
## AI Coding Workflow Core

**Minimize scarce Codex work while preserving correctness.** Codex freezes
intent and performs bounded semantic review; Claude owns planning,
implementation, revision, assigned tests, and long validation; Spark supplies
optional bounded advice; humans own merge and destructive/high-impact approval.

Use `OBSERVE -> ROUTE -> PLAN -> DISPATCH -> EXECUTE -> VERIFY -> REVIEW`.
Prefer LSP, `ai/locate-code.py`, targeted reads, initialized CodeGraph, and local
deterministic tools over broad reads. Do not browse the web for local repository
failures unless the user explicitly requests external/current information.

Apply this workflow only to non-trivial work where Claude delegation is expected
to remove material Codex work and longer latency is acceptable. For tiny/urgent
edits, ordinary code questions, read-only investigation, tight interactive
debugging, or unreliable Claude/isolation/evidence, record `workflow bypassed:
<reason>` and use ordinary Codex/local tools without a task card or Spark call.

## Claude-First Ownership

Before every initial, revision, narrow, retry, split-child, or next-phase action,
ROUTE from a short current brief. `ownership_profile=claude-first` is the
default. Claude owns source-writing unless the human explicitly chooses Codex,
the task is confirmed high-risk core semantics, or Codex is applying a reviewed
deterministic correction. `economy-first` is an explicit alternative profile.
Spark may replace Codex estimation when ownership or task shape is uncertain;
`preflight-bundle` remains diagnostic/compatibility-only.
Unavailable or schema-invalid Spark auto-disables without strong-model fallback.

Use Claude `execution-builder` for a frozen solution, `batch-builder` for
mechanical work, and `exploratory-builder` for bounded new-feature work whose
implementation path is not yet clear. Single-task wall time is advisory because
portfolio concurrency belongs to independent user terminals. Optimize accepted
output per Codex token, not total downstream-model tokens. Confirmed high-risk core semantics
may bias only toward Codex; unknown risk raises review rigor without silently
changing the owner.

For a large or multi-phase feature with a clear goal but open implementation
path, ROUTE should prefer Claude `solution-planner` when the structured contract
is expected to remove at least 30% of Codex planning work. Claude produces a validated
structured solution contract; Codex performs one adversarial review and freezes
it. Only blocking findings or incorporated spec changes reopen planning.
Recommended findings become backlog. Route every frozen implementation slice
back to Claude independently and do not repeat whole-project planning in cards.

For delegated work, read only `ai/task-card-components/catalog.md`, select one
preset plus material gates, and run `python ai/compose_task_card.py ...`. Fill the
composed short card; the monolithic template is compatibility-only. The integrated
runner performs this only after the positive route, inlines bounded context once,
and dispatches the composed Markdown rather than the source Task JSON. Revision
cards bind accepted evidence and describe only the delta.

## Dispatch and Validation

- Builder Claude gets one responsibility, exact paths/symbols, a source-of-truth
  example, forbidden paths, measurable acceptance, and narrow validation.
- `execution-only` requires explicit context-sufficient and eligible markers.
- Builder does not write acceptance tests or run broad suites unless explicitly
  assigned a mixed exception or narrow sanity check.
- Checker/Test Claude is conditional, not automatic. Dispatch it only when test
  writing, long-running validation, or evidence processing materially reduces
  Codex work. If local deterministic checks already close acceptance and no test
  changes are required, record `checker skipped: deterministic evidence sufficient`.
- Codex reviews Builder direction before any Checker dispatch. Final semantic
  review and merge authorization never belong to Spark or Claude.
- Do not coordinate multiple projects or portfolio concurrency inside the Skill.
  The user runs one repository workflow per terminal. Legacy within-repository
  parallel helpers remain explicit compatibility tools, never an automatic route.

## Recovery and Intervention

Do not spend Codex turns polling unchanged processes. Use compact machine fields
and the persistent monitor; inspect a bounded diff only at review/terminal events.
Useful on-plan diff/report/progress favors waiting or reviewed same-worktree
continuation. Interrupt only for corroborated no-progress or confirmed deviation.

Classify a failed Claude round before retry/takeover. Transport before useful
interaction, approval/sandbox blockers, dirty source, and stale HEAD are not model
failures. Preserve useful evidence. One acknowledgement-only/no-progress round
requires one tighter retry; two current-task rounds may permit scoped takeover.
Prior-session failures do not transfer automatically.

Dirty source/stale HEAD is a delegation blocker, not a forced Codex edit. Restore
a reliable base or obtain explicit authority. After Codex accepts the main
direction, prefer one reviewed same-worktree Claude continuation. A fresh route
may select a reviewer-owned correction only for a deterministic local delta.

Missing prose is an evidence gap. Recover from matching diff and deterministic
checks when possible. Seeded/fallback reports never count as Claude completion.
No model merges automatically.

## Context and Safety

- Keep artifacts file-backed under `.worktrees/` or `ai/plans/<task-id>/`; return
  compact summaries and paths, not full logs.
- Spark is advisory, normally direct-output, and cannot satisfy acceptance,
  replace Claude implicitly, interrupt a process, approve review, or authorize merge.
- External MCP/plugins are default-off and do not widen Bash/Edit authority.
- Destructive commands, deletion, migrations, auth/permission, billing,
  deployment, public API, secrets, and production-data changes require explicit
  human authority.

## On-Demand References

Load only the relevant installed skill reference:

| Need | Reference |
|---|---|
| ownership/Spark/result modes | `references/routing-and-spark.md` |
| task cards/specs/context packets | `references/task-card-policy.md` |
| Claude probes/timeouts/monitoring | `references/claude-runtime.md` |
| review/Checker/takeover | `references/review-policy.md` |
| worktrees/continuation/parallel | `references/worktree-and-parallel.md` |
| retrieval/context budgets | `references/mcp-policy.md` |
| setup/update/doctor | `references/setup-policy.md` |
| metrics/regressions | `references/benchmark-policy.md` |
<!-- AI-CODING-WORKFLOW:END managed -->
