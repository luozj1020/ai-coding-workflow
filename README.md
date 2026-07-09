# AI Coding Workflow Skill

A reusable Codex / Claude Code workflow skill for installing a local multi-agent coding workflow into software repositories.

English | [中文](README_CN.md)

## What it does

ai-coding-workflow bootstraps repositories with:
- `AGENTS.md` - shared rules for all agents
- `CLAUDE.md` - Claude Code execution rules
- Task-card and evidence-packet templates
- Safe dispatch/review/loop scripts for Codex + Claude Code workflows
- Optional Codex Spark helper for `gpt-5.3-codex-spark` review/evidence checks and tiny isolated micro-builder work
- Builder / Checker-Test task modes for separating implementation from validation
- Direction / boundary acknowledgement gates with anti-loop rules
- Managed blocks for idempotent updates

## Common actions

| Action | When | Command |
|--------|------|---------|
| **Install Skill** | Once per computer | `python scripts/install_for_codex.py` |
| **Update Skill** | After pulling a newer checkout | `python scripts/update_skill.py --bootstrap-current` |
| **Bootstrap project** | Once per repository | `python scripts/install_workflow.py .` |
| **Refresh project workflow** | Existing bootstrapped repository | `python scripts/install_workflow.py . --update-workflow-files` |

These actions are separate. Installing the Skill only makes Codex discover the workflow; it does not create or refresh the target repository's `ai/` directory. Already bootstrapped projects keep local copies of `ai/dispatch-to-claude.sh`, `ai/task-card-template.md`, and other workflow files. Use `update_skill.py --bootstrap-current` or `install_workflow.py . --update-workflow-files` to refresh those local copies after updating the Skill.

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
    plan-task-template.md
    plan-findings-template.md
    plan-progress-template.md
  references/
    loop-model.md        -> Loop state machine and stop conditions
    operating-model.md   -> Agent roles and handoff model
    review-policy.md     -> Code review division of labor
    mcp-policy.md        -> Information retrieval order
    benchmark-policy.md  -> Quality / speed / cost / stability evaluation
  scripts/
    install_workflow.py  -> Bootstrap a repository
    install_for_codex.py -> Install skill for Codex discovery
    update_skill.py      -> Convenience updater for skill + optional repo bootstrap
    dispatch-to-claude.sh -> Dispatch task cards to Claude Code
    check-worktree.sh    -> Run checker-only validation and write a checker report
    review-with-codex.sh -> Send evidence to Codex/GPT for review
    run-codex-spark.sh   -> Optional gpt-5.3-codex-spark auxiliary runner
    run-loop.sh          -> Optional loop runner (dispatch + review)
    status-claude.sh     -> Inspect Claude dispatch status and artifacts
    watch-claude.sh      -> Show CLI progress panel for running dispatches
    kill-claude.sh       -> Stop a recorded Claude dispatch process
    cleanup-worktree.sh  -> Remove stopped worktrees while preserving evidence
    pwsh-utf8.ps1        -> Configure PowerShell UTF-8 sessions
    doctor_workflow.py   -> Read-only readiness check for dispatch/review loop
    clean_runtime.py     -> Preview/remove ignored runtime artifacts
    install_context_tools.py -> Check/install context tools (LSP, linting)
    summarize-loop-run.py -> Summarize workflow quality, speed, cost, and stability
    benchmark-loop-runs.py -> Aggregate loop summaries into a lightweight benchmark
    init-spec.py         -> Create ai/specs/YYYY-MM-DD--slug.md
    plan-to-task-cards.py -> Generate task cards from reviewed plan sections
    init-plan.py         -> Create ai/plans/<task-id>/ planning files
    session-catchup.py   -> Generate resume-context.md from plan and artifacts
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

The installer prints exact bootstrap commands after installation. From this cloned Skill repository, you can also install the Skill and bootstrap a target project in one command:

```powershell
python .\scripts\install_for_codex.py --bootstrap-repo E:\path\to\your-project
```

