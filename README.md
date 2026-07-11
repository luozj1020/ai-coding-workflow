# AI Coding Workflow Skill

A reusable Codex / Claude Code workflow skill for installing a local multi-agent coding workflow into software repositories.

English | [中文](README_CN.md)

## What it does

ai-coding-workflow bootstraps repositories with:
- `AGENTS.md` - shared rules for all agents
- `CLAUDE.md` - Claude Code execution rules
- Task-card and evidence-packet templates
- Safe dispatch/review/loop scripts for Codex + Claude Code workflows
- Default-on optional Codex Spark helper for `gpt-5.3-codex-spark` task-size classification, task-card audits, plan splitting, validation planning, failure triage, review/evidence checks, parallel DAG planning, tiny isolated micro-builder work, and narrow auditable controlled-builder work
- Execution profiles for token-saving balanced dispatch, safe full-context dispatch, and explicit fast large-repository dispatch
- Large-repository dispatch options for managed worktree reuse and reduced expensive untracked-file scans
- Local-validation gates and task-card validation command extraction
- Builder / Checker-Test task modes for separating implementation from validation
- Direction / boundary acknowledgement gates with anti-loop rules
- Managed blocks for idempotent updates

## Common actions

| Action | When | Command |
|--------|------|---------|
| **Install Skill** | Once per computer | `python scripts/install_for_codex.py` |
| **Update Skill** | After pulling a newer checkout | `python scripts/update_skill.py --bootstrap-current` |
| **Bootstrap project** | Once per repository | `python scripts/install_workflow.py .` |
| **Bootstrap local-only** | Repositories that should not commit workflow control-plane files | `python scripts/install_workflow.py . --local-only` |
| **Refresh project workflow** | Existing bootstrapped repository | `python scripts/install_workflow.py . --update-workflow-files` |

These actions are separate. Installing the Skill only makes Codex discover the workflow; it does not create or refresh the target repository's `ai/` directory. Already bootstrapped projects keep local copies of `ai/dispatch-to-claude.sh`, `ai/task-card-template.md`, and other workflow files. Use `update_skill.py --bootstrap-current` or `install_workflow.py . --update-workflow-files` to refresh those local copies after updating the Skill.

Use `--local-only` when a target repository should use `ai/`, `AGENTS.md`, `CLAUDE.md`, and `.worktrees/` locally but should not commit them. It writes those control-plane paths to `.git/info/exclude` and leaves `.gitignore` untouched; `doctor_workflow.py` accepts this as the local-only ignore mode.

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
    locate-code.py       -> Low-token code locator with bounded CodeGraph fallback
    review-with-codex.sh -> Send evidence to Codex/GPT for review
    run-codex-spark.sh   -> Optional gpt-5.3-codex-spark auxiliary runner
    run-parallel-loop.sh -> Experimental parallel dispatch helper
    run-loop.sh          -> Optional loop runner (dispatch + review)
    status-claude.sh     -> Inspect Claude dispatch status and artifacts
    watch-claude.sh      -> Show CLI progress panel for running dispatches
    kill-claude.sh       -> Stop a recorded Claude dispatch process
    cleanup-worktree.sh  -> Remove stopped worktrees while preserving evidence
    pwsh-utf8.ps1        -> Configure PowerShell UTF-8 sessions
    doctor_workflow.py   -> Read-only readiness check for dispatch/review loop
    code-search-service.py -> Optional Zoekt/Sourcegraph setup and diagnostics
    clean_runtime.py     -> Preview/remove ignored runtime artifacts
    install_context_tools.py -> Check/install context tools (LSP, linting)
    summarize-loop-run.py -> Summarize workflow quality, speed, cost, and stability
    benchmark-loop-runs.py -> Aggregate loop summaries into a lightweight benchmark
    init-spec.py         -> Create ai/specs/YYYY-MM-DD--slug.md
    plan-to-task-cards.py -> Generate task cards from reviewed plan sections
    init-plan.py         -> Create ai/plans/<task-id>/ planning files
    session-catchup.py   -> Generate resume-context.md from plan and artifacts
    validate-parallel-plan.py -> Validate parallel DAG plan JSON against schema v1
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
- Optional code-search service readiness for Zoekt and Sourcegraph.

