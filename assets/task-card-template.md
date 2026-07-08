# Task Card

## ID

<!-- e.g., PROJ-123 -->

## Task Type

<!-- normal | control-plane -->

## Executor

<!-- Claude Code | Codex control-plane hotfix | human -->

## Task Mode

<!-- builder | checker-test | mixed-exception | control-plane. Prefer builder followed by checker-test for non-trivial work. -->

| Field | Value |
|-------|-------|
| Mode | builder / checker-test / mixed-exception / control-plane |
| Builder scope | implementation only; no acceptance test writing or broad test execution unless narrow sanity check is explicitly listed |
| Checker/Test scope | write/update tests, run assigned validation, produce report; no broad implementation rewrite unless a concrete small fix is explicitly allowed |
| Codex direction review required before checker/test? | yes/no |
| Mixed implementation + test-writing allowed in one Claude dispatch? | no / yes, mixed-exception rationale: |

Mixed-task guard: if a task asks one Claude dispatch to implement, write tests, run validation, and stop at phase gates, prefer splitting it into a Builder task followed by a Checker/Test task. Use `mixed-exception` only when the task is intentionally tiny or the human explicitly asks for a single combined pass; record the rationale so a later stall is not misattributed to Claude execution quality.

## Phase Responsibility Matrix

<!-- Codex completes this before dispatch. Keep the active phase explicit so Claude does not infer testing or confirmation duties. -->

| Phase | Codex owns | Claude owns | Explicitly not Claude-owned | Explicitly not Codex-owned |
|-------|------------|-------------|-----------------------------|----------------------------|
| OBSERVE / PLAN | Evidence gathering, unknowns, task card, scope, acceptance criteria | N/A unless this is an exploration task | Product edits | Broad implementation without dispatch |
| BUILDER EXECUTE | Progress observation, partial diff direction review | Scoped implementation, progress updates, direction report | Acceptance tests and broad validation unless explicitly allowed | Direct implementation edits while Builder has not hit threshold |
| DIRECTION REVIEW | Decide wait / revise / split / dispatch checker-test / takeover threshold | Provide report/progress/blockers | Repeated confirmation after proceed | Validating an unaccepted direction |
| CHECKER / TEST | Dispatch validation task and review evidence quality | Assigned test writing, assigned validation, failure evidence | Broad implementation rewrite unless allowed small fix | Treating unassigned tests as Claude failure |
| FINAL REVIEW / MERGE | Accept/revise/split/reject; human merge remains separate | N/A unless re-dispatched | N/A | Automatic merge or direct edit without threshold |

## Stall / Ambiguity Triage

<!-- Codex completes this before dispatch and reviews it when Claude appears stuck. Use it to distinguish Claude execution failure from orchestration ambiguity. -->

| Check | Value |
|-------|-------|
| Task mixes builder and checker/test responsibilities? | yes/no |
| If mixed, split before dispatch? | yes/no + reason |
| Dirty source or stale HEAD risk acknowledged? | yes/no/not applicable |
| HEAD contains required prior context for Claude? | yes/no/not applicable |
| Dirty source blocks reliable Claude dispatch? | yes/no |
| Required progress artifacts | CLAUDE_PROGRESS.md / CLAUDE_TASK_CARD.md checklist / CLAUDE_REPORT.md |
| Long-running command expected? | yes/no + command |
| Permission/tool approval risk? | yes/no + sandbox/write/network/auth/forbidden-file details |
| Ambiguity likely to cause stop-and-report? | yes/no + field |
| If Claude is quiet, first diagnosis step | inspect progress artifacts and partial diff before declaring failure |
| Conditions that prove real Claude no-progress | no artifact growth, no worktree change, no status output, no permission blocker, and no reported blocker after grace period |

## Delegation Restoration Gate

<!-- Codex completes this when dirty source, stale HEAD, missing local workflow files, permissions, or environment state blocks reliable Claude dispatch. These are delegation blockers, not automatic Codex takeover triggers. -->

