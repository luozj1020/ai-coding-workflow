---
name: ai-coding-workflow
description: Install, update, or run a Claude-first workflow for non-trivial repository changes when durable delegated output justifies longer latency and bounded Codex review. Avoid tiny or urgent edits, ordinary questions, read-only or interactive debugging, latency-sensitive work, and environments without reliable Claude isolation or review evidence.
---

# AI Coding Workflow

## Applicability Gate

Use this Skill for multi-file or multi-phase implementation, mechanical batches,
assigned tests, or long validation. Otherwise record
`workflow bypassed: <reason>` and use ordinary local tools.

In a bootstrapped repository, managed `AGENTS.md` is authoritative. Load only
the one reference needed for the current operation. For end-to-end work, load
references sequentially as phase boundaries change; do not load multiple
references preemptively.

## Core Contract

- Follow `OBSERVE -> ROUTE -> PLAN -> DISPATCH -> EXECUTE -> VERIFY -> REVIEW`.
- `claude-first` is the default: Codex owns core planning in the Task Card and
  bounded semantic review; Claude owns implementation, revision, and
  assigned Builder or Checker/Test work. `solution-planner` is explicit opt-in,
  never an automatic route. Explicit human ownership remains authoritative.
- Gather bounded deterministic evidence before model work. Treat every model
  report as a claim until diff, receipt, and validation evidence agree.
- Keep one writing owner, exact write scope, isolated worktrees, and hash-bound
  continuation/recovery. Missing or stale ownership evidence fails closed.
- Models never merge. Human approval remains required for destructive,
  production-impacting, or materially ambiguous decisions.

## Minimal Procedure

1. For concrete indexed-code symbols/relationships, query worktree-valid
   CodeGraph once. Use `ai/locate-code.py` for behavior/files; use lexical
   search for Shell/config/text/unsupported languages. Record result/skip
   before targeted reads.
2. Route from current facts before creating a card. Load routing policy only
   when ownership or Spark behavior is relevant.
3. Keep Codex's short plan in the Task Card. Treat it as Codex's only normal
   handwritten workflow artifact; deterministic helpers serialize routing,
   review, freeze, receipt, and continuation artifacts. Load task-card policy
   for details.
4. Dispatch with `bash ai/dispatch-to-claude.sh <card>` and use the runtime
   reference for host retry, monitoring, continuation, or failure attribution.
5. Verify deterministically. Use Checker/Test only when assigned test or
   validation work materially reduces Codex effort.
6. Review bounded evidence and return accept, revise, split, or reject. Humans
   perform final merge.

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
