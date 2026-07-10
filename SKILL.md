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

Bootstrap local-only control-plane files for repositories that should not commit workflow artifacts:

```bash
python ~/.codex/skills/ai-coding-workflow/scripts/install_workflow.py . --local-only
```

From a cloned copy, install the skill and bootstrap a repository:

```bash
python scripts/install_for_codex.py --bootstrap-repo /path/to/repo
```

After skill installation, `install_for_codex.py` performs a read-only context intelligence check for common LSP tools, CodeGraph CLI availability, `.codegraph/` initialization for bootstrapped repositories, and optional Zoekt/Sourcegraph code-search service readiness. In an interactive terminal it asks whether to configure optional code-search services; non-interactive installs skip the prompt. Use `--code-search-services skip` or `--code-search-services check` for deterministic automation. It does not install LSP tools or run `codegraph init` automatically.

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
- Use LSP/CodeGraph/MCP before broad reads when they are cheap enough for the repository size.
- In large repositories, use `python ai/locate-code.py "symbol or behavior" --path <area> --max-files 12` as the default low-token locator before dispatch. Backend order is Zoekt when indexed, Sourcegraph when `SOURCEGRAPH_URL` is configured, lexical `rg`/`git grep`, then bounded CodeGraph only for concrete symbols. Ask CodeGraph only for concrete files, symbols, or call paths with a short timeout; if it times out once, record the timeout and continue with locator output plus targeted line reads instead of retrying broad graph queries.
- For local repository work, do not call web search unless the user explicitly asks for internet lookup, remote repository state, external documentation, or current third-party facts. Spark, Claude, CodeGraph, or filesystem failures should be diagnosed from local artifacts instead of triggering web search.
- Delegate whole-file scans, long logs, and multi-file implementation to Claude.
- Codex owns the full planning task card; dispatch defaults to the `balanced` profile, rendering a compact Claude execution card and brief prompt while preserving `TASK_CARD_FULL.md` for audit. Use `CLAUDE_CODE_EXECUTION_PROFILE=safe` for ambiguous or high-risk work that needs the standard prompt and non-compact execution card.
- For bounded loop work, Codex should fill `Goal Loop Contract`: loop type, success signal, max attempts, repeated-failure/no-improvement/regression stop rules, required evidence, budget, and benchmark tags.
- For ambiguous feature, UX, API, or data-model work, Codex should fill `Spec Gate` and link a reviewed spec artifact. Use `ai/init-spec.py` for lightweight specs and `ai/plan-to-task-cards.py` to derive small task cards from reviewed `### Task N: ...` plan sections.
- For bugfixes, regressions, failing tests, and repeated failed attempts, Codex should fill `Root Cause Gate` before assigning a fix: reproduce or cite the symptom, identify likely cause, check similar patterns, and stop after repeated failed fixes instead of guess-and-patch.
- For tiny low-risk edits, Codex should fill or mentally evaluate `Small Change Fast Path Gate` before dispatch. Codex may edit directly only when the change is local, expected to touch no more than two small files, needs no broad context, has no public API/data/security/migration/permission/concurrency/cross-module contract risk, and has narrow validation or an explicit validation-skip reason. When task size is unclear, prefer Spark `task-size-classifier` before spending stronger-model Codex/Claude context. Record why Claude was not dispatched, files touched, validation evidence, and the escalation condition. If scope expands or uncertainty appears, stop fast path and return to task-card + Claude dispatch.
- For test-critical work, Codex should fill `Test-First / TDD Contract`: red evidence before production edits, green evidence after implementation, and explicit test/production owner split.
- Before claiming work ready for human merge, Codex should fill `Finish Branch Gate`: accepted phase links, fresh verification, dirty/untracked artifact classification, out-of-scope check, remaining risks, and review/merge instructions.
- For strategic or risky work, Codex should fill `Advisor Gate`: advisor role/model, consult timing, read-only orientation requirement, state-changing edit checkpoint, call cap, output budget, result visibility, conflict reconciliation, fallback behavior, and evidence artifact.
- For execution-stage quota savings, leave `Codex Spark Gate` at `auto` by default and run `ai/run-codex-spark.sh` with `gpt-5.3-codex-spark` for eligible auxiliary work. Spark quota is treated as cheaper than strong Codex/Claude context, so use it for uncertain task-size routing before spending stronger-model tokens. Prefer an explicit `--mode` when Codex already knows the support role; default `--mode auto` is for low-risk routing and resolves to an applicable stage bundle: ordinary pre-Builder use resolves to `preflight-bundle`, diff/report/evidence use resolves to `postflight-bundle`, Checker/Test remains `validation-planner`, and failed/no-report evidence includes failure triage. In aggressive budget mode, failed evidence also adds revision drafting responsibility. Budget mode is controlled by `AI_SPARK_BUDGET_MODE` / `--budget-mode`: `balanced` (default), `aggressive` (enables additional revision drafting on failure), or `conservative` (legacy single-role routing). Recommend at most three short Spark helper invocations per task — a preflight call, an optional targeted or failure role call, and a postflight call — as a workflow recommendation, not cross-process daemon or state enforcement. New explicit read-only modes: `observe-synthesizer`, `task-card-drafter`, `context-packet-builder`, `preflight-bundle`, `direction-precheck`, `acceptance-matrix`, `postflight-bundle`, `revision-drafter`, `lesson-extractor`. Bundle output uses seven compressed headings: Decision Summary, Risk Flags, Scope and Boundaries, Acceptance Matrix, Evidence Conflicts, Required Codex Decisions, Recommended Next Action. Use `parallel-planner` to propose a reviewed DAG scheduling plan for independent task cards without executing or dispatching; Spark produces strict schema-v1 JSON and standard reconciliation fields only — Codex/human must review and save the plan before running `ai/run-parallel-loop.sh --plan <json>`. When using explicit `task-size-classifier` mode or conservative auto routing (balanced/aggressive ordinary preflight is `preflight-bundle`), the helper runs Codex from the Spark artifact directory with `workspace-write` sandbox so local helper initialization has a writable working directory without granting write access to the source repository. Prefer these read-only support modes over `micro-builder`; pass explicit `--artifact` files for evidence/failure work so Spark sees bounded excerpts instead of broad logs. Use `micro-builder` only when the task card explicitly authorizes Spark source edits, limits scope to one or two small files, rules out public API/data/security/migration/permission/concurrency/cross-module contract risk, names exact narrow validation, and runs in an isolated worktree. If Spark is unavailable, auth/network-blocked, quota-exhausted, or fails during local helper initialization, auto-disable it for that run and continue the main workflow unless `--require-spark` was explicitly used. Do not silently fall back to GPT-5.5 or another stronger model. Spark never authorizes merge; strong Codex review remains required; Spark does not independently satisfy acceptance; no implicit strong-model fallback; no model-tier routing in this change. Spark output is advisory: record task-size classification, routing recommendation, `accepted_suggestions`, `ignored_suggestions`, `conflicts_with_claude`, `conflicts_with_local_evidence`, and `acceptance_satisfied_by_spark`, but Spark must not replace Claude Builder ownership, Codex final review, or independent acceptance verification. For summary/benchmark aggregation across multiple reports, record: helper invocation count, total Spark calls, unique modes/stages/roles, budget modes, provisional status, strong-review required, merge authorization status, and auto-disable occurrences/reasons.
- For large repositories or slow filesystems, fill `Worktree / Large Repo Strategy Gate` before dispatch. Also fill `Claude Context Packet` with locator output, target files/modules, relevant symbols, source-of-truth examples, forbidden paths, known constraints, and narrow validation commands so Claude does not rediscover the repository. Default to complete evidence; use `CLAUDE_CODE_EXECUTION_PROFILE=fast-large-repo` only when the task card accepts managed worktree reuse, skipped unrelated untracked scans, and summary diff evidence. It must not reset an existing `.worktrees/reuse/claude-managed` unless `CLAUDE_CODE_REUSE_WORKTREE_RESET=1` is explicit after preserving or reviewing prior evidence. Manual knobs remain available: `CLAUDE_CODE_WORKTREE_STRATEGY=reuse-managed` only for `.worktrees/reuse/claude-managed`, `CLAUDE_CODE_LARGE_REPO_MODE=1` only when reduced untracked-file evidence is acceptable, and `CLAUDE_CODE_EVIDENCE_MODE=summary` only when patch evidence can be recovered from the preserved worktree. Ensure `.worktrees/*` is ignored while `.worktrees/.gitkeep` remains trackable, or use `install_workflow.py --local-only` so `ai/`, `AGENTS.md`, `CLAUDE.md`, and `.worktrees/` are ignored through `.git/info/exclude` without changing `.gitignore`. After an interrupted dispatch, prefer `python ai/clean_runtime.py --task-id claude-...` to inspect or remove only that run's stopped artifacts.
- For experimental wall-clock reduction, Codex may fill `Parallel Execution Gate` and run `ai/run-parallel-loop.sh`. Two compatible paths exist: (1) flat independent cards with positional arguments, and (2) a reviewed DAG plan with `bash ai/run-parallel-loop.sh --plan ai/plans/.../parallel-plan.json`. The plan must be strict schema-v1 JSON produced by Spark `parallel-planner` mode or hand-written, then reviewed and saved by Codex/human before dispatch. Schema fields: `schema_version` (must be `1`), `group_id`, `max_concurrency`, `failure_policy` (currently `skip-dependents`), and `tasks` containing `id`, `task_card`, and `depends_on` per task. The scheduler starts only dependency-ready tasks up to the concurrency cap; a failed prerequisite skips all transitive dependents while unrelated branches continue. All cards still require scope-gate and overlap checks; review and merge remain serial.
- Use the task card `Unknowns` section to reduce the information gap before execution: known unknowns, assumed knowns, blindspot scan request, architecture-changing questions, reference examples, and where deviations must be recorded.
- For multi-phase or multi-part tasks, accepting one Claude round only closes that phase; remaining implementation/test phases stay Claude-owned and must be dispatched as next task cards unless a takeover threshold or explicit human override applies.
- Task cards must say whether Claude writes tests, runs tests, or leaves verification to Codex/humans; test-code tasks can be delegated to Claude when the user asks for tests or Codex makes them acceptance-critical.
- Validation should follow the task card first. Prefer exact checker commands with `ai/check-worktree.sh --task-card CLAUDE_TASK_CARD.md --no-discover --command 'label=command'`; broad checker discovery is optional and should be enabled only when requested. If `Local validation allowed?` is `no`, do not run local checks; provide commands only, and classify checker success as artifact collection OK plus validation skipped by policy, not as tests passing. If Claude cannot run Python/Node/test commands due to approval or sandbox policy, record the blocked command and let Codex/human rerun it instead of treating the implementation as failed.
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
- Use `ai/benchmark-loop-runs.py` to aggregate multiple loop runs into a lightweight living benchmark with quality, speed, dispatch stage timings, cost, stability, loop type, and benchmark tags.
- Benchmark and evidence reports should include advisor, Spark, and parallel-dispatch usage when available: calls, model/person or model slug, visibility, advice followed, conflicts reconciled, stop reason/truncation, Spark enabled/invoked state, Spark mode, Spark task-size classification/routing/confidence, Spark exit code, auto-disable reason, strong-model fallback status, parallel group/concurrency/failure count, and token/cost fields when known. They should also preserve Claude evidence classification, spec adherence, root-cause evidence, TDD mode, and red/green evidence when present.
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
- `codex-spark.report.md`, `codex-spark.prompt.md`, `codex-spark.result.txt`, `codex-spark.stderr.log`, `codex-spark.artifacts.txt`, and optional `codex-spark.diff` from `ai/run-codex-spark.sh`
- `parallel-summary.md`, `parallel-events.jsonl`, and per-task dispatch logs from `ai/run-parallel-loop.sh`

## When To Load More

Read only the relevant reference for the current need:

- `references/mcp-policy.md`: context retrieval order and LSP/CodeGraph/MCP use.
- `references/loop-model.md`: loop state machine, wait policy, stop conditions.
- `references/review-policy.md`: Codex review decisions and checker evidence.
- `references/benchmark-policy.md`: quality/speed/cost/stability summary.
- `references/operating-model.md`: role boundaries and handoff model.

For local usage details, see installed `ai/README.md` or repository `README.md`.
