# Spec

## Title

Large Repository Execution Channel Optimization

## Problem

In large repositories, dispatch overhead can exceed the scoped edit time. Fresh worktrees materialize many files, managed reuse still performs repository-wide reset/clean/status work, and a retry after acknowledgement-only/no-progress cannot safely reuse the exact clean worktree. Monitor helpers also infer the worktree from the task id, so managed reuse reports the wrong path. Claude may exit while the dispatcher appears active, and mechanical Builder tasks spend their short useful window repeating planning already encoded by Codex.

## Desired Behavior

- Every dispatch writes a machine-readable runtime identity artifact containing the actual worktree, task id, strategy, base commit, branch, and role PID artifact paths. Status/watch resolve live files through this artifact and remain backward compatible.
- A completed Claude child transitions immediately to dispatcher finalization; monitor output distinguishes the Claude child, checker, and dispatcher states.
- An explicitly requested retry may reuse the exact prior task worktree only when identity, repository, base commit/HEAD, clean implementation state, and absence of live role processes are proven. No reset or clean is run on this path.
- Mechanical Builder tasks may opt into `execution-only`, which renders a minimal execution projection and requires substantive progress (source diff, valid progress update, valid report/blocker) within a configurable deadline.
- Large-repository shortcuts are risk-gated and evidence-labeled. Targeted diagnostics never claim whole-worktree cleanliness.
- Doctor verifies documented helpers and offers preview-only diagnostics for possible index/worktree hash disagreement.

## Non-Goals

- No automatic deletion, reset, clean, staging, renormalization, merge, or migration.
- No unconditional managed worktree reuse or fast-large-repo routing.
- No replacement of Claude Builder ownership by Spark or Codex for multi-file implementation.
- No claim of global cleanliness from target-file-only checks.

## Acceptance Surface

<!-- Testable criteria, commands, screenshots, traces, or reviewer checks that prove the behavior. -->

- [ ] Managed and fresh dispatch tests prove status/watch use the recorded actual worktree path.
- [ ] Child-exit tests prove finalization starts without waiting for the watchdog/no-output timeout.
- [ ] Retry-in-place tests cover safe reuse and rejection for dirty, stale, mismatched, or live runs.
- [ ] Execution-only tests prove minimal card rendering and first-substantive-progress timeout behavior.
- [ ] Doctor tests cover missing documented helpers and preview-only hash mismatch guidance.
- [ ] Existing dispatcher, monitor, installer, doctor, Spark, and parallel-loop suites remain green.

## Constraints

- Shell helpers must remain usable on Linux, WSL, and Git for Windows.
- Runtime identity files must be simple text/JSON artifacts and tolerate older runs where they are absent.
- Retry-in-place is opt-in and fail-closed; ambiguity falls back to a new dispatch or an actionable error.
- Hash diagnostics are bounded to explicit target files and never mutate the index.
- Local validation follows each task card; no broad discovery by default.

## Alternatives Considered

- Always reuse `.worktrees/reuse/claude-managed`: rejected because unrelated or stale evidence can be destroyed and concurrent tasks can collide.
- Always let Codex edit mechanical tasks: rejected because multi-file execution remains Claude-owned; instead execution-only removes duplicated planning overhead.
- Trust PID or `git status` alone: rejected because wrapper/child roles differ and mounted filesystems may expose stat-cache anomalies; use role identities plus bounded corroboration.

## Risks and Unknowns

- Exact Claude CLI process-tree behavior varies by platform; tests should use controlled fake processes.
- A source diff is not the only valid progress signal, so the deadline must accept explicit progress/report/blocker evidence.
- Mounted-filesystem hash disagreement is expensive to diagnose broadly; only explicit file lists are safe for automatic diagnostics.

## Plan Derivation

<!-- Link generated plan/task-card artifacts when available. -->

| Artifact | Path |
|----------|------|
| Plan | Codex runtime plan for this session |
| Task cards | `ai/plans/large-repo-execution-channel/task-cards/` |
