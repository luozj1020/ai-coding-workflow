# Spark Execution-Cost Routing Spec

## Problem

For tiny changes, the task card, context packet, dispatch startup, evidence report, and review material can cost more tokens and changed control-plane lines than the implementation itself. The workflow currently classifies size and risk but does not explicitly compare direct Codex editing cost with Claude delegation overhead.

## Terminology

- **Codex fast path**: a pre-dispatch ownership decision for a tiny low-risk task.
- **Codex takeover**: a post-dispatch intervention after the current-task repeated-failure threshold. Cost estimation must never label a pre-dispatch route as takeover.

## Desired Behavior

### Spark estimator

- Add explicit read-only mode `execution-cost-estimator`.
- Include the estimator role in `preflight-bundle` and extend `task-size-classifier` with the same cost fields.
- Spark returns machine-readable lines:
  - `predicted_diff_lines_low=<integer>`
  - `predicted_diff_lines_high=<integer>`
  - `predicted_files=<integer|unknown>`
  - `context_scope=local|bounded|broad|unknown`
  - `validation_complexity=none|low|medium|high|unknown`
  - `delegation_overhead=low|medium|high`
  - `estimated_direct_work_units=<positive integer>`
  - `estimated_delegated_work_units=<positive integer>`
  - `delegation_to_direct_ratio=<decimal>`
  - `economic_recommendation=codex-fast-path|claude-builder`
  - `safety_eligible=yes|no`
  - `recommended_owner=codex-fast-path|claude-builder|spec-first|human-clarification`
  - `confidence=high|medium|low`
  - `risk_flags=none|comma-separated flags`
  - `reason=<one short paragraph>`
  - `stop_condition=<one sentence>`
- Work units are relative estimates, not claimed token-accounting measurements.

### Safety gate

Spark may recommend Codex fast path only when all are true:

- predicted upper diff bound is at or below the configured fast-path threshold (default 60 added+deleted lines);
- predicted files are at most 2;
- context scope is local;
- validation complexity is none or low;
- confidence is high;
- risk flags are none;
- no public API, data model, security, migration, permission, concurrency, cross-module contract, broad-context, or complex-test-design risk.

If economic recommendation is Codex but safety eligibility is no, final owner is Claude/spec/human, never Codex fast path.

### Configuration and evidence

- Add `--fast-path-max-diff-lines N` and `CODEX_FAST_PATH_MAX_DIFF_LINES`; default 60, allowed 1..200.
- Persist parsed cost fields in minimal/full Spark reports.
- Direct mode returns the machine-readable estimate downstream without local persistence.
- Report must retain `Codex fast path approved? | pending Codex review`; Spark recommendation is advisory.

### Task card

Add an `Execution Cost / Fast Path Gate` with:

- predicted diff range/files;
- direct/delegated work units and ratio;
- delegation overhead;
- context and validation complexity;
- economic recommendation;
- safety eligibility and risk flags;
- Spark confidence;
- final Codex owner decision and rationale;
- fast-path exit/escalation condition.

## Non-goals

- No automatic source edit merely because Spark recommends Codex.
- No weakening of existing two-file/risk/validation Small Change Fast Path rules.
- No post-Claude takeover based on cost.
- No model-tier routing or strong-model fallback.
- No claim that estimated work units equal billable tokens.

## Acceptance

- Help and mode validation include `execution-cost-estimator` and the threshold option/env.
- Explicit estimator prompt requires all machine-readable fields and safety rules.
- Preflight roles and prompt include cost estimation without adding an eighth bundle heading.
- Task-size classifier includes cost fields.
- Minimal/full reports parse and expose estimator fields; direct output remains file-free.
- Tests cover safe tiny, economically attractive but unsafe, uncertain/low-confidence, and over-threshold recommendations.
- Installer/template/docs propagation and focused regression pass.
