# Task Card

## ID

<!-- e.g., PROJ-123 -->

## Task Type

<!-- normal | control-plane -->

## Executor

<!-- Claude Code | Codex control-plane hotfix | human -->

## Task Mode

<!-- builder | checker-test | mixed-exception | control-plane. Prefer builder followed by checker-test for non-trivial work. -->

| Field | Value |
|-------|-------|
| Mode | builder / checker-test / mixed-exception / control-plane |
| Builder scope | implementation only; no acceptance test writing or broad test execution unless narrow sanity check is explicitly listed |
| Checker/Test scope | write/update tests, run assigned validation, produce report; no broad implementation rewrite unless a concrete small fix is explicitly allowed |
| Codex direction review required before checker/test? | yes/no |
| Mixed implementation + test-writing allowed in one Claude dispatch? | no / yes, mixed-exception rationale: |

Mixed-task guard: if a task asks one Claude dispatch to implement, write tests, run validation, and stop at phase gates, prefer splitting it into a Builder task followed by a Checker/Test task. Use `mixed-exception` only when the task is intentionally tiny or the human explicitly asks for a single combined pass; record the rationale so a later stall is not misattributed to Claude execution quality.

## Phase Responsibility Matrix

<!-- Codex completes this before dispatch. Keep the active phase explicit so Claude does not infer testing or confirmation duties. -->

| Phase | Codex owns | Claude owns | Explicitly not Claude-owned | Explicitly not Codex-owned |
|-------|------------|-------------|-----------------------------|----------------------------|
| OBSERVE / PLAN | Evidence gathering, unknowns, task card, scope, acceptance criteria | N/A unless this is an exploration task | Product edits | Broad implementation without dispatch |
| BUILDER EXECUTE | Progress observation, partial diff direction review | Scoped implementation, progress updates, direction report | Acceptance tests and broad validation unless explicitly allowed | Direct implementation edits while Builder has not hit threshold |
| DIRECTION REVIEW | Decide wait / revise / split / dispatch checker-test / takeover threshold | Provide report/progress/blockers | Repeated confirmation after proceed | Validating an unaccepted direction |
| CHECKER / TEST | Dispatch validation task and review evidence quality | Assigned test writing, assigned validation, failure evidence | Broad implementation rewrite unless allowed small fix | Treating unassigned tests as Claude failure |
| FINAL REVIEW / MERGE | Accept/revise/split/reject; human merge remains separate | N/A unless re-dispatched | N/A | Automatic merge or direct edit without threshold |

## Stall / Ambiguity Triage

<!-- Codex completes this before dispatch and reviews it when Claude appears stuck. Use it to distinguish Claude execution failure from orchestration ambiguity. -->

| Check | Value |
|-------|-------|
| Task mixes builder and checker/test responsibilities? | yes/no |
| If mixed, split before dispatch? | yes/no + reason |
| Dirty source or stale HEAD risk acknowledged? | yes/no/not applicable |
| HEAD contains required prior context for Claude? | yes/no/not applicable |
| Dirty source blocks reliable Claude dispatch? | yes/no |
| Required progress artifacts | CLAUDE_PROGRESS.md / CLAUDE_TASK_CARD.md checklist / CLAUDE_REPORT.md |
| Long-running command expected? | yes/no + command |
| Permission/tool approval risk? | yes/no + sandbox/write/network/auth/forbidden-file details |
| Network diagnostics needed? | no / yes, set `CLAUDE_CODE_NETWORK_MONITOR=1`; optional healthcheck URL: |
| Ambiguity likely to cause stop-and-report? | yes/no + field |
| If Claude is quiet, first diagnosis step | inspect progress artifacts and partial diff before declaring failure |
| Conditions that prove real Claude no-progress | no artifact growth, no worktree change, no status output, no permission blocker, and no reported blocker after grace period |

## Worktree / Large Repo Strategy Gate

<!-- Codex completes this before dispatch for large repositories or slow filesystems. Default remains fresh isolated worktree with complete evidence. Use large-repo shortcuts only when startup/read cost is a material blocker and the evidence tradeoff is acceptable. -->

| Field | Value |
|-------|-------|
| Repository size concern? | no / yes, reason |
| Worktree strategy | fresh / reuse-managed |
| Reuse command env | none / `CLAUDE_CODE_WORKTREE_STRATEGY=reuse-managed CLAUDE_CODE_REUSE_WORKTREE_RESET=1` |
| Large repo read mode | off / `CLAUDE_CODE_LARGE_REPO_MODE=1` |
| Evidence tradeoff accepted? | no / yes, untracked scans and untracked patch evidence may be skipped |
| Safety boundary | never reset or clean source repo; reuse only `.worktrees/reuse/claude-managed` |
| Cleanup expectation | remove fresh worktree after review / keep managed reuse worktree / human decides |

