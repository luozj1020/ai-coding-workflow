# Agents

This file defines shared rules for all AI agents working in this repository.

<!-- AI-CODING-WORKFLOW:BEGIN managed -->
## Core Principle

**Codex designs and reviews. Claude edits. Tools gather low-token evidence first.**

- Codex/GPT is responsible for top-level design, planning, and review.
- Claude Code is responsible for concrete file modifications.
- LSP, codegraph, and MCP tools are used before broad file reads or repository scans.

## Agent Roles

- **Codex / GPT**  -  planner and reviewer. Decomposes work into task cards, gathers context using low-token tools, reviews execution evidence, returns structured accept/revise/split/reject decisions with explicit next-loop instructions. Does NOT write code during review.
- **Claude Code**  -  executor. Implements task cards in isolated worktrees, makes concrete file edits, runs tests and lint, produces evidence packets with assumptions, failed checks, and lessons learned.
- **MiMo / DeepSeek**  -  optional high-token helper. Assists with exhaustive diff scanning, long log analysis, test suggestions.
- **LSP / Codegraph / MCP**  -  low-token project intelligence. First choice for definitions, references, diagnostics, callers, callees, impact analysis.

## Information Retrieval Order

1. LSP definitions/references/diagnostics
2. Codegraph callers/callees/dependencies/impact radius
3. Targeted search (grep, ripgrep)
4. Targeted snippet reads
5. Whole-file reads only when necessary
6. Full repository scan only with explicit human approval

## Workflow

The workflow is an explicit loop, not a linear handoff. See `ai/README.md` for local usage details.

1. **OBSERVE:** Codex/GPT gathers context using low-token tools (LSP, codegraph, MCP).
2. **PLAN:** Codex/GPT creates or revises a task card from `ai/task-card-template.md` with acceptance criteria, budget, and stop conditions.
3. **DISPATCH:** Task card is sent to Claude Code via `ai/dispatch-to-claude.sh` or `ai/run-loop.sh`.
4. **EXECUTE:** Claude Code implements the task card in an isolated worktree.
5. **VERIFY:** Claude Code runs checks and produces an evidence packet from `ai/evidence-packet-template.md`.
6. **REVIEW:** Codex/GPT reviews the evidence via `ai/review-with-codex.sh` or `ai/run-loop.sh` and returns a structured decision.
7. **LEARN:** Both agents capture lessons from the iteration.
8. Loop continues until: accept, max iterations reached, token budget exhausted, human stops, or reject without alternative.

## Safety Rules

All of the following require **explicit human approval** before execution:

- Destructive commands (e.g., `rm -rf`, `DROP TABLE`, `git push --force`)
- File deletion
- Database migrations
- Auth / permission changes
- Billing changes
- Deployment changes
- Public API changes
- Secret or credential edits
- Production data changes

Agents must not perform any of the above autonomously. When in doubt, stop and ask the human.
<!-- AI-CODING-WORKFLOW:END managed -->

## Project-specific rules

Add project-specific rules here. This section is user-owned and should not be overwritten by the workflow installer.
