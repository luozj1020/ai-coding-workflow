---
name: ai-coding-workflow
description: Install, update, or operate a local multi-agent coding workflow for software repositories. Use for Codex and Claude Code collaboration, Spark routing, AGENTS.md or CLAUDE.md setup, task cards, evidence packets, isolated worktrees, execution monitoring, review, and LSP/CodeGraph/MCP-first repository work.
---

# AI Coding Workflow

Use progressive disclosure. Read this file for the core workflow, then load only the reference named for the current operation. In an already-bootstrapped repository, its managed `AGENTS.md` is the authoritative project policy.

## Entry Points

Install or update the user-level skill:

```bash
python scripts/install_for_codex.py
```

Update the skill and refresh the current repository:

```bash
python scripts/update_skill.py --bootstrap-current
```

Bootstrap from an installed skill, then check readiness:

```bash
python ~/.codex/skills/ai-coding-workflow/scripts/install_workflow.py .
python ai/doctor_workflow.py
```

For guided setup, local-only installation, workflow-version repair, or automatic LSP/CodeGraph/Zoekt configuration, read `references/setup-policy.md` before acting.

## Core Loop

Use OBSERVE → ROUTE → PLAN → DISPATCH → EXECUTE → VERIFY → REVIEW → LEARN.

1. **OBSERVE:** Prefer LSP, bounded locator, initialized CodeGraph, MCP, and targeted snippets over broad reads. In large repositories use `ai/locate-code.py`; do not retry a broad CodeGraph timeout.
2. **ROUTE:** Before every full initial or revised task card, use a short Spark `execution-cost-estimator`/`preflight-bundle` brief unless a deterministic tiny skip applies. Decide Codex fast path, Claude delegation, spec-first, or clarification before paying task-card cost.
3. **PLAN:** When delegation is selected, write `ai/task-card-template.md`; create a spec first for ambiguous product/API/UX direction. Make phase ownership, acceptance, stop conditions, forbidden paths, and narrow validation explicit.
4. **DISPATCH:** Run `bash ai/dispatch-to-claude.sh <task-card>`. Codex owns the full card; Claude receives a compact execution view and works in an isolated worktree.
5. **EXECUTE:** Builder Claude implements one responsibility. Codex reviews direction before Checker/Test Claude writes or runs assigned tests.
6. **VERIFY:** Prefer exact task-card checks. Preserve diff, report, progress, validation, and failure evidence; prose alone never proves completion.
7. **REVIEW:** Codex returns accept, revise, split, reject, or a threshold-supported scoped takeover. No model merges automatically.
8. **LEARN:** Carry evidence-backed decisions and reusable failure patterns into the next bounded iteration.

Use `aiwf efficient prepare`, `aiwf dispatch-efficient --execute`, and `aiwf efficient review` for quota/latency-controlled runs. Without `--execute`, dispatch is preview-only. `aiwf loop` remains the compatible legacy loop.

## Non-Negotiable Rules

- Codex designs and reviews. Claude edits. Spark supplies bounded auxiliary judgment. Humans control merge.
- Split Builder implementation from Checker/Test validation when validation matters. A combined implementation/test/broad-validation card requires an explicit `mixed-exception` rationale.
- A non-blocking acknowledgement that recommends `proceed` must continue editing in the same run; acknowledgement-only is no implementation progress.
- One Claude failure is not takeover permission. Classify the attempt, preserve useful work, tighten once, and apply the current-task repeated-failure threshold. Prior-session failures do not transfer automatically.
- A permission/tool approval blocker, transport failure before useful progress, dirty source, or stale HEAD is an orchestration/environment condition, not model failure.
- Dirty source or stale HEAD is a delegation blocker, not a takeover trigger. Restore a trustworthy dispatch base first.
- Missing Claude prose is an evidence gap. Recover from a matching diff and deterministic checks when possible; seeded/fallback reports never count as Claude completion.
- Do not spend Codex turns polling unchanged heartbeats. Use the persistent monitor and inspect compact transitions at review or terminal boundaries.
- For local repository work, do not browse the web unless the user requests internet lookup, remote state, external documentation, or current third-party facts.
- Destructive/high-risk actions require explicit human authority. Spark cannot satisfy acceptance, approve review, authorize merge, or silently fall back to a stronger model.

