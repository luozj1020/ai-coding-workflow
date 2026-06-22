# AI Coding Workflow Skill

A reusable Codex / Claude Code workflow skill for installing a local multi-agent coding workflow into software repositories.

English | [中文](README_CN.md)

## What it does

ai-coding-workflow bootstraps repositories with:
- `AGENTS.md` - shared rules for all agents
- `CLAUDE.md` - Claude Code execution rules
- Task-card and evidence-packet templates
- Safe dispatch/review scripts for Codex + Claude Code workflows
- Managed blocks for idempotent updates

## Repository layout

```
ai-coding-workflow/
  README.md              ← This file
  LICENSE                ← MIT license
  .gitignore
  SKILL.md              ← Skill entry point for Codex discovery
  agents/
    openai.yaml         ← Skill metadata for OpenAI/Codex
  assets/
    AGENTS.md           ← Template for agent rules
    CLAUDE.md           ← Template for Claude Code rules
    README.md           ← Template for local usage guide
    task-card-template.md
    evidence-packet-template.md
  references/
    operating-model.md  ← Agent roles and handoff model
    review-policy.md    ← Code review division of labor
    mcp-policy.md       ← Information retrieval order
  scripts/
    install_workflow.py ← Bootstrap a repository
    install_for_codex.py← Install skill for Codex discovery
    dispatch-to-claude.sh← Dispatch task cards to Claude Code
    review-with-codex.sh← Send evidence to Codex/GPT for review
```

## Install as a Codex skill

### Windows PowerShell

```powershell
git clone https://github.com/<your-name>/ai-coding-workflow.git

$dst = "$env:USERPROFILE\.codex\skills\ai-coding-workflow"
Remove-Item -Recurse -Force $dst -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$env:USERPROFILE\.codex\skills" | Out-Null
Copy-Item -Recurse -Force ".\ai-coding-workflow" $dst
```

Or use the install script:

```powershell
cd ai-coding-workflow
python .\scripts\install_for_codex.py
```

### macOS / Linux

```bash
git clone https://github.com/<your-name>/ai-coding-workflow.git
mkdir -p ~/.codex/skills
rm -rf ~/.codex/skills/ai-coding-workflow
cp -R ai-coding-workflow ~/.codex/skills/ai-coding-workflow
```

## Bootstrap a repository

After installing the skill, bootstrap any repository:

### Windows PowerShell

```powershell
python $env:USERPROFILE\.codex\skills\ai-coding-workflow\scripts\install_workflow.py E:\path\to\repo
```

### macOS / Linux

```bash
python ~/.codex/skills/ai-coding-workflow/scripts/install_workflow.py /path/to/repo
```

## Update an existing repository

Run the same command again. The installer uses managed blocks to preserve your project-specific rules:

```powershell
# Windows
python $env:USERPROFILE\.codex\skills\ai-coding-workflow\scripts\install_workflow.py E:\path\to\repo
```

```bash
# macOS / Linux
python ~/.codex/skills/ai-coding-workflow/scripts/install_workflow.py /path/to/repo
```

## Typical workflow

1. **Produce task cards** using `ai/task-card-template.md`
2. **Gather evidence** through LSP/codegraph/MCP first
3. **Dispatch to Claude Code**: `bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md`
4. **Review with Codex**: `bash ai/review-with-codex.sh ai/task-cards/PROJ-123.md .worktrees/claude-<timestamp>/result.json .worktrees/claude-<timestamp>/diff.patch`
5. **Human reviews** and merges

## Windows notes

On Windows, `bash` in PATH may resolve to WSL rather than Git Bash. If WSL has no default distro, direct `bash -n` calls fail. This does not mean scripts are invalid.

The installer (`install_workflow.py`) searches for Git Bash explicitly and reports `WARN_SKIPPED` when bash is unavailable - it never treats this as a hard failure.

**Options:**
1. Install Git for Windows and ensure `C:\Program Files\Git\bin` is before WSL in PATH.
2. Install a WSL distro (`wsl --install -d Ubuntu`).
3. Validate through the installer instead of running `bash -n` directly.

## Safety policy

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

## License

MIT License - see [LICENSE](LICENSE) for details.
