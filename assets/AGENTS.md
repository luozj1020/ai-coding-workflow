# Agents

This file defines shared rules for AI agents working in this repository.

<!-- AI-CODING-WORKFLOW:BEGIN managed -->
## Core Principle

**Codex designs and reviews. Claude edits. Tools gather low-token evidence first.**

- Codex/GPT plans, gathers low-token evidence, writes task cards, and reviews results.
- Claude Code implements scoped edits in isolated worktrees and returns compressed evidence.
- LSP, `ai/locate-code.py`, bounded CodeGraph, and MCP are preferred before broad reads or repository scans. In large repositories, locator backend order is Zoekt when indexed, Sourcegraph when configured, lexical `rg`/`git grep`, then bounded CodeGraph only for concrete symbols.
- For local repository work, do not use web search unless the user explicitly asks for internet lookup, remote repository state, external documentation, or current third-party facts. Spark, Claude, CodeGraph, or filesystem failures are not reasons to search the web by default.

## Workflow

Use the explicit loop: OBSERVE -> PLAN -> DISPATCH -> EXECUTE -> VERIFY -> REVIEW -> LEARN.

- PLAN with `ai/task-card-template.md`.
- For ambiguous product/API/UX changes, create or cite a spec with `ai/init-spec.py` and fill `Spec Gate` before implementation dispatch.
- For multi-step plans, use `ai/plan-to-task-cards.py` or manual equivalents to derive small task cards from reviewed plan sections.
- For bounded loops, fill `Goal Loop Contract` with success signal, max attempts, stop rules, required evidence, budget, and benchmark tags.
- For tasks needing stronger strategic judgment, fill `Advisor Gate`: advisor role/model, timing, call cap, output budget, result visibility, conflict reconciliation, fallback behavior, and evidence artifact.
- Leave `Codex Spark Gate` at `auto` by default for eligible low-latency `gpt-5.3-codex-spark` auxiliary work: task-size classification, task-card audit, plan splitting, validation planning, failure triage, review-only, evidence-checker, or explicitly authorized tiny isolated micro-builder work. Because Spark quota is separate and cheaper than strong Codex/Claude context, prefer Spark for uncertain task-size routing before spending stronger-model tokens, and prefer an explicit Spark mode when the support role is already known. If Spark is unavailable or quota-exhausted, auto-disable it for that run and continue the main workflow. Do not use Spark as an implicit Claude replacement, acceptance substitute, or strong-model fallback.
- Dispatch defaults to `CLAUDE_CODE_EXECUTION_PROFILE=balanced`: compact Claude card, brief prompt, fresh worktree, and full diff evidence. Use `safe` for ambiguous/high-risk work that needs the standard prompt and non-compact card.
- For large repositories or slow filesystems, fill `Worktree / Large Repo Strategy Gate` before dispatch. Default to complete evidence; use `fast-large-repo`, `reuse-managed`, `CLAUDE_CODE_LARGE_REPO_MODE=1`, or `CLAUDE_CODE_EVIDENCE_MODE=summary` only as explicit performance tradeoffs. Do not reset an existing `.worktrees/reuse/claude-managed` unless `CLAUDE_CODE_REUSE_WORKTREE_RESET=1` is explicit after prior evidence is preserved or reviewed. After an interrupted dispatch, prefer `python ai/clean_runtime.py --task-id claude-...` to inspect or remove only that run's stopped artifacts.
- For large repositories, run `python ai/locate-code.py "symbol or behavior" --path <area> --max-files 12` before dispatch when target files are unclear. Fill `Claude Context Packet` with locator output, target files/modules, relevant symbols, source-of-truth examples, paths Claude must not read/modify, known constraints, and narrow validation commands. Claude should use this packet before broad repository search.
- Bootstrap should keep `.worktrees/*` ignored while preserving `.worktrees/.gitkeep`; if doctor reports missing ignore rules, rerun the installer or add the rules before dispatching large tasks.
- Experimental: fill `Parallel Execution Gate` and use `ai/run-parallel-loop.sh` only for independent task cards with non-overlapping file/module scopes. Parallel dispatch does not change serial review and human merge requirements.
- DISPATCH with `ai/dispatch-to-claude.sh` or `ai/run-loop.sh`; dispatch preserves the full Codex task card and renders a smaller Claude execution card.
- Split risky work into Builder Claude (implementation only) followed by Checker/Test Claude (tests, validation, report) after Codex accepts the implementation direction.
- Before dispatch, check for mixed builder/checker responsibilities, dirty-source risk, permission/tool approval risk, long-running validation, and missing progress-artifact requirements so a later stall is not misdiagnosed as Claude execution failure.
- REVIEW with `ai/review-with-codex.sh` or loop output.
- Do not merge automatically; human review and merge remain separate.

