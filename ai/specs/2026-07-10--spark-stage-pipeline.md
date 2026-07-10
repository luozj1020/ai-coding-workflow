# Spec

## Title

spark-stage-pipeline

## Problem

Spark has many narrowly named modes, but `auto` resolves each invocation to exactly one role. The main Codex model therefore still rereads raw task cards, diffs, and evidence to repeat mechanical synthesis. Spark's separate quota remains underused while strong-model context is spent on task-card drafting, context compression, direction prechecks, acceptance mapping, and revision drafting.

## Desired Behavior

1. Add explicit read-only modes:
   - `observe-synthesizer`
   - `task-card-drafter`
   - `context-packet-builder`
   - `preflight-bundle`
   - `direction-precheck`
   - `acceptance-matrix`
   - `postflight-bundle`
   - `revision-drafter`
   - `lesson-extractor`
2. `preflight-bundle` performs risk classification, bounded evidence synthesis, task-card drafting, Context Packet drafting, unknown/risk extraction, and split/parallel recommendations in one Spark invocation.
3. `postflight-bundle` performs task-card-to-diff direction checking, boundary/omission checks, acceptance-criteria mapping, evidence conflict detection, validation recommendations, and a provisional `accept/revise/split/escalate` recommendation in one invocation.
4. All bundle outputs use a stable compressed handoff with these headings: `Decision Summary`, `Risk Flags`, `Scope and Boundaries`, `Acceptance Matrix`, `Evidence Conflicts`, `Required Codex Decisions`, and `Recommended Next Action`.
5. `auto` becomes stage-aware:
   - no artifacts and ordinary Builder planning -> `preflight-bundle` in aggressive/balanced modes;
   - diff or successful report/checker artifacts -> `postflight-bundle` in aggressive mode;
   - failure/no-report artifacts -> `failure-triage` with revision-drafter responsibilities;
   - conservative mode preserves the existing narrow single-role routing;
   - explicit `--mode` always wins.
6. Add `AI_SPARK_BUDGET_MODE=aggressive|balanced|conservative`, default `balanced`, and record requested/effective budget mode in reports.
7. Report stage, roles executed, bounded input artifacts, recommended next action, provisional acceptance, strong review requirement, and Spark call count for this helper invocation. Spark still cannot authorize merge or independently provide final acceptance.
8. Update loop summaries and benchmarks to preserve the new stage/budget/role/provisional fields when artifacts are available.
9. Update task-card, workflow rules, installed README, and root English/Chinese documentation. Generated `ai/` copies in the source worktree remain installer outputs rather than hand-edited source.

## Non-Goals

- No Sol/Terra/Luna routing or model-selection hierarchy.
- No automatic quota-percentage retrieval or attempt to equalize progress bars exactly.
- No automatic merge or Spark-owned final acceptance.
- No expansion of source-edit scope beyond the existing `micro-builder` contract in this phase.
- No multi-call daemon or automatic invocation across process boundaries; the workflow invokes preflight before Builder and postflight after evidence exists.
- No silent fallback to a stronger model.

## Acceptance Surface

<!-- Testable criteria, commands, screenshots, traces, or reviewer checks that prove the behavior. -->

- [ ] Every new mode is accepted by the CLI and has a precise prompt contract.
- [ ] `auto` routes fixture task cards/artifacts to the expected stage for all three budget modes.
- [ ] Explicit modes override stage/budget inference.
- [ ] Bundle prompts contain all required compressed-handoff headings and preserve advisory-only ownership.
- [ ] Reports expose budget mode, pipeline stage, roles, provisional acceptance, strong-review requirement, and call count.
- [ ] Existing Spark auto-disable, micro-builder, failure-triage, parallel-planner, and old-mode tests remain green.
- [ ] Summarizer/benchmark tests verify new fields without breaking older artifact compatibility.
- [ ] Installer/task-card/docs tests and focused Spark pipeline tests pass.

## Constraints

- Default remains advisory and no stronger-model fallback is allowed.
- Bundle roles share one bounded prompt per stage to avoid repeated context transfer.
- Artifact excerpts retain the existing line cap.
- Old explicit mode names and report fields remain compatible.
- `conservative` preserves old `auto` behavior as closely as possible.
- `aggressive` should normally produce two workflow invocations per task: preflight and postflight, with failure/revision as a conditional third invocation.

## Alternatives Considered

1. One Spark call per atomic role: rejected because repeated prompt/context overhead may erase quota and latency benefits.
2. Two composite stage bundles plus explicit atomic modes: chosen because it increases Spark work while keeping evidence bounded and roles inspectable.
3. Let Spark fully accept low-risk tasks immediately: deferred until benchmark data shows a low overturn and regression rate.

## Risks and Unknowns

- The CLI does not expose a stable automatic quota-percentage API, so budget mode is manual/environment-driven.
- `balanced` postflight policy should remain conservative in v1: run postflight only when explicitly invoked or when evidence artifacts make the stage unambiguous.
- Provisional acceptance is a recommendation only; `strong_review_required` remains `yes` or `batch`, never merge authorization.

## Plan Derivation

<!-- Link generated plan/task-card artifacts when available. -->

| Artifact | Path |
|----------|------|
| Plan | This spec |
| Task cards | Builder runtime/metrics card, documentation card, then Checker/Test card |
