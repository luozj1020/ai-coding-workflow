# Agents

Project-specific text outside the managed block is preserved by the installer.

<!-- AI-CODING-WORKFLOW:BEGIN managed -->
## AI Coding Workflow Core

**Minimize scarce Codex work while preserving correctness.** Codex owns core
planning, frozen plan files, and bounded semantic review; Claude owns
implementation, revision, assigned tests, and long validation; Spark supplies
optional bounded advice; humans own merge and destructive/high-impact approval.

Use `OBSERVE -> ROUTE -> PLAN -> DISPATCH -> EXECUTE -> VERIFY -> REVIEW`.
Prefer LSP, `ai/locate-code.py`, targeted reads, worktree-matched CodeGraph, and local
deterministic tools over broad reads. Do not browse the web for local repository
failures unless the user explicitly requests external/current information.
After deterministic validation and bounded Codex review, an ordinary-risk
composed task card proceeds directly to dispatch without a second human
confirmation. Use `aiwf run --preview` for zero-model inspection. Material
product/API/data-model ambiguity and destructive/high-impact actions still
require explicit human authority.

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
Codex owns the short core plan in the Task Card, its only normal handwritten
workflow artifact. Deterministic helpers render route,
review, hash, receipt, freeze, and continuation artifacts; Codex must not
hand-edit `*.route.json`, `adversarial-review.json`,
`solution-contract*.json`, or runtime receipts. Claude `solution-planner` is
never inferred and requires explicit `solution_planner_opt_in=true`.
Spark may replace Codex estimation when ownership or task shape is uncertain.
When Spark quota is available, every non-Express Claude delegation should use
one bounded `task-card-audit` by default; an unresolved owner instead uses
`execution-cost-estimator`. The audit is advisory and cannot expand scope or
rewrite frozen acceptance. `preflight-bundle` remains diagnostic/compatibility-only.
Unavailable or schema-invalid Spark auto-disables without strong-model fallback.
Sandbox-network Spark failures are different: stop before Claude, persist the
`needs_host_execution` receipt, and rerun the identical Spark request once from
an explicitly authorized host boundary. Cache a successful host execution
preference so later calls do not repeat the known-failing sandbox route.
The outer Codex/orchestrator must consume exit 75 immediately: rerun the exact
command with host execution permission (`sandbox_permissions=require_escalated`
when that tool surface is available) and the recorded host-authority flag.
Do not report Spark unavailable or continue to Claude until that single host
attempt finishes. If host permission is not already granted, request it once.
Apply the same rule to Claude `needs_host_execution`, preserving its task card,
worktree, and session lineage with the receipt's stable CLI retry:
`bash ai/dispatch-to-claude.sh <card> --execution-env host
--retry-in-place-task-id <task-id>` (or `--reviewed-continuation <approval>`).
When the handoff receipt names a dirty snapshot, add
`--dirty-source-mode snapshot`; when a fixed tool set is required, add
`--tool-profile <profile>`. Do not prepend legacy environment selectors.
If either local launcher lacks these stable CLI options or Spark's exit-75 host
handoff, refresh the bootstrapped project workflow before any model call. Never
work around a stale launcher by prepending environment assignments. The Skill
updater refreshes an already-bootstrapped current repository by default;
`--skill-only` is the explicit opt-out. Treat Spark routing event
`implementation` as the `next-phase` compatibility alias.

Use Claude `execution-builder` for a frozen solution, `batch-builder` for
mechanical work, and `exploratory-builder` for bounded new-feature work whose
implementation path is not yet clear. Prefer one Claude execution round; do not
add serial model roles merely to save Codex tokens. Confirmed high-risk core
semantics may bias only toward Codex; unknown risk raises review rigor.

For large or multi-phase work, default to a short Codex plan followed by
deterministic card generation and Claude Builder execution. Preserve
`solution-planner` only as a latency-tolerant explicit opt-in; its validated
contract receives one Codex adversarial review before deterministic freeze.
Route every frozen implementation slice to Claude independently.

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
- Treat model reports as claims. Require report/diff/test/receipt consistency;
  keep dispatch success, artifact validity, validation success, and semantic
  acceptance separate. Missing or contradictory claims are `needs-review`.
