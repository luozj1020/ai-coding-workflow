# Agents

This file defines shared rules for AI agents working in this repository.

<!-- AI-CODING-WORKFLOW:BEGIN managed -->
## Core Principle

**Codex designs and reviews. Claude edits. Tools gather low-token evidence first.**

- Codex/GPT plans, gathers low-token evidence, writes task cards, and reviews results.
- Claude Code implements scoped edits in isolated worktrees and returns compressed evidence.
- LSP, CodeGraph, and MCP are preferred before broad reads or repository scans.

## Workflow

Use the explicit loop: OBSERVE -> PLAN -> DISPATCH -> EXECUTE -> VERIFY -> REVIEW -> LEARN.

- PLAN with `ai/task-card-template.md`.
- DISPATCH with `ai/dispatch-to-claude.sh` or `ai/run-loop.sh`; dispatch preserves the full Codex task card and renders a smaller Claude execution card.
- Split risky work into Builder Claude (implementation only) followed by Checker/Test Claude (tests, validation, report) after Codex accepts the implementation direction.
- Before dispatch, check for mixed builder/checker responsibilities, dirty-source risk, permission/tool approval risk, long-running validation, and missing progress-artifact requirements so a later stall is not misdiagnosed as Claude execution failure.
- REVIEW with `ai/review-with-codex.sh` or loop output.
- Do not merge automatically; human review and merge remain separate.

## Codex Intervention Policy

After a Claude execution round, Codex normally reviews only. It may edit directly only when Claude has repeatedly missed the target, the loop hit a stop condition, or Claude is unavailable. Record the failed attempts, intervention reason, edit scope, and validation evidence.

Claude no-progress, early exit, invalid result, or one failed attempt is evidence for a tighter next task card, not permission for Codex to patch. Prefer smaller Claude revisions with clearer acceptance criteria before takeover.

Dirty source or stale HEAD is a delegation blocker, not a Codex takeover trigger. Codex should first restore a reliable Claude base by committing an accepted phase, stashing/patching uncommitted source changes, refreshing workflow files, re-dispatching from updated HEAD, requesting an explicit dirty-source override, or stopping for human input. Codex may take over only when restoration is impossible/unsafe and a current-task threshold or explicit human override is recorded.

Prior-session Claude failures are carry-forward context, not automatic takeover permission. A fresh task or session should re-dispatch Claude unless current-task artifacts prove the same threshold or the human explicitly asks Codex to take over.

Missing Claude `result.json`, `CLAUDE_REPORT.md`, or acceptance evidence is an evidence gap, not automatically an implementation failure. If the diff matches the task card and assigned checks pass, Codex should reconstruct a minimal evidence packet from artifacts, diff, and verification output instead of re-dispatching only to get prose. Re-dispatch Claude when the task card explicitly assigned test writing, test execution, or acceptance evidence to Claude and that evidence cannot be recovered.

Seeded and fallback reports are not valid Claude-owned reports. A `CLAUDE_REPORT.md` containing `AI-CODING-WORKFLOW:DISPATCH-SEEDED-REPORT` or an archived report containing `AI-CODING-WORKFLOW:DISPATCH-FALLBACK-REPORT` must be classified as `seeded report only` or `no valid report`, not completion.

If a tightened second Claude task also exits without result/report and without useful progress, treat it as current-task repeated failure. A Codex takeover must cite both attempts, salvage any reviewer-accepted first-round direction, avoid broad rewrites, and add only the missing implementation, acceptance tests, and evidence needed to satisfy the task card.

If one Builder attempt exits after acknowledgement with no code diff and no valid Claude-owned report, tighten and re-dispatch once. If the tightened Builder attempt again exits after acknowledgement with no code diff and no valid report, Codex may perform scoped takeover after recording both attempt artifacts.

If the first attempt produced a useful scoped diff but no valid report/evidence, Codex may accept that direction only after running the assigned narrow checks. If a tightened retry produces no useful progress, Codex may salvage the accepted direction in scoped takeover.

For multi-phase or multi-part tasks, accepting one Claude round closes only that phase. Codex may reprioritize or rewrite the next task card, but remaining implementation/test-writing phases stay Claude-owned unless a current-task takeover threshold is reached or the human explicitly asks Codex to take over.

## Context Lifecycle

Keep default context small and file-backed:

- Codex task cards should expose material unknowns and decision gates before dispatch.
- Codex should complete the Execution Readiness Gate and Handoff Contract before implementation dispatch.
- Codex should fill the Phase Responsibility Matrix so each phase has a clear owner and explicit non-owner duties.
- Store long-lived state in `.worktrees/` artifacts or `ai/plans/<task-id>/`.
- Preserve failures, checker reports, progress logs, and review decisions as artifact paths.
- Use `CLAUDE_PROGRESS.md` for Goal, Current Phase, Next Check, Blocker, and Last Update.
- Use optional `*.network.log` metadata only when `CLAUDE_CODE_NETWORK_MONITOR=1`; it may support network/proxy/auth attribution but does not expose request contents and is not implementation evidence.
- Use `loop-events.jsonl` for append-only loop orchestration when available.
- If Claude is quiet but the implementation worktree is changing, review the partial diff against the plan before interrupting.
- Claude reports should record unknowns resolved, new unknowns discovered, and deviations from plan.
- Codex reviews should produce next-task-ready instructions for revise, split, or reject decisions.

