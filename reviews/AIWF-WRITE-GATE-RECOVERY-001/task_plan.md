# Task Plan

## Task ID

AIWF-WRITE-GATE-RECOVERY-001

## Goal

Recover from the rejected broad implementation through narrow, independently
validated slices while preserving exact write-scope enforcement.

## Status

planned

## Phases

| Phase | Status | Owner | Acceptance Check | Notes |
|-------|--------|-------|------------------|-------|
| 1 | complete | Codex | Session environment and exact writer runtime tests pass | Control-plane prerequisite completed locally |
| 2 | planned | Original Builder | One responsibility and exact product paths | Do not reuse the rejected seven-file delta |
| 3 | planned | Original Builder or Checker/Test | Syntax/import and narrow tests pass per changed test file | Stop immediately on the first malformed file |
| 4 | planned | Codex | Scope, diff, report, and validation evidence agree | Human retains merge authority |

## Task Sections

### Task 1: Re-freeze the minimal product slice

Goal: Recover the smallest independently useful responsibility from the
original product task.

Files/modules: Populate only from the original frozen task card; the external
seven-file path list is unavailable and must not be guessed.

Must do: Declare exact write paths, source-of-truth symbols, one responsibility,
and narrow acceptance.

Must not do: Reuse the rejected diff, broaden paths, rewrite unrelated fixtures,
or combine implementation and broad test work.

Acceptance criteria: Codex confirms the slice is context-sufficient and every
write path is necessary.

Validation: Task-card lint plus Spark task-card audit when available.

### Task 2: Implement one narrow responsibility

Goal: Produce only the source delta for Task 1.

Files/modules: The exact paths frozen by Task 1.

Must do: Use complete-file or unique-fragment receipt-validated writes; keep
the parent directories read-only.

Must not do: Add tests unless explicitly assigned, introduce scratch files in
the repository, or touch a second responsibility.

Acceptance criteria: Scope audit passes and the diff contains no unrelated
rewrites.

Validation: Syntax/import or the smallest deterministic implementation check.

### Task 3: Add or repair tests as a separate slice

Goal: Validate the accepted implementation direction without recreating the
reported line-73 syntax failure.

Files/modules: Exact test paths must be frozen after Task 2 review.

Must do: Run syntax/import validation immediately after each test-file write,
then run that single test file.

Must not do: Write another test file while the current file is syntactically
invalid or perform broad implementation rewrites.

Acceptance criteria: Every changed test file parses/imports and its narrow test
command has a receipt.

Validation: Per-file syntax/import followed by per-file narrow test execution.

### Task 4: Perform bounded semantic review

Goal: Decide accept, revise, split, or reject from immutable evidence.

Files/modules: Builder diff, report, scope receipt, and validation receipts.

Must do: Compare acceptance claims with actual assertions and confirm no
rejected external delta entered the baseline.

Must not do: Treat model reports as facts or authorize an automatic merge.

Acceptance criteria: Review decision is structured and evidence paths resolve.

Validation: `review-decision-v1` schema validation and final deterministic checks.

## Decisions

| Time | Decision | Rationale |
|------|----------|-----------|
| 2026-08-03 | Reject the reported seven-file implementation | User-reported unrelated rewrites and a syntax error make it unsafe to salvage as an accepted baseline |
| 2026-08-03 | Split recovery by responsibility and test ownership | Keeps scope and validation failures local and attributable |

## Errors Encountered

| Time | Error / Failed Action | Evidence | Resolution |
|------|------------------------|----------|------------|
| 2026-08-03 | External diff and exact test path unavailable locally | `evidence-boundary.json` | Preserve reported facts with provenance; leave hashes and paths unknown |

## Completion Gate

| Field | Value |
|-------|-------|
| Enabled | no |
| Required complete phases | 2, 3, 4 |
| Required checks | exact scope, per-file syntax/import, narrow tests, structured Codex review |
