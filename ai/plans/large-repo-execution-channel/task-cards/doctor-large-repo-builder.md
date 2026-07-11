# Task Card

## ID

doctor-large-repo-execution-diagnostics-builder

## Task Mode

| Field | Value |
|---|---|
| Mode | builder |
| Builder scope | doctor diagnostics and doctor-facing template/docs only; no tests |
| Checker/Test scope | separate later task |

## Claude Context Packet

| Field | Value |
|---|---|
| CodeGraph status | not indexed |
| Target files/modules | `scripts/doctor_workflow.py`, `assets/README.md`, `assets/task-card-template.md` |
| Relevant symbols/functions | `WORKFLOW_REQUIRED_FILES`, workflow version comparison, tracked-file large-repo findings, `run_doctor`, `main` |
| Do not read / do not modify | dispatcher, tests, other scripts/assets |
| Narrow validation commands | `python -m py_compile scripts/doctor_workflow.py` |

## Goal

Make doctor detect missing documented helpers and provide bounded, read-only mounted-filesystem hash diagnostics while making large-repo recommendations explicitly risk-gated.

## Handoff Contract

1. Add a maintained set of documented runtime helpers (at minimum `ai/locate-code.py`, dispatcher/status/watch, checker, clean_runtime, Spark and parallel helpers). Doctor reports each missing helper clearly even if other bootstrap files exist; reuse the existing workflow-version refresh guidance.
2. Add optional bounded hash/index diagnostics through repeatable CLI `--hash-path REPO_RELATIVE_PATH` (preferred) or a clearly documented environment equivalent if preserving positional compatibility is simpler. Reject absolute paths, traversal, missing files, directories, and more than 20 paths.
3. For each explicit path only, compare filesystem content hash (`git hash-object --no-filters <path>` or equivalent), index blob (`git rev-parse :path`), and normal `git status --porcelain -- path`. If filesystem hash differs from index while normal status is empty, warn `possible stat-cache/index mismatch`. This is advisory and preview-only: never run update-index, add, renormalize, checkout, reset, clean, or delete.
4. Report exact safe next diagnostics (`git diff --no-ext-diff -- <path>`, hashes) and state that `git add --renormalize` is never automatic and requires human judgment.
5. Change large-repo recommendation wording: tracked count alone triggers a strategy review, not unconditional fast-large-repo/reuse. Recommend fast-large-repo only when risk rows are low, targets are exact, serial reuse is safe, and reduced untracked/patch evidence is accepted. Otherwise keep fresh/full evidence. Mention execution-only for exact mechanical Builder tasks and retry-in-place for a clean no-diff same-task retry.
6. Update task template/docs with the explicit hash diagnostic invocation and risk-gated routing, without promising global cleanliness from target-only checks.

## Acceptance Criteria

- Doctor remains read-only and standard-library-only.
- Existing one-positional-repo invocation remains compatible.
- Target-only hash diagnostics are bounded and label their evidence scope.
- Missing documented helper and risk-gated large-repo messages are actionable.
- No tests modified in this Builder phase.

## Testing Responsibility

Builder runs py_compile only. Checker adds focused doctor tests.

## Required Report

Edit after bounded reads. Report touched files, acceptance mapping, syntax outcome, deviations, and risks.
