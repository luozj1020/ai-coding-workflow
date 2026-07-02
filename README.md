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
  README.md              -> English documentation
  README_CN.md           -> Chinese documentation
  LICENSE                -> MIT license
  .gitignore
  SKILL.md               -> Skill entry point for Codex discovery
  agents/
    openai.yaml          -> Skill metadata for OpenAI/Codex
  assets/
    AGENTS.md            -> Template for agent rules
    CLAUDE.md            -> Template for Claude Code rules
    README.md            -> Template for local usage guide
    task-card-template.md
    evidence-packet-template.md
  references/
    loop-model.md        -> Loop state machine and stop conditions
    operating-model.md   -> Agent roles and handoff model
    review-policy.md     -> Code review division of labor
    mcp-policy.md        -> Information retrieval order
  scripts/
    install_workflow.py  -> Bootstrap a repository
    install_for_codex.py -> Install skill for Codex discovery
    dispatch-to-claude.sh -> Dispatch task cards to Claude Code
    review-with-codex.sh -> Send evidence to Codex/GPT for review
    run-loop.sh          -> Optional loop runner (dispatch + review)
    status-claude.sh     -> Inspect Claude dispatch status and artifacts
    watch-claude.sh      -> Show CLI progress panel for running dispatches
    kill-claude.sh       -> Stop a recorded Claude dispatch process
    cleanup-worktree.sh  -> Remove stopped worktrees while preserving evidence
    pwsh-utf8.ps1        -> Configure PowerShell UTF-8 sessions
    doctor_workflow.py   -> Read-only readiness check for dispatch/review loop
    clean_runtime.py     -> Preview/remove ignored runtime artifacts
  tests/
    test_*.py            -> Installer, dispatch, and helper regression tests
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

### If Claude Code is not installed

The skill can still install, generate task cards, run the workflow doctor, and support Codex review. Only the execution step that calls `claude -p` is unavailable. `dispatch-to-claude.sh` checks for the `claude` command before creating a worktree and exits with a clear error if it is missing. Run `python ai/doctor_workflow.py` in a bootstrapped project to confirm whether Claude CLI is available.

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
ai/status-claude.sh
ai/watch-claude.sh
ai/kill-claude.sh
ai/cleanup-worktree.sh
ai/pwsh-utf8.ps1
ai/doctor_workflow.py
ai/clean_runtime.py
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

**Core principle:** Codex designs and reviews. Claude edits. Tools gather low-token evidence first. Codex stays within a low-token context budget; broad reads and multi-file work are delegated to Claude. Claude returns compressed evidence (summaries + artifact paths) instead of pasted logs.

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