It only prints suggestions. It does not install LSP tools and does not run `codegraph init` automatically. Use `python ~/.codex/skills/ai-coding-workflow/scripts/install_context_tools.py` to inspect LSP install suggestions, and run `codegraph init` inside a target repository when you want that repository indexed.

When run from an interactive terminal, the installer asks whether to configure optional code-search services. Non-interactive installs skip the prompt. To control it explicitly:

```bash
python scripts/install_for_codex.py --code-search-services ask
python scripts/install_for_codex.py --code-search-services skip
python scripts/install_for_codex.py --code-search-services check
```

In large repositories, prefer the bounded locator before spending CodeGraph time:

```bash
python ai/locate-code.py "symbol or behavior to change" --path src --max-files 12
```

`locate-code.py` uses `git ls-files` plus `rg`/`git grep` to produce candidate files, short snippets, and targeted read commands. CodeGraph is still useful for concrete symbols and call paths, but it is no longer the default broad locator in large repositories. If Zoekt is installed and indexed, `--backend auto` uses it before lexical fallback. Sourcegraph can be used when `SOURCEGRAPH_URL` is configured. In `auto` CodeGraph mode, the helper skips CodeGraph above a tracked-file threshold; use `--codegraph try --codegraph-timeout 12` only for a specific file/symbol query.

Optional indexed search setup:

```bash
python ai/code-search-service.py doctor
python ai/code-search-service.py install-zoekt --yes
python ai/code-search-service.py index-zoekt --repo . --yes
AI_CODE_LOCATOR_BACKEND=auto python ai/locate-code.py "symbol or behavior"
```

`install-zoekt --yes` runs three `go install` commands. The helper streams command output and prints periodic `still running...` heartbeats when Go is downloading or compiling quietly. Use `--progress-interval 5` before the subcommand to make heartbeats more frequent, or `--progress-interval 0` to disable them:

```bash
python ai/code-search-service.py --progress-interval 5 install-zoekt --yes
```

Sourcegraph is treated as an external/self-hosted service, not a default local dependency. Use `python ai/code-search-service.py sourcegraph-plan` for Docker Compose guidance, then set `SOURCEGRAPH_URL` and optionally `SOURCEGRAPH_TOKEN` when a service is available.

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
ai/code-search-service.py
ai/locate-code.py
ai/review-with-codex.sh
ai/run-codex-spark.sh
ai/run-parallel-loop.sh
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
ai/validate-parallel-plan.py
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

Small low-risk edits can use a Codex-only fast path instead of dispatching Claude. Use it only when the change is local, expected to touch no more than two small files, needs no broad context, has no public API/data/security/migration/permission/concurrency/cross-module contract risk, and has narrow validation or an explicit validation-skip reason. Record why Claude was not dispatched, files touched, validation evidence, and the condition that would have escalated to Claude. If scope expands or uncertainty appears, stop and return to task-card + Claude dispatch.

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

Dispatch defaults to the `balanced` execution profile: compact Claude task card, brief prompt, fresh worktree, and full diff evidence. This reduces prompt/task-card tokens while preserving the review evidence path. The full Codex planning card is still copied to `TASK_CARD_FULL.md`.

Use `safe` when a task is ambiguous, high-risk, or needs the full standard prompt and non-compact execution card:

```bash
CLAUDE_CODE_EXECUTION_PROFILE=safe \
bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md
```

Use `fast-large-repo` only after filling the large-repo gate and accepting the evidence tradeoff:

```bash
CLAUDE_CODE_EXECUTION_PROFILE=fast-large-repo \
bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md
```

`fast-large-repo` uses the managed reuse worktree, skips unrelated untracked scans, and writes summary diff evidence instead of full patch text. It never resets the source repository. If `.worktrees/reuse/claude-managed` already exists, preserve or review its evidence first, then explicitly add `CLAUDE_CODE_REUSE_WORKTREE_RESET=1` to reset only that managed worktree.

For large repositories, fill `Claude Context Packet` before dispatch. Keep it execution-facing and small: target files/modules, relevant symbols, source-of-truth examples, paths Claude must not read or modify, known constraints, and narrow validation commands. Use `python ai/locate-code.py "symbol or behavior" --path src --max-files 12` to build this packet cheaply. If this packet is incomplete, Claude should stop and report instead of rediscovering the whole repository.

