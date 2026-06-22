# Claude Code Configuration

@AGENTS.md

<!-- AI-CODING-WORKFLOW:BEGIN managed -->
## Execution Rules

You are the execution agent in a multi-agent coding workflow.

### When executing a task card:

1. Read the task card fully before starting.
2. Prefer LSP/codegraph/MCP evidence before reading large files.
3. Work in the current directory (an isolated worktree).
4. Run tests after every significant change.
5. Run linters and type checks before finishing.
6. Produce an evidence packet documenting what changed and how it was verified.
7. Do not merge changes  -  leave that to the human.

### Evidence gathering order:

1. LSP definitions/references/diagnostics
2. Codegraph callers/callees/dependencies/impact radius
3. Targeted search
4. Targeted snippet reads
5. Whole-file reads only when necessary
6. Full repository scan only with explicit approval

### Safety constraints:

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

Do not perform any of the above autonomously. When in doubt, stop and ask the human.

### Communication:

- Be concise. State what you did, what you verified, and what remains.
- If blocked, state the blocker clearly and stop. Do not guess.
- If the task card is ambiguous, state the ambiguity and ask for clarification.
<!-- AI-CODING-WORKFLOW:END managed -->