## Ownership Routing

Calibrate Spark's raw upper edit estimate by 1.5 normally and 2.0 for tests/fixtures, orchestration, or cross-platform work. Use repository-scale gates:

| Scale | Ordinary Codex gate | Concentrated core-semantic gate |
|---|---:|---:|
| small | 100 lines / 2 files | no expansion |
| medium | 100 / 2 | 250 / 3 |
| large | 150 / 3 | 500 / 5 |
| giant | 200 / 3 | 500 / 5 |

Large/giant core-semantic work favors Codex when Claude would reacquire context and Codex would fully rereview it. Tests/checker work, mechanical batches, long validation/log/evidence processing, and independent auxiliary units above one file or 50 calibrated lines favor Claude. Risk raises isolation, validation, review, or approval rigor and may bias only toward Codex; it must never push work from Codex to Claude. Actual edits may exceed prediction while scope, solution, and context remain stable.

Read `references/routing-and-spark.md` before invoking Spark, handling Spark failure, changing result/diagnostic modes, or authorizing Spark writes.

## Claude Execution and Recovery

Prefer dense execution cards: one responsibility, 1–5 exact files, named symbols, one source-of-truth example, forbidden paths, measurable acceptance, and narrow validation. `execution-only` requires both context-sufficient and execution-only-eligible markers.

When Claude is quiet or produces zero output, attribute the condition before retrying: ambiguity, mixed roles, dirty base, permission/tool approval blocker, long validation, missing progress, transport, or true no-progress. Useful on-plan diff/report/progress favors waiting or same-worktree continuation. Interrupt for confirmed deviation or corroborated lack of useful progress.

Read `references/claude-runtime.md` before dispatch diagnostics, health checks, timeout changes, monitoring, retry/takeover, advisor continuation, or report reconstruction. Read `references/review-policy.md` for direction/final review and `references/loop-model.md` for the full state machine.

## Task Cards, Worktrees, and Parallelism

Read `references/task-card-policy.md` before authoring specs, task cards, Context Packets, JSON task cards, or final evidence. Use exact validation commands and keep long-lived state under `.worktrees/` or `ai/plans/<task-id>/`.

Read `references/worktree-and-parallel.md` before dirty-source restoration, managed worktree reuse/reset, large-repository evidence tradeoffs, interrupted-run cleanup, checker reuse, or parallel dispatch. Parallel execution is opt-in, normally capped at two, and requires independent write scopes/contracts plus serial review and human merge.

## When To Load More

Load only the directly relevant first-level reference:

| Need | Reference |
|---|---|
| Install, update, bootstrap, doctor, environment tools | `references/setup-policy.md` |
| Spark routing, modes, failures, diagnostics, writing | `references/routing-and-spark.md` |
| Claude execution, probe, monitoring, retry, takeover | `references/claude-runtime.md` |
| Dirty worktrees, large repositories, parallel DAGs | `references/worktree-and-parallel.md` |
| Specs, gates, Context Packets, JSON cards, evidence | `references/task-card-policy.md` |
| Retrieval order and context budgets | `references/mcp-policy.md` |
| Loop states and stop conditions | `references/loop-model.md` |
| Builder/Checker and Codex review decisions | `references/review-policy.md` |
| Roles and handoff model | `references/operating-model.md` |
| Metrics and regression comparison | `references/benchmark-policy.md` |

Do not load all references preemptively. For local command detail, prefer installed `ai/README.md` or repository `README.md` rather than expanding default skill context.
