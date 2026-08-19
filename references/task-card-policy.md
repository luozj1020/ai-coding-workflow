# Task Card and Evidence Policy

Load this reference when authoring task cards/specs, choosing gates, building Context Packets, using JSON task cards, or assembling final evidence.

## Planning Gates

Create a legacy Markdown task card only after routing selects actual Claude
delegation or an explicit human-requested planning/spec artifact. The primary
JSON-backed `aiwf submit` path freezes the Task JSON, renders its execution card
deterministically, and ends the Codex episode; Codex does not hand-author that Markdown. A direct Codex
change—including Skill/workflow maintenance—uses `aiwf direct` plus its exact
paths/checks instead; do not compose a `control-plane` card merely to document
or audit your own local edit. Legacy Markdown work reads the small
`ai/task-card-components/catalog.md`, selects one preset and only material
gates, then lets the local zero-model composer read and join their bodies:

```bash
python ai/compose_task_card.py --preset builder --gate root-cause --output ai/task-cards/TASK.md
```

For JSON-backed delegation, the reviewed Task JSON is the only task contract;
`delegation-task-card.md` is a disposable deterministic execution projection.
Do not use an editor or `apply_patch` to change generated cards, route JSON,
adversarial review JSON, solution-contract JSON, receipts, hashes, or
continuation approvals.
Keep ephemeral routing on stdout when possible. When a file is required, use
the deterministic helper's `--output`; Claude alone writes a Planner draft.

When routing facts already exist, let the deterministic selector choose the
minimal preset and gates:

```bash
python ai/compose_task_card.py --select-from routing-facts.json --output ai/task-cards/TASK.md
```

If those facts select `codex-fast-path`, the command returns `skip_card=true`
and writes no delegation card.

The integrated `aiwf submit` freeze path performs selection after routing and renders
`delegation-task-card.md` from reviewed Task JSON plus bounded routing facts.
It does not create a duplicate standalone Context Packet or pass source JSON to
the Markdown dispatcher. After deterministic validation and Codex contract
freeze, the durable supervisor owns dispatch. Product ambiguity that prevents
a frozen contract and destructive/high-impact actions remain stop conditions
requiring human authority.

The local composer remains for explicit legacy Markdown cards. On the JSON
path, Codex reviews the compact JSON goal, boundaries, acceptance, and high-risk
invariants; the renderer, not Codex, formats the execution card. Spark may
return advisory structured missing fields but cannot rewrite the contract. Do
not read `ai/task-card-template.md` by default; it is compatibility-only.
Never omit a material stop condition:

New JSON tasks put every executable stop in top-level `stop_conditions`. The
v1 `handoff.stop_condition` field remains accepted only to render legacy audit
cards; the base profile no longer generates it, and execution projections never
read it.

Execution presets ship with conservative Post-Implementation defaults. The
frozen contract, not a mid-run Codex decision, assigns tests, validation,
documentation, and long tail work. Do not leave ambiguous placeholders merely
to make the card look complete.

Every JSON task is assessed on declared write-path count, responsibilities, and
new modules. The result remains visible as task-shape evidence. In a frozen
Bookend task, size alone is advisory and cannot create a mid-task Codex split
checkpoint; concrete authority, contradictory acceptance, or an unbounded
write boundary still fails before submission. Foreground compatibility runs
retain their historical `split-required` behavior.

Tasks implementing aggregation, eligibility, quorum, fallback, or acceptance
gates must declare `extensions.complex_gate_contract`. When enabled, it
contains at least two concrete negative counterexamples and one fail-closed
condition; the JSON validator rejects incomplete contracts and the execution
renderer passes them verbatim to Builder. When inapplicable but explicitly
audited, set `enabled=false` with `not_applicable_reason`. This stronger task
contract does not automatically justify a Checker model; deterministic checks
remain the default.

Every `Write paths` and `Full file replacement paths` item is a pure
repository-relative path. Put descriptions such as “new focused test module”
in surrounding prose, never after the path; dispatch lint rejects an unquoted
whitespace-bearing item instead of creating a file whose name contains the
annotation. Backtick-quote a legitimate path that itself contains spaces.