## Codex Intervention Policy

After a Claude execution round, Codex normally reviews only. It may edit directly only when Claude has repeatedly missed the target, the loop hit a stop condition, or Claude is unavailable. Record the failed attempts, intervention reason, edit scope, and validation evidence.

Claude no-progress, early exit, invalid result, or one failed attempt is evidence for a tighter next task card, not permission for Codex to patch. Prefer smaller Claude revisions with clearer acceptance criteria before takeover.

Dirty source or stale HEAD is a delegation blocker, not a Codex takeover trigger. Codex should first restore a reliable Claude base by committing an accepted phase, stashing/patching uncommitted source changes, refreshing workflow files, re-dispatching from updated HEAD, requesting an explicit dirty-source override, or stopping for human input. Codex may take over only when restoration is impossible/unsafe and a current-task threshold or explicit human override is recorded.

Prior-session Claude failures are carry-forward context, not automatic takeover permission. A fresh task or session should re-dispatch Claude unless current-task artifacts prove the same threshold or the human explicitly asks Codex to take over.

Missing Claude `result.json`, `CLAUDE_REPORT.md`, or acceptance evidence is an evidence gap, not automatically an implementation failure. If the diff matches the task card and assigned checks pass, Codex should reconstruct a minimal evidence packet from artifacts, diff, and verification output instead of re-dispatching only to get prose. Re-dispatch Claude when the task card explicitly assigned test writing, test execution, or acceptance evidence to Claude and that evidence cannot be recovered.

Seeded and fallback reports are not valid Claude-owned reports. A `CLAUDE_REPORT.md` containing `AI-CODING-WORKFLOW:DISPATCH-SEEDED-REPORT` or an archived report containing `AI-CODING-WORKFLOW:DISPATCH-FALLBACK-REPORT` must be classified as `seeded report only` or `no valid report`, not completion.

If a tightened second Claude task also exits without result/report and without useful progress, treat it as current-task repeated failure. A Codex takeover must cite both attempts, salvage any reviewer-accepted first-round direction, avoid broad rewrites, and add only the missing implementation, acceptance tests, and evidence needed to satisfy the task card.

If one Builder attempt exits after acknowledgement with no code diff and no valid Claude-owned report, tighten and re-dispatch once. If the tightened Builder attempt again exits after acknowledgement with no code diff and no valid report, Codex may perform scoped takeover after recording both attempt artifacts.

If the first attempt produced a useful scoped diff but no valid report/evidence, Codex may accept that direction only after running the assigned narrow checks. If a tightened retry produces no useful progress, Codex may salvage the accepted direction in scoped takeover.

For multi-phase or multi-part tasks, accepting one Claude round closes only that phase. Codex may reprioritize or rewrite the next task card, but remaining implementation/test-writing phases stay Claude-owned unless a current-task takeover threshold is reached or the human explicitly asks Codex to take over.

## Context Lifecycle

Keep default context small and file-backed:

