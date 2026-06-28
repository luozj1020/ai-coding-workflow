# AI Coding Workflow Skill

A reusable Codex / Claude Code workflow skill for installing a local multi-agent coding workflow into software repositories.

English | [中文](README_CN.md)

## What it does

ai-coding-workflow bootstraps repositories with:
- `AGENTS.md` - shared rules for all agents
- `CLAUDE.md` - Claude Code execution rules
- Task-card and evidence-packet templates
- Safe dispatch/review/loop scripts for Codex + Claude Code workflows
- Managed blocks for idempotent updates

## Two actions

| Action | When | Command |
|--------|------|---------|
| **Install Skill** | Once per computer | `python scripts/install_for_codex.py` |
| **Bootstrap project** | Once per repository | `python scripts/install_workflow.py .` |

## Repository layout

```
ai-coding-workflow/
  README.md              ← English documentation
  README_CN.md           ← Chinese documentation
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
    loop-model.md       ← Loop state machine and stop conditions
    operating-model.md  ← Agent roles and handoff model
    review-policy.md    ← Code review division of labor
    mcp-policy.md       ← Information retrieval order
  scripts/
    install_workflow.py ← Bootstrap a repository
    install_for_codex.py← Install skill for Codex discovery
    dispatch-to-claude.sh← Dispatch task cards to Claude Code
    review-with-codex.sh← Send evidence to Codex/GPT for review
    run-loop.sh         ← Optional loop runner (dispatch + review)
```

---

## Scenario A: Install Skill on a new computer

This installs the skill to your user-level Codex skills directory. Do this once per computer.

### Windows PowerShell

```powershell
git clone https://github.com/luozj1020/ai-coding-workflow.git
cd ai-coding-workflow
python .\scripts\install_for_codex.py
```

Or manually:

```powershell
git clone https://github.com/luozj1020/ai-coding-workflow.git

$dst = "$env:USERPROFILE\.codex\skills\ai-coding-workflow"
Remove-Item -Recurse -Force $dst -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$env:USERPROFILE\.codex\skills" | Out-Null
Copy-Item -Recurse -Force ".\ai-coding-workflow" $dst
```

### macOS / Linux

```bash
git clone https://github.com/luozj1020/ai-coding-workflow.git
cd ai-coding-workflow
python scripts/install_for_codex.py
```

Or manually:

```bash
git clone https://github.com/luozj1020/ai-coding-workflow.git
mkdir -p ~/.codex/skills
rm -rf ~/.codex/skills/ai-coding-workflow
cp -R ai-coding-workflow ~/.codex/skills/ai-coding-workflow
```

Then restart Codex.

**Test it works:**

```
Use ai-coding-workflow to explain how to install the workflow in this repo.
```

If Codex can answer and reference this skill's installer, the skill is active.

---

## Scenario B: Bootstrap a new project

After the skill is installed, bootstrap any repository. Do this once per project.

### Windows PowerShell

```powershell
cd E:\path\to\your-new-project
python $env:USERPROFILE\.codex\skills\ai-coding-workflow\scripts\install_workflow.py .
```

### macOS / Linux

```bash
cd /path/to/your-new-project
python ~/.codex/skills/ai-coding-workflow/scripts/install_workflow.py .
```

This generates or updates in your project:

```
AGENTS.md
CLAUDE.md
ai/task-card-template.md
ai/evidence-packet-template.md
ai/README.md
ai/dispatch-to-claude.sh
ai/review-with-codex.sh
ai/run-loop.sh
.worktrees/.gitkeep
```

---

## Update an existing project

Run the same command again. The installer uses managed blocks to preserve your project-specific rules:

```powershell
# Windows
python $env:USERPROFILE\.codex\skills\ai-coding-workflow\scripts\install_workflow.py .
```

```bash
# macOS / Linux
python ~/.codex/skills/ai-coding-workflow/scripts/install_workflow.py .
```

---

## Typical daily workflow

The workflow is an explicit loop: **OBSERVE  ->  PLAN  ->  DISPATCH  ->  EXECUTE  ->  VERIFY  ->  REVIEW  ->  LEARN  ->  repeat.**

**Core principle:** Codex designs and reviews. Claude edits. Tools gather low-token evidence first.

**Step 1: Initialize project** (once)

