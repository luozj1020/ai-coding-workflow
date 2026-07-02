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
8. Create `ai/run-loop.sh`  -  optional loop runner that composes dispatch and review.
9. Create `ai/status-claude.sh`, `ai/watch-claude.sh`, `ai/kill-claude.sh`, and `ai/cleanup-worktree.sh`  -  control-plane helpers for stuck or completed Claude dispatches.
10. Create `ai/pwsh-utf8.ps1`  -  Windows PowerShell UTF-8 session helper.
11. Create `ai/doctor_workflow.py`  -  read-only readiness check for the dispatch/review loop.
12. Create `ai/clean_runtime.py`  -  preview and remove ignored runtime artifacts.
13. Create `ai/install_context_tools.py`  -  check and optionally install context tools (LSP, linting).
14. Create `.worktrees/.gitkeep`  -  placeholder for isolated worktrees.
15. Make shell scripts executable (`chmod +x`).
16. Validate shell scripts with `bash -n`.

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

### Core Principle

**Codex designs and reviews. Claude edits. Tools gather low-token evidence first.**

Codex is constrained to low-token evidence (LSP, codegraph, targeted snippets). Broad reads, long logs, multi-file implementation, and full scans are delegated to Claude Code. Claude returns compressed evidence (summaries + artifact paths), not pasted large logs.

The workflow is an explicit loop: OBSERVE  ->  PLAN  ->  DISPATCH  ->  EXECUTE  ->  VERIFY  ->  REVIEW  ->  LEARN  ->  repeat.

### 1. Gather Context (OBSERVE)

Before creating a task card, gather context using low-token tools:

1. LSP definitions, references, and diagnostics
2. Codegraph callers, callees, dependencies, and impact radius
3. Targeted search (grep, ripgrep)
4. Targeted snippet reads
5. Whole-file reads only when necessary
6. Full repository scan only with explicit human approval

See `references/mcp-policy.md` for details.

### 2. Produce Task Cards (PLAN)

Use `ai/task-card-template.md` to create a task card for each work item. The card captures:

- Goal and context
- Acceptance criteria
- Files/modules involved
- Dependencies and constraints
- LSP/codegraph/MCP evidence gathered before implementation
- Loop context (for revision iterations): parent task, iteration, prior decision, revision instructions, budget/stop conditions
- Execution phases for non-trivial work: phase scope, exit evidence, and whether Claude must stop before the next phase

### 3. Route Implementation to Claude Code (DISPATCH)

Dispatch a task card to Claude Code:

```bash
bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md
```

This creates an isolated git worktree, invokes `claude -p` with `--permission-mode acceptEdits`, and saves these artifacts under `.worktrees/`:

| Artifact | Description |
|----------|-------------|
| `*.result.json` | Raw Claude JSON output |
| `*.status.txt` | Claude stderr / execution log |
| `*.diffstat.txt` | `git diff --stat` for tracked files |
| `*.diff` | Full diff, including untracked implementation files |
| `*.source-status.txt` | Source repo state before dispatch |
| `*.worktree-status.txt` | Worktree state after execution |
| `*.untracked.txt` | Listing and patch evidence for untracked files |
| `*.usage.txt` | Claude token/cost usage summary extracted from the JSON result |
| `*.report.md` | Claude modification report for human/Codex review |
| `*.claude-progress.md` | Claude self-reported milestone progress for status display and review evidence |
| `*.review.txt` | Persisted Codex review output |
| `*.codex-events.jsonl` | Raw Codex JSON events when available |
| `*.codex-usage.txt` | Codex review token/cost usage summary when available |

It does **not** merge automatically.

