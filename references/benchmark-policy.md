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

- Canonical `.ai-workflow/model-usage.jsonl` records for Claude, Codex, and Spark.
- Per-role token/cache/cost totals and `usage_complete`; unavailable fields stay null.
- Legacy Claude/Codex usage summaries only when no canonical ledger is present.
- Number of turns when available.
- Task-card and review-packet bytes.
- Control-plane seconds before implementation.
- Model calls by role and whether Checker was actually dispatched.
- Approximate Claude diff reuse: normalized Claude-added lines retained in the
  final accepted diff. This is a routing signal, not semantic correctness proof.

Spark usage is quota telemetry, not benchmark spend. Always record its calls and
tokens by role, but exclude role `spark` from provider/calculated economic cost
and from cost-per-improvement comparisons. Summaries retain raw `by_arm` usage
and expose the charged view separately as `billable_by_arm`; a pricing entry may
also declare `"billable": false`. This reflects the workflow's bundled/free
Spark allowance without hiding consumption of that allowance.

### Improvement Quantity

Do not treat changed lines as delivered value. Freeze task-specific weighted
`improvement_units` before running any arm, then record
`improvement_units_satisfied` in each run. The summary compares satisfied weight,
completion ratio, cost per satisfied weight, and active seconds per satisfied
weight. A candidate that delivers less frozen improvement than the direct arm is
a content regression even if it is cheaper.

Record `semantic_diff_lines`, `changed_files`, `tests_added`, and `tests_passed`
as descriptive diagnostics only. They explain implementation breadth but never
increase the value score by themselves, preventing broad rewrites or self-authored
tests from being rewarded merely for being larger.

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

The deterministic regression suite uses the production owner router rather than
an always-Claude fake pipeline:

```bash
python ai/run-benchmark-suite.py --cases benchmarks/cases
```

Lane alone never triggers Claude or Spark. Default cases simulate one combined
Codex implementation/review call. A case must provide explicit `routing_facts`
that use the default Claude-first route to exercise execution, and Spark is counted
only when the router requests one uncertain-candidate estimate. These fake calls
test control-plane semantics, not model quality or real token economics.

Use `ai/summarize-loop-run.py` to aggregate existing artifacts:

```bash
python ai/summarize-loop-run.py .worktrees/loop-<timestamp> \
  --output .worktrees/loop-<timestamp>/loop-quality-summary.md \
  --json-output .worktrees/loop-<timestamp>/loop-quality-summary.json
```

`ai/run-loop.sh` writes these summaries automatically when it stops.

`ai/run-loop.sh` also writes `loop-events.jsonl`. Treat it as the durable event stream for comparing workflow behavior across runs.

## Controlled Economics Experiment

Compare the same task/repetition matrix across exactly three arms:
`codex-direct`, `delegation-no-spark`, and `full-workflow`. Generate and validate
the matrix before model execution:

```bash
python ai/aiwf.py experiment init --experiment-id routing-v1 \
  --task-id TASK-A --task-id TASK-B --repetitions 3 \
  --output .ai-workflow/experiments/routing-v1/manifest.json
python ai/aiwf.py experiment validate \
  .ai-workflow/experiments/routing-v1/manifest.json
python ai/aiwf.py experiment prepare \
  .ai-workflow/experiments/routing-v1/manifest.json
```

For an independent multi-unit batch, add the diagnostic fourth arm with
`--include-parallel-arm`. It creates `delegation-parallel-no-spark` with maximum
concurrency two. The run is valid only when metrics record at least two units,
max concurrency two, no Spark use, and serial reconciliation evidence. Do not
enable this arm for overlapping write scopes or shared-contract work.

Bind `AI_WORKFLOW_MODEL_USAGE_LEDGER` to the manifest's per-run ledger. After
all runs, require `validate --check-artifacts` before `experiment summarize`.
Do not compare incomplete matrices or replace unavailable provider usage with
estimates. Pair cost/time with acceptance, first-pass success, takeover, and
diff-reuse evidence; lower token use alone is not a passing result.

Use a reviewed, dated price catalog when providers do not report comparable
costs:

```bash
python ai/aiwf.py experiment summarize MANIFEST.json \
  --pricing ai/examples/model-pricing.json --output SUMMARY.json
```

Calculated cost remains separate from provider-reported cost. Both economic views
exclude Spark while raw Spark calls/tokens remain visible. A pair becomes a
`balanced-candidate` only when acceptance/first-pass quality and frozen
improvement quantity do not regress,
active elapsed time is at most 2.0x the direct baseline, and calculated or
complete provider cost saves at least 15%. These are independent gates rather
than a weighted score. A reviewed manifest `balance_policy` may override the
thresholds; missing cost evidence produces `insufficient-economic-evidence`.

Record time with separate clocks. `active_elapsed_seconds` is model, tool, and
deterministic workflow execution and is the only duration used for arm-to-arm
efficiency comparisons. `human_approval_seconds` records measured confirmation
wait only when the dispatcher exposes it. `end_to_end_elapsed_seconds` includes
both. Put an observed gap that cannot be attributed reliably in
`unattributed_wait_seconds`; never guess that it was human approval. Keep
`total_elapsed_seconds` equal to active time for compatibility with older tools.
For Claude dispatch diagnosis, preserve the companion `phase-metrics.json` and
compare `context_acquisition_seconds`, `implementation_seconds`,
`validation_seconds_observed`, and `tail_seconds`. These observer-derived phase
boundaries are approximate and explain latency; the arm-level active clock
remains the efficiency denominator.
`experiment prepare` exports `AI_WORKFLOW_CLAUDE_PHASE_METRICS_FILE` in each
run context. When that environment is used for dispatch, the dispatcher copies
the canonical phase artifact into the run directory and `experiment summarize`
aggregates phase totals and medians without manual transcription.
The summary may still report quality, stability, and elapsed time for a matrix
containing a failed call with unavailable usage, but it sets `comparable=false`
and suppresses affected token/cost deltas. Do not present those partial totals as
an economic comparison.

For a real repository, create one reviewed JSON task spec per independent task
using `benchmarks/real-project-task.example.json`, then freeze it together with
the repository baseline:

In an installed target repository the same template is available at
`ai/examples/real-project-task.json`.

```bash
python ai/aiwf.py experiment init --experiment-id real-v1 \
  --project-root /path/to/project \
  --task-spec /path/to/task-a.json --task-spec /path/to/task-b.json \
  --repetitions 3 --output /path/to/results/real-v1/manifest.json
python ai/aiwf.py experiment prepare /path/to/results/real-v1/manifest.json
python ai/aiwf.py experiment status /path/to/results/real-v1/manifest.json
```

`prepare` refuses tracked dirty state by default, snapshots each task by SHA-256,
records the Git base commit, and emits per-run arm contracts. Run in manifest
sequence: arm order rotates across repetitions to reduce warm-cache and provider
time bias. `full-workflow` means faithful Skill auto-routing; if it selects the
Codex fast path, stop there. `--forced-full-pipeline` is diagnostic-only and must
not be presented as normal Skill economics. Use `status` to resume without
inventing missing results. A changed project HEAD is reported as drift; every
actual run must still start from the recorded base commit.

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
