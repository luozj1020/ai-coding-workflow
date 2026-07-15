# Task Card and Evidence Policy

Load this reference when authoring task cards/specs, choosing gates, building Context Packets, using JSON task cards, or assembling final evidence.

## Planning Gates

Write the full task card only after pre-card routing selects delegation/spec-first. Use `ai/task-card-template.md`. Fill only gates relevant to the task, but never omit a material stop condition:

- Spec Gate for ambiguous product, UX, API, or data-model direction.
- Root Cause Gate for bugs, regressions, or repeated failed fixes.
- Test-First/TDD Contract when red/green evidence is acceptance-critical.
- Goal Loop Contract for bounded iterative work.
- Advisor Gate for one-call strategic advice.
- Worktree/Large Repo and Parallel gates when those execution paths apply.
- Finish Branch Gate before claiming readiness for human merge.

Task cards must assign implementation, test writing, validation, direction review, and final review separately. Record known unknowns, assumed knowns, architecture-changing questions, reference examples, forbidden paths, and where deviations must be reported.

## Context Packet

For large repositories, run `ai/locate-code.py` when ownership is unclear. Include 1–5 target files, exact symbols, bounded root-cause excerpts, one correct reference pattern, forbidden paths, constraints, measurable acceptance, and exact narrow validation. If the packet is incomplete, Claude should stop-and-report rather than scan broadly.

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