| Check | Value |
|-------|-------|
| Delegation blocker present? | no / dirty source / stale HEAD / outdated workflow files / permission-tool approval / external environment |
| Why Claude would not see required context | |
| Restoration path selected | commit accepted phase / stash or patch source changes / refresh workflow files / re-dispatch from updated HEAD / request explicit dirty-source override / stop for human |
| Restoration attempted before Codex takeover? | yes/no + evidence |
| If not restored, why impossible or unsafe? | |
| Codex takeover justified instead of restoration? | no / yes + threshold or explicit human override |
| Return-to-delegation condition | next task from clean updated HEAD / after human approval / after tool permission fixed |

## Direction Review Gate

<!-- Codex completes this after a Builder task before dispatching Checker/Test work. If the builder direction is wrong, revise or interrupt instead of testing the wrong approach. -->

| Check | Value |
|-------|-------|
| Builder diff matches planned direction? | yes/no/partial |
| Continue waiting for Builder? | yes/no + reason |
| Interrupt and narrow task? | yes/no + reason |
| Dispatch Checker/Test task next? | yes/no + task-card path |
| Codex takeover threshold reached? | yes/no + cited artifacts |

## Direction / Boundary Acknowledgement

<!-- Claude completes this before editing. Use blocking approval for ambiguous, multi-file, high-risk, public API, data model, security, migration, permission, or production-impacting work. If Claude has material confusion, it must stop-and-report instead of guessing. -->

| Field | Value |
|-------|-------|
| Required before editing? | yes/no |
| Blocking Codex approval required? | yes/no |
| Maximum acknowledgement rounds | 0 / 1 |
| Re-acknowledgement allowed only if Codex changes goal/scope/boundaries? | yes/no |
| Claude must state task in own words? | yes/no |
| Claude must list in-scope files/modules? | yes/no |
| Claude must list explicitly out-of-scope boundaries? | yes/no |
| Claude must report confusion before editing? | yes/no |
| Stop if acceptance criteria unclear? | yes/no |
| Stop if testing responsibility unclear? | yes/no |
| Stop if implementation boundary unclear? | yes/no |
| Expected acknowledgement artifact | CLAUDE_PROGRESS.md / CLAUDE_REPORT.md |
| Codex approval artifact, if blocking | |
| Final acknowledgement decision | proceed / narrow-once / split / stop |

Acknowledgement format Claude should write:

- My understanding:
- Planned scope:
- Explicitly out of scope:
- Files/modules likely touched:
- Acceptance criteria interpretation:
- Testing responsibility interpretation:
- Confusions or ambiguities:
- New risks / unknowns:
- Recommendation: proceed / narrow / split / stop-and-report

Anti-loop rule: acknowledgement is a gate, not a discussion loop. Codex should answer with one final decision: proceed, narrow once and re-dispatch, split, or stop. Claude should not request repeated confirmation after approval unless the task goal, scope, boundaries, or risk profile materially changes.

## Task Card Views

<!-- Codex owns this full planning card. Dispatch scripts derive `CLAUDE_TASK_CARD.md` from it and omit Codex-only budget/planning/control sections before prompting Claude. Do not maintain a second hand-written Claude card. -->

## Control-Plane Exception Rationale

<!-- Fill only when Task Type is control-plane. Explain why normal Claude delegation is unsafe, unavailable, or exhausted after repeated failed Claude attempts, cite attempt artifacts, identify any first-round direction Codex will salvage, define the narrow Codex edit scope, and state what condition returns work to the normal Codex-plan / Claude-execute flow. -->

## Goal

<!-- What needs to be accomplished in one sentence. -->

## Context

<!-- Background, related work, constraints, links to design docs or discussions. -->

## Execution Readiness Gate

<!-- Codex completes this before dispatch. If any required field is not ready, create an exploration/prototype task instead of an implementation task. -->

| Check | Ready? | Evidence / Follow-up |
|-------|--------|----------------------|
| Acceptance criteria are testable | yes/no | |
| Expected files/modules are scoped | yes/no | |
| Unknowns and decision gates are explicit | yes/no | |
| Validation commands are known or discoverable | yes/no | |
| Task is implementation-ready, not exploration-only | yes/no | |

## Unknowns

<!-- Codex uses this to reduce the information gap before Claude edits. Keep it concise and actionable. -->

| Type | Notes | Owner / Resolution |
|------|-------|--------------------|
| Known knowns | <!-- Facts already established. --> | |
| Known unknowns | <!-- Questions known before dispatch. --> | |
| Assumed knowns | <!-- Constraints obvious to the human/Codex but easy for Claude to miss. --> | |
| Unknown-unknown scan request | <!-- Blindspot pass Claude should perform before implementation. --> | |

