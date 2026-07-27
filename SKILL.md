---
name: ai-coding-workflow
description: Install, update, or operate a Claude-first local coding workflow for non-trivial repository changes when Codex quota is scarce, a cost-efficient Claude Code compatible model is available, durable delegated output is useful, and longer single-task latency is acceptable (especially across multiple user-managed terminals). Do not use it for tiny or urgent edits, ordinary code questions, read-only analysis, tight interactive debugging, latency-sensitive single-task work, or environments without reliable Claude execution, isolation, and review evidence.
---

# AI Coding Workflow

In a bootstrapped repository, managed `AGENTS.md` is authoritative. Do not
repeat it in working context. Load one relevant reference when needed.

## Applicability Gate

Use for multi-file/multi-phase work, batches, assigned tests, or long validation.
Otherwise record `workflow bypassed: <reason>` and use local tools.

## Default Loop

Use `OBSERVE -> ROUTE -> PLAN -> DISPATCH -> EXECUTE -> VERIFY -> REVIEW`.

1. Gather bounded local evidence with LSP, `ai/locate-code.py`, targeted reads,
   worktree-matched CodeGraph, or MCP. Avoid broad reads and unsolicited web search.
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
4. Compose one catalog preset plus material gates and fill only its short card.
   `--preview` is zero-model; ambiguity and high-impact actions need humans.
5. Dispatch with `bash ai/dispatch-to-claude.sh <card>`. Continue once in the
   same worktree before takeover, then review bounded evidence. Humans merge.

## Hard Rules

- Use one Spark call per non-Express Claude delegation: estimate unresolved
  ownership, otherwise audit the card. On Spark/Claude network exit 75, outer
  Codex retries at an authorized host with recorded continuation fields;
  cache success and never strong-fallback.
- Checker/Test is conditional. Bind runnable interface evidence, validate each
  test file immediately, or prefer deterministic checks.
- One Claude failure is not takeover authority. Classify it, preserve useful
  evidence, and tighten once. Transport/approval/dirty-base conditions are not
  model failures. Explicit human takeover remains authoritative.
- A takeover receipt is only a candidate. `aiwf prepare-takeover` must revoke
  ownership, stop/confirm old process trees, freeze a baseline, and issue the
  single-writer grant; unknown visibility fails closed.
- Enforce exact Write paths in real time. `editor-only` removes Bash; required
  enforcement never degrades to post-run auditing.
- Never poll Claude with `ps`, `tail`, clocks, or Codex turns. Block on
  `monitor-claude.sh wait`. Implementation claims are readiness, not writes;
  Spark compresses ambiguous idle JSON while raw logs stay file-backed.
- Dirty source requires clean restoration or an explicit hash-bound snapshot;
  stale HEAD blocks. Prefer reviewed same-worktree Claude continuation.
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