## Delegation Restoration Gate

<!-- Codex completes this when dirty source, stale HEAD, missing local workflow files, permissions, or environment state blocks reliable Claude dispatch. These are delegation blockers, not automatic Codex takeover triggers. -->

| Check | Value |
|-------|-------|
| Delegation blocker present? | no / dirty source / stale HEAD / outdated workflow files / permission-tool approval / external environment |
| Why Claude would not see required context | |
| Restoration path selected | commit accepted phase / stash or patch source changes / refresh workflow files / re-dispatch from updated HEAD / request explicit dirty-source override / stop for human |
| Restoration attempted before Codex takeover? | yes/no + evidence |
| If not restored, why impossible or unsafe? | |
| Codex takeover justified instead of restoration? | no / yes + threshold or explicit human override |
| Return-to-delegation condition | next task from clean updated HEAD / after human approval / after tool permission fixed |

## Direction Review Gate

<!-- Codex completes this after a Builder task before dispatching Checker/Test work. If the builder direction is wrong, revise or interrupt instead of testing the wrong approach. -->

| Check | Value |
|-------|-------|
| Builder diff matches planned direction? | yes/no/partial |
| Continue waiting for Builder? | yes/no + reason |
| Interrupt and narrow task? | yes/no + reason |
| Dispatch Checker/Test task next? | yes/no + task-card path |
| Codex takeover threshold reached? | yes/no + cited artifacts |

## Direction / Boundary Acknowledgement

<!-- Claude completes this before editing. Use blocking approval for ambiguous, multi-file, high-risk, public API, data model, security, migration, permission, or production-impacting work. If Claude has material confusion, it must stop-and-report instead of guessing. -->

| Field | Value |
|-------|-------|
| Required before editing? | yes/no |
| Blocking Codex approval required? | yes/no |
| Maximum acknowledgement rounds | 0 / 1 |
| Re-acknowledgement allowed only if Codex changes goal/scope/boundaries? | yes/no |
| Claude must state task in own words? | yes/no |
| Claude must list in-scope files/modules? | yes/no |
| Claude must list explicitly out-of-scope boundaries? | yes/no |
| Claude must report confusion before editing? | yes/no |
| Stop if acceptance criteria unclear? | yes/no |
| Stop if testing responsibility unclear? | yes/no |
| Stop if implementation boundary unclear? | yes/no |
| Expected acknowledgement artifact | CLAUDE_PROGRESS.md / CLAUDE_REPORT.md |
| Codex approval artifact, if blocking | |
| Final acknowledgement decision | proceed / narrow-once / split / stop |

Acknowledgement format Claude should write:

- My understanding:
- Planned scope:
- Explicitly out of scope:
- Files/modules likely touched:
- Acceptance criteria interpretation:
- Testing responsibility interpretation:
- Confusions or ambiguities:
- New risks / unknowns:
- Recommendation: proceed / narrow / split / stop-and-report

Anti-loop rule: acknowledgement is a gate, not a discussion loop. Codex should answer with one final decision: proceed, narrow once and re-dispatch, split, or stop. Claude should not request repeated confirmation after approval unless the task goal, scope, boundaries, or risk profile materially changes.

Non-blocking acknowledgement rule: if acknowledgement is non-blocking and Claude recommends `proceed`, Claude must continue implementation in the same run. Stopping after acknowledgement without a concrete blocker or approval requirement is `acknowledgement only` and counts as no implementation progress.

## Small Change Fast Path Gate

<!-- Fill before dispatch. If every row supports direct Codex editing, Codex may skip Claude dispatch for this task and perform the bounded edit directly. If the edit grows beyond this gate, stop and return to task-card + Claude dispatch. -->

| Field | Value |
|-------|-------|
| Fast path candidate? | yes/no |
| Expected files touched | <=2 / >2 |
| Files small and targeted? | yes/no |
| Change type | docs/comment/test assertion/log text/mechanical helper fix/other |
| Public API, data model, security, migration, permission, concurrency impact? | no / yes + explain |
| Broad repository context needed? | no / yes |
| Cross-module contract risk? | no / yes |
| Test design or complex validation needed? | no / yes |
| Direct Codex edit allowed? | yes/no |
| Reason for skipping Claude dispatch | |
| Narrow validation or reason skipped | |
| Escalate to Claude if | files >2 / scope expands / uncertainty appears / validation needs Checker/Test |

Fast path rules:
- Use only for small, local, low-risk edits where Claude dispatch overhead would cost more than the change.
- Do not use for public API, data shape, security, migration, permission, concurrency, broad refactor, or cross-module contract changes.
- Spark is optional on obvious fast path edits. When task size is unclear, prefer Spark `task-size-classifier` before spending stronger Codex/Claude context.
- Record the reason Claude was not dispatched and preserve narrow validation evidence or an explicit validation-skip reason.