**Proxy behavior:** `dispatch-to-claude.sh` runs Claude Code with common proxy environment variables cleared by default (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`, and lowercase variants). This lets Codex keep using your shell proxy while Claude Code goes direct. If Claude Code must inherit the proxy, run:

```bash
CLAUDE_CODE_PROXY_MODE=inherit bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md
```


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
| `*.claude-progress.md` | Claude self-reported milestone progress for status display and review evidence |
| `*.pid` | Claude subprocess PID for status/kill helpers |
| `*.progress.log` | Dispatch heartbeat, timeout, and completion log |
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


### PowerShell UTF-8 setup

Windows PowerShell can corrupt non-ASCII text when console code pages, `$OutputEncoding`, and child process encodings disagree. Before editing or generating Chinese documentation from PowerShell, dot-source the helper:

```powershell
. .\scripts\pwsh-utf8.ps1
```

For an installed repository workflow, use:

```powershell
. .\ai\pwsh-utf8.ps1
```

For future shells, opt in to profile setup:

```powershell
. .\ai\pwsh-utf8.ps1 -Persist
```

This sets console input/output encoding, `$OutputEncoding`, `PYTHONUTF8`, `PYTHONIOENCODING`, and code page `65001` for the current session. Prefer this helper over ad hoc `chcp` commands or PowerShell here-strings containing non-ASCII text.

On Windows, `bash` in PATH may resolve to WSL rather than Git Bash. If WSL has no default distro, direct `bash -n` calls fail. This does not mean scripts are invalid.

The installer (`install_workflow.py`) searches for Git Bash explicitly and reports `WARN_SKIPPED` when bash is unavailable - it never treats this as a hard failure.

**Options:**
1. Install Git for Windows and ensure `C:\Program Files\Git\bin` is before WSL in PATH.
2. Install a WSL distro (`wsl --install -d Ubuntu`).
3. Validate through the installer instead of running `bash -n` directly.

---

## Dispatch Observability

While Claude Code is running, `dispatch-to-claude.sh` now writes a PID artifact and heartbeat log under `.worktrees/`:

- `.worktrees/claude-<id>.pid` records the Claude subprocess PID.
- `.worktrees/claude-<id>.progress.log` records start, heartbeat, timeout, and completion events.
- `CLAUDE_CODE_HEARTBEAT_SECONDS` controls heartbeat frequency; default is `30`.
- `CLAUDE_CODE_TIMEOUT_SECONDS` controls the maximum Claude runtime; default is `600` seconds. Set it to `0` to disable timeout.
- `CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS` optionally stops Claude when result/status/report/progress artifacts do not change. Default is `0` disabled; set a positive value only when you want fast-fail behavior.

Claude is instructed to keep `CLAUDE_PROGRESS.md` updated at natural milestones. The dispatcher reports its size in heartbeats and copies it to `.worktrees/claude-<id>.claude-progress.md`; Codex only spends tokens on it when review/status output is explicitly read.

`dispatch-to-claude.sh` prints copy-paste `Watch Progress` and `Watch Details` commands immediately after it starts Claude and again in the completion summary, so users can check progress directly from Codex CLI without opening docs or artifact files.

`watch-claude.sh` defaults to an obvious status panel: running state, elapsed/quiet seconds, a checklist-derived progress bar, the latest milestone, artifact sizes, and a short stuck-run analysis. It does not print the whole progress document unless `--details` is provided or the run exceeds the stale threshold. Use `--plain` for a lower-noise compact text format.

On timeout or non-zero Claude exit, the dispatcher still collects diffstat, diff, untracked files, usage fallback, worktree status, and a fallback report when possible.

For complex or repeatedly revised work, add an `## Execution Phases` table to the task card. Claude must use it as the outer execution contract, update progress at phase boundaries, and write `CLAUDE_REPORT.md` before long-running validation or before crossing a stop gate.

Dirty-source guard: dispatch blocks when the source worktree has tracked changes, staged changes, or unrelated untracked files because Claude would run from stale `HEAD`. The current task card may be untracked. Use `CLAUDE_CODE_ALLOW_DIRTY_SOURCE=1` only for intentional advanced dispatch.

```bash
CLAUDE_CODE_TIMEOUT_SECONDS=600 CLAUDE_CODE_HEARTBEAT_SECONDS=15 \
  bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md
```

---

## Control-Plane Exception

The normal role split is: Codex plans/reviews, Claude Code edits. One exception is workflow control-plane repair: if the dispatcher, installer, review script, or loop runner is the component that prevents safe delegation, Codex may make a narrowly scoped hotfix after recording a task card and verification evidence. Use this only to restore the workflow itself; route normal product/code changes back through Claude Code.

## Claude Dispatch Operations

Use these helper scripts when a Claude run is slow, stuck, or ready to clean up:

```bash
# Show latest Claude run status, or pass a specific claude-<timestamp> id
bash ai/status-claude.sh
bash ai/status-claude.sh claude-20260701-093934

# Stream progress in a terminal while Claude is running
bash ai/watch-claude.sh claude-20260701-093934

# Expand full progress tails only when needed
bash ai/watch-claude.sh claude-20260701-093934 --details

# Treat unchanged artifacts as suspicious after 180 seconds
bash ai/watch-claude.sh claude-20260701-093934 --stale-after 180

# Stop only the Claude process recorded for that dispatch
bash ai/kill-claude.sh claude-20260701-093934

# Remove the stopped worktree while preserving .worktrees/claude-<id>.* evidence artifacts
bash ai/cleanup-worktree.sh claude-20260701-093934
```

`cleanup-worktree.sh` refuses to run while the recorded Claude PID is still alive. Use `--force` only when `git worktree remove` needs it for a broken or dirty worktree.

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
- ai/doctor_workflow.py exists
- .worktrees/.gitkeep exists
- Second run reports unchanged/skipped files

**Run the workflow doctor to verify readiness:**

```bash
python ai/doctor_workflow.py
```

**Clean up runtime artifacts:**

```bash
# Preview what would be removed (dry-run)
python ai/clean_runtime.py

# Actually remove artifacts
python ai/clean_runtime.py --apply
```

---

## Development verification

Run the local smoke tests before changing installer or workflow scripts:

```powershell
python -m unittest discover -s tests -v
```

The tests use only the Python standard library and cover installer idempotency, managed-block preservation, `CLAUDE.md` import placement, Codex skill copy exclusions, dispatch dirty-source guard behavior, proxy defaults, progress artifacts, watcher parsing, and operation helper installation. Runtime artifacts are created only under ignored workspace paths such as `.worktrees/` and are not part of the release contents.

## License

MIT License - see [LICENSE](LICENSE) for details.

## Links

- GitHub: https://github.com/luozj1020/ai-coding-workflow
- Issues: https://github.com/luozj1020/ai-coding-workflow/issues
