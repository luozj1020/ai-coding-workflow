# AI Coding Workflow  -  Local Usage Guide

## Installing This Skill for Codex

To make this Skill discoverable by Codex, run:

```bash
python ai/scripts/install_for_codex.py
```

This copies the Skill to:
- Windows: `%USERPROFILE%\.codex\skills\ai-coding-workflow`
- Unix/macOS: `$HOME/.codex/skills/ai-coding-workflow`

To update, run the same command again.

## What Is This?

This repository has been set up with a multi-agent AI coding workflow. The workflow splits software work between planning, execution, and review agents:

- **Codex / GPT**  -  plans and reviews
- **Claude Code**  -  implements and verifies
- **MiMo / DeepSeek**  -  optional exhaustive review helper
- **LSP / Codegraph / MCP**  -  low-token code intelligence

## Directory Structure

```
ai/
  task-card-template.md      # Template for planning work items
  evidence-packet-template.md # Template for documenting execution results
  dispatch-to-claude.sh       # Dispatches task cards to Claude Code
  review-with-codex.sh        # Sends evidence to Codex/GPT for review
  README.md                   # This file
.worktrees/                   # Isolated git worktrees for execution
AGENTS.md                     # Shared agent rules
CLAUDE.md                     # Claude Code configuration
```

## Quick Start

### 1. Create a Task Card

Copy the template and fill it in:

```bash
cp ai/task-card-template.md ai/task-cards/PROJ-123.md
# Edit ai/task-cards/PROJ-123.md
```

### 2. Dispatch to Claude Code

```bash
bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md
```

This creates an isolated worktree under `.worktrees/`, runs Claude Code, and saves the result. It does **not** merge automatically.

### 3. Review with Codex

```bash
bash ai/review-with-codex.sh ai/task-cards/PROJ-123.md .worktrees/claude-<timestamp>/result.json .worktrees/claude-<timestamp>/diff.patch
```

Codex reviews the work and returns a decision: accept, revise, split, or reject.

### 4. Merge

After human approval, merge the changes from the worktree.

## Updating the Workflow

To update workflow files without losing project-specific rules:

```bash
python ai/scripts/install_workflow.py /path/to/repo
```

The installer preserves content outside managed markers and only replaces managed blocks.

## Troubleshooting

### Windows: `bash` resolves to broken WSL

On Windows, running `bash` in a terminal may resolve to Windows Subsystem for Linux (WSL) instead of Git Bash. If WSL has no default distro configured, commands like `bash -n ai/dispatch-to-claude.sh` will fail with an error such as:

```
Windows Subsystem for Linux has no installed distributions.
```

This does **not** mean the workflow scripts are invalid. The installer's built-in validation (`install_workflow.py`) searches for Git Bash explicitly and will report `PASS` if scripts are syntactically correct.

**Recommended fixes (pick one):**

1. Install [Git for Windows](https://git-scm.com/download/win) and ensure `C:\Program Files\Git\bin` appears before WSL in your `PATH`.
2. Install a WSL distro (e.g., `wsl --install -d Ubuntu`) so that `bash` works natively.
3. Run validation through the installer rather than directly: `python scripts/install_workflow.py /path/to/repo`.

The installer treats a missing or broken `bash` as `WARN_SKIPPED`, not as a hard failure.

## Safety

All of the following require **explicit human approval** before execution:

- Destructive commands and file deletion
- Database migrations
- Auth / permission changes
- Billing changes
- Deployment changes
- Public API changes
- Secret or credential edits
- Production data changes

Agents must not perform any of the above autonomously. When in doubt, they stop and ask the human.