## Task Card Views

<!-- Codex owns this full planning card. Dispatch scripts derive `CLAUDE_TASK_CARD.md` from it and omit Codex-only budget/planning/control sections before prompting Claude. Do not maintain a second hand-written Claude card. -->

| Field | Value |
|-------|-------|
| Execution profile | balanced / safe / fast-large-repo |
| Claude execution card view | compact default / execution for high-risk tasks |
| Prompt profile | brief default / standard for high-risk or ambiguous tasks |
| Evidence mode | full default / summary only with accepted patch-evidence tradeoff |
| Compact view allowed? | yes/no; yes only after current-phase Goal, Handoff Contract, Testing Responsibility, Validation Contract, and Acceptance Criteria are complete |
| Fast large-repo profile allowed? | no / yes, accepts managed worktree reuse, skipped unrelated untracked scans, and summary diff evidence |
| Dispatch env override | none / `CLAUDE_CODE_EXECUTION_PROFILE=safe` / `CLAUDE_CODE_EXECUTION_PROFILE=fast-large-repo` |
| Full audit card retained? | yes, `TASK_CARD_FULL.md` |

## Claude Context Packet

<!-- Execution-facing packet for large repositories or slow filesystems. Keep it small and concrete so Claude does not rediscover the repository. This section is retained in the Claude execution card. -->

| Field | Value |
|-------|-------|
| Locator command / artifact | `python ai/locate-code.py ...` / artifact path / not needed |
| Locator backend | auto / lexical / Zoekt / Sourcegraph |
| CodeGraph status | skipped / not indexed / timed out once / used for concrete symbol |
| Target files/modules | |
| Relevant symbols/functions | |
| Reference examples / source of truth | |
| Do not read / do not modify | |
| Known constraints | |
| Narrow validation commands | |
| Context is sufficient for execution? | yes/no |
| Escalate before broad search if | missing target file / symbol not found / contract unclear / validation unavailable |

## Control-Plane Exception Rationale

<!-- Fill only when Task Type is control-plane. Explain why normal Claude delegation is unsafe, unavailable, or exhausted after repeated failed Claude attempts, cite attempt artifacts, identify any first-round direction Codex will salvage, define the narrow Codex edit scope, and state what condition returns work to the normal Codex-plan / Claude-execute flow. -->

## Goal

<!-- What needs to be accomplished in one sentence. -->

## Goal Loop Contract

<!-- Use this to turn the task into a bounded goal-based loop with explicit success and stop criteria. Keep it deterministic where possible. -->

| Field | Value |
|-------|-------|
| Loop type | turn-based / goal-based / time-based / proactive |
| Success signal | exact test/check/output/user-visible state that proves done |
| Max attempts / iterations | |
| Stop on repeated failure? | yes/no; same failure threshold: |
| Stop on no improvement? | yes/no; no-progress threshold: |
| Stop on regression? | yes/no; prior passing checks that must not fail: |
| Required evidence before accept | diff + valid report / checker report / screenshots / trace / benchmark summary / human review |
| Token/cost budget, if any | |
| Wall-clock budget, if any | |
| Benchmark tags | <!-- e.g., bugfix, refactor, frontend, product, harness, docs --> |

Loop type guide:
- Turn-based: short or exploratory work where a single reviewed pass is enough.
- Goal-based: work with a concrete success signal and bounded attempts; preferred for `ai/run-loop.sh`.
- Time-based: periodic polling or external-system follow-up.
- Proactive: recurring well-defined pipeline work such as issue triage, dependency updates, or CI repair.

## Advisor Gate

<!-- Use this when a stronger reviewer/planner should advise the executor before risky work. The advisor may be Codex as an external reviewer, Claude's advisor tool, or a human/domain expert. -->

| Field | Value |
|-------|-------|
| Advisor required? | no / yes |
| Advisor role | none / Codex external advisor / Claude advisor tool / human domain expert |
| Advisor model or person | |
| Advisor timing | after orientation / before first write / before final report / when stuck / reconcile conflict |
| Read-only orientation required before advisor? | yes/no |
| Required before state-changing edit? | yes/no |
| Max advisor calls for this task | |
| Advisor output budget | words/tokens cap, if any |
| Advisor result visibility | plaintext / redacted / unavailable / not applicable |
| Reconcile conflicts with local evidence? | yes/no |
| Fallback if advisor unavailable or cap reached | proceed conservatively / stop-and-report / ask human |
| Advisor evidence artifact | CLAUDE_PROGRESS.md / CLAUDE_REPORT.md / review artifact / other |

Advisor timing rules:
- Do not ask for a low-context advisor opinion before basic read-only orientation.
- Treat advisor guidance as high-value input, not unquestionable truth.
- If local evidence conflicts with advisor guidance, record the conflict and reconcile before changing direction.
- If the advisor result is redacted or unavailable to Codex, report the advice category, whether it was followed, and any stop reason/truncation signal.

