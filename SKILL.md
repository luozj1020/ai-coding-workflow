---
name: ai-coding-workflow
description: Install, update, or operate a Claude-first local coding workflow for non-trivial repository changes when Codex quota is scarce, a cost-efficient Claude Code compatible model is available, durable delegated output is useful, and longer single-task latency is acceptable (especially across multiple user-managed terminals). Do not use it for tiny or urgent edits, ordinary code questions, read-only analysis, tight interactive debugging, latency-sensitive single-task work, or environments without reliable Claude execution, isolation, and review evidence.
---

# AI Coding Workflow

In a bootstrapped repository, managed `AGENTS.md` is authoritative. Do not
repeat it in working context. Load one relevant reference when needed.

## Applicability Gate

Use when delegation materially reduces Codex work: multi-file/multi-phase
features, batches, assigned tests, or long validation.

For tiny/urgent edits, code questions, read-only or interactive debugging, or
unreliable Claude/isolation/evidence, record `workflow bypassed: <reason>` and
use ordinary Codex/local tools. Do not compose a card or invoke Spark for bypass.

## Default Loop

Use `OBSERVE -> ROUTE -> PLAN -> DISPATCH -> EXECUTE -> VERIFY -> REVIEW`.

1. Gather bounded local evidence with LSP, `ai/locate-code.py`, targeted reads,
   initialized CodeGraph, or MCP. Avoid broad reads and unsolicited web search.
2. ROUTE from bounded facts before writing an execution artifact. The default
   profile is `claude-first`: Claude owns implementation while Codex spends one
   bounded turn freezing intent and one bounded semantic review. Use
   `ownership_profile=economy-first` only when single-task latency or total
   model usage matters more than preserving Codex quota.
3. Give Claude one convergent `solution-planner` pass for an open multi-phase
   feature, freeze the contract after one Codex adversarial review, then route
   its implementation slices back to Claude. Use `exploratory-builder` for a
   bounded new feature with an unclear implementation path, `batch-builder` for
   mechanical work, and `execution-builder` for an already-frozen solution.
   Codex direct editing is reserved for explicit human ownership, confirmed
   high-risk core semantics, or a reviewer-owned deterministic correction.
4. For delegation, read `ai/task-card-components/catalog.md`, choose one preset
   plus material gates, and run `python ai/compose_task_card.py ...`. Fill only
   the composed short card. `aiwf run` performs this after the positive route,
   inlines bounded context once, and dispatches the Markdown card rather than
   the source Task JSON.
5. Dispatch with `bash ai/dispatch-to-claude.sh <card>`. Continue useful work in
   the same worktree once before takeover; review bounded terminal evidence
   before optional Checker/Test work. Humans merge.

## Hard Rules

- Spark is structured and advisory. Use it to replace Codex estimation,
  task-card shaping, or monitor interpretation. It has no implicit strong-model
  fallback and cannot satisfy acceptance, interrupt, approve, or merge.
- Checker/Test is conditional. Skip model dispatch when deterministic local
  evidence closes acceptance and no test changes are required.
- One Claude failure is not takeover authority. Classify it, preserve useful
  evidence, and tighten once. Transport/approval/dirty-base conditions are not
  model failures. Explicit human takeover remains authoritative.
- Do not poll unchanged Claude heartbeats with Codex turns. Use persistent local
  monitoring and bounded terminal/review summaries.
- Dirty source/stale HEAD blocks reliable delegation; restore or obtain explicit
  authority. Prefer reviewed same-worktree Claude continuation over Codex edits.
- The Skill never coordinates portfolio concurrency. Run one repository workflow
  per user-managed terminal; do not create a cross-project DAG or scheduler.
- Treat Claude wall time as advisory in `claude-first`. Measure accepted output
  per Codex token; do not reject a productive Claude route merely for exceeding
  the direct-execution time ratio.
- No model merges. Destructive and production-impacting actions require explicit
  human authority.
- A frozen solution contract is reopened only by a blocking invariant/acceptance
  defect or an explicitly incorporated spec change. Recommendations go to backlog.
- State-backed continuation, routing, and review must consume hash-bound
  artifacts and fail closed on missing or stale evidence. Explicit human
  ownership remains authoritative. Load the matching reference for details.

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
| ownership, Owner Lease, Handoff Tax, or Spark | `references/routing-and-spark.md` |
| task cards/specs/Context Packets/evidence | `references/task-card-policy.md` |
| Claude probes, timeouts, monitoring, retry attribution | `references/claude-runtime.md` |
| Builder/Checker, Acceptance Graph/Receipt, review/takeover | `references/review-policy.md` |
| worktrees, lease continuation, dirty restoration, parallelism | `references/worktree-and-parallel.md` |
| retrieval order and context budgets | `references/mcp-policy.md` |
| loop state machine | `references/loop-model.md` |
| metrics, Handoff Tax calibration, regression comparison | `references/benchmark-policy.md` |

For command detail, prefer installed `ai/README.md`; do not load multiple
references preemptively.
