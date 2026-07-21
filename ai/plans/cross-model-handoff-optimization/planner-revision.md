<!-- task-card-components: preset=revision; gates=none; schema=1 -->

# Task Card

<!-- Generated from selected components. Fill concise task-specific facts; delete unused placeholder rows. -->

## ID

cross-model-handoff-state-bus-plan-revision

## Task Mode

| Field | Value |
|---|---|
| Mode | builder |

## Goal

Correct the validated draft contract's blocking coverage and slice defects,
then leave a scope-clean, validated `solution-contract.draft.json` ready for
Codex freeze review.

## Scope

- Write paths: `solution-contract.draft.json`, `CLAUDE_PROGRESS.md`, `CLAUDE_REPORT.md`; delete the prior temporary `_build_contract.py`
- Read paths: existing planning artifacts and the already-read tracked workflow sources only
- Forbidden paths: tracked repository source/tests/docs; new helper scripts; `.git/`
- Explicitly out of scope: implementation, new repository exploration, model activation/KV work

## Claude Context Packet

| Field | Value |
|---|---|
| Target files/modules | `solution-contract.draft.json` only, plus control/report artifacts |
| Exact symbols/tests | Contract acceptance and slices S1–S5 |
| Root-cause evidence or relevant excerpt | Draft is schema-valid but S1 claims automatic handoff emission without an integration write path, assigns AC-2 outside S1 capability, omits required Phase-0 metrics, and does not cover Evidence Object Store/Pull Context Broker/explicit leases and handoff-tax stages. |
| Reference implementation/source of truth | Accepted draft SHA-256 `db4a1c5febef87e8ffb86cdbe50310553839aa50ae4f277f55128b22585b2762`; original task card constraints |
| Known constraints | One delta-only revision; preserve valid goal/invariants/non-goals; no extra generator helper may remain. |
| Do not read/modify | Tracked files and unrelated worktree artifacts |
| Context sufficient for execution? | yes |
| Execution-only eligible? | yes |

## Handoff Contract

- Must do: fix every blocking correction below, validate the JSON, remove `_build_contract.py`, and complete the short report.
- Must not do: repeat repository exploration, edit tracked files, or broaden into implementation.
- May decide: exact number of later slices, provided every DOCX phase is represented and dependencies remain independently executable.
- Stop and report when: the contract schema prevents a required correction.

## Acceptance Criteria

- [ ] Contract validates and no `_build_contract.py` remains.
- [ ] S1's acceptance IDs are achievable by S1 and its write scope includes the actual dispatch/run integration point that emits one event per cross-model handoff.
- [ ] S1 explicitly records task-card/review-packet/payload bytes, receiver reads/searches, time to first meaningful action, repeated exploration/rediscovery, and handoff-induced revision; unavailable observations are `unknown`.
- [ ] Contract has executable later slices for State IR, delta/ACK, rejected-hypothesis ledger, evidence object store, pull context broker, acceptance graph/review receipt, ownership lease/continuation, and handoff-tax routing.
- [ ] State identifiers/hashes and delta base/target binding use consistent terminology.

## Testing Responsibility

| Responsibility | Owner |
|---|---|
| Implementation | Claude revision of planning artifact only |
| Test writing | not applicable |
| Narrow validation | Claude, then Codex |
| Checker model dispatch | no; deterministic schema validation sufficient |
| Direction review | Codex |
| Final review | Codex |

## Validation Contract

- Local validation allowed: yes
- Exact narrow command: `python ai/solution-contract.py validate solution-contract.draft.json`
- Required evidence: validation output, scope-clean status, correction checklist

## Execution Progress

- [ ] Read this card and update `CLAUDE_PROGRESS.md`.
- [ ] Complete the assigned responsibility.
- [ ] Write the required report.

## Stop Conditions

- Scope, solution, or required context materially expands.
- A required contract is ambiguous or unavailable.

## Revision Delta

<!-- Do not repeat the original plan. Bind the accepted baseline artifact and list only corrections. -->

- Accepted baseline task/diff: schema-valid draft SHA-256 `db4a1c5febef87e8ffb86cdbe50310553839aa50ae4f277f55128b22585b2762`; its overall direction and five foundation concepts are accepted.
- Preserve unchanged: goal, compatibility invariants, non-goals, deterministic/hash-bound/event-derived design, unknown-not-zero semantics, preview zero-model-call invariant.
- Required corrections: (1) remove AC-2 from S1 or redefine it to actual handoff-event emission; (2) add the real dispatcher/run integration write path; (3) cover all Phase-0 measurements named above; (4) split/extend later slices to cover the omitted DOCX phases; (5) resolve terminology inconsistencies; (6) turn design choices that block implementation into explicit slice decisions rather than leaving them as global unknowns.
- Exact files/symbols: `solution-contract.draft.json`; delete `_build_contract.py`.
- New write paths allowed: none beyond standard `CLAUDE_PROGRESS.md` and `CLAUDE_REPORT.md`.
- Narrow validation: `python ai/solution-contract.py validate solution-contract.draft.json` and `test ! -e _build_contract.py`.
- Re-route if: a required concept cannot fit the solution-contract schema without changing that schema.

## Required Report

- Each correction completed/not completed
- Files and symbols changed in this continuation
- Deviations from the accepted baseline
- Validation evidence and remaining blocker
