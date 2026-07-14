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
- **LSP / Locator / CodeGraph / MCP**  -  low-token code intelligence with bounded large-repo lookup before broad reads

**Core principle:** Codex designs and reviews. Claude edits. Tools gather low-token evidence first.

For non-trivial changes, split Claude work into two roles:

- **Builder Claude** implements the scoped change and reports the implementation direction. It does not write acceptance tests or run broad suites unless the task card explicitly allows a narrow sanity check.
- **Checker/Test Claude** runs after Codex accepts the builder direction. It writes or updates assigned tests, runs validation commands, and reports evidence without broad implementation rewrites.

Task cards can require **Direction / Boundary Acknowledgement** before editing. Claude restates the goal, scope, out-of-scope boundaries, likely files, acceptance criteria, testing responsibility, confusions, and risks. This is a gate, not a discussion loop: at most one blocking acknowledgement is allowed per task or phase unless Codex materially changes the goal, scope, boundaries, or risk. Codex answers with exactly one decision: proceed, narrow-once/re-dispatch, split, or stop.

Use `ai/init-spec.py` for ambiguous feature, UX, API, or data-model work, then fill `Spec Gate` in the task card. `ai/init-plan.py` creates `task_plan.md` with `### Task N: ...` sections; use `ai/plan-to-task-cards.py` to turn reviewed task sections into scoped task cards. Use `Root Cause Gate` before bugfixes/regressions, `Test-First / TDD Contract` when red-green evidence matters, and `Finish Branch Gate` before claiming work is ready for human merge.

Leave `Codex Spark Gate` at `auto` when a task can benefit from the separate Spark quota pool without spending stronger-model quota. Prefer Spark for uncertain task-size routing before spending stronger Codex/Claude context, but pass an explicit `--mode` when the support role is already known. `--mode auto` resolves to an applicable stage bundle: ordinary pre-Builder use resolves to `preflight-bundle`, diff/report/evidence use resolves to `postflight-bundle`, Checker/Test remains `validation-planner`, and failed/no-report evidence includes failure triage. In aggressive budget mode, failed evidence also adds revision drafting responsibility. Budget mode (`AI_SPARK_BUDGET_MODE` / `--budget-mode`): `balanced` (default), `aggressive` (enables additional revision drafting on failure), `conservative` (legacy single-role routing). Recommend at most three short Spark helper invocations per task — a preflight call, an optional targeted or failure role call, and a postflight call — as a workflow recommendation, not cross-process daemon or state enforcement. New explicit read-only modes: `observe-synthesizer`, `task-card-drafter`, `context-packet-builder`, `preflight-bundle`, `direction-precheck`, `acceptance-matrix`, `postflight-bundle`, `revision-drafter`, `lesson-extractor`, `execution-cost-estimator`. Bundle output uses seven compressed headings: Decision Summary, Risk Flags, Scope and Boundaries, Acceptance Matrix, Evidence Conflicts, Required Codex Decisions, Recommended Next Action. Prefer read-only modes: `task-size-classifier`, `task-card-audit`, `plan-splitter`, `validation-planner`, `failure-triage`, `review-only`, `evidence-checker`, or `parallel-planner`; use `micro-builder` only when the task card authorizes Spark source edits, limits scope to one or two small files, rules out public API/contract risk, and gives exact narrow validation in the helper-created isolated worktree; use `controlled-builder` for narrow auditable source-write work with explicit `--allow-write` paths (1–3), required `--max-diff-lines` (1–200), all public API/data/security/migration/permission/concurrency/cross-module risks excluded, existing pattern/source-of-truth required, forced full artifacts and isolated worktree, and tracked/untracked path/line/binary evidence checked after run — violations exit non-zero, remain isolated, never modify source, merge, or satisfy acceptance. Spark result delivery modes: `direct` (advisory default, no permanent directory), `minimal` (stdout + compact report), `full` (all evidence preserved). `--output` without `--result-mode` selects `minimal`; `--output --result-mode direct` is invalid. Source-writing modes force `full`. Spark evidence is auxiliary, auto-disables when unavailable or quota-exhausted, must not silently fall back to GPT-5.5 or another stronger model, cannot independently satisfy acceptance, cannot authorize merge, and requires strong Codex review. No implicit strong-model fallback. No model-tier routing in this change. For summary/benchmark aggregation across multiple reports, record: helper invocation count, total Spark calls, unique modes/stages/roles, budget modes, provisional status, strong-review required, merge authorization status, and auto-disable occurrences/reasons.

