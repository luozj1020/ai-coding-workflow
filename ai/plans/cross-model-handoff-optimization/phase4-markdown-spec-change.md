# Phase 4 Markdown Spec Change

- Source of truth: `AI Coding Workflow 跨模型信息传递优化规划.md`, §8 and §16 Phase 4.
- Incorporated by: Codex, following the user's explicit instruction to start Phase 4.
- Identity: SHA-256 of canonical immutable object content, provenance, selector,
  and dependency metadata; exact objects are deduplicated.
- Layout: `.ai-workflow/objects/<first-two-hex>/<remaining-hex>.json`.
- Invalidation: separate reversible validity sidecars; content objects are never
  modified by commit/file/symbol/build/validation/worktree checks.
- Packet boundary: reference-only by default, all references resolved before
  write, no silent omission or automatic inline fallback.
- Receiver cache: first successful read is a miss, subsequent reads are hits;
  unreadable references are counted separately.
- Compatibility: legacy string Evidence refs remain accepted by State IR; new
  content-addressed refs use the same string field without a schema break.
- Deferred: broker-driven retrieval and receiver-specific prefetch remain Phase 5.