## Codex Spark Gate

<!-- Optional execution-stage auxiliary using OpenAI gpt-5.3-codex-spark. Default to auto so eligible task-size classification, planning, task-card audit, validation planning, failure triage, review, and evidence work can use separate Spark quota before spending stronger-model context. Spark is auxiliary: if unavailable, unauthenticated, network-blocked, or quota-exhausted, auto-disable it for this run and continue the main workflow. It is not a default Claude replacement and must not silently fall back to a stronger model. -->

| Field | Value |
|-------|-------|
| Spark enabled? | auto / no / yes |
| Spark purpose | explicit mode preferred / auto / task-size-classifier / review-only / task-card-audit / plan-splitter / validation-planner / failure-triage / evidence-checker / parallel-planner / micro-builder / observe-synthesizer / task-card-drafter / context-packet-builder / preflight-bundle / direction-precheck / acceptance-matrix / postflight-bundle / revision-drafter / lesson-extractor / none |
| Spark model | gpt-5.3-codex-spark |
| Budget mode | balanced (default) / aggressive / conservative |
| Pipeline stage | auto / preflight / midflight / postflight |
| Roles used | <!-- comma-separated list of Spark roles invoked --> |
| Call cap recommendation | at most 3 short Spark helper invocations per task (workflow recommendation, not enforcement) |
| Quota rationale | separate Spark quota / latency / cost / not applicable |
| Invocation helper | ai/run-codex-spark.sh |
| Sandbox | read-only / workspace-write |
| Isolated worktree required? | yes/no/not applicable |
| Source edits allowed? | no / yes, only in micro-builder worktree |
| Allowed files/modules | |
| Validation commands allowed | none / exact commands |
| Micro-builder max files | not allowed / 1-2 small files |
| Micro-builder public API or contract risk? | not allowed / no |
| Micro-builder narrow validation | not allowed / exact command |
| Auto-disable when unavailable? | yes |
| Auto-disable conditions | missing CLI / model access denied / auth or network blocker / Spark quota exhausted |
| Strong-model fallback allowed? | no / yes, explicit human approval required |
| Required Spark artifact | .worktrees/.../codex-spark.report.md |
| Spark artifact inputs | none / specific `.worktrees/...` files via `--artifact` |
| Spark result can satisfy acceptance? | no, advisory input only |
| Spark can replace Claude Builder? | no |
| Spark can approve final review? | no |
| Spark authorizes merge? | no |
| Stop conditions | unclear scope / non-availability helper failure / explicit require-spark failure |

Spark rules:
- When `Spark purpose` is `auto`, use stage routing / bundle selection; prefer an explicit `Spark purpose` when Codex already knows the needed support role. Use `auto` only for low-risk helper routing when task size or artifact type is uncertain.
- `--mode auto` resolves to an applicable stage bundle: ordinary pre-Builder use resolves to `preflight-bundle`, diff/report/evidence use resolves to `postflight-bundle`, Checker/Test remains `validation-planner`, and failed/no-report evidence includes failure triage. In aggressive budget mode, failed evidence also adds revision drafting responsibility.
- Budget mode (`AI_SPARK_BUDGET_MODE` / `--budget-mode`): `balanced` is the default, `aggressive` enables additional revision drafting on failure, and `conservative` uses legacy single-role routing.
- Recommend at most three short Spark helper invocations per task: a preflight call, an optional targeted or failure role call, and a postflight call. This is a workflow recommendation, not cross-process daemon or state enforcement.
- New explicit read-only modes: `observe-synthesizer`, `task-card-drafter`, `context-packet-builder`, `preflight-bundle`, `direction-precheck`, `acceptance-matrix`, `postflight-bundle`, `revision-drafter`, `lesson-extractor`.
- Bundle output must use the seven compressed headings: Decision Summary, Risk Flags, Scope and Boundaries, Acceptance Matrix, Evidence Conflicts, Required Codex Decisions, Recommended Next Action.
- Use `task-size-classifier` to cheaply route work before Codex spends stronger-model tokens: `codex-fast-path`, `spark-review-only`, `spark-micro-builder`, `claude-builder`, `checker-test`, `spec-first`, or `human-clarification`.
- Prefer `task-size-classifier`, `task-card-audit`, `plan-splitter`, `validation-planner`, `failure-triage`, `review-only`, `evidence-checker`, or `parallel-planner` before `micro-builder`.
- Use `parallel-planner` to propose a reviewed DAG scheduling plan for independent task cards. Spark produces strict schema-v1 JSON only — it does not execute or dispatch. Codex/human must review and save the plan before running `ai/run-parallel-loop.sh --plan <json>`.
- Use `task-card-audit` to catch missing gates, mixed responsibilities, unclear acceptance, and likely Claude stall risks before dispatch.
- Use `plan-splitter` to propose Builder/Checker slices or independent parallelizable task cards.
- Use `validation-planner` to propose exact low-noise checks without running broad suites.
- Use `failure-triage` with explicit `--artifact` inputs after a stalled/failed run to attribute the failure before spending stronger-model context.
- Run `micro-builder` only when the task card explicitly authorizes Spark source edits, limits scope to one or two small files, rules out public API/data/security/migration/permission/concurrency/cross-module contract risk, names exact narrow validation, and uses `--sandbox workspace-write` in the helper-created isolated worktree.
- Spark evidence can inform Codex review, but it does not override Claude reports, task-card ownership, or required validation. Spark output cannot independently satisfy acceptance; Codex must verify and record acceptance separately.
- Treat Spark as default-on optional support. If Spark is unavailable or quota-exhausted, record the auto-disable report and continue the main Claude/Codex workflow.
- Do not consume GPT-5.5/strong-model quota as an implicit fallback. If Spark is unavailable or insufficient, report the gap and let Codex or the human decide the next model.
- No implicit strong-model fallback. No model-tier routing in this change.
- Final evidence must record `accepted_suggestions`, `ignored_suggestions`, `conflicts_with_claude`, `conflicts_with_local_evidence`, and `acceptance_satisfied_by_spark`.
- For summary/benchmark aggregation across multiple reports, record: helper invocation count, total Spark calls, unique modes/stages/roles, budget modes, provisional status, strong-review required, merge authorization status, and auto-disable occurrences/reasons.

