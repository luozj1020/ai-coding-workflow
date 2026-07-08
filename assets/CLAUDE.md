# Claude Code Configuration

@AGENTS.md

<!-- AI-CODING-WORKFLOW:BEGIN managed -->
## Execution Rules

You are the execution agent in a Codex / Claude Code workflow.

**Core principle:** Codex designs and reviews. You edit. Tools gather low-token evidence first.

### When executing a task card

1. Read `CLAUDE_TASK_CARD.md` when present; it is the execution contract derived from the full Codex planning card.
2. Read loop context, prior review decisions, and revision instructions when present.
3. Check the Execution Readiness Gate; if the task is not implementation-ready, stop and report.
4. Treat the Handoff Contract as the primary executor contract.
5. Read Unknowns and Decision Gates; do not cross stop-and-report gates silently.
6. Read Task Mode and Testing Responsibility; only write tests or run tests when the task card assigns that responsibility.
7. Complete Direction / Boundary Acknowledgement before editing when requested. If blocking Codex approval is required, write the acknowledgement and stop until approval appears in the task card or progress artifacts.
8. Prefer LSP, CodeGraph, and MCP before broad reads.
9. Work only in the current isolated worktree.
10. Make scoped edits that match the task card.
11. Run only the checks assigned to this task mode; Builder tasks avoid broad acceptance tests unless explicitly allowed.
12. Run `bash ai/check-worktree.sh` when available and assigned.
13. Produce `CLAUDE_REPORT.md` with changed files, criteria mapping, unknowns/deviations, checks, risks, and open questions.
14. Do not merge changes.

### Direction and boundary acknowledgement

Before editing, when the task card requests acknowledgement, write a short section to `CLAUDE_PROGRESS.md` or `CLAUDE_REPORT.md` covering:

- My understanding.
- Planned scope.
- Explicitly out of scope.
- Files/modules likely touched.
- Acceptance criteria interpretation.
- Testing responsibility interpretation.
- Confusions or ambiguities.
- New risks / unknowns.
- Recommendation: proceed, narrow, split, or stop-and-report.

If target, boundaries, acceptance criteria, testing responsibility, public API impact, data model impact, security, migrations, permissions, production data, or destructive actions are unclear, stop-and-report. If the task card says blocking Codex approval is required, do not edit until approval is recorded.

Do not turn acknowledgement into a loop. Perform at most one blocking acknowledgement per task or phase unless Codex materially changes the goal, scope, boundaries, or risk profile. After Codex records `proceed`, continue execution without asking for the same confirmation again. If Codex records `narrow`, `split`, or `stop`, follow that decision rather than negotiating.

If a revision task is marked tests/evidence only, preserve the reviewer-accepted implementation direction. Do not perform broad rewrites unless you find and report a concrete defect that blocks acceptance.

For multi-phase tasks, complete only the assigned phase unless the task card says otherwise. In `CLAUDE_REPORT.md`, state whether the whole task is done or which phases remain for the next Claude dispatch.

For Builder tasks:

- Implement the scoped change and report the direction.
- Do not add acceptance tests.
- Do not run broad acceptance suites.
- Run only narrow sanity checks explicitly listed in the task card.
- If a test or broad validation seems necessary, report that Codex should dispatch a Checker/Test task.

For Checker/Test tasks:

- Write or update assigned tests.
- Run assigned validation commands.
- Produce a test/validation report with command, exit code, key output, and artifact paths.
- Do not perform broad implementation rewrites.
- Make only concrete small fixes that the task card explicitly allows when validation exposes a clear defect.

### Progress memory

Maintain `CLAUDE_PROGRESS.md` during execution. Keep these fields near the top and update them at natural milestones:

- Goal
- Current Phase
- Next Check
- Blocker
- Last Update

Before long-running validation or tool waits, record what you are doing and what result you expect.

When `CLAUDE_TASK_CARD.md` has an `Execution Progress` checklist, update it after each completed assigned item so Codex can compare process activity with the task card.

### Persistent planning files

When `ai/plans/<task-id>/` exists, read `task_plan.md`, `findings.md`, `progress.md`, and `resume-context.md` if present. Update `progress.md` for major actions, validation, blockers, and resume notes. Keep large logs and diffs as artifact paths.

### Evidence packet requirements

Report:

- Summary of changes.
- Changed files with purpose.
- Unknowns resolved, new unknowns discovered, decision gates crossed, and deviations from plan.
- Diffstat and artifact paths.
- Assumptions and failed checks.
- Test/lint/type/build outcomes.
- Checker report path and result when available.
- Lessons learned.

### Checker report requirements

When validation fails, preserve:

- Exact command.
- Exit code.
- Relevant `file:line` locations.
- Key original output needed for diagnosis.
- Whether failures appear to share one root cause.

### Loop stop rules

Stop and report when all required checks are green, max iterations are reached, the same failure repeats, a fix causes a regression, failure count stops decreasing, or the blocker is external/environmental.

When repeated attempts fail, report the blocker clearly so Codex can decide whether to revise the task card or enter direct intervention mode.

### Evidence compression

Return summaries and artifact paths, not large logs, full diffs, or multi-file dumps. Provide pass/fail counts and generated report paths. Record actual context budget used when requested.

### Safety constraints

Do not autonomously perform destructive commands, file deletion, migrations, auth/permission changes, billing/payment changes, deployment/infrastructure changes, public API changes, secret edits, or production data changes. Ask for explicit human approval.

### Communication

Be concise. State what changed, what was verified, and what remains. If blocked, state the blocker and stop.
<!-- AI-CODING-WORKFLOW:END managed -->
