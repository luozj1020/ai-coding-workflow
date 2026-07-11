# Task Card

## ID

large-repo-execution-only-checker

## Task Mode

| Field | Value |
|---|---|
| Mode | checker-test |
| Builder scope | complete in `8975263`, `bdafa5b` |
| Checker/Test scope | tests only; tiny implementation fix only if directly exposed |

## Claude Context Packet

| Field | Value |
|---|---|
| CodeGraph status | not indexed |
| Target files/modules | `tests/test_dirty_source_guard.py`, `tests/test_install_workflow.py` |
| Relevant behavior | execution-only preflight, minimal card headings/content, short prompt, standard compatibility, progress signals and timeout classification |
| Do not read / do not modify | unrelated tests/docs/scripts; no broad implementation rewrite |
| Narrow validation commands | `python -m pytest -q tests/test_dirty_source_guard.py tests/test_install_workflow.py` |

## Goal

Add deterministic regression coverage for execution-only Builder mode and first-substantive-progress timeout.

## Required Tests

- invalid mode and execution-only non-builder fail before worktree creation;
- standard defaults to timeout 0 and preserves normal execution card headings;
- execution-only defaults to 120, renders a materially smaller card with retained `##` headings and required contract, and emits the short prompt;
- seed-only fake Claude is stopped/classified at a short configured deadline;
- a source diff, non-seeded progress update, valid report, and explicit non-seeded blocker each prevent first-progress timeout (representative parameterization is fine);
- fallback/status/progress evidence records `first_progress_timeout` and this does not claim acceptance/takeover.

## Validation Contract

```validation
python -m pytest -q tests/test_dirty_source_guard.py tests/test_install_workflow.py
```

Local validation allowed? yes.

## Acceptance Criteria

- No real Claude/network calls.
- Focused suites pass.
- No duplicate broad validation command is added by the task itself; run the exact command once.
- Report coverage gaps and any tiny implementation fix separately.