**Default-on optional: use Codex Spark during execution planning**

If your Codex quota separates `gpt-5.3-codex-spark` from stronger models, leave `Codex Spark Gate` at `auto` for eligible tasks. Spark is auxiliary, not a default Claude replacement; use its cheaper quota for uncertain task-size routing before spending stronger Codex/Claude context. Prefer an explicit `--mode` when you already know the needed support role, and use `auto` when routing is the point. Budget mode is controlled by `AI_SPARK_BUDGET_MODE` / `--budget-mode`: `balanced` (default), `aggressive` (enables additional revision drafting on failure), `conservative` (legacy single-role routing). Recommend at most three short Spark helper invocations per task — a preflight call, an optional targeted or failure role call, and a postflight call — as a workflow recommendation, not cross-process daemon or state enforcement. If the CLI, model access, auth, network, or Spark quota is unavailable, the helper writes an auto-disabled report and exits 0 so the main Claude/Codex workflow can continue:

- `auto`: stage routing / bundle selection. It resolves to an applicable stage bundle: ordinary pre-Builder use resolves to `preflight-bundle`, diff/report/evidence use resolves to `postflight-bundle`, Checker/Test remains `validation-planner`, and failed/no-report evidence includes failure triage. In aggressive budget mode, failed evidence also adds revision drafting responsibility.
- `task-size-classifier`: classify tiny/small/medium/large/unknown and recommend `codex-fast-path`, `spark-review-only`, `spark-micro-builder`, `claude-builder`, `checker-test`, `spec-first`, or `human-clarification`. Includes execution-cost fields when available.
- `execution-cost-estimator`: read-only mode that predicts diff range/files and relative direct/delegated work units for a task. Work units are relative estimates, not token-accounting measurements. The estimator returns machine-readable fields: `predicted_diff_lines_low`, `predicted_diff_lines_high`, `predicted_files`, `context_scope`, `validation_complexity`, `delegation_overhead`, `estimated_direct_work_units`, `estimated_delegated_work_units`, `delegation_to_direct_ratio`, `economic_recommendation`, `safety_eligible`, `recommended_owner`, `confidence`, `risk_flags`, `reason`, and `stop_condition`. Codex fast path is allowed only when the economic recommendation favors it AND the deterministic safety gate passes: <=2 files, local context, low/none validation, high confidence, no risk flags, and upper diff within the configured threshold. The threshold is controlled by `--fast-path-max-diff-lines N` or `CODEX_FAST_PATH_MAX_DIFF_LINES` (default 60, valid 1..200). This is a pre-dispatch fast-path decision, not a post-Claude takeover; it never automatically edits source. The estimator is also included in `preflight-bundle` and `task-size-classifier` output.
- `review-only`: quick read-only critique of the task card or likely direction.
- `task-card-audit`: check missing gates, mixed responsibilities, unclear acceptance, and likely Claude stall risks before dispatch.
- `plan-splitter`: propose smaller Builder/Checker task cards or independent parallelizable slices.
- `validation-planner`: propose exact low-noise validation commands without running broad suites.
- `failure-triage`: inspect bounded artifacts after a stalled/failed run and recommend wait/re-dispatch/narrow/takeover.
- `evidence-checker`: quick evidence sanity check after artifacts exist.
- `parallel-planner`: propose a reviewed DAG scheduling plan for independent task cards. Spark produces strict schema-v1 JSON only — it does not execute or dispatch. Codex/human must review and save the plan before running `bash ai/run-parallel-loop.sh --plan <json>`.
- `micro-builder`: tiny scoped edits only, in the helper-created isolated worktree, and only when the task card authorizes Spark source edits, limits scope to one or two small files, rules out public API/contract risk, and names exact narrow validation.
- `controlled-builder`: narrow auditable source-write mode with explicit `--allow-write` paths (1–3), required `--max-diff-lines` (1–200), risk exclusions for public API/data/security/migration/permission/concurrency/cross-module, forced full artifacts and isolated worktree, and tracked/untracked path/line/binary evidence checked after run. Violations exit non-zero, remain isolated, never modify source, merge, or satisfy acceptance.
- `observe-synthesizer`: read-only mode for synthesizing observation evidence.
- `task-card-drafter`: read-only mode for drafting task card content.
- `context-packet-builder`: read-only mode for building context packets.
- `preflight-bundle`: read-only stage bundle for ordinary pre-Builder use.
- `direction-precheck`: read-only mode for pre-checking implementation direction.
- `acceptance-matrix`: read-only mode for building acceptance matrices.
- `postflight-bundle`: read-only stage bundle for diff/report/evidence use.
- `revision-drafter`: read-only mode for drafting revision instructions.
- `lesson-extractor`: read-only mode for extracting lessons from completed work.
- `execution-cost-estimator`: read-only mode that predicts diff range/files and relative direct/delegated work units. Work units are relative estimates, not token-accounting measurements. Included in `preflight-bundle` and `task-size-classifier` output. Codex fast path is allowed only when the economic recommendation favors it AND the deterministic safety gate passes. Threshold: `--fast-path-max-diff-lines N` / `CODEX_FAST_PATH_MAX_DIFF_LINES` (default 60, valid 1..200). Pre-dispatch decision only; never automatically edits source.

