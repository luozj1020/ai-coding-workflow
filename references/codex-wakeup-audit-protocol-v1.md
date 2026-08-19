# Codex Wakeup Audit Protocol v1

## Status and Purpose

This protocol is frozen at version 1. Changing an enum, inference rule, or
aggregation denominator requires a new protocol version. Historical results
must identify the protocol version that produced them.

The audit answers two separate questions:

1. **Observed facts:** how many Codex inference episodes occurred, at which
   recorded responsibility stages, with what observed token and duration data.
2. **Bookend counterfactual:** which episodes can be proven necessary or safely
   avoidable under the frozen Bookend execution model.

Observed facts and counterfactual classifications are separate tables. Missing
historical fields remain null or `indeterminate`; they are never filled with
zero and never inferred merely because a Codex call occurred.

The audit is read-only. It does not invoke a model, alter runtime artifacts,
refresh progress, change failure attribution, or authorize retry, takeover,
acceptance, or merge.

## Unit of Analysis

One row represents one **Codex inference episode** recorded by the canonical
model-usage ledger. Shell commands, blocking monitors, and broker diagnostic
records are not inference episodes.

The canonical identity is `(run_id, task_id, call_id)`. Identical duplicate
records with that identity are folded. Conflicting duplicates retain one row
with conflicting fields set to null and `evidence_quality=conflicting`.
Records without a complete identity remain separate and are marked incomplete.

Broker transitions are folded by `reservation_id`; `reserved -> running ->
terminal` is one reservation, not three calls. Unmatched reservations and
diagnostic control activity are reported separately and are not silently added
to usage totals.

`logical_task_id` is distinct from `task_id`. Historical `task_id` values are
not treated as cross-run logical identities unless an explicit, hash-bound
artifact supplies `logical_task_id`. Consequently, logical-task denominators
remain null when that binding is incomplete.

Observed `(run_id, task_id)` denominators also fail closed when separate usage
ledgers reuse the same pair with different call sets. Such a collision may be a
historical ID reuse or a fragmented run; the audit reports it and does not
guess how many tasks existed.

## Source Precedence

1. Canonical `model-usage.jsonl`: call identity, recorded stage, model, tokens,
   duration, result, and usage completeness.
2. Model-call broker ledger: reservation identity, request identity, retry and
   state transitions, input/evidence hashes, and non-inference diagnostics.
3. Event v2: event chain and explicit `audit_v1` annotations uniquely bound to
   a call. Event proximity alone does not prove causation.
4. Run metrics: observed completion, acceptance, experiment arm, and owner.
5. Policy text: whether a synchronization point is mandated by the frozen
   policy version. Policy does not prove that a historical runtime followed it.

Raw prompts, model output bodies, source diffs, API configuration, user
identity, and absolute external paths are outside the audit dataset.

## Frozen Stage Enum

The audit reuses the workflow economics responsibilities without remapping:

```text
repository-discovery
intent-freeze
planning-review
monitoring
diff-review
revision-drafting
final-review
```

Any other recorded value is preserved as `observed_stage` and aggregated under
`stage=unclassified`. An old `stage=review` or `stage=execute` is not rewritten
after the fact.

## Frozen Trigger and Cause Enums

`proximate_trigger` is the immediate mechanical event:

```text
task-created
builder-started
builder-progress
builder-complete
validation-complete
validation-failed
timeout
transport-failure
session-resume-failure
scope-violation
report-missing
review-revise
human-change
high-risk-boundary
```

`root_cause` is the reason a decision or recovery was actually needed:

```text
policy-mandated-review
semantic-uncertainty
architecture-decision
contract-conflict
mechanical-failure
transport-runtime
evidence-gap
implementation-defect
user-requirement-change
unknown
```

Trigger and cause are independent. Neither is derived from the other without a
frozen deterministic rule and evidence reference.

## Strict Semantic-Decision Rule

