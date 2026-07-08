---
name: ai-coding-workflow
description: use this skill when installing, updating, or using a local multi-agent ai coding workflow for software repositories. triggers include setting up codex and claude code collaboration, creating agents.md or claude.md rules, generating task-card and evidence-packet templates, dispatching work from codex to claude code, reviewing execution evidence, and enforcing lsp/codegraph/mcp-first development workflows.
---

# AI Coding Workflow Skill

Use this skill to install, update, or operate the local Codex / Claude Code coding workflow. Keep the default context lean: load detailed references only when the current task needs them.

## Quick Commands

Install or update the skill for Codex discovery:

```bash
python scripts/install_for_codex.py
```

Convenient update wrapper:

```bash
python scripts/update_skill.py --bootstrap-current
```

`update_skill.py --bootstrap-current` updates both the user-level Codex skill and the current repository's local workflow files. Plain project files under `ai/` are refreshed through `install_workflow.py --update-workflow-files`; running `install_workflow.py` without that flag reports outdated files but does not overwrite them.

Bootstrap a target repository:

```bash
python ~/.codex/skills/ai-coding-workflow/scripts/install_workflow.py .
```

From a cloned copy, install the skill and bootstrap a repository:

```bash
python scripts/install_for_codex.py --bootstrap-repo /path/to/repo
```

After skill installation, `install_for_codex.py` performs a read-only context intelligence check for common LSP tools, CodeGraph CLI availability, and `.codegraph/` initialization for bootstrapped repositories. It prints suggestions only; it does not install tools or run `codegraph init`.

## Modes

### Install

Run `install_for_codex.py` once per machine so Codex can discover this skill. The installer copies the skill directory to `~/.codex/skills/ai-coding-workflow` and prints bootstrap commands for target repositories.

### Update

Run the same install command again. Repository bootstrap updates managed blocks in `AGENTS.md` and `CLAUDE.md`, preserves user-owned content outside managed markers, reports outdated plain workflow files by default, and validates shell scripts with `bash -n`. Use `install_workflow.py --update-workflow-files` or `update_skill.py --bootstrap-current` to refresh existing local `ai/*` workflow files in already bootstrapped projects.

### Use

Before dispatching work, verify the target repository has `ai/dispatch-to-claude.sh` and `ai/task-card-template.md`. If not, bootstrap it first and run:

```bash
python ai/doctor_workflow.py
```

If doctor reports `workflow-version` warnings, the repository is still using older local workflow copies. Refresh with the command doctor prints, or run the installed skill updater with `--bootstrap-current` from that repository.

Core loop:

1. OBSERVE: gather low-token context with LSP, CodeGraph, MCP, and targeted snippets.
2. PLAN: create or revise a task card from `ai/task-card-template.md`.
3. DISPATCH: run `bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md`.
4. VERIFY: Claude edits in an isolated worktree and produces report/checker evidence.
5. REVIEW: run `bash ai/review-with-codex.sh ...` or `bash ai/run-loop.sh ...`.
6. LEARN: carry accept/revise/split/reject decisions into the next iteration.

## Hot-Path Rules

- Codex designs and reviews; Claude Code edits.
- Codex must make phase ownership explicit in the task card: OBSERVE/PLAN and direction review belong to Codex, Builder execution belongs to Claude, Checker/Test belongs to Claude only after Codex accepts direction, and final merge belongs to humans.
- After a Claude execution round, Codex normally accepts, revises, splits, or rejects; it does not patch implementation files directly.
- Split execution into Builder and Checker/Test Claude tasks when validation risk matters: Builder Claude implements and reports direction without acceptance testing; Codex reviews direction; Checker/Test Claude writes/runs tests and reports validation.
- Do not dispatch one mixed implementation + test-writing + broad-validation task unless it is explicitly marked `mixed-exception` with a rationale; otherwise split into Builder then Checker/Test.
- Builder Claude should not add tests or run broad acceptance suites unless the task card explicitly allows a narrow sanity check; Checker/Test Claude should not perform broad implementation rewrites unless tests expose a concrete allowed fix.
- Before editing, Claude should perform Direction / Boundary Acknowledgement when requested: restate understanding, scope, out-of-scope boundaries, likely files, acceptance interpretation, testing responsibility, confusion, risks, and proceed/narrow/split/stop recommendation.
- Use blocking Codex approval for ambiguous, multi-file, high-risk, public API, data model, security, migration, permission, or production-impacting tasks; if Claude has material confusion, it must stop-and-report instead of guessing.
- Prevent acknowledgement loops: at most one blocking acknowledgement per task or phase unless Codex materially changes goal, scope, boundaries, or risk. Codex must answer with proceed, narrow-once/re-dispatch, split, or stop; Claude must not ask for the same confirmation again after approval.
- Claude no-progress, early exit, invalid result, or one failed attempt is not enough for Codex takeover; tighten the task card and re-dispatch Claude.
- Codex may directly intervene only after repeated Claude failure or an external blocker, and must record the intervention reason, scope, and validation.
- Prior-session Claude failures are context, not automatic takeover permission; re-dispatch Claude unless the current task cites matching loop artifacts or the user explicitly asks Codex to take over.
- If a narrowed second Claude round also exits with no result/report and no useful progress, current-task repeated failure is enough for a control-plane takeover; Codex should salvage the best prior Claude direction, limit edits to the accepted scope, and add required tests/evidence.
- Use LSP/CodeGraph/MCP before broad reads.
- Delegate whole-file scans, long logs, and multi-file implementation to Claude.
- Codex owns the full planning task card; dispatch renders a smaller Claude execution card and omits Codex-only budget, planning, and control-plane sections from Claude's prompt.
- For multi-phase or multi-part tasks, accepting one Claude round only closes that phase; remaining implementation/test phases stay Claude-owned and must be dispatched as next task cards unless a takeover threshold or explicit human override applies.
- Task cards must say whether Claude writes tests, runs tests, or leaves verification to Codex/humans; test-code tasks can be delegated to Claude when the user asks for tests or Codex makes them acceptance-critical.
- Claude must update `CLAUDE_PROGRESS.md` and, when present, the progress/checklist in `CLAUDE_TASK_CARD.md` after completing each assigned item so Codex can distinguish active progress from stalls.
- Missing Claude result/report is an evidence gap, not automatically an implementation failure. If the diff matches the plan and assigned checks pass, Codex may reconstruct review evidence; re-dispatch Claude only for task-card-required tests or acceptance evidence that cannot be recovered.
- Preserve large outputs as artifact paths and short summaries.
- Do not merge automatically.
- Destructive or high-risk actions require explicit human approval.
- If Claude appears quiet, inspect `ai/watch-claude.sh` or `ai/status-claude.sh`; continue waiting when partial work matches the plan, and interrupt only when it is off-plan, risky, or no longer useful.
- When Claude appears stuck, diagnose orchestration causes before blaming execution: mixed-role task card, unclear testing responsibility, blocking acknowledgement loop, dirty source/stale HEAD, permission/tool approval blocker, long-running command, missing progress artifact, or external environment. Only call it Claude no-progress after progress, status, and worktree evidence are all quiet past the grace period.