Phase ownership is explicit:

| Phase | Codex owns | Claude owns |
|-------|------------|-------------|
| Observe / Plan | Evidence, scope, task card, acceptance criteria, responsibility gates | N/A unless dispatched for exploration |
| Builder Execute | Progress observation and direction review | Scoped implementation, progress updates, direction report |
| Direction Review | Wait, revise, split, dispatch checker-test, or threshold-based takeover decision | Report blockers and avoid repeated confirmation loops |
| Checker/Test | Validation task dispatch and evidence review | Assigned tests, assigned validation, failure evidence |
| Final Review | Accept / revise / split / reject; human merge stays separate | N/A unless re-dispatched |

Small local edits can use a Codex-only fast path instead of dispatching Claude when calibrated size, file count, context sufficiency, solution clarity, confidence, and delegation economics favor it. Risk flags normally increase review, validation, isolation, or approval rigor rather than choosing ownership. They must never push work from Codex to Claude; an explicit risk-based owner override may bias only toward Codex. Record why Claude was not dispatched, files touched, validation evidence, and the material scope/solution/context expansion that would trigger a fresh route decision.

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
  locate-code.py              # Low-token code locator with bounded CodeGraph fallback
  review-with-codex.sh        # Sends evidence to Codex/GPT for review
  run-codex-spark.sh          # Optional gpt-5.3-codex-spark auxiliary runner
  run-parallel-loop.sh        # Experimental parallel dispatch helper
  run-loop.sh                 # Optional loop runner (dispatch + review)
  status-claude.sh            # Inspect Claude dispatch progress/artifacts
  watch-claude.sh             # Stream Claude progress in a terminal
  monitor-claude.sh           # Persist material layered-monitor transitions in background
  kill-claude.sh              # Stop a Claude dispatch by PID artifact
  cleanup-worktree.sh         # Remove stopped Claude worktrees safely
  pwsh-utf8.ps1                # Configure PowerShell UTF-8 session defaults
  doctor_workflow.py          # Read-only readiness check for dispatch/review loop
  code-search-service.py      # Optional Zoekt/Sourcegraph setup and diagnostics
  clean_runtime.py            # Preview/remove ignored runtime artifacts
  install_context_tools.py    # Check/install context tools (LSP, linting)
  summarize-loop-run.py       # Summarize workflow quality, speed, cost, and stability
  benchmark-loop-runs.py      # Aggregate loop summaries into a lightweight benchmark
  init-spec.py                # Create ai/specs/YYYY-MM-DD--slug.md
  plan-to-task-cards.py       # Generate task cards from reviewed plan sections
  init-plan.py                # Create ai/plans/<task-id>/ planning files
  session-catchup.py          # Generate resume-context.md from plan and artifacts
  validate-parallel-plan.py   # Validate parallel DAG plan JSON against schema v1
  task_schema.py              # Shared stdlib loader, validator, and profile composer
  compose-profiles.py         # Compose profiles with a task instance
  lint-task-card.py           # Validate a task card JSON against schema and profiles
  render-task-card.py         # Render a task card JSON as Markdown
  README.md                   # This file
  schemas/
    task-card-v1.schema.json  # Normative JSON Schema for task cards v1
  profiles/
    base.json                 # Base profile with sensible defaults
    bugfix.json               # Bugfix profile narrowing scope and risk defaults
  examples/
    fix-typo-in-readme.json   # Example task card