- Codex task cards should expose material unknowns and decision gates before dispatch.
- Specs should make desired behavior, non-goals, acceptance surface, constraints, alternatives, and risks explicit before broad execution. A waived spec needs a short rationale.
- Use task-card unknowns to reduce information gaps: known unknowns, assumed knowns, blindspot scan requests, architecture-changing questions, reference examples, and deviation recording paths.
- In large repositories, use `python ai/locate-code.py "symbol or behavior" --path <area> --max-files 12` as the default low-token locator. Prefer Zoekt when indexed, Sourcegraph when configured, then lexical search. Ask CodeGraph only for concrete files, symbols, or call paths with a short timeout. If CodeGraph times out once, record the timeout as context evidence and continue with locator output plus targeted line reads instead of retrying broad graph queries.
- Do not request advisor guidance with no task context. Prefer read-only orientation first, then advisor consultation before state-changing edits when the task card requires it.
- Treat Codex Spark evidence as auxiliary: store `codex-spark.report.md` artifacts, record auto-disable reasons when Spark is unavailable, including local helper initialization failures, reconcile conflicts with Claude/local evidence, and require explicit human approval before any strong-model fallback.
- Treat parallel dispatch summaries as orchestration evidence only. Review each diff and evidence packet serially before merging; overlap or shared API changes require a manual reconcile task.
- Codex should complete the Execution Readiness Gate and Handoff Contract before implementation dispatch.
- Codex should fill the Phase Responsibility Matrix so each phase has a clear owner and explicit non-owner duties.
- Store long-lived state in `.worktrees/` artifacts or `ai/plans/<task-id>/`.
- Preserve failures, checker reports, progress logs, and review decisions as artifact paths.
- Use `CLAUDE_PROGRESS.md` for Goal, Current Phase, Next Check, Blocker, and Last Update.
- Use optional `*.network.log` metadata only when `CLAUDE_CODE_NETWORK_MONITOR=1`; it may support network/proxy/auth attribution but does not expose request contents and is not implementation evidence.
- Use `loop-events.jsonl` for append-only loop orchestration when available.
- If Claude is quiet but the implementation worktree is changing, review the partial diff against the plan before interrupting.
- Claude reports should record unknowns resolved, new unknowns discovered, and deviations from plan.
- Codex reviews should produce next-task-ready instructions for revise, split, or reject decisions.

## Loop Engineering Validation Contract

Builder and checker responsibilities must remain separate:

