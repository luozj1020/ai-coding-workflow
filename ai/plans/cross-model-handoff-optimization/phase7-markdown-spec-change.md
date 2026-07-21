# Phase 7 Ownership Lease — Frozen Contract

## Ownership and compatibility

Codex directly implemented this phase under the human's standing ownership
choice. Owner Lease is opt-in and does not start, stop, or merge model sessions;
it emits deterministic authority for the existing dispatcher to consume.

## Continuity selection

- Mechanical revisions and test fixes select the original Builder unless an
  explicit human owner overrides it.
- Other continuations retain the current Builder by default.
- Every owner change records from/to owner, reason, cumulative handoff count,
  and the previous lease ID. Explicit ownership remains authoritative.
- The lease exposes both session identity and normalized selected model so the
  communication-aware Router can consume it without guessing from prose.

## Call and session gates

- No semantic blocker means `advisor.action=skip`.
- No new immutable Evidence Object means `reviewer.action=skip`.
- A same-owner continuation with unattempted recovery remains `requested` and
  `resume-required`; it is not execution authority.
- A same-owner new session is granted only after `resume_status=failed`.
  A successful recovery reuses the recorded session. An actual owner switch
  inherently uses a new session and requires a switch reason.

## Lease chain

Lease IDs bind canonical content. Renewals bind the previous lease, increment
generation and renewal count, and preserve handoff count. Unknown fields,
unsafe input, cross-task previous leases, and invalid state hashes fail closed.
Explicit `expired` and reason-bearing `revoked` transitions are hash-chained
terminal states; a terminal lease cannot be transitioned again, and a revoked
lease cannot be renewed.