.worktrees/                   # Isolated git worktrees for execution
ai/plans/                     # Persistent planning files for long-running tasks
AGENTS.md                     # Shared agent rules
CLAUDE.md                     # Claude Code configuration
```

## Quick Start

### JSON Task Cards (opt-in)

Task cards can be authored as structured JSON instead of Markdown. JSON provides schema validation, deterministic profile composition, and machine-readable acceptance criteria. Existing Markdown task cards remain fully supported — JSON is purely opt-in.

```bash
# Lint a task card JSON
python ai/lint-task-card.py ai/task-cards/PROJ-123.json

# Compose profiles and merge with task instance
python ai/compose-profiles.py ai/task-cards/PROJ-123.json --output composed.json

# Render as Markdown (audit for humans, execution for Claude)
python ai/render-task-card.py ai/task-cards/PROJ-123.json --view audit
python ai/render-task-card.py ai/task-cards/PROJ-123.json --view execution
```

Key behaviors:
- **JSON is source of truth** when both `.json` and `.md` exist for the same task.
- **Audit view** includes risk, extensions, full handoff. **Execution view** includes only goal, scope, acceptance, validation, stop conditions.
- **Conflict hard-fail.** Profile composition raises on conflicting scalars; use `lint-task-card.py` to catch before dispatch.
- Schemas live at `ai/schemas/`, profiles at `ai/profiles/`, examples at `ai/examples/`.

### 1. Create a Task Card

Copy the template and fill it in:

```bash
cp ai/task-card-template.md ai/task-cards/PROJ-123.md
# Edit ai/task-cards/PROJ-123.md
```

For bounded loops, fill `Goal Loop Contract` in the task card. Prefer deterministic fields such as success signal, max attempts, repeated-failure threshold, no-improvement threshold, regression stop rule, required evidence, and benchmark tags. Use `Spec Gate` before broad ambiguous work, `Root Cause Gate` before bugfixes/regression fixes, `Test-First / TDD Contract` when red-green evidence matters, and `Finish Branch Gate` before claiming work ready for merge. Use `Advisor Gate` when a stronger model, Codex reviewer, or human expert should advise before risky work; record timing, call caps, output budget, result visibility, conflict reconciliation, and fallback behavior. Leave `Codex Spark Gate` at `auto` when Spark should perform low-cost task-size classification, task-card audit, plan splitting, validation planning, failure triage, or review/evidence checking, with auto-disable on Spark unavailability. Use micro-builder only after explicit tiny-scope authorization. Use `Unknowns` to record blindspot scan requests, questions that would change architecture, reference examples, and where Claude should record deviations from plan.

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

Default `--mode auto` resolves to an applicable stage bundle: ordinary pre-Builder use resolves to `preflight-bundle`, diff/report/evidence use resolves to `postflight-bundle`, Checker/Test remains `validation-planner`, and failed/no-report evidence includes failure triage. In aggressive budget mode, failed evidence also adds revision drafting responsibility.

Budget mode (`AI_SPARK_BUDGET_MODE` / `--budget-mode`): `balanced` (default), `aggressive` (enables additional revision drafting on failure), `conservative` (legacy single-role routing).

Recommend at most three short Spark helper invocations per task: a preflight call, an optional targeted or failure role call, and a postflight call. This is a workflow recommendation, not cross-process daemon or state enforcement.

New explicit read-only modes: `observe-synthesizer`, `task-card-drafter`, `context-packet-builder`, `preflight-bundle`, `direction-precheck`, `acceptance-matrix`, `postflight-bundle`, `revision-drafter`, `lesson-extractor`, `execution-cost-estimator`.

Bundle output uses seven compressed headings: Decision Summary, Risk Flags, Scope and Boundaries, Acceptance Matrix, Evidence Conflicts, Required Codex Decisions, Recommended Next Action.

Use `task-size-classifier` to spend cheaper Spark quota before stronger-model context when task size is unclear. It should classify the task as `tiny`, `small`, `medium`, `large`, or `unknown` and recommend `codex-fast-path`, `spark-review-only`, `spark-micro-builder`, `claude-builder`, `checker-test`, `spec-first`, or `human-clarification`. It also includes execution-cost fields when available.

The `execution-cost-estimator` mode predicts diff range/files and relative direct/delegated work units for a task. Run a fresh estimate before every initial, revision, narrowed retry, re-dispatch, split-child, or next-phase task card; use `--routing-event initial|revision|narrow|retry|next-phase` and do not reuse the previous card's owner decision. Work units are relative estimates, not token-accounting measurements. The helper calibrates Spark's raw upper line estimate by 1.5x normally and 2.0x for tests/fixtures, shell/process orchestration, and cross-platform work. Codex fast path is allowed when the economic recommendation and deterministic owner gate favor it: calibrated upper bound within the configured threshold, <=2 files, local context, high confidence, and complete economics. Risk flags and validation complexity normally affect review, validation, isolation, and approval rigor rather than ownership. Risk must never push work from Codex to Claude; if a human or policy explicitly applies a risk-based owner override, it may bias high-risk work only toward Codex. Actual edits may exceed the estimate while scope, solution, and context remain stable. This is a pre-dispatch decision, not a post-Claude takeover; it never automatically edits source. The estimator is also included in `preflight-bundle` and `task-size-classifier` output.

When using explicit `task-size-classifier` mode or conservative auto routing (balanced/aggressive ordinary preflight is `preflight-bundle`), the helper runs Codex from the Spark artifact directory with `workspace-write` sandbox. This gives local helper initialization a writable working directory without granting write access to the source repository, and the mode contract still forbids source edits.

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

For a reviewed DAG parallel plan:

```bash
bash ai/run-codex-spark.sh ai/task-cards/PROJ-123.md --mode parallel-planner
```

`parallel-planner` produces strict schema-v1 JSON and standard reconciliation fields only. Spark does not execute or dispatch; Codex/human must review and save the JSON plan before running `bash ai/run-parallel-loop.sh --plan ai/plans/.../parallel-plan.json`.

For tiny scoped edits only, use micro-builder mode. The task card must authorize Spark source edits, limit scope to one or two small files, rule out public API/contract risk, and provide exact narrow validation. The helper creates an isolated worktree and refuses dirty source repositories unless explicitly overridden:

```bash
bash ai/run-codex-spark.sh ai/task-cards/PROJ-123.md --mode micro-builder --sandbox workspace-write
```

Spark artifacts include `codex-spark.report.md`, `codex-spark.prompt.md`, `codex-spark.result.txt`, `codex-spark.stderr.log`, `codex-spark.artifacts.txt`, `codex-spark.worktree-status.txt`, and optional `codex-spark.diff`. Spark does not silently fall back to GPT-5.5 or another stronger model. Use `--require-spark` only when Spark availability should become a hard failure.

Spark output is advisory. Record `accepted_suggestions`, `ignored_suggestions`, `conflicts_with_claude`, `conflicts_with_local_evidence`, and `acceptance_satisfied_by_spark` in the Spark follow-up table, but do not let Spark replace Claude Builder ownership, Codex final review, or independent acceptance verification. Spark never authorizes merge; strong Codex review remains required; no implicit strong-model fallback; no model-tier routing in this change. For summary/benchmark aggregation across multiple reports, record: helper invocation count, total Spark calls, unique modes/stages/roles, budget modes, provisional status, strong-review required, merge authorization status, and auto-disable occurrences/reasons.

**Spark result delivery modes** control how results are returned and persisted via `--result-mode`:

- **`direct`** (default for advisory/read-only runs): sends raw result on stdout, uses a cleaned temporary workspace, creates no permanent Spark directory. No `codex-spark.report.md` or other files are written. Choose `direct` when only the inline result matters and file-backed metrics are not needed.
- **`minimal`**: sends raw result on stdout and persists only a compact `codex-spark.report.md`. Use when persistent metrics or benchmark aggregation is required but full evidence is unnecessary.
- **`full`**: preserves prompt, result, stderr, status, diff, task-card, and manifest evidence. Use when complete audit trails are required.

When `--output` is passed without an explicit `--result-mode`, the helper selects `minimal`. Combining `--output` with `--result-mode direct` is invalid — `direct` creates no persistent artifacts. Source-writing modes (`controlled-builder`, `micro-builder`) force `full` artifacts.

**Observability tradeoff:** `direct` mode intentionally has no file-backed metrics — no `codex-spark.report.md`, no artifact directory, no manifest. This is by design for lightweight advisory calls. When benchmark aggregation, quality tracking, or audit evidence is needed across multiple Spark invocations, choose `minimal` or `full` so `ai/benchmark-loop-runs.py` and `ai/summarize-loop-run.py` can aggregate results.

**Spark diagnostics (`--diagnostics`):** when a direct-mode call produces an unusable result (empty response, availability/execution failure, or schema-invalid estimator output), `--diagnostics failure` (default) writes a compact redacted record under `.worktrees/spark-diagnostic-<timestamp>/`. Secrets are stripped from stderr excerpts. `--diagnostics off` disables all persistence. `--diagnostics full` copies all evidence (prompt, result, stderr, status metadata) into the permanent directory for reproduction. Successful calls remain zero-persistence. Estimator output classified as `schema-invalid` auto-disables Spark (exits 0) unless `--require-spark` is set.

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

### Large Repositories / Slow Filesystems

Fill `Worktree / Large Repo Strategy Gate` before dispatch when `git worktree add`, filesystem reads, or dispatcher status/diff collection are materially slow. Defaults keep complete evidence. Prefer the explicit fast profile when the gate accepts managed reuse and summary evidence:

Tracked-file count is only a signal to review this gate. Use `fast-large-repo` or managed reuse only when risk is low, targets are exact, dispatch is serial, and reduced untracked/patch evidence is explicitly accepted; otherwise keep a fresh worktree with full evidence. Exact mechanical Builder tasks may use `CLAUDE_CODE_BUILDER_MODE=execution-only`. A completed no-diff run may be retried in the same clean fresh worktree with `CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID=<prior-task-id>` after the dispatcher proves the recorded identity and safety conditions.

Use `python ai/locate-code.py "symbol or behavior" --path src --max-files 12` before dispatch to build the `Claude Context Packet` cheaply. It ranks candidate files from path hints and lexical matches, prints short snippets, and suggests exact line reads. If Zoekt is installed and indexed, `--backend auto` uses it before lexical fallback. Sourcegraph can be used when `SOURCEGRAPH_URL` is configured. CodeGraph is bounded: `auto` skips graph search in large tracked-file repos, while `--codegraph try --codegraph-timeout 12` is reserved for specific file/symbol/call-path questions. If CodeGraph times out, record it once and continue with locator output plus targeted line reads instead of repeating broad graph queries.

For optional indexed search setup:

```bash
python ai/code-search-service.py doctor
python ai/code-search-service.py install-zoekt --yes
python ai/code-search-service.py index-zoekt --repo . --yes
```

`install-zoekt --yes` streams the underlying `go install` output and prints periodic `still running...` heartbeats while Go downloads or compiles quietly. Pass `--progress-interval 5` before the subcommand for more frequent updates:

```bash
python ai/code-search-service.py --progress-interval 5 install-zoekt --yes
```

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

Two compatible paths exist for parallel dispatch:

**Path 1: Flat independent cards (positional arguments)**

Parallel remains opt-in. First use the local zero-token classifier; ordinary serial tasks do not call Spark:

```bash
python ai/assess-parallel-opportunity.py --json \
  --work-units 3 --write-scopes src/a,src/b,src/c \
  --estimated-minutes 30 --validation-count 3