```bash
python scripts/install_for_codex.py --bootstrap-repo /path/to/your-project
```

For routine updates from a cloned checkout, use the wrapper:

```bash
python scripts/update_skill.py
python scripts/update_skill.py --bootstrap-current
python scripts/update_skill.py --pull --bootstrap-repo /path/to/your-project
```

`python scripts/update_skill.py` updates only the user-level Codex Skill. `--bootstrap-current` and `--bootstrap-repo` additionally refresh the target repository's local workflow files with `--update-workflow-files`, so existing projects receive new dispatcher, review prompt, template, and helper behavior.

When running from an already installed skill but updating from a separate clone, point it at the clone:

```bash
python ~/.codex/skills/ai-coding-workflow/scripts/update_skill.py \
  --source /path/to/ai-coding-workflow \
  --bootstrap-current
```

During Skill installation, the installer performs a read-only context intelligence check:
- LSP tools such as `pyright`, `typescript-language-server`, `gopls`, and `rust-analyzer`.
- CodeGraph CLI availability.
- CodeGraph repository initialization when `--bootstrap-current` or `--bootstrap-repo` is used.

It only prints suggestions. It does not install LSP tools and does not run `codegraph init` automatically. Use `python ~/.codex/skills/ai-coding-workflow/scripts/install_context_tools.py` to inspect LSP install suggestions, and run `codegraph init` inside a target repository when you want that repository indexed.

**Test it works:**

```
Use ai-coding-workflow to explain how to install the workflow in this repo.
```

If Codex can answer and reference this skill's installer, the skill is active.

### If Claude Code is not installed

The skill can still install, generate task cards, run the workflow doctor, and support Codex review. Only the execution step that calls `claude -p` is unavailable. `dispatch-to-claude.sh` checks for the `claude` command before creating a worktree and exits with a clear error if it is missing. Run `python ai/doctor_workflow.py` in a bootstrapped project to confirm whether Claude CLI is available.

---

## Scenario B: Bootstrap a new project

After the skill is installed, bootstrap any repository. Do this once per project. This is the step that creates `ai/dispatch-to-claude.sh` and the rest of the local workflow files.

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
ai/plan-task-template.md
ai/plan-findings-template.md
ai/plan-progress-template.md
ai/README.md
ai/dispatch-to-claude.sh
ai/check-worktree.sh
ai/review-with-codex.sh
ai/run-codex-spark.sh
ai/run-loop.sh
ai/status-claude.sh
ai/watch-claude.sh
ai/kill-claude.sh
ai/cleanup-worktree.sh
ai/pwsh-utf8.ps1
ai/doctor_workflow.py
ai/clean_runtime.py
ai/install_context_tools.py
ai/summarize-loop-run.py
ai/benchmark-loop-runs.py
ai/init-spec.py
ai/plan-to-task-cards.py
ai/init-plan.py
ai/session-catchup.py
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

By default, existing plain workflow files under `ai/` are not overwritten. If they differ from the installed Skill, the installer reports them as `outdated`. To refresh an already bootstrapped project after a Skill update, run:

```bash
python ~/.codex/skills/ai-coding-workflow/scripts/install_workflow.py . --update-workflow-files
```

or from a cloned Skill checkout:

```bash
python scripts/update_skill.py --bootstrap-current
```

---

## Typical daily workflow

The workflow is an explicit loop: **OBSERVE  ->  PLAN  ->  DISPATCH  ->  EXECUTE  ->  VERIFY  ->  REVIEW  ->  LEARN  ->  repeat.**

**Core principle:** Codex designs and reviews. Claude edits. Tools gather low-token evidence first. Codex stays within a low-token context budget; broad reads and multi-file work are delegated to Claude. Claude returns compressed evidence (summaries + artifact paths) instead of pasted logs.

For non-trivial changes, split the work into two Claude roles:

- **Builder Claude** implements the scoped change and reports direction. It does not write acceptance tests or run broad suites unless the task card explicitly allows a narrow sanity check.
- **Checker/Test Claude** runs after Codex accepts the builder direction. It writes or updates assigned tests, runs validation commands, and reports evidence without broad implementation rewrites.

