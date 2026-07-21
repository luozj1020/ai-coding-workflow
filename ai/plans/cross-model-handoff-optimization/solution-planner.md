<!-- task-card-components: preset=solution-planner; gates=large-repo; schema=1 -->

# Task Card

<!-- Generated from selected components. Fill concise task-specific facts; delete unused placeholder rows. -->

## ID

cross-model-handoff-state-bus

## Task Mode

| Field | Value |
|---|---|
| Mode | builder |

## Goal

Produce a validated solution contract that turns the supplied cross-model
handoff optimization plan into independently executable repository slices,
with the first slice delivering useful handoff measurement without changing
existing workflow outcomes.

## Scope

- Write paths: `solution-contract.draft.json`, `CLAUDE_PROGRESS.md`, `CLAUDE_REPORT.md`
- Read paths: tracked `scripts/`, `schemas/`, `tests/`, `references/`, `assets/`, `SKILL.md`, `README.md`
- Forbidden paths: product/source edits in this planning round; installed/untracked `ai/`; `.git/`; the source DOCX
- Explicitly out of scope: hidden-state mapping, KV-cache sharing, provider APIs, implementation, tests, broad redesign unrelated to handoff state.

## Claude Context Packet

| Field | Value |
|---|---|
| Target files/modules | `scripts/event_writer.py`, dispatch/finalization and summary scripts, `scripts/install_workflow.py`, `schemas/`, `tests/` |
| Exact symbols/tests | Existing run-event writer/schema, workflow summaries/economics, installer registration tests |
| Root-cause evidence or relevant excerpt | Current handoffs serialize full task cards/reports; receiver reconstruction, repeated reads/search, rediscovery, and handoff-induced revision are not directly measured. |
| Reference implementation/source of truth | Existing run events, artifact manifests, review packets, worktree state hashes, and deterministic JSON tooling |
| Known constraints | Canonical sources are tracked top-level paths; installed `ai/` is generated. Python 3.9 compatibility. Missing metrics must be `unknown`, never zero or estimated. Preview must make zero model calls. Existing Markdown task cards remain compatible. |
| Do not read/modify | Untracked installed `ai/`, `.git/`, unrelated workflow features |
| Context sufficient for execution? | yes |
| Execution-only eligible? | no; this is solution planning |

## Handoff Contract

- Must do: map the document's phases onto existing primitives; define a minimal, backward-compatible Phase 0 and State IR sequence; make each implementation slice independently testable.
- Must not do: edit source or tests; invent measured values; require an extra ACK model call; duplicate canonical run/event data without justification.
- May decide: exact schema versions, module boundaries, and whether Phase 0 plus the smallest State IR foundation should be one or two slices.
- Stop and report when: the existing architecture makes a required invariant impossible, or a public/user-facing compatibility decision is needed.

## Acceptance Criteria

- [ ] `solution-contract.draft.json` validates with `python ai/solution-contract.py validate solution-contract.draft.json`.
- [ ] The contract covers the end-state concepts: State IR, event log, delta/ACK, rejected hypotheses, evidence references, acceptance/review receipts, ownership continuity, and communication-aware routing.
- [ ] The first implementation slice is narrow: handoff event schema/recording/summary metrics integrated with existing run artifacts, with unknown semantics and preview behavior specified.
- [ ] Later slices preserve compatibility and have explicit dependencies, write scopes, acceptance IDs, and deterministic validation.
- [ ] The design reuses existing event, hash, installer, summary, and economics primitives where appropriate instead of creating parallel infrastructure.

## Testing Responsibility

| Responsibility | Owner |
|---|---|
| Implementation | Claude in later independently routed slices |
| Test writing | Claude in the corresponding implementation slice |
| Narrow validation | Claude in each slice; Codex reruns deterministic checks |
| Checker model dispatch | no; deterministic schema/unit/integration evidence should suffice |
| Direction review | Codex |
| Final review | Codex |

## Validation Contract

- Local validation allowed: yes
- Exact narrow command: `python ai/solution-contract.py validate solution-contract.draft.json`
- Required evidence: validated contract, repository integration points, slice dependency order, explicit compatibility and migration rules

## Execution Progress

- [ ] Read this card and update `CLAUDE_PROGRESS.md`.
- [ ] Complete the assigned responsibility.
- [ ] Write the required report.

## Stop Conditions

- Scope, solution, or required context materially expands.
- A required contract is ambiguous or unavailable.

## Claude Solution Planner Contract

| Field | Value |
|---|---|
| Planning owner | Claude |
| Adversarial review owner | Codex |
| Maximum Codex planning review rounds | 1 |
| Required durable output | `solution-contract.draft.json` |
| Source edits allowed | no |
| Contract state after review | frozen or rejected |

- Produce one coherent end-state design, invariants, acceptance criteria, and
  independently executable slices inside the declared exploration boundary.
- Prefer decisions that reduce downstream coupling and repeated context reads.
- Record genuine unknowns; do not hide them behind optional implementation ideas.
- Do not write source code, tests, or prose-only repository summaries in this phase.
- Exit after the structured draft validates. Codex owns the single adversarial
  review and contract freeze.

## Solution Contract Inputs

- Observable goal: reduce cross-model payload repetition and context reconstruction by evolving from full-text handoffs to shared state plus deltas, while preserving task success and existing compatibility paths.
- Exploration/read boundary: tracked workflow implementation, schemas, tests, docs, and installer only.
- Existing constraints and invariants: code/tests/tool output remain authoritative; state updates are event-derived and hash-bound; frozen decisions cannot be silently overwritten; stale state/deltas fail closed; missing metrics are unknown; legacy runs remain readable; Markdown task cards remain supported; model outputs alone are not repository truth.
- Known integration points: run event writer/schema, dispatcher artifacts/finalization, run summaries, evidence hashing, worktree state hashing, context cache, review packets/decisions, route-task and workflow economics, installer and doctor.
- Non-goals: model activation/KV transfer, complete chain-of-thought persistence, forcing express-lane tasks into multi-model flow, replacing state synchronization with larger cards.
- Required acceptance surface: deterministic hashes, traceable events, delta base-state binding, short ACK validation, negative-knowledge evidence/reopen rules, stale evidence handling, changed-acceptance-only review, original-builder continuation, handoff metrics and routing explanations.

## Required Draft Shape

The JSON draft must contain `schema_version`, `task_id`, `goal`, `end_state`,
`invariants`, `non_goals`, `unknowns`, `acceptance`, and `slices`. Each slice
declares its write scope, dependencies, and acceptance IDs. Validate it with:

```bash
python ai/solution-contract.py validate solution-contract.draft.json
```

## Stop Conditions

- The observable goal or exploration boundary is not sufficiently defined.
- A product/API/data decision requires human authority.
- No plan can produce independently reviewable implementation slices.

## Worktree / Large Repo Strategy Gate

- Repository scale / I/O evidence: medium Python/shell workflow repository; CodeGraph is initialized; canonical tracked sources are separate from generated installed `ai/` files.
- Strategy: fresh
- Large-repo mode/evidence tradeoff: use CodeGraph/targeted reads; retain full planning artifacts and validation output.
- Reset authorization, if any: none.
- Cleanup/retention expectation: retain dispatcher worktree and artifacts for Codex review; do not merge.
