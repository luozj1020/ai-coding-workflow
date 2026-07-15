---
name: ai-coding-workflow
description: Install, update, or operate a local Codex, Claude Code, and Spark coding workflow with routing, task cards, isolated worktrees, monitoring, review, and low-token repository evidence.
---

# AI Coding Workflow

In a bootstrapped repository, its managed `AGENTS.md` is authoritative. Do not
repeat those rules in working context. Use this file only as an entrypoint and
load one directly relevant reference when needed.

## Default Loop

Use `OBSERVE -> ROUTE -> PLAN -> DISPATCH -> EXECUTE -> VERIFY -> REVIEW`.

1. Gather bounded local evidence with LSP, `ai/locate-code.py`, targeted reads,
   initialized CodeGraph, or MCP. Avoid broad reads and unsolicited web search.
2. Before any initial/revised/narrowed card, ROUTE from a short current brief.
   Use deterministic tiny routing or optional Spark before paying card cost.
3. Prefer Codex direct work when delegation cannot reduce context reacquisition,
   semantic rereview, or total control-plane work. Prefer Claude for mechanical
   batches, independent units, assigned tests, long validation/evidence work, or
   changes where Codex can review selectively.
4. For delegation, read `ai/task-card-components/catalog.md`, choose one preset
   plus material gates, and run `python ai/compose_task_card.py ...`. Fill only
   the composed short card.
5. Dispatch with `bash ai/dispatch-to-claude.sh <card>`. Review direction before
   optional Checker/Test work. Humans merge.

## Hard Rules

- Spark is optional and advisory. No implicit strong-model fallback; it cannot
  satisfy acceptance, replace Claude implicitly, interrupt, approve, or merge.
- Checker/Test is conditional. Skip model dispatch when deterministic local
  evidence closes acceptance and no test changes are required.
- One Claude failure is not takeover authority. Classify it, preserve useful
  evidence, and tighten once. Transport/approval/dirty-base conditions are not
  model failures. Explicit human takeover remains authoritative.
- Do not poll unchanged Claude heartbeats with Codex turns. Use persistent local
  monitoring and bounded terminal/review summaries.
- Dirty source/stale HEAD blocks reliable delegation; restore or obtain explicit
  authority. Reviewer-owned bounded correction requires a fresh revision ROUTE.
- No model merges. Destructive and production-impacting actions require explicit
  human authority.

## Setup

```bash
python scripts/install_for_codex.py
python scripts/update_skill.py --bootstrap-current
python ~/.codex/skills/ai-coding-workflow/scripts/install_workflow.py .
python ai/doctor_workflow.py
```

Use `aiwf efficient prepare`, `aiwf dispatch-efficient --execute`, and
`aiwf efficient review` only when their extra artifacts have expected decision
value. `aiwf loop` remains the compatibility path.

## Reference Router

| Operation | Load |
|---|---|
| install/update/bootstrap/environment tools | `references/setup-policy.md` |
| ownership routing or Spark invocation/failure/writes | `references/routing-and-spark.md` |
| task cards/specs/Context Packets/evidence | `references/task-card-policy.md` |
| Claude probes, timeouts, monitoring, retry attribution | `references/claude-runtime.md` |
| Builder/Checker, review, bounded correction, takeover | `references/review-policy.md` |
| worktrees, dirty restoration, continuation, parallelism | `references/worktree-and-parallel.md` |
| retrieval order and context budgets | `references/mcp-policy.md` |
| loop state machine | `references/loop-model.md` |
| metrics and regression comparison | `references/benchmark-policy.md` |

For command detail, prefer installed `ai/README.md`; do not load multiple
references preemptively.