- Do not coordinate multiple projects or portfolio concurrency inside the Skill.
  The user runs one repository workflow per terminal. Legacy within-repository
  parallel helpers remain explicit compatibility tools, never an automatic route.

## Recovery and Intervention

Do not poll or use `ps`, `tail`, or clock-only liveness. Block once on
`monitor-claude.sh wait`; inspect bounded evidence only at review boundaries.
Interrupt only for corroborated no-progress or deviation.
At timeout boundaries, keep Claude running during one bounded Spark evaluation.
Product growth refreshes the full window and invalidates pending advice. The
dispatcher owns stop/extend, requires product-idle corroboration, and enforces
the hard cap.

Classify a failed Claude round before retry/takeover. Transport before useful
interaction, approval/sandbox blockers, dirty source, and stale HEAD are not model
failures. Preserve useful evidence. One acknowledgement-only/no-progress round
requires one tighter retry; two current-task rounds may permit scoped takeover.
Prior-session failures do not transfer automatically.

Dirty source/stale HEAD is a delegation blocker, not a forced Codex edit. Restore
a reliable base or obtain explicit authority. After Codex accepts the main
direction, prefer one reviewed same-worktree Claude continuation. A fresh route
may select a reviewer-owned correction only for a deterministic local delta.
Resolve the workflow runtime root from Git's common dir. Every fresh execution
worktree must be a direct child of that one top-level `.worktrees/`; dispatching
from a linked worktree must create a sibling and never a recursive
`source/.worktrees/child`. A reviewed task card may remain outside the selected
source worktree and be passed by absolute path; do not copy it into the accepted
product baseline merely to make dispatch find it.

Missing prose is an evidence gap. Recover from matching diff and deterministic
checks when possible. Seeded/fallback reports never count as Claude completion.
No model merges automatically.

Codex takeover is an atomic single-writer transfer. A threshold receipt is only
a candidate: revoke/declare absent the Owner Lease, stop and identity-confirm
old process trees, freeze a stable baseline, then issue the Codex grant. Unknown
visibility fails closed; its ownership marker forbids later Claude continuation.
All automatic/manual stops are task-identity-bound process-tree operations;
PID-only kills fail closed, catchable dispatcher exits write terminal evidence,
and confirmed terminal runs remove transient PID hints.
Writing roles use exact paths with real-time read-only-root enforcement when
required. `editor-only` removes Bash.

Before a revision or test fix, use a granted Owner Lease to prefer the original
Builder and recorded session. Do not open a fresh same-owner session until
resume failure is recorded. Skip Advisor without a semantic blocker and
Reviewer without new evidence; record a reason for every model switch.

Communication-aware routing may apply Handoff Tax only from hash-valid observed
calibration with sufficient complete samples and explicit cost weights. Missing
history remains unknown/canary. Spark/model estimates are advisory and cannot
override observed facts, a valid lease, or explicit human ownership.

## Context and Safety

- Before accepting CodeGraph output, verify `codegraph status . -j` identifies
  the current Git worktree with no mismatch or pending changes. Discard
  warning-bearing results and never put them in a Context Packet. During
  delegation only a `*.codegraph-worktree.json` receipt with `status=ready`
  permits graph use; `CLAUDE_CODE_CODEGRAPH_POLICY=repair` explicitly opts into
  sync/reindex cost.
- Keep artifacts file-backed under `.worktrees/` or `ai/plans/<task-id>/`; return
  compact summaries and paths, not full logs.
- Skill feedback is user-triggered and read-only. Summarize the current
  conversation plus minimum necessary runtime receipts; do not persist
  telemetry, invoke Spark/Claude, create a task card, or start remediation
  until the user separately requests changes.
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
| user-triggered Skill feedback | `references/feedback-policy.md` |
<!-- AI-CODING-WORKFLOW:END managed -->