## Parallel Execution Gate

<!-- Experimental. Use only after Codex has split work into independent task cards with explicit file/module boundaries. Two compatible paths: (1) flat independent cards with positional arguments, (2) reviewed DAG plan with --plan <json>. Parallel dispatch can improve wall-clock time, but final review and merge remain serial. -->

| Field | Value |
|-------|-------|
| Parallel allowed? | no / yes |
| Parallel group id | |
| Parallel helper | ai/run-parallel-loop.sh |
| Dispatch path | flat positional / reviewed DAG plan (`--plan <json>`) |
| Reviewed plan artifact? | no / yes, path: ai/plans/.../parallel-plan.json |
| Plan produced by | not applicable / Spark parallel-planner / hand-written |
| Plan schema version | not applicable / 1 |
| Failure policy | not applicable / skip-dependents |
| Max concurrency | 2 / exact cap |
| Dependency order | independent / after task ID / blocks task ID |
| Allowed files/modules | |
| Conflict files/modules | |
| Shared API/data model touched? | yes/no |
| Shared validation resource touched? | yes/no |
| Merge strategy | serial review / accept one / merge all after review / choose best / manual reconcile |
| Review order | per-task / aggregate summary first / checker after merge |
| Stop if scope overlap detected? | yes/no |
| Stop if any dispatch fails? | yes/no |
| Required aggregate artifact | .worktrees/parallel-.../parallel-summary.md |

Parallel rules:
- Keep this gate `no` unless the task card is one member of a reviewed parallel group.
- For the flat path, use positional task-card arguments. For the DAG path, use `--plan <json>` with a reviewed schema-v1 JSON plan.
- Spark `parallel-planner` only proposes the plan — Codex/human must review and save the JSON before dispatch. Spark output is advisory and must not execute automatically.
- Schema fields: `schema_version` (must be `1`), `group_id`, `max_concurrency`, `failure_policy` (currently `skip-dependents`), and `tasks` containing `id`, `task_card`, `depends_on` per task.
- Scheduling semantics: only dependency-ready tasks start, up to the concurrency cap. With `skip-dependents`, a failed prerequisite skips transitive dependents while unrelated branches continue.
- An explicit CLI `--max-concurrency` overrides the plan's cap. All cards still require scope-gate and overlap checks.
- Do not parallelize shared API, data model, migration, security, permission, or global config changes without explicit human approval and manual reconcile plan.
- Default merge strategy is serial review: inspect each diff and evidence packet independently before merging anything.
- If the helper detects overlapping `Allowed files/modules`, stop unless the experiment explicitly allows overlap and names the manual reconcile owner.

## Context

<!-- Background, related work, constraints, links to design docs or discussions. -->

## Spec Gate

<!-- Use this for new features, UX/API changes, ambiguous requests, or work where user intent can be lost. For tiny mechanical fixes, mark "not required" with rationale. -->

