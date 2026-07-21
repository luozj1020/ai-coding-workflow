# Phase 8 Communication-Aware Routing — Frozen Contract

## Ownership and compatibility

Codex directly implemented this phase under the human's standing ownership
choice. Existing routing remains compatible when no Handoff Tax artifact or
continuation lease is supplied. Explicit human ownership has highest priority.

## Observed Handoff Tax

- Tax remains an auditable vector: serialization bytes, reconstruction seconds,
  rediscovery count, and handoff-induced revision count. Supporting cache/read
  components remain visible.
- Missing measurements stay null/unknown rather than zero. Zero samples produce
  `unknown`; incomplete or fewer-than-three complete samples produce
  `unknown`/`canary`; sufficient complete observations produce `calibrated`.
- Units are combined only by `calibrate-handoff-routing.py` with an explicit
  reviewed cost policy and positive direct baselines. Calibration records the
  source estimate IDs, policy, sample count, component medians, and ratios.

## Router behavior

- A valid continuation owner selects `same-model-single-pass` before introducing
  another model switch.
- Verified observed calibration adjusts delegated cost, active time, and Codex
  work-reduction estimates. A negative benefit can bypass the cross-model flow.
- The decision explains selected communication mode, continuity state, tax
  status/application/veto, and major reason components.
- Model-produced or malformed tax estimates are ignored for deterministic
  economics. Insufficient observations remain canary instead of becoming facts.

## Spark decision

Spark is not added to the default Phase 7/8 path. A valid lease or calibrated
observed tax makes routing deterministic and forces Spark `skip`. With
insufficient history, an explicitly requested Spark call may advise task shape,
but `spark_estimate_authoritative=false`: it cannot set Handoff Tax, override
the user's owner, satisfy acceptance, approve review, or authorize merge.
