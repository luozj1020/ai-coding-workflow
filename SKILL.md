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
2. PLAN: create or revise a task card from `ai/task-card-template.md`; for ambiguous work, first create a short spec with `ai/init-spec.py` and fill `Spec Gate`.
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
- If acknowledgement is non-blocking and Claude recommends `proceed`, Claude must continue implementation in the same run. It must not stop after acknowledgement unless it records a concrete blocker, stop condition, or explicit approval need.
- Prevent acknowledgement loops: at most one blocking acknowledgement per task or phase unless Codex materially changes goal, scope, boundaries, or risk. Codex must answer with proceed, narrow-once/re-dispatch, split, or stop; Claude must not ask for the same confirmation again after approval.
- Treat `acknowledgement only` as no implementation progress: no code diff, no valid Claude-owned report, and only acknowledgement/proceed text.
- Claude no-progress, early exit, invalid result, or one failed attempt is not enough for Codex takeover; tighten the task card and re-dispatch Claude.
- Dirty source or stale HEAD is a delegation blocker, not a takeover trigger. Restore a reliable Claude base first: commit the accepted phase, stash/patch source changes, refresh local workflow files, re-dispatch from updated HEAD, request explicit dirty-source override, or stop for human input.
- Codex may directly intervene only after repeated Claude failure or an external blocker, and must record the intervention reason, scope, and validation.
- Prior-session Claude failures are context, not automatic takeover permission; re-dispatch Claude unless the current task cites matching loop artifacts or the user explicitly asks Codex to take over.
- If a narrowed second Claude round also exits with no result/report and no useful progress, current-task repeated failure is enough for a control-plane takeover; Codex should salvage the best prior Claude direction, limit edits to the accepted scope, and add required tests/evidence.
- If one Builder attempt exits after acknowledgement with no code diff and no valid report, tighten and re-dispatch once. If the tightened Builder attempt again exits after acknowledgement with no code diff and no valid report, Codex may perform scoped takeover after recording both attempt artifacts.
- If the first attempt produced a useful scoped diff but no valid report/evidence, Codex may accept that direction only after running the assigned narrow checks. If a tightened retry produces no useful progress, Codex may salvage the accepted direction in scoped takeover.
- Use LSP/CodeGraph/MCP before broad reads.
- For local repository work, do not call web search unless the user explicitly asks for internet lookup, remote repository state, external documentation, or current third-party facts. Spark, Claude, CodeGraph, or filesystem failures should be diagnosed from local artifacts instead of triggering web search.
- Delegate whole-file scans, long logs, and multi-file implementation to Claude.
- Codex owns the full planning task card; dispatch renders a smaller Claude execution card and omits Codex-only budget, planning, and control-plane sections from Claude's prompt.
- For bounded loop work, Codex should fill `Goal Loop Contract`: loop type, success signal, max attempts, repeated-failure/no-improvement/regression stop rules, required evidence, budget, and benchmark tags.
- For ambiguous feature, UX, API, or data-model work, Codex should fill `Spec Gate` and link a reviewed spec artifact. Use `ai/init-spec.py` for lightweight specs and `ai/plan-to-task-cards.py` to derive small task cards from reviewed `### Task N: ...` plan sections.
- For bugfixes, regressions, failing tests, and repeated failed attempts, Codex should fill `Root Cause Gate` before assigning a fix: reproduce or cite the symptom, identify likely cause, check similar patterns, and stop after repeated failed fixes instead of guess-and-patch.
- For test-critical work, Codex should fill `Test-First / TDD Contract`: red evidence before production edits, green evidence after implementation, and explicit test/production owner split.
- Before claiming work ready for human merge, Codex should fill `Finish Branch Gate`: accepted phase links, fresh verification, dirty/untracked artifact classification, out-of-scope check, remaining risks, and review/merge instructions.
- For strategic or risky work, Codex should fill `Advisor Gate`: advisor role/model, consult timing, read-only orientation requirement, state-changing edit checkpoint, call cap, output budget, result visibility, conflict reconciliation, fallback behavior, and evidence artifact.
- For execution-stage quota savings, leave `Codex Spark Gate` at `auto` by default and run `ai/run-codex-spark.sh` with `gpt-5.3-codex-spark` for eligible auxiliary work. Default to review-only/read-only or evidence-checker; use micro-builder only for tiny scoped edits in an isolated worktree. If Spark is unavailable, auth/network-blocked, quota-exhausted, or fails during read-only sandbox helper initialization, auto-disable it for that run and continue the main workflow unless `--require-spark` was explicitly used. Do not silently fall back to GPT-5.5 or another stronger model.
- For large repositories or slow filesystems, fill `Worktree / Large Repo Strategy Gate` before dispatch. Default to fresh isolated worktrees and full evidence; use `CLAUDE_CODE_WORKTREE_STRATEGY=reuse-managed` only for the managed `.worktrees/reuse/claude-managed` worktree, and use `CLAUDE_CODE_LARGE_REPO_MODE=1` only when the reduced untracked-file evidence tradeoff is acceptable. Ensure `.worktrees/*` is ignored while `.worktrees/.gitkeep` remains trackable.
- For experimental wall-clock reduction, Codex may fill `Parallel Execution Gate` and run `ai/run-parallel-loop.sh` only for independent task cards with explicit non-overlapping file/module scopes. It parallelizes dispatch only; review and merge remain serial.
- Use the task card `Unknowns` section to reduce the information gap before execution: known unknowns, assumed knowns, blindspot scan request, architecture-changing questions, reference examples, and where deviations must be recorded.
- For multi-phase or multi-part tasks, accepting one Claude round only closes that phase; remaining implementation/test phases stay Claude-owned and must be dispatched as next task cards unless a takeover threshold or explicit human override applies.
- Task cards must say whether Claude writes tests, runs tests, or leaves verification to Codex/humans; test-code tasks can be delegated to Claude when the user asks for tests or Codex makes them acceptance-critical.
- Validation should follow the task card first. Prefer exact checker commands with `ai/check-worktree.sh --task-card CLAUDE_TASK_CARD.md --no-discover --command 'label=command'`; broad checker discovery is optional and should be enabled only when requested. If `Local validation allowed?` is `no`, do not run local checks; provide commands only. If Claude cannot run Python/Node/test commands due to approval or sandbox policy, record the blocked command and let Codex/human rerun it instead of treating the implementation as failed.
- Claude must update `CLAUDE_PROGRESS.md` and, when present, the progress/checklist in `CLAUDE_TASK_CARD.md` after completing each assigned item so Codex can distinguish active progress from stalls.
- Missing Claude result/report is an evidence gap, not automatically an implementation failure. If the diff matches the plan and assigned checks pass, Codex may reconstruct review evidence; re-dispatch Claude only for task-card-required tests or acceptance evidence that cannot be recovered.
- A `CLAUDE_REPORT.md` containing `AI-CODING-WORKFLOW:DISPATCH-SEEDED-REPORT` or `AI-CODING-WORKFLOW:DISPATCH-FALLBACK-REPORT` is not a valid Claude-owned report. Watch/status tools should classify it as `seeded report only` or `no valid report`, not completion.
- A valid Claude report must include touched files, acceptance criteria mapping, checks run or blocked, out-of-scope confirmation, and remaining risks. Missing required report fields are an evidence gap for review.
- For repeated dispatches, commit or otherwise restore prior task-card artifacts before re-dispatch. Only the current task card may be exempt from dirty-source checks; previous untracked task cards are delegation blockers unless explicitly treated as approved control-plane artifacts.
- Preserve large outputs as artifact paths and short summaries.
- Do not merge automatically.
- Destructive or high-risk actions require explicit human approval.
- If Claude appears quiet, follow the monitoring escalation ladder: L0 compact `ai/watch-claude.sh` heartbeat/progress first; L1 partial diff review when worktree changes exist; L2 `ai/status-claude.sh` or watch details only after repeated suspect snapshots; L3 network/status/diff corroboration when quiet time exceeds the interrupt window; L4 `ai/kill-claude.sh` only after multiple evidence sources agree useful progress is unlikely.
- Prefer machine-readable monitor fields from `watch-claude.sh`/`status-claude.sh` (`monitor_level`, `action`, `evidence_state`, quiet/elapsed seconds, suspect count) before reading full progress, status, or network tails.
- Optional network diagnostics are available with `CLAUDE_CODE_NETWORK_MONITOR=1`. They record metadata-only process socket snapshots in `*.network.log`; optional `CLAUDE_CODE_NETWORK_HEALTHCHECK_URL` records healthcheck status. Do not treat network metadata as request-content evidence or implementation evidence.
- Use `ai/benchmark-loop-runs.py` to aggregate multiple loop runs into a lightweight living benchmark with quality, speed, cost, stability, loop type, and benchmark tags.
- Benchmark and evidence reports should include advisor, Spark, and parallel-dispatch usage when available: calls, model/person or model slug, visibility, advice followed, conflicts reconciled, stop reason/truncation, Spark mode, Spark exit code, strong-model fallback status, parallel group/concurrency/failure count, and token/cost fields when known. They should also preserve spec adherence, root-cause evidence, TDD mode, and red/green evidence when present.
- When Claude appears stuck, diagnose orchestration causes before blaming execution: mixed-role task card, unclear testing responsibility, blocking acknowledgement loop, dirty source/stale HEAD, permission/tool approval blocker, long-running command, missing progress artifact, or external environment. Only call it Claude no-progress after progress, status, and worktree evidence are all quiet past the grace period.
- If dirty source/stale HEAD blocks dispatch, Codex must record the Delegation Restoration Gate and explain why restoration was attempted or impossible before any direct intervention.

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
- `*.network.log` when `CLAUDE_CODE_NETWORK_MONITOR=1`
- `*.usage.txt`, `*.worktree-status.txt`, `*.untracked.txt`
- `codex-spark.report.md`, `codex-spark.result.txt`, `codex-spark.stderr.log`, and optional `codex-spark.diff` from `ai/run-codex-spark.sh`
- `parallel-summary.md`, `parallel-events.jsonl`, and per-task dispatch logs from `ai/run-parallel-loop.sh`

## When To Load More

Read only the relevant reference for the current need:

- `references/mcp-policy.md`: context retrieval order and LSP/CodeGraph/MCP use.
- `references/loop-model.md`: loop state machine, wait policy, stop conditions.
- `references/review-policy.md`: Codex review decisions and checker evidence.
- `references/benchmark-policy.md`: quality/speed/cost/stability summary.
- `references/operating-model.md`: role boundaries and handoff model.

For local usage details, see installed `ai/README.md` or repository `README.md`.
