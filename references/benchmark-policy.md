# Benchmark Policy

## Purpose

This workflow is a harness, not just a launcher. Evaluate changes to the harness with the same discipline used to evaluate model output: quality, speed, cost, and stability.

## Metrics

### Quality

Quality answers whether the loop produced an acceptable result.

Signals:

- Final review decision: `ACCEPT`, `REVISE`, `SPLIT`, `REJECT`, or unknown.
- Checker result: `ALL GREEN`, `FAILED`, missing, or unknown.
- Acceptance criteria coverage in the Claude report and Codex review.

### Speed

Speed answers how long the workflow took to reach a stopping condition.

Signals:

- Elapsed wall-clock time from progress logs.
- Number of iterations.
- Number of dispatches and reviews.

### Cost

Cost answers how much model budget was spent.

Signals:

- Claude token/cost usage summaries.
- Codex token/cost usage summaries.
- Number of turns when available.
- Task-card and review-packet bytes.
- Control-plane seconds before implementation.
- Model calls by role and whether Checker was actually dispatched.
- Approximate Claude diff reuse: normalized Claude-added lines retained in the
  final accepted diff. This is a routing signal, not semantic correctness proof.

Record accepted task economics with:

```bash
python ai/aiwf.py economics record \
  --metrics RUN/run-metrics.json \
  --claude-diff RUN/claude.diff --final-diff RUN/final.diff \
  --task-type core-semantic --repository-scale large \
  --owner claude-builder --accepted yes \
  --append-history .ai-workflow/economics-history.jsonl
```

`collect-task-facts.py` reads accepted same-task-type history. Three or more
reuse samples below 30% bias ownership toward Codex; at least 70% median reuse
and 70% first-pass success bias toward Claude. History never satisfies review
or acceptance by itself.

Primary `aiwf run`, efficient `final-candidate` review, and accepted legacy
loops write `workflow-economics.json` automatically. Accepted history is
idempotent by run/task identity. Diff reuse remains unavailable unless both the
Claude diff and the final accepted diff are explicitly bound; the workflow
records that evidence gap rather than inventing a percentage.

### Stability

Stability answers whether the harness behaved predictably.

Signals:

- Runtime timeout or no-output timeout.
- Missing reports or fallback reports.
- Checker mutation guard failures.
- Missing checker reports.
- Repeated failures, regressions, or lack of progress.
- Missing or malformed append-only loop events.

## Summary Artifacts

Use `ai/summarize-loop-run.py` to aggregate existing artifacts:

```bash
python ai/summarize-loop-run.py .worktrees/loop-<timestamp> \
  --output .worktrees/loop-<timestamp>/loop-quality-summary.md \
  --json-output .worktrees/loop-<timestamp>/loop-quality-summary.json
```

`ai/run-loop.sh` writes these summaries automatically when it stops.

`ai/run-loop.sh` also writes `loop-events.jsonl`. Treat it as the durable event stream for comparing workflow behavior across runs.

## Regression Use

When changing workflow prompts, dispatch scripts, review policy, or checker behavior:

1. Run the unit tests.
2. Run at least one representative task through the loop when Claude/Codex CLIs are available.
3. Compare the new quality summary against prior runs.
4. Treat lower quality, higher iteration count, missing checker evidence, or new stability findings as harness regressions unless there is a deliberate tradeoff.

## Benchmark Task Set

Maintain a small set of representative task cards for recurring evaluation:

- Documentation-only change.
- Shell workflow script fix.
- Python helper behavior fix.
- Installer idempotency change.
- Failing-test repair.

The task set should be stable enough for comparison but updated when the workflow's target use cases change.