Bundle output uses seven compressed headings: Decision Summary, Risk Flags, Scope and Boundaries, Acceptance Matrix, Evidence Conflicts, Required Codex Decisions, Recommended Next Action.

Run the default auto-selected read-only helper:

```bash
bash ai/run-codex-spark.sh ai/task-cards/PROJ-123.md
```

When using explicit `task-size-classifier` mode or conservative auto routing (balanced/aggressive ordinary preflight is `preflight-bundle`), the helper runs Codex from the Spark artifact directory with `workspace-write` sandbox. This gives local helper initialization a writable working directory without granting write access to the source repository, and the mode contract still forbids source edits.

The `execution-cost-estimator` mode and its inclusion in `preflight-bundle`/`task-size-classifier` support a `--fast-path-max-diff-lines N` flag (also `CODEX_FAST_PATH_MAX_DIFF_LINES`) to configure the upper diff-line threshold for Codex fast-path eligibility. Default is 60, valid range is 1..200. When the predicted upper diff bound exceeds this threshold, the safety gate rejects Codex fast path regardless of the economic recommendation.

Run an evidence check:

```bash
bash ai/run-codex-spark.sh ai/task-cards/PROJ-123.md --mode evidence-checker \
  --artifact .worktrees/claude-<id>.report.md \
  --artifact .worktrees/claude-<id>.checker-report.md
```

Run a pre-dispatch task-card audit or validation plan:

```bash
bash ai/run-codex-spark.sh ai/task-cards/PROJ-123.md --mode task-card-audit
bash ai/run-codex-spark.sh ai/task-cards/PROJ-123.md --mode validation-planner
```

Run failure triage on bounded artifacts:

```bash
bash ai/run-codex-spark.sh ai/task-cards/PROJ-123.md --mode failure-triage \
  --artifact .worktrees/claude-<id>.status.txt \
  --artifact .worktrees/claude-<id>.progress.log
```

Propose a reviewed DAG parallel plan:

```bash
bash ai/run-codex-spark.sh ai/task-cards/PROJ-123.md --mode parallel-planner
```

`parallel-planner` produces strict schema-v1 JSON and standard reconciliation fields only. Spark does not execute or dispatch; Codex/human must review and save the JSON plan before running `bash ai/run-parallel-loop.sh --plan ai/plans/.../parallel-plan.json`.

Run a tiny isolated Spark edit only when the task card explicitly allows it:

```bash
bash ai/run-codex-spark.sh ai/task-cards/PROJ-123.md --mode micro-builder --sandbox workspace-write
```

Spark artifacts are written under `.worktrees/codex-spark-*`, including `codex-spark.report.md`, `codex-spark.prompt.md`, `codex-spark.result.txt`, `codex-spark.stderr.log`, `codex-spark.artifacts.txt`, `codex-spark.worktree-status.txt`, and optional `codex-spark.diff`. The helper does not silently fall back to GPT-5.5 or another stronger model. If local helper initialization fails, for example due to an app-server write requirement, the helper marks Spark auto-disabled and exits 0 unless `--require-spark` was used.