By default, `dispatch-to-claude.sh` clears common proxy environment variables only for the Claude Code subprocess (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`, and lowercase variants), so Codex can keep using proxy settings while Claude Code goes direct. Set `CLAUDE_CODE_PROXY_MODE=inherit` to pass proxy variables through to Claude Code.

The dispatcher also refuses to create a Claude worktree from a dirty source worktree by default because Claude would run from stale `HEAD`. Tracked changes, staged changes, and unrelated untracked files block dispatch; the current task card may be untracked and is exempt. Set `CLAUDE_CODE_ALLOW_DIRTY_SOURCE=1` only when intentionally dispatching from stale `HEAD`.

If Claude Code is not installed, installation, task-card generation, doctor checks, and Codex review still work. Only dispatch execution is unavailable. `dispatch-to-claude.sh` checks for the `claude` command before creating a worktree and exits with a clear error if the CLI is missing.

### 4. Route Final Review to Codex / GPT (REVIEW)

Send execution evidence to Codex/GPT for review:

```bash
bash ai/review-with-codex.sh ai/task-cards/PROJ-123.md .worktrees/claude-<timestamp>.result.json .worktrees/claude-<timestamp>.diff
```

To include extra evidence (usage summary, repository status, untracked files):

```bash
bash ai/review-with-codex.sh ai/task-cards/PROJ-123.md .worktrees/claude-<timestamp>.result.json .worktrees/claude-<timestamp>.diff .worktrees/claude-<timestamp>.usage.txt .worktrees/claude-<timestamp>.source-status.txt .worktrees/claude-<timestamp>.worktree-status.txt .worktrees/claude-<timestamp>.untracked.txt .worktrees/claude-<timestamp>.claude-progress.md .worktrees/claude-<timestamp>.progress.log .worktrees/claude-<timestamp>.pid
```

Codex reviews the work and returns a structured decision: **accept**, **revise**, **split**, or **reject**, with explicit next-loop instructions.

### 5. Run the Loop (Optional)

Use the loop runner to compose dispatch and review automatically:

```bash
bash ai/run-loop.sh ai/task-cards/PROJ-123.md [max-iterations]
```

The loop runner:
- Dispatches the task card to Claude Code.
- Sends evidence to Codex/GPT for review.
- If the decision is `revise`, creates a revised task card and loops.
- Stops on `accept`, `split`, `reject`, max iterations, or unknown decision.
- Persists all decision/review output in `.worktrees/loop-<timestamp>/`.
- Writes `loop-usage-summary.md` with available Claude and Codex usage summaries.
- Does NOT merge automatically. Human must review and merge.

### 6. Learn and Repeat

After each iteration, both agents capture lessons:
- Codex records planning lessons (what approaches worked, what to avoid).
- Claude records execution lessons (what commands failed, what assumptions were made).
- Review feedback flows into the next iteration's task card.

See `references/loop-model.md` for the full loop state machine.

## Dispatch Observability

`dispatch-to-claude.sh` records Claude execution progress while the executor is running:

- `*.pid` stores the Claude subprocess PID.
- `*.progress.log` stores dispatcher heartbeat, timeout, and completion events.
- `*.claude-progress.md` stores Claude self-reported milestone progress copied from `CLAUDE_PROGRESS.md`.
- `CLAUDE_CODE_HEARTBEAT_SECONDS` controls heartbeat frequency and defaults to `30`.
- `CLAUDE_CODE_TIMEOUT_SECONDS` controls maximum runtime and defaults to `600`; `0` disables timeout.
- `CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS` optionally stops Claude when result/status/report/progress artifacts do not change; default is `0` disabled. Set it to a positive number only when you want fast-fail behavior.

The dispatcher prints copy-paste `Watch Progress` and `Watch Details` commands as soon as Claude starts and again in the completion summary, so users can view progress in Codex CLI without opening documentation or artifact files.

If Claude times out or exits non-zero, the dispatcher continues to collect available evidence instead of aborting before diff/report generation.

For complex or repeatedly revised tasks, use `## Execution Phases` in the task card. The dispatcher prompt requires Claude to treat that table as the outer execution contract, update progress at phase boundaries, and write `CLAUDE_REPORT.md` before long-running validation or before crossing a stop gate.

## Control-Plane Exception

The default workflow is strict: Codex plans/reviews and Claude Code edits. If the workflow control plane itself is broken (for example `dispatch-to-claude.sh`, `review-with-codex.sh`, `run-loop.sh`, or the installer prevents delegation or evidence collection), Codex may make a narrowly scoped hotfix directly. Requirements for this exception:

- Record a task card describing the control-plane defect.
- Keep edits limited to workflow infrastructure.
- Preserve existing user-owned content and safety constraints.
- Run local verification and document exact evidence.
- Return normal implementation work to Claude Code after delegation is reliable again.

## Claude Dispatch Operations

Installed projects include helper scripts for stuck or long-running Claude dispatches:

- `ai/status-claude.sh [claude-<timestamp>]` shows PID state, artifact sizes, progress tail, status tail, and worktree git status.
- `ai/watch-claude.sh [claude-<timestamp>]` shows an obvious CLI status panel by default, using the live worktree `CLAUDE_PROGRESS.md` while a run is active; use `--details` to expand progress tails, and `--stale-after seconds` to surface stuck-run analysis.
- `ai/kill-claude.sh <claude-<timestamp>> [--kill-after seconds]` stops only the Claude process recorded in the PID artifact.
- `ai/cleanup-worktree.sh <claude-<timestamp>> [--force]` removes a stopped Claude worktree while preserving `.worktrees/claude-<id>.*` evidence artifacts.

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

## Workflow Doctor

Run the doctor to check whether a repository is ready for the dispatch/review loop:

```bash
python ai/doctor_workflow.py
```

The doctor is read-only and reports:

| Check | What it looks for |
|-------|-------------------|
| repo | .git directory exists |
| git | git CLI available in PATH |
| dirty | uncommitted changes in source worktree |
| artifacts | runtime entries under `.worktrees/` and `tmp-*` at repo root |
| bash | bash resolution; warns if WSL may intercept on Windows |
| claude | Claude CLI in PATH |
| proxy | common proxy env vars (values are masked) |
| codex-skill | `~/.codex/skills/ai-coding-workflow` exists |

Exit code `0` means no hard errors; non-zero means at least one `ERROR` was found. Warnings do not cause a non-zero exit.

When runtime artifacts exist, the doctor suggests running `ai/clean_runtime.py` to preview and optionally remove them.

## Troubleshooting


### Windows: PowerShell UTF-8 setup

When reading or writing non-ASCII files from Windows PowerShell, first dot-source the installed helper:

```powershell
. .\ai\pwsh-utf8.ps1
```

Use `-Persist` only when the user explicitly wants the helper added to their PowerShell profile:

```powershell
. .\ai\pwsh-utf8.ps1 -Persist
```

Agents should prefer UTF-8 file APIs and this helper over PowerShell here-strings containing non-ASCII text. This avoids mojibake and replacement characters in files such as `README_CN.md`.

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

- `references/loop-model.md`  -  loop state machine, role responsibilities, stop conditions
- `references/operating-model.md`  -  agent roles and handoff model
- `references/review-policy.md`  -  code review division of labor and structured decisions
- `references/mcp-policy.md`  -  information retrieval order