- Spec Gate for ambiguous product, UX, API, or data-model direction.
- Root Cause Gate for bugs, regressions, or repeated failed fixes.
- Test-First/TDD Contract when red/green evidence is acceptance-critical.
- Goal Loop Contract for bounded iterative work.
- Advisor Gate for one-call strategic advice.
- Worktree/Large Repo and Parallel gates when those execution paths apply.
- Finish Branch Gate before claiming readiness for human merge.

Use the `revision` preset for narrowed retries and reviewer-requested corrections. Bind the accepted baseline and describe only the delta; do not copy the original task card. The dispatcher preserves the composed card as the full audit artifact and derives Claude's current-phase view with an execution-section allowlist.

For a counted no-progress, acknowledgement-only, or report-evidence mismatch,
a recovery redispatch requires an explicit `Revision Delta` or `Required
Revisions` in the new card. Pass the prior deterministic classification through
`--recovery-classification`; do not hand-copy model logs, prior diffs, or full
conversation history into the card. Transport retry remains an exact same-card,
same-worktree `--retry-in-place-task-id` operation, not a revision.

Use `exploratory-builder` only when explicitly selected for a bounded feature
whose goal is stable but implementation path remains unclear. It must produce
source changes plus evidence, not a prose-only repository survey. Large or
multi-phase work defaults to a short Codex plan, deterministic card generation,
and Claude Builder execution.

Use `solution-planner` only when the user explicitly opts in and pre-card routing
selects `claude-converge-codex-freeze`; size and open implementation paths never
select it automatically. Claude must produce the structured solution
contract named by the card. Codex reviews that artifact once and classifies every
finding as `blocking`, `recommended`, `backlog`, or `spec-change`. Resolve
blocking findings; defer recommendations/backlog; reject or explicitly
incorporate spec changes. Then freeze with `aiwf solution-contract freeze`.
Serialize the review with `ai/solution-contract.py review --finding
SEVERITY:DISPOSITION:SUMMARY --output adversarial-review.json`; do not hand-edit
that JSON. The helper also supports an empty review by omitting `--finding`.
Implementation cards bind the frozen contract hash and include only their slice;
they must not invite Claude to repeat repository-wide planning. Codex performs
one adversarial freeze review, not full task-card authorship plus replanning.

Role/preset names and runtime task modes are separate namespaces. JSON-backed
execution cards encode `task-mode=builder` and
`builder-mode=solution-planning` in their deterministic header;
`execution-builder`, `batch-builder`, and `exploratory-builder` map similarly.
Legacy Markdown tables remain accepted. Dispatch normalizes known role aliases
for compatibility before capability probing, preserves declared and effective
values in receipts, and rejects unknown or conflicting combinations before
Claude starts. `solution-planning` uses the minimal Builder profile: Read/Edit/
Bash plus the receipt-bound exact writer when native Write is absent.
Repository location stays available through bounded Bash/`rg`, so missing native
Glob/Grep does not block a planning session.

Testing responsibility must state whether Checker model dispatch is required.
Default to local deterministic validation. Select Checker only for assigned test
writing, long validation/log processing, or an independent evidence responsibility
that reduces Codex work; otherwise record `checker skipped: deterministic evidence sufficient`.

Task cards must assign implementation, test writing, validation, direction review, and final review separately. Record known unknowns, assumed knowns, architecture-changing questions, reference examples, forbidden paths, and where deviations must be reported.

## Context Packet

Any CodeGraph-derived entry must record the producing worktree, Git HEAD/tree binding, and matching CodeGraph worktree receipt. If the receipt is absent, reports `worktreeMismatch`, or does not match the execution worktree/state, omit that graph-derived entry and use LSP/locator/targeted-read evidence instead. Do not paste a warning-bearing result into the packet and ask Claude to resolve its provenance.

