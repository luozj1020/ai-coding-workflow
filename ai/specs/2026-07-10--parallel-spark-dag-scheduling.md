# Spec

## Title

parallel-spark-dag-scheduling

## Problem

The experimental parallel helper only launches a flat list behind a concurrency cap. It does not execute dependency paths, propagate prerequisite failures, or consume a reviewed schedule. Task cards contain a free-form `Dependency order` field, but the runner ignores it. Spark can suggest plan slices through `plan-splitter`, but there is no explicit contract for proposing a bounded parallel DAG.

## Desired Behavior

1. `run-codex-spark.sh --mode parallel-planner` produces an advisory scheduling proposal and never edits source or dispatches Claude. Its contract asks for strict JSON matching the reviewed schema below, followed by the standard Spark reconciliation fields.
2. `run-parallel-loop.sh --plan <json>` accepts a Codex-reviewed JSON plan in addition to the existing positional-card interface.
3. Plan schema version 1:

   ```json
   {
     "schema_version": 1,
     "group_id": "group-slug",
     "max_concurrency": 2,
     "failure_policy": "skip-dependents",
     "tasks": [
       {"id": "task-a", "task_card": "path/to/a.md", "depends_on": []},
       {"id": "task-b", "task_card": "path/to/b.md", "depends_on": ["task-a"]}
     ]
   }
   ```

4. The validator rejects unknown schema versions, unknown keys, invalid IDs, duplicate IDs/cards, missing task cards, unknown dependencies, self-dependencies, cycles, invalid concurrency, and unsupported failure policies before any dispatch starts.
5. Task-card paths are resolved relative to the plan file. The plan's concurrency cap is used unless an explicit CLI `--max-concurrency` override is supplied.
6. Scheduling starts only dependency-ready tasks, up to the concurrency cap. With `skip-dependents`, a failed task prevents all transitive dependents from dispatching while unrelated branches continue.
7. Summary/events/manifest evidence records task IDs, dependencies, dispatched/completed/skipped states, failure reasons, plan path, and effective concurrency.
8. Existing positional invocation remains a flat, independent group with current behavior.
9. Scope-gate and overlap checks remain mandatory for every scheduled card; no automatic merge is introduced.

## Non-Goals

- Spark output is not executed directly. A human/Codex must save and review the JSON plan before dispatch.
- No automatic worktree merge, conflict resolution, shared-API parallelization, or speculative execution.
- No arbitrary natural-language parsing of `Dependency order`.
- No dynamic graph mutation after dispatch starts.
- No destructive cancellation of already-running Claude processes.

## Acceptance Surface

<!-- Testable criteria, commands, screenshots, traces, or reviewer checks that prove the behavior. -->

- [ ] Spark recognizes `parallel-planner`; help/prompt/report record the requested and resolved mode correctly.
- [ ] A valid fork/join plan runs prerequisites before dependents while independent ready tasks may overlap.
- [ ] Failed prerequisites produce skipped downstream evidence and do not block unrelated branches under `skip-dependents`.
- [ ] Invalid and cyclic plans fail before the fake dispatcher is invoked.
- [ ] Existing flat positional tests remain green.
- [ ] `python -m pytest -q tests/test_run_codex_spark.py tests/test_run_parallel_loop.py tests/test_validate_parallel_plan.py` passes.
- [ ] `bash -n scripts/run-codex-spark.sh scripts/run-parallel-loop.sh` passes.

## Constraints

- Standard-library Python only for JSON validation; no `jq` or new dependency.
- Strict parsing; never evaluate Spark text or shell fragments.
- Builder and Checker/Test responsibilities remain separate.
- Tracked source files live under `scripts/`, `assets/`, `tests/`, root docs, and `SKILL.md`. Generated `ai/` copies in the user's source worktree are not edited during Builder phases.
- Review and merge remain serial even when dispatch runs concurrently.

## Alternatives Considered

1. Parse the existing free-form `Dependency order` field: rejected because natural language is ambiguous and unsafe for execution.
2. Add task IDs/dependencies only to Markdown cards: workable, but makes a group-level cap/policy and global validation harder to review.
3. Reviewed JSON plan plus task-card safety gates: chosen because it is bounded, versioned, strictly validated, and easy for Spark to propose without granting it execution authority.

## Risks and Unknowns

- Exact event names and normalized validator output are implementation details, but tests must assert stable state semantics rather than timing-sensitive ordering.
- `stop-new` is intentionally deferred; schema version 1 supports only `skip-dependents` to avoid ambiguous cancellation semantics.
- Hierarchical path-overlap detection is outside this phase; existing explicit scope tokens remain enforced.

## Plan Derivation

<!-- Link generated plan/task-card artifacts when available. -->

| Artifact | Path |
|----------|------|
| Plan | This spec plus Builder task cards under `task-cards/` |
| Task cards | `task-cards/parallel-spark-planner.md`, `task-cards/parallel-dag-runner.md`, then checker/integration cards |
