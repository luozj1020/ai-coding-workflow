# Phase 5 Pull Context Broker — Frozen Contract

## Ownership and compatibility

Codex directly implemented this phase under the human's standing ownership
choice. Pull Context is opt-in. Existing L0/L1/L2 packets and review packets are
unchanged; the Broker uses the Phase 4 Evidence Object Store as its only object
layer.

## Query and state binding

- A request contains an exact Workflow State `state_id`, requester identity,
  intent, symbols, include kinds, and content byte budget.
- The Broker validates the Workflow State and rejects any request whose
  `state_id` differs before reading or generating repository evidence.
- Supported v1 include kinds are `definition`, `callers`, `callees`, `tests`,
  and `build-rules`.
- Optional requested paths cannot widen `state.next_action.allowed_paths`.

## Retrieval and generation

- Valid matching Evidence Objects are cache hits and are never regenerated.
- A cache hit must also match the current repository file hash and any bound
  commit/worktree dependency; canonical object integrity alone is insufficient.
- Cache misses use bounded, model-free repository scans to produce symbol
  slices, caller sites, a bounded callee set, related test slices, and matching
  build-rule slices.
- Generated objects bind repository commit when available, file hash, and the
  Workflow State repository hash. Files larger than the local scan cap, binary
  files, skipped runtime/vendor directories, and symlinks are not traversed.
- No generator returns a whole file and no object is byte-truncated to fit a
  response.
- Lexical output is explicitly labeled as candidate evidence, never semantic
  proof. Caller/callee candidates require LSP, CodeGraph, build, or test
  confirmation for correctness-critical decisions.

## Response identity, budget, and failures

- Responses contain Phase 4 object references only. Content is pulled later
  with `evidence-store.py read`.
- `context_id` binds the semantic response and is stable across a cache miss on
  the first request and a cache hit on the same later request.
- `max_bytes` limits the sum of referenced object content bytes; `max_objects`
  provides an independent object-count cap.
- Unresolved query slots explicitly distinguish `not-found`, `stale`,
  `permission-denied`, and `budget-exceeded`.
- Duplicate object IDs are removed and role/phase priority controls ordering.

## Deferred

Acceptance–Evidence–Diff Graph and Review Receipt remain Phase 6 work. Phase 5
does not infer acceptance support or reopen accepted decisions.
