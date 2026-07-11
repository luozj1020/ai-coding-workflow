# Dispatch Latency and Evidence Convergence Plan

Spec: `ai/specs/2026-07-11--dispatch-latency-and-evidence-convergence.md`

## Task 1: Dispatcher convergence and Checker reuse

Implement conservative task-mode/risk parsing, eligible Checker `reuse-managed` defaulting, quiet worktree creation, and stable approval-blocked early convergence in `scripts/dispatch-to-claude.sh` only.

## Task 2: Authoritative validation aggregation

Preserve Claude validation provenance while computing checker-authoritative final validation status in `scripts/summarize-loop-run.py` only.

## Task 3: Preview-only runtime inventory

Add `.worktrees` count/size/age cleanup guidance to `scripts/doctor_workflow.py` only; never delete.

## Task 4: Process-role monitoring

After Task 1 direction is accepted, update dispatcher PID artifacts plus `scripts/status-claude.sh` and `scripts/watch-claude.sh` so dispatcher/Claude/checker states are distinct and plain watch always emits terminal machine fields.

## Task 5: Checker tests and propagation

Checker/Test tasks add focused tests, installer assertions, templates/docs, then run the bounded regression suite.

