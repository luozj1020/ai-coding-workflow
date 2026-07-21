# Phase 2 Markdown Spec Change

- Source of truth: `AI Coding Workflow 跨模型信息传递优化规划.md`, §6.3–6.4 and §16 Phase 2.
- Incorporated by: Codex, following the user's explicit instruction to enter Phase 2.
- Supersedes for this slice: the earlier frozen contract's combined provisional
  S3 names and unresolved delta-format decision.
- Chosen delta format: deterministic, self-contained field-level semantic diff,
  bound to both canonical base and target State IR IDs.
- ACK bound: 8192 UTF-8 bytes, short fields/lists, one receiver-authored repair.
- Fail-closed boundary: receiver base, Delta target, State IR target, ACK state,
  and recomputed Delta content must all agree before execution is allowed.
- Deferred: evidence freshness, Context Broker fulfillment, acceptance graph,
  review receipts, ownership lease, and communication-aware routing.
