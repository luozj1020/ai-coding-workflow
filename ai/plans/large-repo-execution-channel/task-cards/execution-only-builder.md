# Task Card

## ID

large-repo-execution-only-builder

## Task Type

normal

## Executor

Claude Code

## Task Mode

| Field | Value |
|---|---|
| Mode | builder |
| Builder scope | dispatcher execution projection and progress deadline only; no tests |
| Checker/Test scope | separate later task |
| Codex direction review required before checker/test? | yes |
| Mixed implementation + test-writing allowed in one Claude dispatch? | no |

## Direction / Boundary Acknowledgement

| Field | Value |
|---|---|
| Required before editing? | no |
| Blocking Codex approval required? | no |
| Maximum acknowledgement rounds | 0 |

## Claude Context Packet

| Field | Value |
|---|---|
| CodeGraph status | not indexed |
| Target files/modules | `scripts/dispatch-to-claude.sh`, `assets/task-card-template.md` |
| Relevant symbols/functions | execution profile defaults; `render_claude_task_card`; prompt rendering; seeded progress/report; heartbeat loop |
| Do not read / do not modify | tests, doctor, status/watch, docs, other scripts |
| Narrow validation commands | `bash -n scripts/dispatch-to-claude.sh` |
| Context is sufficient for execution? | yes |

## Goal

Add an opt-in execution-only Builder mode and a safe first-substantive-progress deadline so mechanical tasks start editing without repeating Codex planning.

## Handoff Contract

1. Add `CLAUDE_CODE_BUILDER_MODE=standard|execution-only`, default `standard`. Reject invalid values. Execution-only is allowed only for task mode `builder`; otherwise fail before worktree creation.
2. In execution-only mode render a minimal `CLAUDE_TASK_CARD.md` containing only execution-relevant sections: ID, Task Mode, Claude Context Packet, Goal, Handoff Contract/Required Revisions/Required Changes, Acceptance Criteria, Testing Responsibility, Validation Contract, Required Report, explicit forbidden/out-of-scope content. Preserve the full card as `TASK_CARD_FULL.md`. Do not invent a second manually maintained task card.
3. Use a short execution-only prompt: read named target files/sections, update progress, edit immediately, report blocker/split when scope is insufficient, obey testing boundary. Explicitly say not to restate or redesign the plan.
4. Add `CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS`. Default `0` in standard mode and `120` in execution-only mode. Accept non-negative integers.
5. During the existing heartbeat loop, mark first substantive progress when any of these occurs: implementation worktree change; `CLAUDE_PROGRESS.md` is no longer seeded and has meaningful changed content; a non-seeded valid report exists; or progress/report records a concrete blocker, stop, split, permission/approval need. Merely touching seeded files or changing task-card/prompt/control files is not progress.
6. If the deadline expires without substantive progress, stop only the Claude child using the existing conservative stop mechanism, classify `first_progress_timeout`, immediately finalize evidence, and leave dispatcher/checker semantics intact. This is no-progress evidence, not acceptance and not automatic Codex takeover.
7. Log mode, deadline, detected progress signal, and timeout classification in progress/status/fallback JSON. Keep standard mode behavior unchanged.
8. Add template fields explaining when execution-only is safe: exact target files, mechanical implementation, no acknowledgement/architecture discovery; otherwise standard.

## Acceptance Criteria

- Standard mode has no new deadline and existing card/prompt output remains compatible.
- Execution-only card is materially shorter and contains the complete execution contract.
- A source diff, valid progress, valid report, or explicit blocker prevents false timeout.
- Seed-only silence times out at the configured deadline and transitions directly to finalization.
- No tests are modified in this Builder phase.

## Testing Responsibility

Builder runs shell syntax only. Checker adds deterministic fake-Claude tests.

## Required Report

Edit after the bounded read; do not restate the plan. Report files, acceptance mapping, syntax outcome, deviations, and remaining risks.
