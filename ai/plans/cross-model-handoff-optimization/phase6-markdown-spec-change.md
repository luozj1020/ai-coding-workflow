# Phase 6 Acceptance Graph and Review Receipt — Frozen Contract

## Ownership and compatibility

Codex directly implemented this phase under the human's standing ownership
choice. Phase 6 is opt-in. The legacy full review-packet path remains unchanged;
the incremental path consumes Phase 1 Workflow State and Phase 4 immutable
Evidence Objects.

## Acceptance Graph

- Every graph binds the exact `state_id`, repository-state hash, and task ID.
- Acceptance evidence refs must be immutable object IDs declared by the same
  Workflow State. Missing, stale, unknown, unreadable, corrupt, or
  permission-denied objects fail graph construction.
- State `satisfied` becomes graph `supported` only with an explicit passing
  `test-result`/`acceptance-record` or evidence declaring a semantic guarantee.
  A bounded lexical candidate, diff, or source slice is not proof by itself.
- Contradictory evidence wins over supporting evidence and remains visible.
- Decision hashes make stable-ID decision changes visible to delta review.

## Incremental review and revision

- The first review includes the whole graph because all items are new.
- A later review omits a supported, unchanged item only when a valid prior
  Receipt, bound to the exact prior Graph and State, classified it as accepted.
- Conditional, rejected, unsupported, contradictory, reopened, new, or changed
  items remain in review scope.
- Revision mode includes only failing/reopened evidence subgraphs, plus items
  made conditional or rejected by the prior Receipt.
- Packet identity binds its complete canonical content and is revalidated when
  validating a Receipt.

## Reopening and Receipt safety

- New immutable `diff-hunk` refs and explicit repository-relative changed paths
  are compared with the evidence paths of previously supported items. An
  overlap changes the graph status to `reopened` before review.
- A Receipt binds both the exact Workflow State hash and Acceptance Graph hash.
  It cannot accept an unsupported, contradictory, or reopened item, and when a
  packet is supplied it must classify every and only item in that packet.
- Graph, packet, object, and Receipt identities are deterministic SHA-256 IDs;
  schemas and runtime validators reject unknown fields.

## Deferred

Phase 6 does not authenticate Evidence Object producers; multi-user/remote
stores still need a signature or trust policy. Automatically applying a reopen
as a Workflow State event is deferred: the graph exposes `reopened_acceptance`
without mutating the source State. Ownership continuity remains Phase 7.
