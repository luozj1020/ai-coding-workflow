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
