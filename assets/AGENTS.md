# Agents

Project-specific text outside the managed block is preserved by the installer.

<!-- AI-CODING-WORKFLOW:BEGIN managed -->
## AI Coding Workflow Core

**Use the least expensive path that preserves correctness.** Codex owns routing,
design, and semantic review; Claude owns only delegated execution; Spark supplies
optional bounded advice; humans own merge and destructive/high-impact approval.

Use `OBSERVE -> ROUTE -> PLAN -> DISPATCH -> EXECUTE -> VERIFY -> REVIEW`.
Prefer LSP, `ai/locate-code.py`, targeted reads, initialized CodeGraph, and local
deterministic tools over broad reads. Do not browse the web for local repository
failures unless the user explicitly requests external/current information.

## Economy-First Ownership

Before every initial, revision, narrow, retry, split-child, or next-phase card,
ROUTE from a short current brief. A deterministic tiny skip or optional Spark
`execution-cost-estimator`/`preflight-bundle` happens before card authoring.
Unavailable or schema-invalid Spark auto-disables without strong-model fallback.

Choose Codex direct editing when it already holds the exact context and Claude
delegation would not reduce semantic rereview, context reacquisition, or total
control-plane work. This includes focused core-semantic changes in large projects
that Codex must fully rereview. Choose Claude for mechanical batches, independent
units, assigned test writing, long validation/log/evidence work, or implementation
that permits sampled rather than full Codex review. Risk increases rigor and may
bias only toward Codex; it must never push ownership toward Claude.

For delegated work, read only `ai/task-card-components/catalog.md`, select one
preset plus material gates, and run `python ai/compose_task_card.py ...`. Fill the
composed short card; the monolithic template is compatibility-only. Revision cards
bind accepted evidence and describe only the delta.

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
- Parallel execution is opt-in, normally max two, with independent write scopes
  and serial reconciliation/review.

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
direction, a fresh revision ROUTE may select a reviewer-owned bounded correction
when the remaining change is deterministic, local, and already in Codex context.

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
