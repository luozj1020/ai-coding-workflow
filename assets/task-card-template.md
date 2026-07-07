# Task Card

## ID

<!-- e.g., PROJ-123 -->

## Task Type

<!-- normal | control-plane -->

## Executor

<!-- Claude Code | Codex control-plane hotfix | human -->

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

<!-- Codex must decide this before dispatch. Writing test code and running tests are separate responsibilities. If the user requested tests or Codex marks tests acceptance-critical, test code can be part of Claude's implementation task. If tests are not required, say so explicitly and explain why. -->

| Decision | Value |
|----------|-------|
| Test code changes are in scope? | yes/no |
| Why tests are or are not in scope | user requested / acceptance-critical / regression coverage / not needed because ... |
| Claude must write or update tests? | yes/no |
| Claude must run tests before finishing? | yes/no |
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
- Follow `Testing Responsibility`: do not add tests when test code is out of scope; do not skip required Claude-run tests unless blocked and reported.
- Missing Claude report/result is evidence-gap handling: if assigned checks pass and acceptance evidence owner is not Claude, Codex may reconstruct minimal evidence instead of re-dispatching only for prose.
- Run `bash ai/check-worktree.sh` when available.
- Preserve failed command, exit code, key original output, and `file:line` locations.
- Do not weaken, delete, skip, or rewrite checks just to get a green result.

## Execution Phases

<!-- Split non-trivial work into reviewable phases. Claude Code may decompose work inside a phase, but must not merge phases unless this section explicitly allows it. -->

| Phase | Scope | Exit Evidence | Stop Before Next Phase? |
|-------|-------|---------------|-------------------------|
| A | <!-- e.g., tests only / implementation only / docs only --> | <!-- exact files, test output, or report update expected --> | yes/no |
| B | | | |
| C | | | |

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