| Field | Value |
|-------|-------|
| Spec required? | yes/no + rationale |
| Spec artifact | ai/specs/YYYY-MM-DD--slug.md / task card section / not required |
| Problem statement confirmed? | yes/no |
| User-visible behavior described? | yes/no/not applicable |
| Public API/data model/UX impact described? | yes/no/not applicable |
| Alternatives considered | |
| Non-goals listed | yes/no |
| Review gate before implementation | Codex accepted / human accepted / not required |
| Plan/task-card derivation path | ai/plan-to-task-cards.py output / manual task cards / not applicable |

Spec rule: do not dispatch broad or ambiguous implementation work until the spec is reviewed or explicitly waived. A spec may be short, but it must make the target behavior, non-goals, and acceptance surface concrete enough that Claude does not need to invent product direction.

## Execution Readiness Gate

<!-- Codex completes this before dispatch. If any required field is not ready, create an exploration/prototype task instead of an implementation task. -->

| Check | Ready? | Evidence / Follow-up |
|-------|--------|----------------------|
| Acceptance criteria are testable | yes/no | |
| Expected files/modules are scoped | yes/no | |
| Unknowns and decision gates are explicit | yes/no | |
| Validation commands are known or discoverable | yes/no | |
| Task is implementation-ready, not exploration-only | yes/no | |

## Unknowns

<!-- Codex uses this to reduce the information gap before Claude edits. Keep it concise and actionable. -->

| Type | Notes | Owner / Resolution |
|------|-------|--------------------|
| Known knowns | <!-- Facts already established. --> | |
| Known unknowns | <!-- Questions known before dispatch. --> | |
| Assumed knowns | <!-- Constraints obvious to the human/Codex but easy for Claude to miss. --> | |
| Unknown-unknown scan request | <!-- Blindspot pass Claude should perform before implementation. --> | |
| Questions that would change architecture | <!-- Ask before editing if the answer would change data model, API, UX, or ownership boundaries. --> | |
| Reference examples / source-of-truth files | <!-- Existing code, docs, screenshots, or specs Claude should treat as examples. --> | |
| Deviation recording path | <!-- e.g., CLAUDE_REPORT.md Deviations, implementation-notes.md, or stop-and-report. --> | |

## Root Cause Gate

<!-- Use this for bugfixes, failing tests, regressions, flaky behavior, performance problems, or repeated failed attempts. -->

| Field | Value |
|-------|-------|
| Root cause required before fix? | yes/no + rationale |
| Symptom reproduced? | yes/no/not required |
| Minimal failing case or evidence | test / log / command / trace / user report |
| Suspected root cause | |
| Root cause confidence | high/medium/low |
| Similar patterns checked | yes/no + files or query |
| Fix targets root cause, not symptom? | yes/no |
| Stop after repeated failed fixes? | yes/no; threshold: 3 attempted fixes or task-specific value |

Root cause rule: for bugfix/debugging tasks, do not guess-and-patch. Reproduce or cite the failure, identify why it happens, search for nearby pattern matches, then make the smallest fix that addresses the cause. After repeated failed fixes, stop and question the design or task framing.

## Decision Gates

<!-- Decisions that may change architecture, data model, UX, risk, or scope. Say whether Claude may decide, must choose the conservative option, or must stop and report. -->

| Decision | Why It Matters | Claude Authority | Stop Condition |
|----------|----------------|------------------|----------------|
| | | autonomous / conservative / stop-and-report | |

## Handoff Contract

<!-- Compact executor contract. This is the fastest section for Claude and reviewers to compare against. -->

| Field | Items |
|-------|-------|
| Must do | |
| Must not do | |
| May decide | |
| Must report | |
| Stop condition | |

## Acceptance Criteria

<!-- How to verify the work is complete. Be specific and testable. -->

- [ ] Criterion 1
- [ ] Criterion 2
- [ ] Criterion 3

## Testing Responsibility

<!-- Codex must decide this before dispatch. Writing test code and running tests are separate responsibilities. Prefer: Builder Claude implements without acceptance testing; after Codex accepts direction, Checker/Test Claude writes/runs tests and reports validation. If the user requested tests or Codex marks tests acceptance-critical, create a checker-test task unless a mixed exception is justified. -->

| Decision | Value |
|----------|-------|
| Test code changes are in scope? | yes/no |
| Why tests are or are not in scope | user requested / acceptance-critical / regression coverage / not needed because ... |
| Claude must write or update tests? | yes/no |
| Claude must run tests before finishing? | yes/no |
| Builder may run narrow sanity checks? | yes/no + commands |
| Broad acceptance test execution owner | Checker/Test Claude / Codex / human / not required |
| Codex/human will run verification after Claude? | yes/no |
| Acceptance evidence owner | Claude / Codex / human |
| Evidence-only redispatch allowed? | yes/no; only when task-card-required evidence cannot be reconstructed |
| No-test rationale, if applicable | |

## Test-First / TDD Contract

<!-- Use this when behavior is new, bug-prone, acceptance-critical, or user-requested tests are part of done. Keep Builder and Checker/Test split unless a mixed-exception is justified. -->