Spark output is advisory. Record `accepted_suggestions`, `ignored_suggestions`, `conflicts_with_claude`, `conflicts_with_local_evidence`, and `acceptance_satisfied_by_spark` in the Spark follow-up table. Spark cannot independently satisfy acceptance, replace Claude Builder ownership, or approve Codex final review. Spark never authorizes merge; strong Codex review remains required; no implicit strong-model fallback; no model-tier routing in this change. For summary/benchmark aggregation across multiple reports, record: helper invocation count, total Spark calls, unique modes/stages/roles, budget modes, provisional status, strong-review required, merge authorization status, and auto-disable occurrences/reasons.

**Spark result delivery modes** control how results are returned and persisted via `--result-mode`:

- **`direct`** (default for advisory/read-only runs): sends raw result on stdout, uses a cleaned temporary workspace, creates no permanent Spark directory. No `codex-spark.report.md` or other files are written. Choose `direct` when only the inline result matters and file-backed metrics are not needed.
- **`minimal`**: sends raw result on stdout and persists only a compact `codex-spark.report.md`. Use when persistent metrics or benchmark aggregation is required but full evidence is unnecessary.
- **`full`**: preserves prompt, result, stderr, status, diff, task-card, and manifest evidence. Use when complete audit trails are required.

When `--output` is passed without an explicit `--result-mode`, the helper selects `minimal`. Combining `--output` with `--result-mode direct` is invalid — `direct` creates no persistent artifacts. Source-writing modes (`controlled-builder`, `micro-builder`) force `full` artifacts.

**Observability tradeoff:** `direct` mode intentionally has no file-backed metrics — no `codex-spark.report.md`, no artifact directory, no manifest. This is by design for lightweight advisory calls. When benchmark aggregation, quality tracking, or audit evidence is needed across multiple Spark invocations, choose `minimal` or `full` so `ai/benchmark-loop-runs.py` and `ai/summarize-loop-run.py` can aggregate results.

**Controlled-builder permission mode** provides narrow, auditable source-write permission for Spark:

- The task card must specify 1–3 exact `--allow-write` paths with a matching `Controlled-builder allowed paths` row.
- `--max-diff-lines` is required, range 1–200.
- All public API, data model, security, migration, permission, concurrency, and cross-module contract risks are excluded by policy.
- An existing pattern or source-of-truth must be identified.
- Narrow validation is required — no broad test suites.
- After the run, tracked and untracked paths, line counts, and binary evidence are checked.
- Violations exit non-zero, remain isolated in the worktree, never modify the source, never merge, and never satisfy acceptance criteria.

```bash
bash ai/run-codex-spark.sh ai/task-cards/PROJ-123.md --mode controlled-builder \
  --allow-write src/module.py --allow-write tests/test_module.py \
  --max-diff-lines 150 --sandbox workspace-write
```

The task card for `controlled-builder` must include:

| Field | Value |
|-------|-------|
| Result mode | `full` (forced) |
| Controlled-builder authorized? | yes |
| Controlled-builder allowed paths | exact 1–3 paths |
| Max files | 3 |
| Max diff lines | <=200 |
| Risk exclusions | one row per: public API, data model, security, migration, permission, concurrency, cross-module |
| Existing pattern / source-of-truth | file or pattern reference |
| Narrow validation | exact command |

**Large repositories / slow filesystems**

For large repositories, fill `Worktree / Large Repo Strategy Gate` before dispatch. Defaults keep complete evidence. When `git worktree add`, dispatcher filesystem reads, or full patch generation are the bottleneck, prefer the explicit fast profile:

```bash
CLAUDE_CODE_EXECUTION_PROFILE=fast-large-repo \
bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md
```

For narrower manual control, opt in to managed reuse:

```bash
CLAUDE_CODE_WORKTREE_STRATEGY=reuse-managed \
CLAUDE_CODE_REUSE_WORKTREE_RESET=1 \
bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md
```

This reuses only `.worktrees/reuse/claude-managed` and resets/cleans only that managed worktree, never the source repository.
Bootstrap also keeps workflow runtime artifacts ignored with:

```gitignore
/.worktrees/*
!/.worktrees/.gitkeep
```

