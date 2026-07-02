# Task Card

## ID

<!-- e.g., PROJ-123 -->

## Task Type

<!-- normal | control-plane -->

## Executor

<!-- Claude Code | Codex control-plane hotfix | human -->

## Control-Plane Exception Rationale

<!-- Fill only when Task Type is control-plane. Explain why normal Claude delegation is unsafe or unavailable, and what condition returns work to the normal Codex-plan / Claude-execute flow. -->

## Goal

<!-- What needs to be accomplished in one sentence. -->

## Context

<!-- Background, related work, constraints, links to design docs or discussions. -->

## Acceptance Criteria

<!-- How to verify the work is complete. Be specific and testable. -->

- [ ] Criterion 1
- [ ] Criterion 2
- [ ] Criterion 3

## Execution Phases

<!-- Split non-trivial work into reviewable phases. Claude Code may decompose work inside a phase, but must not merge phases unless this section explicitly allows it. -->

| Phase | Scope | Exit Evidence | Stop Before Next Phase? |
|-------|-------|---------------|-------------------------|
| A | <!-- e.g., tests only / implementation only / docs only --> | <!-- exact files, test output, or report update expected --> | yes/no |
| B | | | |
| C | | | |


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
- **Budget / Stop conditions:** <!-- e.g., max 5 iterations, token budget, or "human stop only" -->
- **Required evidence:** <!-- Types of evidence the reviewer expects: e.g., test output, LSP diagnostics, diffstat -->