| Field | Value |
|-------|-------|
| TDD mode | required / recommended / not applicable |
| Failing test required before production change? | yes/no + rationale |
| Red evidence command/artifact | |
| Green evidence command/artifact | |
| Refactor pass allowed after green? | yes/no |
| Existing behavior preserved by tests? | yes/no/not applicable |
| Test owner | Checker/Test Claude / Codex / human |
| Production-change owner | Builder Claude / Codex control-plane / human |

TDD rule: when TDD mode is required, do not accept production edits without a failing test or equivalent failing evidence first, then a passing check after the implementation. If Builder and Checker/Test are split, Codex must stage this as separate task cards or record the explicit mixed-exception rationale.

## Validation Contract

<!-- List the exact checks expected for this task. Prefer exact task-card commands and aggregate commands such as pnpm check when available. Broad project discovery is optional because it can create unrelated noise in large or already-failing projects. -->

| Check | Command | Required? | Notes |
|-------|---------|-----------|-------|
| Local validation allowed? | yes/no | required | If no, Claude/Codex must provide commands only and must not run local checks |
| Tests | | yes/no | |
| Lint | | yes/no | |
| Type check | | yes/no | |
| Build | | yes/no | |
| Format check | | yes/no | |
| Project aggregate check | | yes/no | |

Optional exact validation command blocks for `ai/check-worktree.sh --task-card`:

```bash validation
# one command per non-comment line; each line is run as a separate check
```

Checker expectations:
- Follow `Task Mode` and `Testing Responsibility`: Builder tasks do not add tests or run broad suites; Checker/Test tasks do not skip assigned test writing or validation unless blocked and reported.
- Missing Claude report/result is evidence-gap handling: if assigned checks pass and acceptance evidence owner is not Claude, Codex may reconstruct minimal evidence instead of re-dispatching only for prose.
- Seeded or fallback reports are not valid Claude-owned reports. Reports containing `AI-CODING-WORKFLOW:DISPATCH-SEEDED-REPORT` or `AI-CODING-WORKFLOW:DISPATCH-FALLBACK-REPORT` count as missing report evidence.
- A valid Claude report must include touched files, acceptance criteria mapping, checks run or blocked, out-of-scope confirmation, and remaining risks.
- Prefer exact assigned checks with `bash ai/check-worktree.sh --no-discover --command 'label=command'` when available.
- Use broad discovery only when the task card explicitly allows it: `bash ai/check-worktree.sh --discover` or dispatcher env `CLAUDE_CODE_CHECKER_DISCOVER=1`.
- If `Local validation allowed?` is `no`, do not run local validation. Report the exact commands the reviewer should run instead.
- If Claude cannot run Python/Node/test commands because of approval or sandbox policy, record the exact blocked command and leave it for Codex/human rerun instead of treating the implementation itself as failed.
- Preserve failed command, exit code, key original output, and `file:line` locations.
- Do not weaken, delete, skip, or rewrite checks just to get a green result.

## Execution Progress

<!-- Claude updates this checklist in `CLAUDE_TASK_CARD.md` after each completed assigned item. Do not rely on it as the only evidence; it complements `CLAUDE_PROGRESS.md`, report artifacts, and diff review. -->

- [ ] Item 1
- [ ] Item 2
- [ ] Item 3

## Execution Phases

<!-- Split non-trivial work into reviewable phases. Claude Code may decompose work inside a phase, but must not merge phases unless this section explicitly allows it. If Codex dispatches only high-priority phases first, remaining implementation/test-writing phases stay Claude-owned and require follow-up task cards after review. -->

| Phase | Owner | Scope | Exit Evidence | Stop Before Next Phase? | Continuation After Accept |
|-------|-------|-------|---------------|-------------------------|---------------------------|
| A | Claude/Codex/human | <!-- e.g., tests only / implementation only / docs only --> | <!-- exact files, test output, or report update expected --> | yes/no | next Claude task / done / human decision |
| B | | | | | |
| C | | | | | |

## Delegation Continuity Gate

<!-- Codex completes this after each accepted phase. A completed high-priority subset is not permission for Codex to implement the remaining subset. -->

| Check | Value |
|-------|-------|
| Accepted phase(s) | |
| Remaining implementation/test-writing phases | |
| Next executor for remaining phases | Claude Code / Codex control-plane / human |
| If not Claude, threshold or human override cited | |

## Finish Branch Gate

<!-- Complete before claiming the branch/task is ready for human merge. This is separate from accepting one Claude phase. -->

| Check | Value |
|-------|-------|
| All accepted phases linked | |
| Required verification rerun fresh? | yes/no + commands |
| Evidence packet complete? | yes/no |
| Dirty/untracked artifacts classified? | yes/no |
| Out-of-scope changes absent or explained? | yes/no |
| Remaining risks documented? | yes/no |
| Human review/merge instructions prepared? | yes/no |

