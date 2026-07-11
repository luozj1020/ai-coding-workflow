# Task: runtime inventory doctor Builder

## Task Mode

| Field | Value |
|---|---|
| Mode | builder |
| Local validation allowed? | no |

## Goal

Implement spec item 7 in `scripts/doctor_workflow.py` only.

## Scope

- Inspect the repository `.worktrees/` directory read-only.
- Report runtime entry count, approximate total disk use, oldest entry age/path, and useful age buckets.
- When accumulation is material, print preview-only cleanup guidance using existing safe cleanup helpers where possible.
- Clearly state that doctor does not delete anything.
- Handle missing/unreadable directories and filesystem errors without failing doctor.
- Keep traversal bounded enough for hundreds or thousands of entries.

## Boundaries

- Edit only `scripts/doctor_workflow.py`.
- Do not delete, prune, reset, or clean anything.
- Do not edit tests/docs or run tests.
