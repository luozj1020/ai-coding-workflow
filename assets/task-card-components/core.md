# Task Card

## ID

## Task Mode

| Field | Value |
|---|---|
| Mode | {{TASK_MODE}} |

## Goal

<!-- one observable outcome -->

## Scope

- Write paths:
- Read paths:
- Forbidden paths:
- Explicitly out of scope:

## Claude Context Packet

| Field | Value |
|---|---|
| Target files/modules | |
| Exact symbols/tests | |
| Exact interface signatures | |
| Runnable construction/call example | |
| Async/sync contract | |
| Interface evidence hash | |
| Root-cause evidence or relevant excerpt | |
| Reference implementation/source of truth | |
| Known constraints | |
| Do not read/modify | |
| Context sufficient for execution? | yes/no |
| Execution-only eligible? | yes/no |

## Handoff Contract

- Must do:
- Must not do:
- May decide:
- Stop and report when:

## Acceptance Criteria

- [ ]

## Testing Responsibility

| Responsibility | Owner |
|---|---|
| Implementation | |
| Test writing | |
| Narrow validation | |
| Checker model dispatch | no / yes + expected Codex work reduction |
| Direction review | Codex |
| Final review | Codex |

## Validation Contract

- Local validation allowed: yes/no; validation required: yes/no
- Exact narrow command:
- Required evidence:
- Small isolated task: prefer the smallest reusable implementation or fixture; target <=30 changed lines or explain why the frozen contract requires more.
- Use `$TMPDIR` for scratch/generated validation helpers; repository-local scratch is forbidden unless listed in Write paths.

## Execution Progress

- [ ] Read this card, update `CLAUDE_PROGRESS.md`, and complete the assigned responsibility.
- [ ] Write the required report.

## Stop Conditions

- Scope, solution, or required context materially expands.
- A required contract is ambiguous or unavailable.
