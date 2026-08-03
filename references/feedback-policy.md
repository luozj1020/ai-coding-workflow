# Workflow Feedback Policy

Feedback is a local, deterministic evidence export. It never invokes Spark or
Claude, reads API configuration, uploads data, changes workflow state, refreshes
progress timers, or authorizes retries, takeover, merge, or broader access.

## Commands

```bash
# Default and explicitly read-only forms
python ai/collect-workflow-feedback.py --preview --task-id TASK_ID
aiwf feedback --preview --task-id TASK_ID

# Persist one scrubbed record locally
aiwf feedback --record --task-id TASK_ID \
  --issue false-progress --issue completion-tail-delay \
  --rating safety=8 --rating efficiency=4

# Aggregate recorded metrics without comments or raw records
aiwf feedback --bundle
```

`--preview` is the default when no mode is supplied. `--record` writes one JSON
file under `.ai-workflow/feedback/`. `--bundle` is read-only unless an explicit
`--output` is supplied. Recording feedback is never a completion requirement
and must not interrupt normal dispatch or review.

## Evidence Boundary

The collector reads only small machine-readable runtime, outcome, result, and
phase-metric JSON receipts. It exports an allowlisted set of classifications,
booleans, counts, ratings, relative receipt names, the Git HEAD, and the local
runtime script hash. It does not export prompts, task-card text, source, diffs,
raw logs, command bodies, API/MCP configuration, environment variables, user
names, or absolute paths.

Optional comments are local, limited to 1000 characters, and excluded from
bundles. Users should still avoid secrets and product content. Artifact paths
outside the repository are replaced with `external-artifact-redacted`.

## Interpretation

Feedback is diagnostic, not acceptance evidence. Model interaction, artifact
validity, validation, semantic acceptance, and merge authority remain separate.
Missing receipts remain unknown rather than false. A user-selected issue does
not count as a model failure and cannot change retry or takeover thresholds.

Recommended issue categories cover repeated host confirmation, missing Spark
terminal state, false progress, completion-tail delay, failed session resume,
write-scope blockers, tool-capability mismatch, monitor noise, process lifecycle,
report inconsistency, and validation environment failure.

Use `schemas/workflow-feedback-v1.schema.json` for persisted records. Aggregate
trends across multiple completed runs before changing defaults; retain the
underlying task receipts when a regression decision needs auditability.