`semantic_decision_required=true` only when omitting the Codex inference would
leave a choice that cannot be decided by the frozen contract, deterministic
tools, or a still-valid prior semantic receipt. Qualifying cases are contract
contradiction, architecture choice, behavior ambiguity, high-risk semantic
implication, invalidation of a prior semantic decision, or a material user
requirement change.

Timeouts, compile or test failures, missing reports, exit-code inspection,
scope verification, transport retry, and known mechanical revisions are
`false` when this fact is explicitly recorded. The audit never marks the field
`true` merely because Codex happened to perform semantic review after waking.

## Frozen Bookend Counterfactual Enum

Inference episodes use exactly one value:

```text
required_freeze
required_final_review
required_semantic_escalation
avoidable_by_deterministic_guard
avoidable_by_owner_convergence
avoidable_by_review_reuse
indeterminate
```

`non_inference_control_activity` is a separate broker/control-plane count, not
an inference-episode counterfactual value and never carries model-usage tokens.

Historical automatic classification is deliberately narrow:

- exact `stage=intent-freeze` -> `required_freeze`;
- exact `stage=final-review` -> `required_final_review`;
- a valid explicit `audit_v1` annotation may supply another value when it is
  uniquely call-bound and includes evidence references;
- everything else -> `indeterminate`.

Absence of evidence that a call was required does not make it avoidable.
Occurrence of a call does not make it policy-required.

## Annotation Contract

Future usage records or Event v2 `detail` objects may contain:

```json
{
  "audit_v1": {
    "call_id": "optional when already call-bound",
    "logical_task_id": null,
    "session_id": null,
    "iteration": 1,
    "proximate_trigger": "builder-complete",
    "root_cause": "policy-mandated-review",
    "policy_required": true,
    "user_triggered": false,
    "semantic_decision_required": false,
    "bookend_counterfactual": "avoidable_by_owner_convergence",
    "classification_confidence": "deterministic",
    "evidence_refs": ["sha256:..."]
  }
}
```

`classification_confidence` is one of:

```text
deterministic
policy-derived
human-reviewed
indeterminate
```

Any explicit non-`indeterminate` counterfactual requires a non-empty evidence
reference list. Contradictory or invalid annotations are ignored for
counterfactual totals and reported as classification errors.

## Output Tables

### Table A — Observed Facts

Group Codex usage by the frozen stages plus `unclassified`. Report calls,
complete calls, known input/output/reasoning tokens, missing-token call counts,
median input where complete, and active elapsed time. A total token or median
field is null if any contributing episode lacks that field; the known subtotal
is reported separately.

Task metrics distinguish observed `(run_id, task_id)` identities from explicit
cross-run `logical_task_id` identities. `tokens / accepted task` is null unless
acceptance binding and token completeness are both complete.

### Table B — Bookend Counterfactual

Group the same inference episodes by the frozen counterfactual enum. Report:

- known required tokens;
- known safely avoidable tokens;
- known indeterminate tokens;
- missing token counts in every bucket;
- non-inference control activity separately.

When all input-token evidence is complete, the Bookend retained-token interval
is:

```text
minimum = required tokens
maximum = required tokens + indeterminate tokens
```

The maximum is the conservative retained cost after subtracting only safely
avoidable calls. It is not called a floor. If usage is incomplete, both bounds
remain null and only known subtotals are shown. Indeterminate tokens are never
counted as savings.

## Historical Audit Rules

- Freeze this protocol before selecting or reading the historical sample.
- Record included roots/ledgers and excluded invalid ledgers without copying
  their raw contents.
- Do not mutate or normalize historical artifacts in place.
- Do not combine direct, delegated, diagnostic, simulated, and production arms
  without separately reporting their observed arm/owner labels.
- A malformed ledger is excluded as a unit and reported; valid-looking lines
  from it are not silently treated as a complete sample.
- Sample incompleteness produces `insufficient-evidence`, not a savings claim.
