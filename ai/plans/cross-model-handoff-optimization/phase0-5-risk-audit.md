# Phase 0–5 Risk Gate

Date: 2026-07-21

Decision: **accept the Phase 0–5 foundation for Phase 6 after mitigations in
this audit; retain the residual risks below as explicit Phase 6 gates.**

## Threat and failure model

The reviewed boundary treats Workflow State, model-produced ACK/Delta/Query
documents, Evidence Object stores, receiver metrics, and repository paths as
potentially malformed or stale. It covers accidental concurrency and local
same-user filesystem manipulation. SHA-256 identifiers provide integrity and
identity, not sender authentication or protection against a hostile local user
who can rewrite both content and hashes.

## Resolved blocking finding

### R-01: stale or cross-repository Evidence Object accepted as a Broker hit

Before mitigation, the Broker matched cached objects by kind and symbol and
trusted object integrity/sidecar state. A valid old object with no sidecar could
therefore be returned for a different current file.

Mitigation:

- require current repository path containment and regular-file readability;
- require the current file hash to equal the object's dependency hash;
- compare bound commit and worktree hashes;
- continue checking validity sidecars;
- regenerate on mismatch and count it as a miss, not a hit.

Evidence: `test_cache_hit_must_match_current_repository_file_hash`.

## Resolved high findings

### R-02: concurrent Workflow State transitions could fork the event chain

Mitigation: the complete state-read, ancestry-check, transition, event append,
and state-write section now uses a cross-platform exclusive lock. A competing
transition re-reads the advanced state and fails its stale base binding.

Evidence: `test_concurrent_transitions_serialize_on_state_lock`.

### R-03: repository and object-store path boundary gaps

Mitigation:

- reject traversal, Unix absolute, and Windows drive-absolute paths in State,
  ACK, Evidence metadata/current context, and Context Query inputs;
- never scan repository symlinks;
- reject symlinked Evidence store roots, shards, objects, validity records, and
  receiver metric files.

### R-04: unbounded input and repository enumeration

Mitigation:

- bound State/JSON/event logs, Context Query documents, Evidence metadata,
  object files, validity records, metrics, reference lists, source files,
  symbols, paths, query slots, cache index size, and repository scan count;
- use deterministic `os.walk(..., followlinks=False)` rather than materializing
  an unbounded recursive path list;
- reject oversized content before reading it into memory and recheck after read.

### R-05: receiver cache metrics could be internally inconsistent

Mitigation: validate per-receiver `reads == hits + misses` and require every
global total to equal both its receiver-row sum and the hit/miss identity.

### R-06: lexical caller/callee candidates could be mistaken for proof

Mitigation: every response reference now carries `evidence_quality`.
Definitions/tests/build rules are `exact-text-candidate`; callers/callees are
`bounded-lexical-candidate`. Pulled object content also records the local
analysis method and `semantic_guarantee=false`.

Evidence: Python fixture, C++/Bazel fixture, and current-repository Python
canaries.

## Residual medium risks and Phase 6 gates

1. Lexical discovery can still have false positives and false negatives. Phase
   6 must not mark Acceptance satisfied from a lexical candidate alone; require
   deterministic validation or a semantic provider.
2. Content hashes do not authenticate the producer. Remote or multi-user object
   stores require a separate trust/signature policy before use.
3. Event-first persistence intentionally leaves a recoverable tail-ahead-of-
   state condition after a crash. Automatic recovery remains backlog; current
   validation fails closed and reports the mismatch.
4. The 100,000-object Broker index cap may reduce cache efficiency but does not
   authorize stale reuse; missed entries are regenerated and revalidated.
5. Empty `next_action.allowed_paths` means no additional State-level read
   restriction. Deployments requiring least-privilege reads must populate it or
   add a separate read-scope field in a future schema revision.
6. Local same-user filesystem compromise is outside the authenticity model.
   Tools prevent common symlink/path escapes but do not defend against an
   attacker who can continuously rewrite the repository and State artifacts.

## Phase 6 admission conditions

- Every supported Acceptance must reference immutable Evidence Objects.
- `satisfied` may not rely only on `bounded-lexical-candidate` evidence.
- Review Receipt and Acceptance Graph must bind the exact Workflow State hash.
- New diff/file hashes must reopen affected accepted items before review.
- Contradictory, stale, unknown, unreadable, or permission-denied Evidence must
  fail closed rather than be omitted.
