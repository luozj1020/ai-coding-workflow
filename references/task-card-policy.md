# Task Card and Evidence Policy

Load this reference when authoring task cards/specs, choosing gates, building Context Packets, using JSON task cards, or assembling final evidence.

## Planning Gates

Write a task card only after pre-card routing selects delegation/spec-first. Codex reads the small `ai/task-card-components/catalog.md`, selects one preset and only material gates, then lets the local zero-model composer read and join their bodies:

```bash
python ai/compose_task_card.py --preset builder --gate root-cause --output ai/task-cards/TASK.md
```

When routing facts already exist, let the deterministic selector choose the
minimal preset and gates:

```bash
python ai/compose_task_card.py --select-from routing-facts.json --output ai/task-cards/TASK.md
```

If those facts select `codex-fast-path`, the command returns `skip_card=true`
and writes no delegation card.

The integrated `aiwf run` path performs the same selection only after routing,
fills `delegation-task-card.md` from reviewed Task JSON and routing facts, and
inlines bounded context into that one card. It does not create a duplicate
standalone Context Packet or pass the source JSON to the Markdown dispatcher.

The local composer fills deterministic fields from routing facts; Spark may
return structured missing fields. Codex reviews only the compact goal,
boundaries, acceptance, and high-risk invariants instead of authoring a long
card. Do not read `ai/task-card-template.md` by default; it is compatibility-only.
Never omit a material stop condition:

Builder presets ship with conservative Post-Implementation defaults: changed-file self-review is enabled, while narrow validation, documentation, and long validation are disabled/not-required until Codex replaces them with exact assignments. Do not leave ambiguous placeholders or enable broad tail work merely to make the card look complete.

- Spec Gate for ambiguous product, UX, API, or data-model direction.
- Root Cause Gate for bugs, regressions, or repeated failed fixes.
- Test-First/TDD Contract when red/green evidence is acceptance-critical.
- Goal Loop Contract for bounded iterative work.
- Advisor Gate for one-call strategic advice.
- Worktree/Large Repo and Parallel gates when those execution paths apply.
- Finish Branch Gate before claiming readiness for human merge.

Use the `revision` preset for narrowed retries and reviewer-requested corrections. Bind the accepted baseline and describe only the delta; do not copy the original task card. The dispatcher preserves the composed card as the full audit artifact and derives Claude's current-phase view with an execution-section allowlist.

Use `exploratory-builder` for a bounded new feature whose goal is stable but
implementation path remains unclear. It must produce source changes plus
evidence, not a prose-only repository survey. Use `solution-planner` first for
large/multi-phase work, then bind each implementation card to the frozen
contract and return execution ownership to Claude.

Use `solution-planner` only when pre-card routing selects
`claude-converge-codex-freeze`. Claude must produce the structured solution
contract named by the card. Codex reviews that artifact once and classifies every
finding as `blocking`, `recommended`, `backlog`, or `spec-change`. Resolve
blocking findings; defer recommendations/backlog; reject or explicitly
incorporate spec changes. Then freeze with `aiwf solution-contract freeze`.
Implementation cards bind the frozen contract hash and include only their slice;
they must not invite Claude to repeat repository-wide planning. Codex performs
one adversarial freeze review, not full task-card authorship plus replanning.

Testing responsibility must state whether Checker model dispatch is required.
Default to local deterministic validation. Select Checker only for assigned test
writing, long validation/log processing, or an independent evidence responsibility
that reduces Codex work; otherwise record `checker skipped: deterministic evidence sufficient`.

Task cards must assign implementation, test writing, validation, direction review, and final review separately. Record known unknowns, assumed knowns, architecture-changing questions, reference examples, forbidden paths, and where deviations must be reported.

## Context Packet

For large repositories, run `ai/locate-code.py` when scope is unclear. Include
likely files/symbols and known constraints, but allow a bounded
`exploratory-builder` to discover the implementation path. Missing exact files
is not by itself a reason for Codex to perform broad discovery first.

## Evidence

Keep long-lived state under `.worktrees/` or `ai/plans/<task-id>/`. Preserve task card, base commit, diff/diffstat, changed/untracked paths, Claude progress/report, checker output, validation commands/results, Spark invoke/skip reason, review decision, and remaining risks. Missing report prose can be reconstructed from deterministic artifacts; seeded/fallback prose cannot satisfy completion.

No model authorizes merge. Codex gives accept/revise/split/reject; humans merge.

## JSON Task Cards

JSON is optional. When JSON and Markdown share a task identity, JSON is source of truth. Use:

```bash
python ai/lint-task-card.py task.json
python ai/compose-profiles.py task.json --output composed.json
python ai/render-task-card.py task.json --view execution
```

Profile scalar conflicts hard-fail. Audit view retains risk and handoff detail; execution view contains only goal, scope, acceptance, validation, and stop conditions. Installed schemas, profiles, and examples live under `ai/schemas/`, `ai/profiles/`, and `ai/examples/`.