- Use `Spec Gate` before broad or ambiguous implementation. Do not let Claude invent product direction when a short reviewed spec would make the target behavior concrete.
- For bugfixes, regressions, failing tests, and repeated failed attempts, fill `Root Cause Gate`: reproduce or cite the symptom, identify the likely cause, check similar patterns, and stop after repeated failed fixes instead of guess-and-patch.
- Use `Small Change Fast Path Gate` before dispatch for tiny low-risk edits. Codex may edit directly only when the change is local, expected to touch no more than two small files, needs no broad context, has no public API/data/security/migration/permission/concurrency/cross-module contract risk, and has narrow validation or an explicit validation-skip reason. If task size is unclear, prefer Spark `task-size-classifier` before spending stronger-model tokens. If scope expands or uncertainty appears, stop fast path and return to task-card + Claude dispatch.
- Use `Test-First / TDD Contract` when tests are user-requested, acceptance-critical, or the change is bug-prone. Required TDD needs red evidence before production edits and green evidence after implementation.
- Builder Claude implements scoped changes and reports direction. Builder tasks do not write acceptance tests or run broad suites unless the task card explicitly allows narrow sanity checks.
- A single Claude task that mixes implementation, test writing, broad validation, and phase stop gates must be split unless the task card explicitly marks `mixed-exception` and explains why one combined pass is intentional.
- Before editing, Claude performs Direction / Boundary Acknowledgement when requested. It must state understanding, scope, out-of-scope boundaries, likely files, acceptance interpretation, testing responsibility, confusion, risks, and proceed/narrow/split/stop recommendation.
- Use blocking Codex approval for ambiguous, multi-file, high-risk, public API, data model, security, migration, permission, or production-impacting work. If Claude has material confusion, it must stop-and-report instead of guessing.
- If acknowledgement is non-blocking and Claude recommends `proceed`, Claude must continue implementation in the same run. It must not stop after acknowledgement unless it records a concrete blocker, stop condition, or explicit approval need.
- Avoid acknowledgement loops: one blocking acknowledgement per task or phase unless Codex materially changes goal, scope, boundaries, or risk. Codex must decide proceed, narrow-once/re-dispatch, split, or stop; Claude must not ask for the same approval again after proceed.
- Treat `acknowledgement only` as no implementation progress: no code diff, no valid Claude-owned report, and only acknowledgement/proceed text.
- Codex reviews builder direction before validation work: wait when the partial diff matches the plan, interrupt and narrow when it runs off-plan, and take over only after the current-task threshold is met.
- When Claude appears stuck, first attribute the stall: task-card ambiguity, mixed-role assignment, dirty source/stale HEAD, permission/tool approval blocker, long-running validation, missing progress updates, external environment, or true Claude no-progress. Use the monitoring escalation ladder: L0 compact watch heartbeat/progress, L1 partial diff review, L2 status/details after repeated suspect snapshots, L3 network/status/diff corroboration after the interrupt window, and L4 kill only after multiple evidence sources agree useful progress is unlikely.
- Prefer machine-readable monitor fields from `ai/watch-claude.sh` and `ai/status-claude.sh` (`monitor_level`, `action`, `evidence_state`, quiet/elapsed seconds, suspect count) before reading full progress, status, or network tails.
- When network diagnostics are enabled, inspect socket summary and healthcheck status before attributing a quiet run to Claude no-progress.
- Treat advisor guidance as high-value input, not a command. If advisor guidance conflicts with local evidence, record the conflict and reconcile before changing direction. If advisor output is redacted or unavailable to Codex, report advice category, whether it was followed, stop reason/truncation signals, and any fallback used.
- Use `Codex Spark Gate` as default-on optional support only when it reduces strong-model quota or latency without weakening ownership. Prefer an explicit Spark mode when the support role is known; default `--mode auto` is for low-risk routing and resolves to `task-size-classifier` before normal dispatch, `validation-planner` before Checker/Test tasks, `failure-triage` after failed/no-report artifacts, `review-only` for diff review, and `evidence-checker` for report/evidence review. When `auto` resolves to `task-size-classifier`, the helper runs Codex from the Spark artifact directory with `workspace-write` sandbox so local helper initialization has a writable working directory without granting write access to the source repository. Prefer these read-only modes, and pass explicit `--artifact` inputs for evidence/failure work. Use `micro-builder` only when the task card explicitly authorizes Spark source edits, limits scope to one or two small files, rules out public API/data/security/migration/permission/concurrency/cross-module contract risk, names exact narrow validation, and runs in a helper-created isolated worktree with `--sandbox workspace-write`. Missing CLI, model access, auth/network issues, local helper initialization failures, or Spark quota exhaustion should auto-disable Spark for the run unless `--require-spark` was explicitly used. Spark must not silently fall back to GPT-5.5 or another stronger model, cannot replace Claude Builder ownership, cannot approve final review, and cannot independently satisfy acceptance criteria. Record task-size classification, routing recommendation, `accepted_suggestions`, `ignored_suggestions`, `conflicts_with_claude`, `conflicts_with_local_evidence`, and `acceptance_satisfied_by_spark` in the final evidence.
- Use `Worktree / Large Repo Strategy Gate` when `git worktree add`, filesystem reads, dispatcher status collection, or Claude-side read operations are materially slow. `CLAUDE_CODE_WORKTREE_STRATEGY=reuse-managed` may reuse only `.worktrees/reuse/claude-managed`; `CLAUDE_CODE_REUSE_WORKTREE_RESET=1` resets and cleans only that managed worktree, never the source repository. `CLAUDE_CODE_LARGE_REPO_MODE=1` reduces expensive untracked scans and untracked patch evidence, so record the evidence tradeoff before relying on it.
- Use `Claude Context Packet` to keep Claude execution narrow in large repositories. Seed it with `locate-code.py` output when file ownership is unclear. If the packet is incomplete and Claude would need broad search, it should stop-and-report or ask for a narrower packet instead of scanning the repository by default.
- Use `Parallel Execution Gate` only for experimental wall-clock reduction after Codex has split the work into independent task cards. Do not parallelize shared API, data model, migration, security, permission, or global config work unless human-approved with a manual reconcile plan.
- When dirty source or stale HEAD is the attribution, fill the Delegation Restoration Gate and try to restore a clean updated dispatch base before considering takeover.
- Checker/Test Claude writes or updates assigned tests, runs assigned validation, and reports results. Checker/Test tasks should not perform broad implementation rewrites; only concrete small fixes allowed by the task card are permitted.
- Use exact task-card validation commands before broad discovery. Prefer `ai/check-worktree.sh --no-discover --command 'label=command'`; enable broad checker discovery only when the task card or human explicitly asks for it. If `Local validation allowed?` is `no`, checker status means artifact collection only and validation must be reported as skipped by policy, not as tests passing. If Claude cannot run a Python/Node/test command due to approval or sandbox policy, record the blocker and let Codex/human rerun the exact command rather than marking the implementation failed.
- Respect `Local validation allowed?`. If it is `no`, Claude/Codex must not run local checks; provide exact commands and risks for the human or CI instead. Markdown fenced command blocks whose info string contains `validation` or `check` are allowed command sources for checker helper extraction.
- Forward checker failures with command, exit code, key output, and `file:line` details.
- Before claiming work ready for merge, fill `Finish Branch Gate`: link accepted phases, rerun required verification fresh, classify dirty/untracked artifacts, document remaining risks, and prepare human review/merge instructions.

