# Task: authoritative validation summary Builder

## Task Mode

| Field | Value |
|---|---|
| Mode | builder |
| Local validation allowed? | no |

## Goal

Implement spec item 5 in `scripts/summarize-loop-run.py` only.

## Scope

- Parse Claude validation state separately from checker helper state.
- Recognize approval/permission-blocked validation conservatively from report/progress/status evidence.
- Compute an authoritative final validation state with precedence: checker FAILED -> failed; checker ALL GREEN -> passed; checker skipped -> skipped/policy; otherwise retain Claude state or unknown.
- Preserve provenance fields so `claude=blocked_by_approval`, `checker=ALL GREEN`, `final=passed` can coexist without contradiction.
- Add fields to JSON and Markdown output without removing existing fields.

## Boundaries

- Edit only `scripts/summarize-loop-run.py`.
- Do not edit tests/docs/benchmark code.
- Do not run tests.