## Decision Gates

<!-- Decisions that may change architecture, data model, UX, risk, or scope. Say whether Claude may decide, must choose the conservative option, or must stop and report. -->

| Decision | Why It Matters | Claude Authority | Stop Condition |
|----------|----------------|------------------|----------------|
| | | autonomous / conservative / stop-and-report | |

## Handoff Contract

<!-- Compact executor contract. This is the fastest section for Claude and reviewers to compare against. -->

| Field | Items |
|-------|-------|
| Must do | |
| Must not do | |
| May decide | |
| Must report | |
| Stop condition | |

## Acceptance Criteria

<!-- How to verify the work is complete. Be specific and testable. -->

- [ ] Criterion 1
- [ ] Criterion 2
- [ ] Criterion 3

## Testing Responsibility

<!-- Codex must decide this before dispatch. Writing test code and running tests are separate responsibilities. Prefer: Builder Claude implements without acceptance testing; after Codex accepts direction, Checker/Test Claude writes/runs tests and reports validation. If the user requested tests or Codex marks tests acceptance-critical, create a checker-test task unless a mixed exception is justified. -->

| Decision | Value |
|----------|-------|
| Test code changes are in scope? | yes/no |
| Why tests are or are not in scope | user requested / acceptance-critical / regression coverage / not needed because ... |
| Claude must write or update tests? | yes/no |
| Claude must run tests before finishing? | yes/no |
| Builder may run narrow sanity checks? | yes/no + commands |
| Broad acceptance test execution owner | Checker/Test Claude / Codex / human / not required |
| Codex/human will run verification after Claude? | yes/no |
| Acceptance evidence owner | Claude / Codex / human |
| Evidence-only redispatch allowed? | yes/no; only when task-card-required evidence cannot be reconstructed |
| No-test rationale, if applicable | |

## Validation Contract

<!-- List the exact checks expected for this task. If unknown, require Claude to discover project checks and record what it found. Prefer aggregate commands such as pnpm check when available. -->

| Check | Command | Required? | Notes |
|-------|---------|-----------|-------|
| Tests | | yes/no | |
| Lint | | yes/no | |
| Type check | | yes/no | |
| Build | | yes/no | |
| Format check | | yes/no | |
| Project aggregate check | | yes/no | |

Checker expectations:
- Follow `Task Mode` and `Testing Responsibility`: Builder tasks do not add tests or run broad suites; Checker/Test tasks do not skip assigned test writing or validation unless blocked and reported.
- Missing Claude report/result is evidence-gap handling: if assigned checks pass and acceptance evidence owner is not Claude, Codex may reconstruct minimal evidence instead of re-dispatching only for prose.
- Run `bash ai/check-worktree.sh` when available.
- Preserve failed command, exit code, key original output, and `file:line` locations.
- Do not weaken, delete, skip, or rewrite checks just to get a green result.

## Execution Progress

<!-- Claude updates this checklist in `CLAUDE_TASK_CARD.md` after each completed assigned item. Do not rely on it as the only evidence; it complements `CLAUDE_PROGRESS.md`, report artifacts, and diff review. -->

- [ ] Item 1
- [ ] Item 2
- [ ] Item 3

## Execution Phases

<!-- Split non-trivial work into reviewable phases. Claude Code may decompose work inside a phase, but must not merge phases unless this section explicitly allows it. If Codex dispatches only high-priority phases first, remaining implementation/test-writing phases stay Claude-owned and require follow-up task cards after review. -->

| Phase | Owner | Scope | Exit Evidence | Stop Before Next Phase? | Continuation After Accept |
|-------|-------|-------|---------------|-------------------------|---------------------------|
| A | Claude/Codex/human | <!-- e.g., tests only / implementation only / docs only --> | <!-- exact files, test output, or report update expected --> | yes/no | next Claude task / done / human decision |
| B | | | | | |
| C | | | | | |

## Delegation Continuity Gate

<!-- Codex completes this after each accepted phase. A completed high-priority subset is not permission for Codex to implement the remaining subset. -->