Task cards can require **Direction / Boundary Acknowledgement** before editing. Claude restates the goal, scope, out-of-scope boundaries, likely files, acceptance criteria, testing responsibility, confusions, and risks. This is a gate, not a discussion loop: at most one blocking acknowledgement is allowed per task or phase unless Codex materially changes the goal, scope, boundaries, or risk. Codex must answer with exactly one decision: proceed, narrow-once/re-dispatch, split, or stop.

For ambiguous feature, UX, API, or data-model work, write a short spec before implementation:

```bash
python ai/init-spec.py "Feature or change name"
```

The spec records desired behavior, non-goals, acceptance surface, constraints, alternatives, and risks. Fill `Spec Gate` in the task card and link the spec. `ai/init-plan.py` creates a `task_plan.md` with `### Task N: ...` sections; after reviewing those sections, generate scoped task cards with:

```bash
python ai/plan-to-task-cards.py ai/plans/PROJ-123/task_plan.md
```

For bugfixes and regressions, fill `Root Cause Gate` before assigning a fix. For acceptance-critical behavior, fill `Test-First / TDD Contract` so red evidence before production edits and green evidence after implementation are explicit. Before saying a branch is ready, fill `Finish Branch Gate` with fresh verification and artifact classification.

Phase ownership is explicit:

| Phase | Codex owns | Claude owns |
|-------|------------|-------------|
| Observe / Plan | Evidence, scope, task card, acceptance criteria, responsibility gates | N/A unless dispatched for exploration |
| Builder Execute | Progress observation and direction review | Scoped implementation, progress updates, direction report |
| Direction Review | Wait, revise, split, dispatch checker-test, or threshold-based takeover decision | Report blockers and avoid repeated confirmation loops |
| Checker/Test | Validation task dispatch and evidence review | Assigned tests, assigned validation, failure evidence |
| Final Review | Accept / revise / split / reject; human merge stays separate | N/A unless re-dispatched |

When Claude appears stuck, first classify the cause before blaming execution: task-card ambiguity, mixed-role assignment, dirty source/stale HEAD, permission or approval blocker, long-running validation, missing progress artifact, external environment, or true no-progress.

Permission or approval blockers include sandbox write denial, forbidden files, missing CLI authentication, network-restricted commands, commands that need human approval, and configured "do not read or modify" paths. These should be recorded in progress/report artifacts and handled as environment or orchestration blockers unless Claude ignored an available allowed path.

Dirty source or stale HEAD is handled the same way: it blocks reliable delegation, but it is not by itself permission for Codex to take over implementation. First restore the delegation path by committing an accepted phase, stashing or patching source changes, refreshing workflow files, re-dispatching from updated HEAD, requesting explicit dirty-source approval, or stopping for human input.

**Step 1: Initialize project** (once)

```powershell
python $env:USERPROFILE\.codex\skills\ai-coding-workflow\scripts\install_workflow.py .
```

**Step 2: Create task card** (in Codex  -  OBSERVE + PLAN)

```
Use ai-coding-workflow to create a task card for implementing <feature>.
```

For bounded loops, fill `Goal Loop Contract` in the task card. Prefer deterministic fields such as success signal, max attempts, repeated-failure threshold, no-improvement threshold, regression stop rule, required evidence, and benchmark tags. Use `Spec Gate` before broad ambiguous work, `Root Cause Gate` before bugfixes/regression fixes, `Test-First / TDD Contract` when red-green evidence matters, and `Finish Branch Gate` before claiming work ready for merge. Use `Advisor Gate` when a stronger model, Codex reviewer, or human expert should advise before risky work; record timing, call caps, output budget, result visibility, conflict reconciliation, and fallback behavior. Use `Unknowns` to record blindspot scan requests, questions that would change architecture, reference examples, and where Claude should record deviations from plan.

**Optional: create persistent planning files** for long-running work:

```bash
python ai/init-plan.py PROJ-123
```

