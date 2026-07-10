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

If the target repository should use the workflow locally but should not commit `ai/`, `AGENTS.md`, `CLAUDE.md`, `.worktrees/`, or `.gitignore` changes, use local-only bootstrap:

```bash
python ~/.codex/skills/ai-coding-workflow/scripts/install_workflow.py . --local-only
```

`--local-only` writes the control-plane paths to `.git/info/exclude` and leaves `.gitignore` untouched. `doctor_workflow.py` treats that as a valid local-only ignore configuration.

On Windows PowerShell:

```powershell
python $env:USERPROFILE\.codex\skills\ai-coding-workflow\scripts\install_workflow.py .
```

For routine updates, use the convenience wrapper from a cloned `ai-coding-workflow` checkout:

```bash
python scripts/update_skill.py --bootstrap-current
```

Updating the user-level Skill and updating this repository's local workflow files are separate operations. `update_skill.py --bootstrap-current` does both: it refreshes the Codex Skill and then runs the repository bootstrap with `--update-workflow-files` so existing `ai/*` workflow files receive new dispatcher, review prompt, template, and helper behavior. Running `install_workflow.py` without that flag reports outdated local files but does not overwrite them.

If running from the installed Skill while using a separate clone as the update source:

```bash
python ~/.codex/skills/ai-coding-workflow/scripts/update_skill.py \
  --source /path/to/ai-coding-workflow \
  --bootstrap-current
```

## What Is This?

This repository has been set up with a multi-agent AI coding workflow. The workflow splits software work between planning, execution, and review agents in an explicit loop:

- **Codex / GPT**  -  plans and reviews (top-level design, not concrete edits)
- **Claude Code**  -  implements in Builder tasks and validates in Checker/Test tasks
- **Codex Spark**  -  default-on optional `gpt-5.3-codex-spark` auxiliary for task-size classification, task-card audits, plan splitting, validation planning, failure triage, evidence checks, or tiny isolated micro-builder work
- **Large-repo mode**  -  optional managed worktree reuse and reduced untracked-file scans for slow filesystems
- **MiMo / DeepSeek**  -  optional exhaustive review helper
- **LSP / Codegraph / MCP**  -  low-token code intelligence (used first, before broad reads)

**Core principle:** Codex designs and reviews. Claude edits. Tools gather low-token evidence first.

For non-trivial changes, split Claude work into two roles:

- **Builder Claude** implements the scoped change and reports the implementation direction. It does not write acceptance tests or run broad suites unless the task card explicitly allows a narrow sanity check.
- **Checker/Test Claude** runs after Codex accepts the builder direction. It writes or updates assigned tests, runs validation commands, and reports evidence without broad implementation rewrites.

Task cards can require **Direction / Boundary Acknowledgement** before editing. Claude restates the goal, scope, out-of-scope boundaries, likely files, acceptance criteria, testing responsibility, confusions, and risks. This is a gate, not a discussion loop: at most one blocking acknowledgement is allowed per task or phase unless Codex materially changes the goal, scope, boundaries, or risk. Codex answers with exactly one decision: proceed, narrow-once/re-dispatch, split, or stop.

Use `ai/init-spec.py` for ambiguous feature, UX, API, or data-model work, then fill `Spec Gate` in the task card. `ai/init-plan.py` creates `task_plan.md` with `### Task N: ...` sections; use `ai/plan-to-task-cards.py` to turn reviewed task sections into scoped task cards. Use `Root Cause Gate` before bugfixes/regressions, `Test-First / TDD Contract` when red-green evidence matters, and `Finish Branch Gate` before claiming work is ready for human merge.

Leave `Codex Spark Gate` at `auto` when a task can benefit from the separate Spark quota pool without spending stronger-model quota. Prefer Spark for uncertain task-size routing before spending stronger Codex/Claude context. Prefer read-only modes: `task-size-classifier`, `task-card-audit`, `plan-splitter`, `validation-planner`, `failure-triage`, `review-only`, or `evidence-checker`; use `micro-builder` only for tiny scoped edits in the helper-created isolated worktree. Spark evidence is auxiliary, auto-disables when unavailable or quota-exhausted, and must not silently fall back to GPT-5.5 or another stronger model.

Phase ownership is explicit:

| Phase | Codex owns | Claude owns |
|-------|------------|-------------|
| Observe / Plan | Evidence, scope, task card, acceptance criteria, responsibility gates | N/A unless dispatched for exploration |
| Builder Execute | Progress observation and direction review | Scoped implementation, progress updates, direction report |
| Direction Review | Wait, revise, split, dispatch checker-test, or threshold-based takeover decision | Report blockers and avoid repeated confirmation loops |
| Checker/Test | Validation task dispatch and evidence review | Assigned tests, assigned validation, failure evidence |
| Final Review | Accept / revise / split / reject; human merge stays separate | N/A unless re-dispatched |

Small low-risk edits can use a Codex-only fast path instead of dispatching Claude. Use it only when the change is local, expected to touch no more than two small files, needs no broad context, has no public API/data/security/migration/permission/concurrency/cross-module contract risk, and has narrow validation or an explicit validation-skip reason. Record why Claude was not dispatched, files touched, validation evidence, and the condition that would have escalated to Claude. If scope expands or uncertainty appears, stop and return to task-card + Claude dispatch.

When Claude appears stuck, first classify the cause before blaming execution: task-card ambiguity, mixed-role assignment, dirty source/stale HEAD, permission or approval blocker, long-running validation, missing progress artifact, external environment, or true no-progress.

Permission or approval blockers include sandbox write denial, forbidden files, missing CLI authentication, network-restricted commands, commands that need human approval, and configured "do not read or modify" paths. These should be recorded in progress/report artifacts and handled as environment or orchestration blockers unless Claude ignored an available allowed path.

Dirty source or stale HEAD is handled the same way: it blocks reliable delegation, but it is not by itself permission for Codex to take over implementation. First restore the delegation path by committing an accepted phase, stashing or patching source changes, refreshing workflow files, re-dispatching from updated HEAD, requesting explicit dirty-source approval, or stopping for human input.

## Directory Structure