## Loop Engineering Validation Contract

Builder and checker responsibilities must remain separate:

- Builder Claude implements scoped changes and reports direction. Builder tasks do not write acceptance tests or run broad suites unless the task card explicitly allows narrow sanity checks.
- A single Claude task that mixes implementation, test writing, broad validation, and phase stop gates must be split unless the task card explicitly marks `mixed-exception` and explains why one combined pass is intentional.
- Before editing, Claude performs Direction / Boundary Acknowledgement when requested. It must state understanding, scope, out-of-scope boundaries, likely files, acceptance interpretation, testing responsibility, confusion, risks, and proceed/narrow/split/stop recommendation.
- Use blocking Codex approval for ambiguous, multi-file, high-risk, public API, data model, security, migration, permission, or production-impacting work. If Claude has material confusion, it must stop-and-report instead of guessing.
- If acknowledgement is non-blocking and Claude recommends `proceed`, Claude must continue implementation in the same run. It must not stop after acknowledgement unless it records a concrete blocker, stop condition, or explicit approval need.
- Avoid acknowledgement loops: one blocking acknowledgement per task or phase unless Codex materially changes goal, scope, boundaries, or risk. Codex must decide proceed, narrow-once/re-dispatch, split, or stop; Claude must not ask for the same approval again after proceed.
- Treat `acknowledgement only` as no implementation progress: no code diff, no valid Claude-owned report, and only acknowledgement/proceed text.
- Codex reviews builder direction before validation work: wait when the partial diff matches the plan, interrupt and narrow when it runs off-plan, and take over only after the current-task threshold is met.
- When Claude appears stuck, first attribute the stall: task-card ambiguity, mixed-role assignment, dirty source/stale HEAD, permission/tool approval blocker, long-running validation, missing progress updates, external environment, or true Claude no-progress. Use the monitoring escalation ladder: L0 compact watch heartbeat/progress, L1 partial diff review, L2 status/details after repeated suspect snapshots, L3 network/status/diff corroboration after the interrupt window, and L4 kill only after multiple evidence sources agree useful progress is unlikely.
- When network diagnostics are enabled, inspect socket summary and healthcheck status before attributing a quiet run to Claude no-progress.
- When dirty source or stale HEAD is the attribution, fill the Delegation Restoration Gate and try to restore a clean updated dispatch base before considering takeover.
- Checker/Test Claude writes or updates assigned tests, runs assigned validation, and reports results. Checker/Test tasks should not perform broad implementation rewrites; only concrete small fixes allowed by the task card are permitted.
- Use `ai/check-worktree.sh` when available.
- Forward checker failures with command, exit code, key output, and `file:line` details.

Stop when work is accepted, max iterations are reached, the same failure repeats, a fix regresses prior behavior, failure count stops improving, or the blocker is external.

## Token Budget and Delegation Contract

Codex should stay on low-token evidence. Delegate broad reads, long logs, exhaustive scans, and multi-file implementation to Claude Code by default.

Task cards must separate test-code scope from test-execution responsibility. Claude may write tests when the user asks for test coverage or Codex marks tests acceptance-critical; otherwise say whether Claude only changes implementation, runs tests, or leaves verification to Codex/humans.

Codex-only planning fields, context budgets, delegation gates, and control-plane continuity fields belong in the full task card. Claude should receive the generated execution card containing only the current execution contract and necessary evidence.

Builder Claude should not add tests or run broad acceptance suites unless explicitly assigned a narrow sanity check. Checker/Test Claude should write or update tests, run assigned validation, and avoid broad implementation rewrites. Claude must update `CLAUDE_PROGRESS.md` and any `CLAUDE_TASK_CARD.md` progress checklist after each completed item.

For repeated dispatches, commit or otherwise restore prior task-card artifacts before re-dispatch. Only the current task card may be exempt from dirty-source checks; previous untracked task cards are delegation blockers unless explicitly treated as approved control-plane artifacts.

### Evidence compression

Claude must return compressed evidence: summaries and artifact paths, not pasted large logs, full diffs, or whole-file dumps. Include changed-file summaries, check pass/fail counts, and generated report paths.

## Persistent Planning Files

For long tasks, use `ai/plans/<task-id>/`:

- `task_plan.md` for phases and decisions.
- `findings.md` for durable context evidence.
- `progress.md` for current state and validation.
- `resume-context.md` generated by `ai/session-catchup.py`.

## Safety Rules

Explicit human approval is required for destructive commands, file deletion, migrations, auth/permission changes, billing/payment changes, deployment/infrastructure changes, public API changes, secrets, and production data changes.

When in doubt, stop and ask.
<!-- AI-CODING-WORKFLOW:END managed -->

## Project-specific rules

Add project-specific rules here. This section is user-owned and should not be overwritten by the workflow installer.