Finish rule: do not claim completion from stale evidence, seeded/fallback reports, or an accepted partial phase. Completion requires fresh verification evidence, artifact classification, and a final review-ready summary.

## Wait Policy

<!-- Used by ai/watch-claude.sh and ai/status-claude.sh to avoid both blind waiting and premature interruption. Choose small for narrow fixes, medium for ordinary feature/test work, and large for broad refactors or slow validation. -->

| Field | Value |
|-------|-------|
| Wait profile | small / medium / large |
| Startup grace seconds | |
| Stale review seconds | |
| Consider interrupt after seconds | |
| Escalation confirmations before details | default 3 |
| Monitor escalation ladder | L0 compact watch heartbeat/progress -> L1 partial diff review -> L2 status/details after repeated suspect snapshots -> L3 network/status/diff corroboration after interrupt window -> L4 kill only after corroborated no-progress |
| Partial diff review rule | Continue waiting when partial work matches the plan; consider interrupting when it is off-plan, risky, or no longer making useful progress. |
| Adaptive timeout | First loop may use a longer fixed timeout; later loops may estimate time from completed progress checklist items. |

## Files / Modules

<!-- List the files or modules expected to be modified. Include LSP/locator/CodeGraph evidence if available. -->

## Codex Context Budget

<!-- Estimated token budget Codex should spend on context gathering before dispatch. Set to 0 if LSP/locator/CodeGraph evidence is sufficient. Claude Code handles high-token reads by default. -->

| Metric | Target |
|--------|--------|
| Max Codex context tokens | |
| LSP/locator/CodeGraph queries planned | |
| Whole-file reads planned (Codex) | |

## LSP / Locator / CodeGraph Evidence

<!-- Structured low-token evidence gathered before implementation. Attach definitions, locator reports, references, callers, callees, impact analysis. Prefer this over whole-file reads. -->

| Query Type | Symbol / File | Result Summary |
|-----------|---------------|----------------|
| LSP definition | | |
| LSP references | | |
| Locator report | | |
| Codegraph callers | | |
| Codegraph impact | | |

## High-Token Delegation Gate

<!-- Codex must delegate the following to Claude Code unless explicitly approved for Codex execution. Check items that apply to this task. -->

- [ ] Reading files > 200 lines
- [ ] Multi-file implementation or refactoring
- [ ] Long log or test output analysis
- [ ] Full repository scan
- [ ] Exhaustive diff review

## Evidence Compression Requirements

<!-- Claude Code must return compressed evidence, not paste large logs or full files. Requirements for this task: -->

- Summarize test output; attach artifact paths instead of full logs
- Link to diff files instead of pasting full diffs into context
- Provide one-paragraph summaries for each changed file
- Attach paths to any generated artifacts (reports, diagnostics, logs)

## Dependencies

<!-- Other task cards, external services, data requirements, blocking decisions. -->

## Evidence

<!-- LSP/locator/CodeGraph/MCP data gathered before implementation. Attach definitions, locator reports, references, callers, callees, impact analysis. -->

## Execution Rules

<!-- Any execution constraints for Claude Code. Leave blank to use defaults from CLAUDE.md. -->

## Notes

<!-- Any additional context, open questions, or constraints for the executor. -->

## Loop Context

<!-- Fill this section when the task card is part of a revision/split/reject loop. Leave blank for first-iteration tasks. -->

- **Parent task ID:** <!-- ID of the task this derives from, or empty if first iteration -->
- **Iteration:** <!-- 1 for first iteration, increment for each revision -->
- **Prior decision:** <!-- The review decision from the previous iteration: accept/revise/split/reject -->
- **Revision instructions:** <!-- Specific instructions from the reviewer for this iteration -->
- **Claude attempts so far:** <!-- Count and short links to prior dispatch/review artifacts -->
- **Prior-session failure evidence:** <!-- Optional artifact links. Context only unless it proves the same current task hit takeover threshold. -->
- **Codex direct intervention eligible?** <!-- yes/no; if yes, cite the threshold reached and allowed edit scope -->
- **Budget / Stop conditions:** <!-- e.g., max 5 iterations, token budget, or "human stop only" -->
- **Required evidence:** <!-- Types of evidence the reviewer expects: e.g., test output, LSP diagnostics, diffstat -->

## Loop Stop Rules

<!-- Override only when this task has stricter project-specific rules. -->

- Stop on ALL GREEN.
- Stop when max iterations are reached.
- Stop when the same failure appears in two consecutive iterations.
- Stop when a fix causes a previously passing check to fail.
- Stop when failure count does not decrease for two consecutive iterations.
- Stop when blocked by external dependency, environment, permission, or unavailable service.