```
ai/
  task-card-template.md      # Template for planning work items
  evidence-packet-template.md # Template for documenting execution results
  spec-template.md           # Template for lightweight specs
  plan-task-template.md       # Persistent task plan template
  plan-findings-template.md   # Persistent findings template
  plan-progress-template.md   # Persistent progress template
  dispatch-to-claude.sh       # Dispatches task cards to Claude Code
  check-worktree.sh           # Runs checker-only validation and writes a report
  review-with-codex.sh        # Sends evidence to Codex/GPT for review
  run-codex-spark.sh          # Optional gpt-5.3-codex-spark auxiliary runner
  run-parallel-loop.sh        # Experimental parallel dispatch helper
  run-loop.sh                 # Optional loop runner (dispatch + review)
  status-claude.sh            # Inspect Claude dispatch progress/artifacts
  watch-claude.sh             # Stream Claude progress in a terminal
  kill-claude.sh              # Stop a Claude dispatch by PID artifact
  cleanup-worktree.sh         # Remove stopped Claude worktrees safely
  pwsh-utf8.ps1                # Configure PowerShell UTF-8 session defaults
  doctor_workflow.py          # Read-only readiness check for dispatch/review loop
  clean_runtime.py            # Preview/remove ignored runtime artifacts
  install_context_tools.py    # Check/install context tools (LSP, linting)
  summarize-loop-run.py       # Summarize workflow quality, speed, cost, and stability
  benchmark-loop-runs.py      # Aggregate loop summaries into a lightweight benchmark
  init-spec.py                # Create ai/specs/YYYY-MM-DD--slug.md
  plan-to-task-cards.py       # Generate task cards from reviewed plan sections
  init-plan.py                # Create ai/plans/<task-id>/ planning files
  session-catchup.py          # Generate resume-context.md from plan and artifacts
  README.md                   # This file
.worktrees/                   # Isolated git worktrees for execution
ai/plans/                     # Persistent planning files for long-running tasks
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

For bounded loops, fill `Goal Loop Contract` in the task card. Prefer deterministic fields such as success signal, max attempts, repeated-failure threshold, no-improvement threshold, regression stop rule, required evidence, and benchmark tags. Use `Spec Gate` before broad ambiguous work, `Root Cause Gate` before bugfixes/regression fixes, `Test-First / TDD Contract` when red-green evidence matters, and `Finish Branch Gate` before claiming work ready for merge. Use `Advisor Gate` when a stronger model, Codex reviewer, or human expert should advise before risky work; record timing, call caps, output budget, result visibility, conflict reconciliation, and fallback behavior. Leave `Codex Spark Gate` at `auto` when Spark should perform low-cost task-size classification, task-card audit, plan splitting, validation planning, failure triage, review/evidence checking, or tiny isolated micro-builder work, with auto-disable on Spark unavailability. Use `Unknowns` to record blindspot scan requests, questions that would change architecture, reference examples, and where Claude should record deviations from plan.

For longer tasks, create persistent planning files:

```bash
python ai/init-plan.py PROJ-123
```

To recover after context loss or `/clear`:

```bash
python ai/session-catchup.py --plan PROJ-123
```

### 2. Dispatch Builder Claude

```bash
bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md
```

For implementation work, set the task card mode to `builder`. Builder Claude owns the scoped edit and progress reporting. If testing is required, state that Builder Claude should stop after implementation evidence and that Codex will dispatch a separate `checker-test` task.

This creates an isolated worktree under `.worktrees/`, runs Claude Code, and saves these artifacts:

If Claude Code is not installed, the rest of the workflow files remain useful for planning, review, and readiness checks. Dispatch execution requires the `claude` command; the dispatcher checks for it before creating a worktree.

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

It does **not** merge automatically.

### Default-On Optional Codex Spark Auxiliary

When the task card leaves `Codex Spark Gate` at `auto`, run Spark as a low-cost auxiliary for eligible tasks. Spark is optional support: if the CLI, model access, auth, network, Spark quota, or local helper initialization is unavailable, the helper writes an auto-disabled report and exits 0 so the main Claude/Codex workflow can continue.

```bash
bash ai/run-codex-spark.sh ai/task-cards/PROJ-123.md
bash ai/run-codex-spark.sh ai/task-cards/PROJ-123.md --mode review-only
bash ai/run-codex-spark.sh ai/task-cards/PROJ-123.md --mode task-card-audit
bash ai/run-codex-spark.sh ai/task-cards/PROJ-123.md --mode validation-planner
```

Default `--mode auto` resolves to `task-size-classifier` before normal dispatch, `validation-planner` for Checker/Test tasks, `failure-triage` for failed/no-report artifacts, `review-only` for diff artifacts, and `evidence-checker` for report/evidence artifacts.

Use `task-size-classifier` to spend cheaper Spark quota before stronger-model context when task size is unclear. It should classify the task as `tiny`, `small`, `medium`, `large`, or `unknown` and recommend `codex-fast-path`, `spark-review-only`, `spark-micro-builder`, `claude-builder`, `checker-test`, `spec-first`, or `human-clarification`.

When `auto` resolves to `task-size-classifier`, the helper runs Codex from the Spark artifact directory with `workspace-write` sandbox. This gives local helper initialization a writable working directory without granting write access to the source repository, and the mode contract still forbids source edits.

For evidence checks:

```bash
bash ai/run-codex-spark.sh ai/task-cards/PROJ-123.md --mode evidence-checker \
  --artifact .worktrees/claude-<id>.report.md \
  --artifact .worktrees/claude-<id>.checker-report.md
```

For stalled or failed runs:

```bash
bash ai/run-codex-spark.sh ai/task-cards/PROJ-123.md --mode failure-triage \
  --artifact .worktrees/claude-<id>.status.txt \
  --artifact .worktrees/claude-<id>.progress.log