| Check | Value |
|-------|-------|
| Accepted phase(s) | |
| Remaining implementation/test-writing phases | |
| Next executor for remaining phases | Claude Code / Codex control-plane / human |
| If not Claude, threshold or human override cited | |

## Wait Policy

<!-- Used by ai/watch-claude.sh and ai/status-claude.sh to avoid both blind waiting and premature interruption. Choose small for narrow fixes, medium for ordinary feature/test work, and large for broad refactors or slow validation. -->

| Field | Value |
|-------|-------|
| Wait profile | small / medium / large |
| Startup grace seconds | |
| Stale review seconds | |
| Consider interrupt after seconds | |
| Partial diff review rule | Continue waiting when partial work matches the plan; consider interrupting when it is off-plan, risky, or no longer making useful progress. |
| Adaptive timeout | First loop may use a longer fixed timeout; later loops may estimate time from completed progress checklist items. |

## Files / Modules

<!-- List the files or modules expected to be modified. Include LSP/codegraph evidence if available. -->

## Codex Context Budget

<!-- Estimated token budget Codex should spend on context gathering before dispatch. Set to 0 if LSP/codegraph evidence is sufficient. Claude Code handles high-token reads by default. -->

| Metric | Target |
|--------|--------|
| Max Codex context tokens | |
| LSP/codegraph queries planned | |
| Whole-file reads planned (Codex) | |

## LSP / Codegraph Evidence

<!-- Structured low-token evidence gathered before implementation. Attach definitions, references, callers, callees, impact analysis. Prefer this over whole-file reads. -->

| Query Type | Symbol / File | Result Summary |
|-----------|---------------|----------------|
| LSP definition | | |
| LSP references | | |
| Codegraph callers | | |
| Codegraph impact | | |

## High-Token Delegation Gate

<!-- Codex must delegate the following to Claude Code unless explicitly approved for Codex execution. Check items that apply to this task. -->

- [ ] Reading files > 200 lines
- [ ] Multi-file implementation or refactoring
- [ ] Long log or test output analysis
- [ ] Full repository scan
- [ ] Exhaustive diff review

## Evidence Compression Requirements

<!-- Claude Code must return compressed evidence, not paste large logs or full files. Requirements for this task: -->

- Summarize test output; attach artifact paths instead of full logs
- Link to diff files instead of pasting full diffs into context
- Provide one-paragraph summaries for each changed file
- Attach paths to any generated artifacts (reports, diagnostics, logs)

## Dependencies

<!-- Other task cards, external services, data requirements, blocking decisions. -->

## Evidence

<!-- LSP/codegraph/MCP data gathered before implementation. Attach definitions, references, callers, callees, impact analysis. -->

## Execution Rules

<!-- Any execution constraints for Claude Code. Leave blank to use defaults from CLAUDE.md. -->

## Notes

<!-- Any additional context, open questions, or constraints for the executor. -->

## Loop Context

<!-- Fill this section when the task card is part of a revision/split/reject loop. Leave blank for first-iteration tasks. -->

- **Parent task ID:** <!-- ID of the task this derives from, or empty if first iteration -->
- **Iteration:** <!-- 1 for first iteration, increment for each revision -->
- **Prior decision:** <!-- The review decision from the previous iteration: accept/revise/split/reject -->
- **Revision instructions:** <!-- Specific instructions from the reviewer for this iteration -->
- **Claude attempts so far:** <!-- Count and short links to prior dispatch/review artifacts -->
- **Prior-session failure evidence:** <!-- Optional artifact links. Context only unless it proves the same current task hit takeover threshold. -->
- **Codex direct intervention eligible?** <!-- yes/no; if yes, cite the threshold reached and allowed edit scope -->
- **Budget / Stop conditions:** <!-- e.g., max 5 iterations, token budget, or "human stop only" -->
- **Required evidence:** <!-- Types of evidence the reviewer expects: e.g., test output, LSP diagnostics, diffstat -->

## Loop Stop Rules

<!-- Override only when this task has stricter project-specific rules. -->

- Stop on ALL GREEN.
- Stop when max iterations are reached.
- Stop when the same failure appears in two consecutive iterations.
- Stop when a fix causes a previously passing check to fail.
- Stop when failure count does not decrease for two consecutive iterations.
- Stop when blocked by external dependency, environment, permission, or unavailable service.