For large repositories, run `ai/locate-code.py` when scope is unclear. Include
likely files/symbols and known constraints, but allow a bounded
`exploratory-builder` to discover the implementation path. Missing exact files
is not by itself a reason for Codex to perform broad discovery first.

For `execution-only` and Checker test-writing tasks, include an executable
interface contract: exact signatures/constructor fields, one runnable call
example, the async/sync rule, and their deterministic evidence hash. A file or
symbol name alone is insufficient. If these facts are unavailable, do not mark
the packet execution-sufficient. Repository-local scratch files are forbidden
unless listed in Write paths; generated helpers use `$TMPDIR`.

Before dispatch, `ai/compile-skill-context.py` may compile a small, hash-bound
`Compiled Execution Guidance` section from the selected preset/gates and compact
routing facts. It uses only deterministic registry entries and retains each
entry's source path, anchor, source hash when available, and task-card hash.
It may rescue bounded retrieval/validation/procedure cues from an unselected
reference, but never synthesizes or trims authority, write scope, acceptance,
validation, or stop conditions. Those remain the exact task-card sections; an
unsafe registry kind, conflict, or stale card binding fails closed.

Registry entries have deterministic applicability conditions, priority, source
provenance, polarity (`positive` action or `negative` boundary), and optional
conflict group/review version. The compilation receipt records why each rule
matched, every occupied conflict group, and selected negative boundaries. Do
not encode authority or a product requirement as a compiler cue.

The compiler uses two deterministic candidate routes: top-down route facts
(preset, role, phase, gate, continuation) and bottom-up task facts (language,
task type, repository scale, CodeGraph status, and present contract sections).
`coverage` is the default strategy: active anchors are retained, then rescue
cues are chosen only when they add an uncovered procedural label. The receipt
therefore records required/covered/uncovered labels, each candidate's route,
marginal coverage, and why it was selected or excluded (including
`zero-marginal-coverage`). Source provenance includes a source hash and, when a
Markdown heading is available, a bound line span/hash. This is evidence for a
smallest sufficient cue set, not permission to infer a missing task contract.

`--context-compile-strategy anchors-only` is a benchmark-only ablation arm. It
retains selected preset/gate anchors but records all rescue cues as omitted;
compare it with the default `coverage` strategy only for identical task,
model/provider, tool-profile, and frozen-contract lanes. The `Required Report`
section is also an explicit output binding: compiler cues may remind Claude not
to confuse control files with evidence, but may never add report fields or
replace the frozen output contract.

When a standard Builder card has all five hard-contract categories (write
boundary, acceptance, validation, stop conditions, and report), dispatch may
derive a bootstrap execution capsule automatically. The capsule receipt proves
every present source section in those categories was retained byte-identically;
it must fail rather than silently omit one. This is context compression, not a
new Builder role or an execution-only readiness claim.

## Evidence

Keep long-lived state under `.worktrees/` or `ai/plans/<task-id>/`. Preserve task card, base commit, diff/diffstat, changed/untracked paths, Claude progress/report, checker output, validation commands/results, Spark invoke/skip reason, review decision, and remaining risks. Missing report prose can be reconstructed from deterministic artifacts; seeded/fallback prose cannot satisfy completion.

No model authorizes merge. Codex gives accept/revise/split/reject; humans merge.

## JSON Task Cards

JSON is the primary contract for delegated `aiwf submit` work. When JSON and
Markdown share an identity, JSON is source of truth and the generated Markdown
must not be edited. Use:

```bash
python ai/lint-task-card.py task.json
python ai/compose-profiles.py task.json --output composed.json
python ai/render-task-card.py task.json --view execution
```

Profile scalar conflicts hard-fail. Audit view retains risk and handoff detail;
execution view contains task ID, goal, scope, acceptance, validation, top-level
stop conditions, task-shape advice, enabled complex-gate counterexamples, and
conditional routing context. It omits the identity table,
full handoff, static builder protocol, progress checklist, and duplicated scope
context. Installed schemas, profiles, and examples live under `ai/schemas/`,
`ai/profiles/`, and `ai/examples/`.
