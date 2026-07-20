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

- Observable goal:
- Exploration/read boundary:
- Existing constraints and invariants:
- Known integration points:
- Non-goals:
- Required acceptance surface:

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