```

For tiny scoped edits only, use micro-builder mode. The helper creates an isolated worktree and refuses dirty source repositories unless explicitly overridden:

```bash
bash ai/run-codex-spark.sh ai/task-cards/PROJ-123.md --mode micro-builder --sandbox workspace-write
```

Spark artifacts include `codex-spark.report.md`, `codex-spark.prompt.md`, `codex-spark.result.txt`, `codex-spark.stderr.log`, `codex-spark.artifacts.txt`, `codex-spark.worktree-status.txt`, and optional `codex-spark.diff`. Spark does not silently fall back to GPT-5.5 or another stronger model. Use `--require-spark` only when Spark availability should become a hard failure.

Spark output is advisory. Record accepted and ignored suggestions in the Spark follow-up table, but do not let Spark replace Claude Builder ownership or Codex final review.

### Large Repositories / Slow Filesystems

Fill `Worktree / Large Repo Strategy Gate` before dispatch when `git worktree add`, filesystem reads, or dispatcher status/diff collection are materially slow. Defaults keep complete evidence. Prefer the explicit fast profile when the gate accepts managed reuse and summary evidence:

Keep CodeGraph queries narrow in large repositories. If a broad query times out, record the timeout as context evidence, narrow once to concrete files/symbols, then fall back to `rg --files` plus targeted line reads instead of repeatedly issuing broad CodeGraph queries.

Fill `Claude Context Packet` before dispatch in large repositories. Keep it small and execution-facing: target files/modules, relevant symbols, source-of-truth examples, paths Claude must not read or modify, known constraints, and narrow validation commands. If the packet is incomplete, Claude should stop-and-report instead of rediscovering the repository with broad searches.

```bash
CLAUDE_CODE_EXECUTION_PROFILE=fast-large-repo \
bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md
```

To reuse the managed Claude worktree without the full fast profile:

```bash
CLAUDE_CODE_WORKTREE_STRATEGY=reuse-managed \
CLAUDE_CODE_REUSE_WORKTREE_RESET=1 \
bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md
```

To reduce expensive untracked scans and untracked patch evidence:

```bash
CLAUDE_CODE_LARGE_REPO_MODE=1 \
bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md
```

The managed reuse path is limited to `.worktrees/reuse/claude-managed`. Large-repo mode preserves tracked/staged diff evidence but intentionally reduces untracked-file evidence.
Bootstrap also keeps workflow runtime artifacts ignored:

```gitignore
/.worktrees/*
!/.worktrees/.gitkeep
```

To skip full patch text but keep the worktree for review:

```bash
CLAUDE_CODE_EVIDENCE_MODE=summary \
bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md
```

### Experimental Parallel Dispatch

When task cards have `Parallel Execution Gate` filled and independent file/module scopes, run:

```bash
bash ai/run-parallel-loop.sh --max-concurrency 2 \
  ai/task-cards/PROJ-123-a.md \
  ai/task-cards/PROJ-123-b.md
```

The helper dispatches tasks concurrently, writes `.worktrees/parallel-*/parallel-summary.md`, and never merges automatically. It refuses ungated task cards and overlapping `Allowed files/modules` by default. Use `--allow-overlap` only for explicit manual-reconcile experiments.

While Claude is running, `*.progress.log` records both artifact growth and implementation worktree changes. `ai/watch-claude.sh` and `ai/status-claude.sh` show partial worktree diffstat/status. In the first waiting rounds, if the worktree is still changing, review the partial diff against the task card and continue waiting when it matches the plan. Interrupt Claude only when the partial implementation is off-plan, risky, or no longer making useful progress.

If Direction / Boundary Acknowledgement is required, Claude should write the acknowledgement before editing. When blocking approval is required, Codex gives one final decision before Claude proceeds. After `proceed`, Claude must continue the assigned task instead of repeatedly asking for the same confirmation.

### 3. Review Direction with Codex

```bash
bash ai/review-with-codex.sh ai/task-cards/PROJ-123.md .worktrees/claude-<timestamp>.result.json .worktrees/claude-<timestamp>.diff
```

To include checker, token/cost, and repository status evidence in the review:

```bash
bash ai/review-with-codex.sh ai/task-cards/PROJ-123.md \
  .worktrees/claude-<timestamp>.result.json \
  .worktrees/claude-<timestamp>.diff \
  .worktrees/claude-<timestamp>.checker-report.md \
  .worktrees/claude-<timestamp>.usage.txt \
  .worktrees/claude-<timestamp>.source-status.txt \
  .worktrees/claude-<timestamp>.worktree-status.txt \
  .worktrees/claude-<timestamp>.untracked.txt \
  .worktrees/claude-<timestamp>.claude-progress.md
