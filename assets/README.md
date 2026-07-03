# AI Coding Workflow  -  Local Usage Guide

## Installing This Skill for Codex

To make this Skill discoverable by Codex, install it from a cloned copy of `ai-coding-workflow`:

```bash
python scripts/install_for_codex.py
```

This copies the Skill to:
- Windows: `%USERPROFILE%\.codex\skills\ai-coding-workflow`
- Unix/macOS: `$HOME/.codex/skills/ai-coding-workflow`

Skill installation and project bootstrap are separate. If another repository does not have `ai/dispatch-to-claude.sh`, run the installed Skill bootstrap command in that repository:

```bash
python ~/.codex/skills/ai-coding-workflow/scripts/install_workflow.py .
```

On Windows PowerShell:

```powershell
python $env:USERPROFILE\.codex\skills\ai-coding-workflow\scripts\install_workflow.py .
```

To update, run the same install/bootstrap commands again.

## What Is This?

This repository has been set up with a multi-agent AI coding workflow. The workflow splits software work between planning, execution, and review agents in an explicit loop:

- **Codex / GPT**  -  plans and reviews (top-level design, not concrete edits)
- **Claude Code**  -  implements and verifies (concrete file modifications)
- **MiMo / DeepSeek**  -  optional exhaustive review helper
- **LSP / Codegraph / MCP**  -  low-token code intelligence (used first, before broad reads)

**Core principle:** Codex designs and reviews. Claude edits. Tools gather low-token evidence first.

## Directory Structure

```
ai/
  task-card-template.md      # Template for planning work items
  evidence-packet-template.md # Template for documenting execution results
  dispatch-to-claude.sh       # Dispatches task cards to Claude Code
  review-with-codex.sh        # Sends evidence to Codex/GPT for review
  run-loop.sh                 # Optional loop runner (dispatch + review)
  status-claude.sh            # Inspect Claude dispatch progress/artifacts
  watch-claude.sh             # Stream Claude progress in a terminal
  kill-claude.sh              # Stop a Claude dispatch by PID artifact
  cleanup-worktree.sh         # Remove stopped Claude worktrees safely
  pwsh-utf8.ps1                # Configure PowerShell UTF-8 session defaults
  doctor_workflow.py          # Read-only readiness check for dispatch/review loop
  clean_runtime.py            # Preview/remove ignored runtime artifacts
  install_context_tools.py    # Check/install context tools (LSP, linting)
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

This creates an isolated worktree under `.worktrees/`, runs Claude Code, and saves these artifacts:

If Claude Code is not installed, the rest of the workflow files remain useful for planning, review, and readiness checks. Dispatch execution requires the `claude` command; the dispatcher checks for it before creating a worktree.

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

It does **not** merge automatically.

### 3. Review with Codex

```bash
bash ai/review-with-codex.sh ai/task-cards/PROJ-123.md .worktrees/claude-<timestamp>.result.json .worktrees/claude-<timestamp>.diff
```

To include token/cost and repository status evidence in the review:

```bash
bash ai/review-with-codex.sh ai/task-cards/PROJ-123.md \
  .worktrees/claude-<timestamp>.result.json \
  .worktrees/claude-<timestamp>.diff \
  .worktrees/claude-<timestamp>.usage.txt \
  .worktrees/claude-<timestamp>.source-status.txt \
  .worktrees/claude-<timestamp>.worktree-status.txt \
  .worktrees/claude-<timestamp>.untracked.txt \
  .worktrees/claude-<timestamp>.claude-progress.md
```

Codex reviews the work and returns a structured decision: accept, revise, split, or reject, with explicit next-loop instructions.

### 4. Merge

After human approval, merge the changes from the worktree.

## Loop Workflow

The workflow is an explicit loop, not a linear handoff. Each iteration follows:

```
OBSERVE  ->  PLAN  ->  DISPATCH  ->  EXECUTE  ->  VERIFY  ->  REVIEW
                                                              |
                                                     accept   ->  DONE
                                                     revise   ->  PLAN (next iteration)
                                                     split    ->  PLAN (child cards)
                                                     reject   ->  OBSERVE (re-plan)
```

### Using the Loop Runner

The optional loop runner composes dispatch and review automatically:

```bash
bash ai/run-loop.sh ai/task-cards/PROJ-123.md [max-iterations]
```

The loop runner:
- Dispatches the task card to Claude Code.
- Sends evidence to Codex/GPT for review.
- If the decision is `revise`, creates a revised task card and loops.
- Stops on `accept`, `split`, `reject`, max iterations, or unknown decision.
- Persists all output in `.worktrees/loop-<timestamp>/`.
- Writes `loop-usage-summary.md` with available Claude and Codex usage summaries.
- Does NOT merge automatically. Human must review and merge.

For the full state model, see the installed `ai-coding-workflow` Skill documentation.

### Manual Loop

You can also run the loop manually:

1. Create a task card.
2. Dispatch: `bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md`
3. Review: `bash ai/review-with-codex.sh ai/task-cards/PROJ-123.md .worktrees/claude-<timestamp>.result.json .worktrees/claude-<timestamp>.diff`
4. If revise: update the task card with revision instructions and go to step 2.
5. If accept: human merges.

## Updating the Workflow

To update workflow files without losing project-specific rules, run the installed Skill bootstrap command again from the target repository:

```bash
python ~/.codex/skills/ai-coding-workflow/scripts/install_workflow.py .
```

On Windows PowerShell:

```powershell
python $env:USERPROFILE\.codex\skills\ai-coding-workflow\scripts\install_workflow.py .
```

The installer preserves content outside managed markers and only replaces managed blocks.

## Troubleshooting

### Missing `ai/` After Installing the Skill

Installing the Codex Skill does not automatically modify every repository. If dispatch fails because `ai/dispatch-to-claude.sh` is missing, run the installed Skill bootstrap command in that repository, then verify with:

```bash
python ai/doctor_workflow.py
```

Do not run `bash ai/dispatch-to-claude.sh ...` until the doctor reports that project workflow files are installed.

### Windows: PowerShell UTF-8 setup

Windows PowerShell can corrupt non-ASCII text when console code pages, `$OutputEncoding`, and child process encodings disagree. Before editing or generating Chinese documentation from PowerShell, dot-source the installed helper:

```powershell
. .\ai\pwsh-utf8.ps1
```

For future shells, opt in to profile setup:

```powershell
. .\ai\pwsh-utf8.ps1 -Persist
```

This sets console input/output encoding, `$OutputEncoding`, `PYTHONUTF8`, `PYTHONIOENCODING`, and code page `65001` for the current session. Prefer this helper over ad hoc `chcp` commands or PowerShell here-strings containing non-ASCII text.

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
