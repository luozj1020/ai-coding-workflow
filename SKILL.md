---
name: ai-coding-workflow
description: Run or update a Claude-first workflow only for non-trivial repository changes that will use durable Claude delegation. Skip questions, read-only work, tiny/urgent local edits, and direct Skill maintenance unless delegation is explicitly requested.
---

# AI Coding Workflow

## Applicability Gate

Classify before references, a Task Card, or a model call:
**bypass** (question/read-only/tiny/urgent: record `workflow bypassed: <reason>`);
**direct** (bounded Codex or Skill maintenance: in a bootstrapped repo run
`python ai/aiwf.py direct --reason ... --path ...`, no card/Spark/Claude);
**delegated** (durable Claude
output materially reduces Codex work); or **setup/update** (load only
`references/setup-policy.md`).

Semantic auto-load without `$` never expands bypass/direct into model work.
For delegation, load only one matching reference; do not load multiple preemptively.

## Core Contract

- Follow `OBSERVE -> ROUTE -> PLAN -> DISPATCH -> EXECUTE -> VERIFY -> REVIEW`.
- For JSON-backed delegation, Codex freezes and reviews the compact Task JSON;
  `aiwf run` deterministically renders the execution card. Claude owns assigned
  Builder/Checker/Test work. Legacy Markdown cards remain an explicit
  compatibility path; `solution-planner` is explicit opt-in.
- Model reports are claims until diff, receipt, and validation agree. Keep one
  writer, exact scope, isolated worktrees, and hash-bound recovery; stale
  ownership fails closed. Models never merge; humans own high-impact approval.

## Minimal Procedure

1. Classify; bypass/direct stop after local edits/checks.
2. Delegated only: query valid CodeGraph once; use `ai/locate-code.py` for
   behavior/files and lexical search for Shell/config/text; record result/skip.
3. Route, freeze/review Task JSON, and dispatch its deterministic execution
   projection. Use Checker/Test only when its assigned work materially reduces
   Codex effort.
4. Verify deterministically, then review bounded evidence: accept, revise,
   split, or reject. Humans merge.

When the user explicitly requests Skill feedback, produce a read-only
retrospective from the current conversation and the minimum necessary runtime
receipts. Do not persist telemetry, invoke a model, create a task card, or start
remediation until the user separately asks for changes.

## Reference Router

| Operation | Load |
|---|---|
| roles, default loop, evidence hierarchy | `references/operating-model.md` |
| install, update, bootstrap, environment tools | `references/setup-policy.md` |
| ownership, Spark, Owner Lease, Handoff Tax | `references/routing-and-spark.md` |
| task cards, solution contracts, Context Packets | `references/task-card-policy.md` |
| Claude probes, host retry, write scope, monitoring, failure attribution | `references/claude-runtime.md` |
| Builder/Checker review, acceptance, takeover, human authority | `references/review-policy.md` |
| worktrees, dirty snapshots, continuation, parallel compatibility | `references/worktree-and-parallel.md` |
| retrieval order and context budgets | `references/mcp-policy.md` |
| compatibility loop state machine | `references/loop-model.md` |
| metrics, calibration, regression comparison | `references/benchmark-policy.md` |
| user-triggered Skill feedback | `references/feedback-policy.md` |

For command syntax, prefer installed `ai/README.md`.
