---
name: ai-coding-workflow
description: use this skill when installing, updating, or using a local multi-agent ai coding workflow for software repositories. triggers include setting up codex and claude code collaboration, creating agents.md or claude.md rules, generating task-card and evidence-packet templates, dispatching work from codex to claude code, reviewing execution evidence, and enforcing lsp/codegraph/mcp-first development workflows.
---

# AI Coding Workflow Skill

This Skill supports three modes: **install**, **update**, and **use**.

## Codex Discovery

To make this Skill discoverable by Codex, install it into the Codex skills directory:

**Windows PowerShell:**
```powershell
python $env:USERPROFILE\.codex\skills\ai-coding-workflow\scripts\install_for_codex.py
```

**Unix/macOS:**
```bash
python ~/.codex/skills/ai-coding-workflow/scripts/install_for_codex.py
```

This copies the Skill to:
- Windows: `%USERPROFILE%\.codex\skills\ai-coding-workflow`
- Unix/macOS: `$HOME/.codex/skills/ai-coding-workflow`

To update the installed Skill, run the same command again.

## Mode: Install

Run the installer to bootstrap a repository with multi-agent workflow files:

```bash
python ai/scripts/install_workflow.py /path/to/repo
```

The installer performs these steps:

1. Create or merge `AGENTS.md`  -  shared rules for all agents. Uses managed markers (`<!-- AI-CODING-WORKFLOW:BEGIN managed -->` / `<!-- AI-CODING-WORKFLOW:END managed -->`) to allow safe future updates.
2. Create or merge `CLAUDE.md`  -  Claude Code execution rules. Imports `AGENTS.md` via `@AGENTS.md` and adds Claude-specific guidance.
3. Create `ai/task-card-template.md`  -  template for planning work items.
4. Create `ai/evidence-packet-template.md`  -  template for documenting execution results.
5. Create `ai/README.md`  -  local usage guide.
6. Create `ai/dispatch-to-claude.sh`  -  dispatches task cards to Claude Code in an isolated worktree.
7. Create `ai/review-with-codex.sh`  -  sends execution evidence to Codex/GPT for review.
8. Create `.worktrees/.gitkeep`  -  placeholder for isolated worktrees.
9. Make shell scripts executable (`chmod +x`).
10. Validate shell scripts with `bash -n`.

## Mode: Update

Run the same installer again to update previously installed files:

```bash
python ai/scripts/install_workflow.py /path/to/repo
```

Update behavior:

- Preserve all content outside managed markers  -  this is user-owned content.
- Replace only the content between `<!-- AI-CODING-WORKFLOW:BEGIN managed -->` and `<!-- AI-CODING-WORKFLOW:END managed -->`.
- If no managed block exists in an existing file, append the managed block near the top and preserve all existing content below it.
- Never overwrite project-specific rules blindly.
- The `## Project-specific rules` section in `AGENTS.md` is always user-owned.

## Mode: Use

### 1. Produce Task Cards

Use `ai/task-card-template.md` to create a task card for each work item. The card captures:

- Goal and context
- Acceptance criteria
- Files/modules involved
- Dependencies and constraints
- LSP/codegraph/MCP evidence gathered before implementation

### 2. Prefer LSP / Codegraph / MCP Evidence

Before reading large files or scanning the full repository, gather evidence through:

1. LSP definitions, references, and diagnostics
2. Codegraph callers, callees, dependencies, and impact radius
3. Targeted search (grep, ripgrep)
4. Targeted snippet reads
5. Whole-file reads only when necessary
6. Full repository scan only with explicit human approval

See `references/mcp-policy.md` for details.

### 3. Route Implementation to Claude Code

Dispatch a task card to Claude Code:

```bash
bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md
```

This creates an isolated git worktree, invokes `claude -p` with `--permission-mode acceptEdits`, and saves the result, status, diffstat, and diff under `.worktrees/`. It does **not** merge automatically.

### 4. Route Final Review to Codex / GPT

Send execution evidence to Codex/GPT for review:

```bash
bash ai/review-with-codex.sh ai/task-cards/PROJ-123.md .worktrees/claude-<timestamp>/result.json .worktrees/claude-<timestamp>/diff.patch
```

Codex reviews the work and returns a decision: **accept**, **revise**, **split**, or **reject**.

## Safety Constraints

All of the following require **explicit human approval** before execution:

- Destructive commands (e.g., `rm -rf`, `DROP TABLE`, `git push --force`, `git reset --hard`)
- File deletion
- Database migrations
- Authentication or authorization changes
- Billing or payment changes
- Deployment or infrastructure changes
- Public API surface changes
- Secret or credential edits (API keys, tokens, passwords)
- Production data changes

Agents must not perform any of the above on their own initiative. When in doubt, stop and ask the human.

## Troubleshooting

### Windows: `bash` resolves to broken WSL

On Windows, `bash` in PATH may resolve to WSL rather than Git Bash. If WSL has no default distro, direct `bash -n` calls fail. This does not mean scripts are invalid.

The installer (`install_workflow.py`) searches for Git Bash explicitly and reports `WARN_SKIPPED` when bash is unavailable  -  it never treats this as a hard failure.

**Options:**
1. Install Git for Windows and ensure `C:\Program Files\Git\bin` is before WSL in PATH.
2. Install a WSL distro (`wsl --install -d Ubuntu`).
3. Validate through the installer instead of running `bash -n` directly.

### Codex does not see the Skill

After running `install_for_codex.py`, restart or reload your Codex session. The Skill is discovered from `~/.codex/skills/ai-coding-workflow/SKILL.md` at session start.

## References

For more detail, see:

- `references/operating-model.md`  -  agent roles and handoff model
- `references/review-policy.md`  -  code review division of labor
- `references/mcp-policy.md`  -  information retrieval order