## Builder / Checker Workflow

Use this split for non-trivial implementation work, user-facing behavior changes, or any task where tests are expected.

1. Codex plans and dispatches a **Builder Claude** task.
   - The task card mode is `builder`.
   - The task card says whether Direction / Boundary Acknowledgement is required and whether it blocks editing.
   - Builder Claude implements the scoped change and reports the implementation direction.
   - Builder Claude does not write acceptance tests and does not run broad test suites.
   - Builder Claude may run only narrow sanity checks explicitly listed in the task card, such as syntax checks or a focused command needed to continue implementation.

2. Codex performs a **direction review** while or after Builder Claude runs.
   - If Direction / Boundary Acknowledgement is blocking, Codex gives one final decision before Claude edits: proceed, narrow-once/re-dispatch, split, or stop.
   - If the partial diff matches the plan, Codex continues waiting for Builder Claude to finish.
   - If the diff is off-plan, risky, or expanding scope, Codex interrupts, narrows the task card, and re-dispatches Claude.
   - If Claude repeatedly runs off-plan, stalls, or exits without useful progress, Codex may enter control-plane takeover after recording the threshold and scope.

3. After the builder direction is accepted, Codex dispatches a **Checker/Test Claude** task when tests or validation are needed.
   - The task card mode is `checker-test`.
   - Checker/Test Claude writes or updates tests when assigned.
   - Checker/Test Claude runs the assigned validation commands and produces a report.
   - Checker/Test Claude avoids broad implementation rewrites. It may make only concrete small fixes that the task card explicitly allows when tests expose a clear defect.

4. Codex performs the final review.
   - Codex checks the report, diff, and validation artifacts.
   - Codex may run a second verification pass when risk warrants it.
   - Codex accepts, revises, splits, rejects, or escalates to a scoped takeover according to the review policy.

Claude progress is part of the control surface: every completed assigned item should update `CLAUDE_PROGRESS.md` and the `Execution Progress` checklist in `CLAUDE_TASK_CARD.md` when present. Codex should use process activity, progress artifacts, and partial diff direction together before deciding to wait, interrupt, re-dispatch, or take over.

## Common Artifacts

Dispatch artifacts live under `.worktrees/`:

- `*.result.json`, `*.status.txt`, `*.diff`, `*.diffstat.txt`
- `*.result.raw.txt` when Claude exits without valid JSON output
- `*.checker-report.md`, `*.checker-logs/`
- `*.report.md`, `*.claude-progress.md`, `*.progress.log`, `*.pid`
- `*.usage.txt`, `*.worktree-status.txt`, `*.untracked.txt`

## When To Load More

Read only the relevant reference for the current need:

- `references/mcp-policy.md`: context retrieval order and LSP/CodeGraph/MCP use.
- `references/loop-model.md`: loop state machine, wait policy, stop conditions.
- `references/review-policy.md`: Codex review decisions and checker evidence.
- `references/benchmark-policy.md`: quality/speed/cost/stability summary.
- `references/operating-model.md`: role boundaries and handoff model.

For local usage details, see installed `ai/README.md` or repository `README.md`.