```powershell
python $env:USERPROFILE\.codex\skills\ai-coding-workflow\scripts\install_workflow.py .
```

**Step 2: Create task card** (in Codex  -  OBSERVE + PLAN)

```
Use ai-coding-workflow to create a task card for implementing <feature>.
```

**Step 3: Execute with Claude Code** (DISPATCH + EXECUTE + VERIFY)

```
Use the coding executor workflow. Execute this task card and return an evidence packet.
```

This generates these artifacts under `.worktrees/`:

| Artifact | Description |
|----------|-------------|
| `*.result.json` | Raw Claude JSON output |
| `*.status.txt` | Claude stderr / execution log |
| `*.diffstat.txt` | `git diff --stat` for tracked files |
| `*.diff` | Full diff, including untracked implementation files |
| `*.source-status.txt` | Source repo state before dispatch |
| `*.worktree-status.txt` | Worktree state after execution |
| `*.untracked.txt` | Listing and patch evidence for untracked files |
| `*.usage.txt` | Claude token/cost usage summary |
| `*.report.md` | Claude modification report for human/Codex review |
| `*.review.txt` | Persisted Codex review output |
| `*.codex-events.jsonl` | Raw Codex JSON events when available |
| `*.codex-usage.txt` | Codex review token/cost usage summary when available |

**Step 4: Review with Codex** (REVIEW)

```
Use ai-coding-workflow to review this execution evidence packet and diff. Decide accept / revise / split / reject.
```

To include token/cost and repository status evidence in the review:

```bash
bash ai/review-with-codex.sh ai/task-cards/PROJ-123.md \
  .worktrees/claude-<id>.result.json \
  .worktrees/claude-<id>.diff \
  .worktrees/claude-<id>.usage.txt \
  .worktrees/claude-<id>.source-status.txt \
  .worktrees/claude-<id>.worktree-status.txt \
  .worktrees/claude-<id>.untracked.txt
```

**Step 5: Loop or Merge**

- If **accept**: human reviews and merges.
- If **revise**: update the task card with revision instructions and go to Step 3.
- If **split**: decompose into child task cards.
- If **reject**: re-plan with updated context.

**Optional: Use the loop runner**

```bash
bash ai/run-loop.sh ai/task-cards/PROJ-123.md 5
```

The loop runner automates Steps 3-5, stopping on accept, max iterations, or human intervention. It also writes `.worktrees/loop-<timestamp>/loop-usage-summary.md` with available Claude and Codex usage summaries. It does NOT merge automatically.

---

## Windows notes

On Windows, `bash` in PATH may resolve to WSL rather than Git Bash. If WSL has no default distro, direct `bash -n` calls fail. This does not mean scripts are invalid.

The installer (`install_workflow.py`) searches for Git Bash explicitly and reports `WARN_SKIPPED` when bash is unavailable - it never treats this as a hard failure.

**Options:**
1. Install Git for Windows and ensure `C:\Program Files\Git\bin` is before WSL in PATH.
2. Install a WSL distro (`wsl --install -d Ubuntu`).
3. Validate through the installer instead of running `bash -n` directly.

---

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

---

## Verify installation

Run these commands to confirm everything works:

```powershell
# Windows PowerShell
mkdir $env:TEMP\ai-workflow-test
cd $env:TEMP\ai-workflow-test
git init
python $env:USERPROFILE\.codex\skills\ai-coding-workflow\scripts\install_workflow.py .
python $env:USERPROFILE\.codex\skills\ai-coding-workflow\scripts\install_workflow.py .
```

```bash
# macOS / Linux
mkdir /tmp/ai-workflow-test
cd /tmp/ai-workflow-test
git init
python ~/.codex/skills/ai-coding-workflow/scripts/install_workflow.py .
python ~/.codex/skills/ai-coding-workflow/scripts/install_workflow.py .
```

Expected result:
- AGENTS.md exists
- CLAUDE.md exists
- ai/ directory exists
- .worktrees/.gitkeep exists
- Second run reports unchanged/skipped files

---

## License

MIT License - see [LICENSE](LICENSE) for details.

## Links

- GitHub: https://github.com/luozj1020/ai-coding-workflow
- Issues: https://github.com/luozj1020/ai-coding-workflow/issues
