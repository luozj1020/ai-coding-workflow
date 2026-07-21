# Phase 3 Markdown Spec Change

- Source of truth: `AI Coding Workflow 跨模型信息传递优化规划.md`, §7 and §16 Phase 3.
- Incorporated by: Codex, following the user's explicit instruction to start Phase 3.
- Ledger identity: canonical normalized-statement SHA-256 plus explicit related
  hypothesis IDs; fuzzy similarity only triggers bounded review.
- Evidence rule: rejection requires State IR evidence; reopening requires at
  least one new Evidence ref and explicit reopen-condition confirmation.
- Synchronization: reject/reopen updates are retry-safe across the Ledger and
  Workflow State IR semantic event log.
- Revision boundary: only exact matches and scope-relevant active rejections are
  rendered, with a configurable bounded item count.
- Deferred: Evidence content verification and staleness remain Phase 4 work.
