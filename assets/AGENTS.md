# Agents

This file defines shared rules for all AI agents working in this repository.

<!-- AI-CODING-WORKFLOW:BEGIN managed -->
## Agent Roles

- **Codex / GPT**  -  planner and reviewer. Decomposes work into task cards, reviews execution evidence, returns accept/revise/split/reject.
- **Claude Code**  -  executor. Implements task cards in isolated worktrees, runs tests and lint, produces evidence packets.
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

1. Human or Codex/GPT creates a task card from `ai/task-card-template.md`.
2. Claude Code executes the task card in an isolated worktree via `ai/dispatch-to-claude.sh`.
3. Claude Code produces an evidence packet from `ai/evidence-packet-template.md`.
4. Codex/GPT reviews the evidence via `ai/review-with-codex.sh`.
5. Decision: accept, revise, split, or reject.
6. Human performs final merge.

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