Stop when work is accepted, max iterations are reached, the same failure repeats, a fix regresses prior behavior, failure count stops improving, or the blocker is external.

Use `ai/benchmark-loop-runs.py` to compare multiple loop runs as a lightweight living benchmark across quality, speed, dispatch stage timings, cost, stability, loop type, benchmark tags, advisor usage, Spark usage, Spark task-size classification/routing/confidence, Spark auto-disable/fallback status, Claude evidence classification, parallel-dispatch usage, spec adherence, root-cause evidence, and TDD usage.

## Token Budget and Delegation Contract

Codex should stay on low-token evidence. Delegate broad reads, long logs, exhaustive scans, and multi-file implementation to Claude Code by default.

Small low-risk edits may stay Codex-owned through the Small Change Fast Path. Record why Claude was not dispatched, files touched, validation evidence, and the condition that would have escalated to Claude.

Task cards must separate test-code scope from test-execution responsibility. Claude may write tests when the user asks for test coverage or Codex marks tests acceptance-critical; otherwise say whether Claude only changes implementation, runs tests, or leaves verification to Codex/humans.

Codex-only planning fields, context budgets, delegation gates, and control-plane continuity fields belong in the full task card. Claude should receive the generated execution card containing only the current execution contract and necessary evidence.

Builder Claude should not add tests or run broad acceptance suites unless explicitly assigned a narrow sanity check. Checker/Test Claude should write or update tests, run assigned validation, and avoid broad implementation rewrites. Claude must update `CLAUDE_PROGRESS.md` and any `CLAUDE_TASK_CARD.md` progress checklist after each completed item.

For repeated dispatches, commit or otherwise restore prior task-card artifacts before re-dispatch. Only the current task card may be exempt from dirty-source checks; previous untracked task cards are delegation blockers unless explicitly treated as approved control-plane artifacts.

### Evidence compression

Claude must return compressed evidence: summaries and artifact paths, not pasted large logs, full diffs, or whole-file dumps. Include changed-file summaries, check pass/fail counts, and generated report paths.

## Persistent Planning Files

For long tasks, use `ai/plans/<task-id>/`:

- `task_plan.md` for phases and decisions.
- `findings.md` for durable context evidence.
- `progress.md` for current state and validation.
- `resume-context.md` generated by `ai/session-catchup.py`.

## Safety Rules

Explicit human approval is required for destructive commands, file deletion, migrations, auth/permission changes, billing/payment changes, deployment/infrastructure changes, public API changes, secrets, and production data changes.

When in doubt, stop and ask.
<!-- AI-CODING-WORKFLOW:END managed -->

## Project-specific rules

Add project-specific rules here. This section is user-owned and should not be overwritten by the workflow installer.