This creates `ai/plans/PROJ-123/task_plan.md`, `findings.md`, and `progress.md`. To resume after context loss or `/clear`:

```bash
python ai/session-catchup.py --plan PROJ-123
```

**Step 3: Dispatch Builder Claude** (DISPATCH + EXECUTE)

```
Use the coding executor workflow. Execute this task card and return an evidence packet.
```

For implementation work, set the task card mode to `builder`. Builder Claude owns the scoped edit and progress reporting. If testing is required, state that Builder Claude should stop after implementation evidence and that Codex will dispatch a separate `checker-test` task.

This generates these artifacts under `.worktrees/`:

**Proxy behavior:** `dispatch-to-claude.sh` runs Claude Code with common proxy environment variables cleared by default (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`, and lowercase variants). This lets Codex keep using your shell proxy while Claude Code goes direct. If Claude Code must inherit the proxy, run:

```bash
CLAUDE_CODE_PROXY_MODE=inherit bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md
```

**Network diagnostics:** by default the dispatcher does not inspect network state. To record metadata-only socket snapshots for the Claude process and its child processes, run:

```bash
CLAUDE_CODE_NETWORK_MONITOR=1 bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md
```

This creates `*.network.log` with proxy mode, redacted proxy settings, tool availability, and per-heartbeat socket summaries such as `established`, `syn_sent`, and `close_wait`. It does not capture packet contents, prompts, request bodies, or tokens. To add an explicit connectivity probe, set `CLAUDE_CODE_NETWORK_HEALTHCHECK_URL`; the dispatcher will run a bounded `curl -I` healthcheck and store only its status/output in the network log.

| Artifact | Description |
|----------|-------------|
| `*.result.json` | Raw Claude JSON output |
| `*.status.txt` | Claude stderr / execution log |
| `*.network.log` | Optional metadata-only network diagnostics when `CLAUDE_CODE_NETWORK_MONITOR=1` |
| `*.diffstat.txt` | `git diff --stat` for tracked files |
| `*.diff` | Full diff, including untracked implementation files |
| `*.checker-report.md` | Checker-only validation report from `ai/check-worktree.sh` |
| `*.checker-logs/` | Full logs for checker commands |
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

While Claude is running, `*.progress.log` records both artifact growth and implementation worktree changes. `ai/watch-claude.sh` and `ai/status-claude.sh` show partial worktree diffstat/status. In the first waiting rounds, if the worktree is still changing, review the partial diff against the task card and continue waiting when it matches the plan. Interrupt Claude only when the partial implementation is off-plan, risky, or no longer making useful progress.

If Direction / Boundary Acknowledgement is required, Claude should write the acknowledgement before editing. When blocking approval is required, Codex gives one final decision before Claude proceeds. After `proceed`, Claude must continue the assigned task instead of repeatedly asking for the same confirmation.

**Step 4: Review direction with Codex** (REVIEW)

```
Use ai-coding-workflow to review this execution evidence packet and diff. Decide accept / revise / split / reject.
```

To include checker, token/cost, and repository status evidence in the review:

```bash
bash ai/review-with-codex.sh ai/task-cards/PROJ-123.md \
  .worktrees/claude-<id>.result.json \
  .worktrees/claude-<id>.diff \
  .worktrees/claude-<id>.checker-report.md \
  .worktrees/claude-<id>.usage.txt \
  .worktrees/claude-<id>.source-status.txt \
  .worktrees/claude-<id>.worktree-status.txt \
  .worktrees/claude-<id>.untracked.txt
```

If the Builder result matches the plan and validation is needed, dispatch a second task card in `checker-test` mode. Checker/Test Claude writes or updates assigned tests, runs the specified commands, and reports the result. Codex then performs the final review and may run a second verification pass when risk warrants it.

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

**Checker-only validation:** Installed projects include `ai/check-worktree.sh`. The dispatcher runs it after Claude finishes and records a checker report. The checker discovers common validation commands, runs them without editing files, preserves failed command output, and marks the report `ALL GREEN` or `FAILED`.

**Workflow quality summary:** `ai/run-loop.sh` also writes `.worktrees/loop-<timestamp>/loop-quality-summary.md` and `.json`. To summarize an existing run manually:

```bash
python ai/summarize-loop-run.py .worktrees/loop-<timestamp> \
  --output .worktrees/loop-<timestamp>/loop-quality-summary.md \
  --json-output .worktrees/loop-<timestamp>/loop-quality-summary.json
```

**Workflow benchmark summary:** To compare multiple loop runs as a lightweight living benchmark:

```bash
python ai/benchmark-loop-runs.py .worktrees/loop-* \
  --output .worktrees/workflow-benchmark.md \
  --json-output .worktrees/workflow-benchmark.json
```

The benchmark aggregates decision, quality score, elapsed time, token/cost totals, stability findings, loop type, benchmark tags, and advisor usage parsed from task cards and reports.

**Append-only loop events:** `ai/run-loop.sh` writes `.worktrees/loop-<timestamp>/loop-events.jsonl`, an append-only event stream for run start, iteration start, dispatch completion, review completion, decisions, revision task creation, and stop reasons. This preserves recovery context without rewriting prior observations.

**Structured progress memory:** Claude is instructed to maintain `CLAUDE_PROGRESS.md` with stable fields: Goal, Current Phase, Next Check, Blocker, and Last Update. This keeps long-running tasks anchored without pasting large logs into prompts.

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

`watch-claude.sh` defaults to a low-cost status panel: running state, elapsed/quiet seconds, a checklist-derived progress bar, the latest milestone, artifact sizes, and a short stuck-run analysis. It does not print full progress/status/network tails unless `--details` is provided or the run has produced repeated suspect snapshots. The default escalation rule is three consecutive suspect snapshots; override it with `--escalation-confirmations` or `CLAUDE_CODE_MONITOR_ESCALATION_CONFIRMATIONS`.

`watch-claude.sh` and `status-claude.sh` also print machine-readable monitor fields (`monitor_level`, `action`, `evidence_state`, quiet/elapsed seconds, suspect count when available). Codex should prefer these low-token fields before reading full status, progress, or network tails.

Monitoring priority is intentionally conservative to avoid false kills:

1. L0: compact `watch-claude.sh` heartbeat/progress only.
2. L1: partial diff review when the worktree is changing; continue waiting if aligned with the task card.
3. L2: `status-claude.sh` or watch details after repeated suspect snapshots.
4. L3: corroborate progress, status, diff, process, and optional network diagnostics after the interrupt window.
5. L4: use `kill-claude.sh` only after multiple evidence sources agree useful progress is unlikely.

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

# Require five repeated suspect snapshots before auto-expanding details
bash ai/watch-claude.sh claude-20260701-093934 --escalation-confirmations 5

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

If the doctor reports `Project workflow is not bootstrapped`, run the bootstrap command it prints. A repository cannot use `bash ai/dispatch-to-claude.sh ...` until the local `ai/` workflow directory exists.

**Clean up runtime artifacts:**

```bash
# Preview what would be removed (dry-run)
python ai/clean_runtime.py

# Actually remove artifacts
python ai/clean_runtime.py --apply
```

**Check context tools:**

```bash
# Check which LSP/linting tools are available (read-only)
python ai/install_context_tools.py

# Show planned install commands for a profile (dry-run)
python ai/install_context_tools.py --apply python --manager npm

# Actually install (requires --apply, --manager, and --yes)
python ai/install_context_tools.py --apply python --manager npm --yes
```

The context tools helper checks for common LSP, linting, and code intelligence
tools (pyright, ruff, mypy, typescript-language-server, gopls, rust-analyzer).
Default invocation is read-only. Actual package execution requires all three
flags: `--apply PROFILE`, `--manager MANAGER`, and `--yes`.

Note: installing context tool binaries does NOT automatically expose them as
Codex LSP/codegraph tools. The Codex agent must be configured separately to
use them.

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