```

Only a `parallel-candidate` result should proceed to one bounded Spark `parallel-planner` call and reviewed task cards. When cards declare independent scopes, owned contracts, validation responsibility, and a common Base commit matching current `HEAD`, run:

```bash
bash ai/run-parallel-loop.sh --max-concurrency 2 \
  ai/task-cards/PROJ-123-a.md \
  ai/task-cards/PROJ-123-b.md
```

The helper dispatches tasks concurrently, writes `.worktrees/parallel-*/parallel-summary.md`, and never merges automatically. It refuses ungated task cards and overlapping `Allowed files/modules` by default. Use `--allow-overlap` only for explicit manual-reconcile experiments.

**Path 2: Reviewed DAG plan (`--plan`)**

For dependency-ordered parallel execution, use Spark `parallel-planner` to propose a reviewed DAG plan:

```bash
bash ai/run-codex-spark.sh ai/task-cards/PROJ-123.md --mode parallel-planner
```

Spark produces strict schema-v1 JSON — it only proposes and never executes. Codex/human must review and save the plan before dispatch. Then run:

```bash
bash ai/run-parallel-loop.sh --plan ai/plans/PROJ-123/parallel-plan.json
```

Schema fields: `schema_version` (must be `1`), `group_id`, `max_concurrency`, `failure_policy` (currently `skip-dependents`), and `tasks` containing `id`, `task_card`, `depends_on` per task. Task-card paths resolve relative to the plan file. An explicit CLI `--max-concurrency` overrides the plan's cap.

Scheduling semantics: the scheduler starts only dependency-ready tasks up to the concurrency cap. With `skip-dependents`, a failed prerequisite prevents all transitive dependents from dispatching while unrelated branches continue. All cards still require scope-gate and overlap checks. Review and merge remain serial.

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

**Checker Reuse Risk Gate:** Before dispatching a `checker-test` task, fill the Checker Reuse Risk Gate in the task card with exact rows: Public API risk, Data model risk, Security risk, Migration risk, Permission risk, Concurrency risk, Cross-module risk, Production impact. Each row must be explicit `no` for task-derived checker worktree reuse to default to `reuse-managed`. Missing, `unknown`, `n/a`, `duplicate`, `high` risk, DAG, or parallel tasks stay `fresh`. The environment variable `CLAUDE_CODE_WORKTREE_STRATEGY=fresh|reuse-managed` overrides this default. Existing reset safety via `CLAUDE_CODE_REUSE_WORKTREE_RESET=1` remains unchanged.

**Authoritative validation timeline:** The dispatcher preserves the Claude blocked state. Checker ALL GREEN is the authoritative signal that makes the final status `passed`. Checker failures set the final status accordingly.

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

The benchmark aggregates advisor usage, diagnostic probe usage/cost, same-worktree continuation success, avoided full redispatches, conservative re-exploration evidence, Spark invocation/auto-disable/fallback status, parallel-dispatch usage, spec adherence, root-cause evidence, and TDD fields. Estimated token/time savings remain unavailable unless an audit contains explicit numeric evidence.

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

Check the effective Claude/CC Switch configuration without printing credentials:

```bash
python ai/claude-healthcheck.py
python ai/claude-healthcheck.py --probe
python ai/claude-healthcheck.py --interaction-route auto --timeout 60
python ai/claude-healthcheck.py --interaction-route compare --timeout 60
```

The endpoint probe is advisory by default because DNS, proxy, and TLS failures may be transient. Use `--require-probe` only when strict automation explicitly wants a network failure to stop before dispatch. A successful Claude interaction remains the authoritative availability signal.

The interaction probe sends a real minimal prompt and therefore consumes model quota, but it is a read-only runtime diagnostic and needs no human review. `auto` tries the route implied by the current proxy environment and stops on success; it tries the alternate only after failure. `compare` always consumes two calls. Apply the recommendation explicitly with `CLAUDE_CODE_PROXY_MODE=inherit|direct`; the diagnostic is not implementation or acceptance evidence.

Classify a completed or failed round before counting it toward takeover:

```bash
python ai/classify-claude-attempt.py --exit-code 1 --outcome api_error --error-text-file .worktrees/<task>.status.txt
```

Transport failure before interaction may retry the same worktree once and does not count as Claude no-progress. Acknowledgement-only, clean exit without progress, and confirmed direction deviation do count. Approval or sandbox blockers remain external blockers.

### Missing `ai/` After Installing the Skill

Installing the Codex Skill does not automatically modify every repository. If dispatch fails because `ai/dispatch-to-claude.sh` is missing, run the installed Skill bootstrap command in that repository, then verify with:

```bash
python ai/doctor_workflow.py
```

On mounted filesystems, use bounded target-only hash diagnostics when file bytes appear different but Git status is empty:

```bash
python ai/doctor_workflow.py . --hash-path path/to/file --hash-path path/to/other-file
```

This compares filesystem, index, and scoped status evidence for at most 20 explicit files. It does not prove global worktree cleanliness and never runs `git add`, `git add --renormalize`, `update-index`, reset, clean, checkout, or deletion. Any renormalization remains a human-reviewed action.

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

For agent-driven runs, prefer `monitor-claude.sh start <task-id>`. It runs the layered watcher as a local background process and persists only material transitions to `.worktrees/<task-id>.monitor-events.log`; read `monitor-claude.sh tail <task-id>` once at a review/terminal boundary instead of spending Codex turns on heartbeat polling. Spark may summarize the compact event log when useful, but local monitoring itself invokes no model.

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

## Claude External Integrations

Claude's built-in tool profiles (Bash, Edit, file operations) are automatic. External MCP servers and plugins are default-off and must be explicitly declared per task card.

Fill `Claude External Integration Gate` in the task card when the task needs repository-local MCP config files or plugin directories:

| Gate rule | Behavior |
|-----------|----------|
| Missing gate or `External integrations allowed?` = `no` | Dispatcher uses `--bare`; no `--mcp-config` or `--plugin-dir` arguments are passed |
| `External integrations allowed?` = `yes` | Only declared repository-relative existing paths are accepted |
| `Strict MCP isolation?` | Must be `yes` whenever integrations are allowed |

When integrations are allowed:

- **Paths are validated after the worktree exists.** The dispatcher rejects absolute paths, empty entries, `..` traversal, control characters, and paths resolving outside the worktree.
- **MCP entries** must be existing repository-relative `.json` files. **Plugin entries** must be existing repository-relative directories or `.zip` files.
- **Paths are passed as arrays, preserving case and spaces.** The dispatcher does not perform global config scan, `mcp list`, `plugin list`, install, enable, or download.
- **Evidence recording** stores only the selected relative paths and any rejection category; MCP/plugin file contents and secrets are never recorded.
- **External integrations do not widen built-in Bash/Edit permissions.** The tool profile and allowed tool set remain unchanged.

**Monitoring environment:** `monitor-claude.sh start` must run in a persistent dispatch or user-terminal environment. Some Codex sandbox tool sessions reap detached children; an empty event log there is visibility/environment evidence, not Claude zero progress. Fall back to one boundary status/diff read, never duplicate dispatch.

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

# Monitor locally in the background; read compact material events later
bash ai/monitor-claude.sh start claude-20260701-093934
bash ai/monitor-claude.sh tail claude-20260701-093934 --lines 30

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

`doctor_workflow.py` runs in preview-only mode: it shows count, size, and age of runtime artifacts. It does not automatically delete anything.

---

## Preserved Constraints

The following constraints are preserved across all workflow changes:

- **No model tiers:** there is no automatic model-tier routing or escalation between Spark, Claude, and stronger models.
- **No implicit fallback:** Spark does not silently fall back to GPT-5.5 or another stronger model. If Spark is unavailable, report the gap and let Codex or the human decide.
- **No automatic merge:** human review and merge remain separate. The workflow never merges automatically.

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