```

Codex reviews the work and returns a structured decision: accept, revise, split, or reject, with explicit next-loop instructions.

If the Builder result matches the plan and validation is needed, dispatch a second task card in `checker-test` mode. Checker/Test Claude writes or updates assigned tests, runs the specified commands, and reports the result. Codex then performs the final review and may run a second verification pass when risk warrants it.

Dispatch defaults to the `balanced` execution profile: compact Claude task card, brief prompt, fresh worktree, and full diff evidence. This reduces prompt/task-card tokens while preserving review evidence. The full planning card remains available as `TASK_CARD_FULL.md`.

Use `safe` for ambiguous or high-risk tasks that need the standard prompt and non-compact execution card:

```bash
CLAUDE_CODE_EXECUTION_PROFILE=safe \
bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md
```

Use `fast-large-repo` only after the large-repo gate records the evidence tradeoff:

```bash
CLAUDE_CODE_EXECUTION_PROFILE=fast-large-repo \
bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md
```

`fast-large-repo` uses the managed reuse worktree, skips unrelated untracked scans, and writes summary diff evidence instead of full patch text. It never resets the source repository. If `.worktrees/reuse/claude-managed` already exists, preserve or review its evidence first, then explicitly add `CLAUDE_CODE_REUSE_WORKTREE_RESET=1` to reset only that managed worktree.

### Checker-Only Validation

Installed projects include `ai/check-worktree.sh`. Prefer exact task-card validation commands:

```bash
bash ai/check-worktree.sh --task-card ai/task-cards/PROJ-123.md --no-discover --command 'tests=pytest tests/test_target.py'
```

The dispatcher records a checker report after Claude finishes, but broad discovery is disabled by default to avoid unrelated validation noise. Pass `CLAUDE_CODE_CHECKER_COMMANDS=$'tests=pytest tests/test_target.py'` for exact dispatcher-run checks, or `CLAUDE_CODE_CHECKER_DISCOVER=1` when the task card explicitly allows broad project discovery.

The checker also reads task-card validation fences when `--task-card` is passed:

```bash validation
bazel test //path/to:target
```

If the task card says `Local validation allowed? | no`, checker reports artifact collection as `OK` and validation as `SKIPPED by policy`; it does not run commands and does not mean tests passed. Use that when the user or repository policy forbids local test execution.

### Project Test Tiers

Use the smallest verification tier that matches the edit:

```bash
# Smoke: shell syntax and whitespace
bash -n scripts/*.sh
git diff --check

# Fast default while editing
python -m pytest -m "not slow"

# Related tests for touched areas
python -m pytest tests/test_run_codex_spark.py tests/test_check_worktree.py

# Full release or pre-commit confidence
python -m pytest tests
```

Tests marked `slow` create repeated temporary repositories, worktrees, or installer runs. Run them before release or when touching dispatcher/worktree/install behavior, not after every small documentation or helper edit.

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
- Writes `loop-quality-summary.md` and `loop-quality-summary.json` with quality, speed, cost, and stability metrics.
- Adds fixed `Spark Status` and `Claude Evidence Classification` sections to loop quality summaries, so Spark availability/fallback and Claude report/diff gaps are visible without reading raw artifacts.
- Writes `loop-events.jsonl` as an append-only event stream for run start, iteration start, dispatch/review completion, decisions, revisions, and stop reasons.
- Does NOT merge automatically. Human must review and merge.

To summarize an existing loop run manually:

```bash
python ai/summarize-loop-run.py .worktrees/loop-<timestamp> \
  --output .worktrees/loop-<timestamp>/loop-quality-summary.md \
  --json-output .worktrees/loop-<timestamp>/loop-quality-summary.json
```

To compare multiple loop runs as a lightweight living benchmark:

```bash
python ai/benchmark-loop-runs.py .worktrees/loop-* \
  --output .worktrees/workflow-benchmark.md \
  --json-output .worktrees/workflow-benchmark.json
```

The benchmark aggregates advisor usage, Spark invocation/auto-disable/fallback status, parallel-dispatch usage, spec adherence, root-cause evidence, and TDD fields when those tables are present in task cards or reports.

The benchmark aggregates decision, quality score, elapsed time, dispatch stage timings, token/cost totals, stability findings, loop type, benchmark tags, advisor usage, Spark task-size classification/routing/confidence, and other workflow metadata parsed from task cards and reports. Stage timings include Claude startup, Claude execution, checker time, and artifact finalization when dispatch progress logs contain those events.

Claude also maintains `CLAUDE_PROGRESS.md` as a compact progress memory with stable fields: Goal, Current Phase, Next Check, Blocker, and Last Update.

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

# Preview only one stopped dispatch and its adjacent runtime artifacts
python ai/clean_runtime.py --task-id claude-20260701-093934

# Remove only one stopped dispatch's runtime artifacts
python ai/clean_runtime.py --task-id claude-20260701-093934 --apply
```

`cleanup-worktree.sh` refuses to run while the recorded Claude PID is still alive. Use `--force` only when `git worktree remove` needs it for a broken or dirty worktree.
`clean_runtime.py --task-id ...` is useful for large repositories because it avoids broad root artifact cleanup and preserves unrelated dispatches.

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