When untracked scans or untracked patch generation are too expensive, use:

```bash
CLAUDE_CODE_LARGE_REPO_MODE=1 \
bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md
```

Large-repo mode keeps tracked/staged diff evidence but skips expensive unrelated untracked scans and untracked patch evidence. Record that evidence tradeoff in the task card before relying on it.

To skip full patch text but keep the worktree for review:

```bash
CLAUDE_CODE_EVIDENCE_MODE=summary \
bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md
```

**Experimental: parallel dispatch**

Two compatible paths exist:

*Path 1: Flat independent cards (positional arguments)*

For independent task cards with non-overlapping file/module scopes, fill `Parallel Execution Gate` in each task card and run:

```bash
bash ai/run-parallel-loop.sh --max-concurrency 2 \
  ai/task-cards/PROJ-123-a.md \
  ai/task-cards/PROJ-123-b.md
```

The helper runs multiple `dispatch-to-claude.sh` jobs concurrently and writes `.worktrees/parallel-*/parallel-summary.md`, `parallel-events.jsonl`, `parallel-manifest.tsv`, and per-task dispatch logs. It refuses task cards that do not say `Parallel allowed? | yes` unless `--allow-ungated` is passed, and it refuses overlapping `Allowed files/modules` unless `--allow-overlap` is passed.

*Path 2: Reviewed DAG plan (`--plan`)*

For dependency-ordered parallel execution, use Spark `parallel-planner` to propose a reviewed DAG plan:

```bash
bash ai/run-codex-spark.sh ai/task-cards/PROJ-123.md --mode parallel-planner
```

Spark produces strict schema-v1 JSON — it only proposes and never executes. Codex/human must review and save the plan before dispatch. Then run:

```bash
bash ai/run-parallel-loop.sh --plan ai/plans/PROJ-123/parallel-plan.json
```

Schema fields: `schema_version` (must be `1`), `group_id`, `max_concurrency`, `failure_policy` (currently `skip-dependents`), and `tasks` containing `id`, `task_card`, `depends_on` per task. Task-card paths resolve relative to the plan file. An explicit CLI `--max-concurrency` overrides the plan's cap.

Scheduling semantics: the scheduler starts only dependency-ready tasks up to the concurrency cap. With `skip-dependents`, a failed prerequisite prevents all transitive dependents from dispatching while unrelated branches continue. All cards still require scope-gate and overlap checks.

This is dispatch parallelism only. It does not merge worktrees, does not replace Codex review, and does not make conflicting implementation safe. Review each diff serially; shared API/data model/config changes should use a normal single-task flow or a manual reconcile task.

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

**Checker-only validation:** Installed projects include `ai/check-worktree.sh`. Prefer exact task-card checks:

```bash
bash ai/check-worktree.sh --task-card ai/task-cards/PROJ-123.md --no-discover --command 'tests=pytest tests/test_target.py'
```

The dispatcher records a checker report after Claude finishes, but broad discovery is disabled by default to avoid unrelated pytest/ruff/mypy noise. Pass `CLAUDE_CODE_CHECKER_COMMANDS=$'tests=pytest tests/test_target.py'` for exact dispatcher-run checks, or `CLAUDE_CODE_CHECKER_DISCOVER=1` when the task card explicitly allows broad project discovery.

**Checker Reuse Risk Gate:** Before dispatching a `checker-test` task, fill the Checker Reuse Risk Gate in the task card with exact rows: Public API risk, Data model risk, Security risk, Migration risk, Permission risk, Concurrency risk, Cross-module risk, Production impact. Each row must be explicit `no` for task-derived checker worktree reuse to default to `reuse-managed`. Missing, `unknown`, `n/a`, `duplicate`, `high` risk, DAG, or parallel tasks stay `fresh`. The environment variable `CLAUDE_CODE_WORKTREE_STRATEGY=fresh|reuse-managed` overrides this default. Existing reset safety via `CLAUDE_CODE_REUSE_WORKTREE_RESET=1` remains unchanged.

**Authoritative validation timeline:** The dispatcher preserves the Claude blocked state. Checker ALL GREEN is the authoritative signal that makes the final status `passed`. Checker failures set the final status accordingly.

The checker also reads task-card validation fences when `--task-card` is passed:

```bash validation
bazel test //path/to:target
```

If the task card says `Local validation allowed? | no`, checker reports artifact collection as `OK` and validation as `SKIPPED by policy`; it does not run commands and does not mean tests passed. Use that when the user or repository policy forbids local test execution; the report should list commands for the human or CI to run.

**Project test tiers:** The workflow test suite has fast checks and slower integration coverage. Use the smallest tier that matches the edit:

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

Tests marked `slow` create repeated temporary repositories, worktrees, or installer runs. They should run before release or when touching dispatcher/worktree/install behavior, not after every small documentation or helper edit.

**Workflow quality summary:** `ai/run-loop.sh` also writes `.worktrees/loop-<timestamp>/loop-quality-summary.md` and `.json`. To summarize an existing run manually:

```bash
python ai/summarize-loop-run.py .worktrees/loop-<timestamp> \
  --output .worktrees/loop-<timestamp>/loop-quality-summary.md \
  --json-output .worktrees/loop-<timestamp>/loop-quality-summary.json
```

The summary includes fixed `Spark Status` and `Claude Evidence Classification` sections. Spark fields record enabled/invoked state, mode, model, artifact path, exit code, auto-disable reason, sandbox, and strong-model fallback status. Claude evidence is classified as `diff + valid report`, `no report but diff accepted`, `diff without report`, `acknowledgement only`, `seeded report only`, `fallback report`, `valid report without diff`, or `no useful progress`.

**Workflow benchmark summary:** To compare multiple loop runs as a lightweight living benchmark:

```bash
python ai/benchmark-loop-runs.py .worktrees/loop-* \
  --output .worktrees/workflow-benchmark.md \
  --json-output .worktrees/workflow-benchmark.json
```

The benchmark aggregates decision, quality score, elapsed time, dispatch stage timings, token/cost totals, stability findings, loop type, benchmark tags, advisor usage, Spark invocation/auto-disable/fallback status, Spark task-size classification/routing/confidence, and parallel-dispatch usage parsed from task cards and reports. Stage timings include Claude startup, Claude execution, checker time, and artifact finalization when dispatch progress logs contain those events.

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
- Machine-readable status fields after finalization: `overall_running=yes`, `running=no`, `claude=not-running`. Only the dispatcher sets these fields; Claude does not finalize its own status.
- `CLAUDE_CODE_HEARTBEAT_SECONDS` controls heartbeat frequency; default is `30`.
- `CLAUDE_CODE_TIMEOUT_SECONDS` controls the maximum Claude runtime; default is `600` seconds. Set it to `0` to disable timeout.
- `CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS` optionally stops Claude when result/status/report/progress artifacts do not change. Default is `0` disabled; set a positive value only when you want fast-fail behavior.
- `CLAUDE_CODE_WORKTREE_PROGRESS` controls worktree progress verbosity. Default `quiet` shows compact timing and path; `verbose` shows detailed worktree state.
- `CLAUDE_CODE_APPROVAL_BLOCKED_CONVERGENCE` enables conservative approval-blocked early convergence. Default `1` (enabled); set to `0` to disable. When enabled, if a valid complete report exists, changes are test-only scoped, an exact validation approval blocker is present, and two stable heartbeats have been observed, the dispatcher triggers the checker helper. This is not validation success or acceptance — it is an early evidence-gathering path.

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

## Preserved Constraints

The following constraints are preserved across all workflow changes:

- **No model tiers:** there is no automatic model-tier routing or escalation between Spark, Claude, and stronger models.
- **No implicit fallback:** Spark does not silently fall back to GPT-5.5 or another stronger model. If Spark is unavailable, report the gap and let Codex or the human decide.
- **No automatic merge:** human review and merge remain separate. The workflow never merges automatically.

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

# Large repos: preview only one stopped dispatch and its adjacent artifacts
python ai/clean_runtime.py --task-id claude-20260709-120000

# Large repos: remove only that stopped dispatch's runtime artifacts
python ai/clean_runtime.py --task-id claude-20260709-120000 --apply
```

`doctor_workflow.py` runs in preview-only mode: it shows count, size, and age of runtime artifacts. It does not automatically delete anything.

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
