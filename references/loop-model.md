# Loop Model

## Overview

The AI coding workflow is an explicit observe/plan/execute/verify/review/learn loop. Each iteration produces concrete artifacts and either completes the task or feeds structured instructions into the next iteration.

Core principle:

**Codex freezes intent and reviews. Claude Code converges the solution contract,
implements, revises, tests, and validates. LSP/locator/CodeGraph/MCP tools gather
low-token evidence first.**

## State Machine

```text
OBSERVE -> ROUTE -> PLAN/DIRECT -> EXECUTE -> VERIFY -> REVIEW -> LEARN
                                                             |
                        +------------------------------------+------------------------------------+
                        |                                    |                                    |
                     ACCEPT                               REVISE                               SPLIT
                        |                                    |                                    |
                       DONE                          PLAN next iteration                 PLAN child tasks
                                                             |
                                                           REJECT
                                                             |
                                                          OBSERVE
```

The loop repeats until Codex accepts the work, the task is split or rejected for replanning, a stop condition is reached, or a human intervenes.

When a Claude round has a usable direction but lacks required evidence, route again
from the accepted diff and remaining delta before creating another card. The new
route may select local deterministic checks, conditional Checker/Test work,
same-worktree Claude continuation, or a reviewer-owned bounded Codex correction.
Do not assume another Claude round merely because the first owner was Claude.

For multi-phase or multi-part tasks, `ACCEPT` can mean "accepted this phase"
rather than "all requested work is done." Route every remaining frozen slice or
test responsibility independently back to Claude unless an explicit/high-risk
Codex exception applies.

When delegation materially reduces work, keep Claude roles separate:

- **Builder Claude** executes implementation tasks and produces direction evidence, not acceptance validation.
- **Codex direction review** decides whether the Builder direction matches the plan, whether to keep waiting, interrupt and narrow, re-dispatch, or enter takeover after repeated current-task failure.
- **Checker/Test Claude** conditionally executes assigned test-writing, long validation, or evidence tasks after Codex accepts the Builder direction.
- **Codex final review** checks validation artifacts and may run a second verification pass before acceptance.

Do not treat every quiet or incomplete Claude run as a Claude execution failure. First classify the stall: task-card ambiguity, mixed Builder/Checker responsibilities, dirty source or stale HEAD, permission/tool approval blocker, long-running validation, missing progress artifact, external environment, or true no-progress. A task that mixes implementation, test writing, broad validation, and phase stop gates should normally be split before dispatch unless it is explicitly marked `mixed-exception`.

Dirty source or stale HEAD is a delegation blocker, not a takeover trigger. Restore a reliable base or obtain explicit dirty-source authority before another delegation. It does not override a fresh economic owner route; an independently selected Codex direct slice is not forced delegation takeover.

## States

## Unknowns Lifecycle

The loop treats unknowns as first-class planning and review evidence:

1. Codex records known facts, known unknowns, assumed-known constraints, and blindspot scan requests in the task card.
2. Codex marks decision gates where architecture, data model, UX, security, or scope could change.
3. Codex/local tools resolve the map by default; an eligible Claude planner or Builder checks only its delegated boundary and records new unknowns or conflicts.
4. Claude may proceed only where the composed task card grants authority; otherwise it stops and reports.
5. Codex reviews whether unknowns were resolved, decision gates were respected, and deviations from plan were justified.

## Handoff Contracts

Each handoff should be directly checkable:

- Codex -> Claude: `Execution Readiness Gate` confirms the task is implementation-ready, and `Handoff Contract` defines Must do, Must not do, May decide, Must report, and Stop condition.
- Claude -> Codex: `Plan Match`, `Validation Confidence`, `Reviewer Should Check`, `Unknowns and Deviations`, and `Reviewer Briefing` make the result reviewable without reconstructing intent from the diff alone.
- Codex -> next task: `Review-to-Next-Task Contract` carries forward context, what to keep, what to change, what not to repeat, new acceptance criteria, new unknowns/decision gates, and a new handoff contract.

### 1. OBSERVE

**Owner:** Codex / GPT

**Purpose:** Gather context about the codebase and the problem before planning.

**Actions:**

- Use LSP to find definitions, references, and diagnostics.
- Use codegraph to find callers, callees, dependencies, and impact radius.
- Use MCP tools for structured project context where available.
- Use targeted search for strings, configs, and docs.
- Use targeted snippet reads for specific lines.
- Read whole files only when lower-cost evidence is insufficient.
- Scan the full repository only with explicit human approval.

**Output:** Context summary attached to the task card under Evidence.

### 2. ROUTE

**Owner:** Codex / deterministic local router

**Purpose:** Select the least expensive owner before any delegation artifact.

**Output:** Codex direct, Claude solution planner, Claude batch/auxiliary Builder,
conditional Checker/Test, or human clarification. Optional Spark advice is used
only for a concrete economically uncertain Claude candidate.

### 3. PLAN

**Owner:** Codex / GPT

**Purpose:** Compose a short Claude card from routing facts, or bind an explicit Codex exception.

**Actions:**

- For explicit Codex direct, stop before card authoring and edit from the reviewed brief.
- For delegation, read `ai/task-card-components/catalog.md`, select a preset plus material gates, and compose the short card locally.
- For a revision, use the `revision` preset and record only the delta against accepted evidence.
- For a split, decompose into smaller task cards.
- For a reject, replan from updated evidence instead of patching blindly.

**Output:** A Codex implementation brief, or one filled delegation card with acceptance criteria, scoped files/modules, evidence, loop metadata, and stop conditions.

### 4. DISPATCH

**Owner:** Human or workflow script

**Purpose:** Send the default Claude task card to an isolated worktree. Explicit Codex direct skips this state.

**Actions:**

- Create an isolated git worktree.
- Copy the task card into the worktree.
- Invoke Claude Code in non-interactive mode.
- Save result, status, diffstat, and diff artifacts.
- Track heartbeat activity from both artifact growth and implementation worktree changes.
- Do not merge automatically.

**Output:** Worktree plus execution artifacts under `.worktrees/`.

**Partial-progress triage:** If Claude appears quiet during early waiting rounds but the worktree has implementation changes, treat that as progress evidence. Codex or the human should review the partial diff against the task card. Continue waiting when the change direction matches the plan; interrupt only when the partial implementation is off-plan, risky, or no longer making useful progress.

**Stall attribution:** Before interrupting or taking over, inspect `*.progress.log`, `*.status.txt`, `*.claude-progress.md`, `CLAUDE_TASK_CARD.md` checklist changes, `CLAUDE_REPORT.md`, and partial diff/status. Permission denials, sandbox write failures, forbidden-file rules, missing CLI/auth, network restrictions, and human-approval requirements are orchestration or environment blockers, not proof that Claude cannot execute the task.

**Adaptive timeout:** The first loop should have enough time for context gathering and first implementation, typically the fixed dispatch timeout. Later loops may estimate timeout from progress evidence: elapsed seconds divided by completed checklist items, multiplied by remaining checklist items plus buffer. If the human or environment sets an explicit timeout, scripts should respect it.

### 5. EXECUTE

**Owner:** Codex by default; Claude only for the positively routed responsibility

**Purpose:** Produce the routed durable implementation or structured planning artifact.

**Actions:**

- Claude implements core and auxiliary work from the short contract; explicit Codex direct creates no card.
- Claude reads the composed short card before acting.
- Prefer LSP/locator/CodeGraph/MCP evidence before broad file reads.
- Check Task Mode, Testing Responsibility, and Stall / Ambiguity Triage before editing.
- If one task card mixes Builder and Checker/Test responsibilities without `mixed-exception`, stop-and-report with a split recommendation.
- Record permission/tool approval blockers explicitly instead of waiting silently.
- Make scoped file changes.
- Run only narrow sanity checks explicitly assigned to the Builder task.
- Record assumptions, attempted commands, and failed checks.
- Update `CLAUDE_PROGRESS.md` and the `CLAUDE_TASK_CARD.md` progress checklist after completing assigned items.
- Keep builder and checker responsibilities separate: implementation happens in the Builder phase, validation and test evidence happen in the Checker/Test phase.

**Output:** Modified files in the isolated worktree.

### 6. DIRECTION REVIEW

**Owner:** Codex / GPT

**Purpose:** Decide whether the Builder implementation direction should proceed to testing.

**Actions:**

- Compare the partial or final diff against Goal, Handoff Contract, Acceptance Criteria, Unknowns, and Decision Gates.
- Continue waiting when worktree changes and progress updates match the plan.
- Interrupt and narrow the task when the implementation is off-plan, risky, or scope-expanding.
- Dispatch a Checker/Test task only after the Builder direction is accepted.
- Route a deterministic local correction separately when Codex already holds the accepted context; reserve failure-based takeover for the current-task threshold or explicit human direction.

**Output:** Accept direction, revise Builder task, split, reject, or dispatch Checker/Test task.

### 7. CHECKER / TEST

**Owner:** Local deterministic tools by default; Checker/Test Claude only when its assigned work materially reduces Codex effort

**Purpose:** Write assigned tests, run validation, and report mechanical evidence.

**Actions:**

- Write or update tests when assigned.
- Run tests, lint, type checks, and build checks listed in the task card.
- Run `ai/check-worktree.sh` when available to produce checker-only validation evidence.
- Compare results against the acceptance criteria.
- Capture verification output, checker report paths, key original failure lines, and known gaps.
- Preserve failed command, exit code, and `file:line` details without lossy summarization.
- Avoid broad implementation rewrites; make only small fixes explicitly allowed by the task card when validation exposes a concrete defect.

**Output:** Evidence packet from `ai/evidence-packet-template.md`.

**Testing responsibility:** Codex decides in the task card whether test code is part of the task, whether Checker/Test Claude must run tests, or whether Codex/human will run verification after Claude. If tests are not required, the task card must say so explicitly.

### 8. REVIEW

**Owner:** Codex / GPT

**Purpose:** Evaluate the evidence and decide the next loop transition.

**Actions:**

- Check whether implementation matches intent.
- Assess regression risk, design coherence, and security implications.
- For a still-running dispatch, review visible partial worktree changes before deciding whether to interrupt.
- Return a structured decision.
- Provide explicit next-loop instructions.

**Output:** One decision: `ACCEPT`, `REVISE`, `SPLIT`, or `REJECT`.

**Constraint:** Codex does not implement fixes during ordinary review. It evaluates and decides unless the loop has reached the direct-intervention threshold.

### 8. LEARN

**Owner:** Codex and Claude Code

**Purpose:** Preserve reusable knowledge from the iteration.

**Actions:**

- Record what worked.
- Record what failed.
- Capture assumptions and review feedback.
- Feed lessons into the next OBSERVE or PLAN phase.

**Output:** Lessons in the evidence packet or project-level workflow notes.

### 9. DONE

**Owner:** Human

**Purpose:** Final approval and merge.

**Actions:**

- Review accepted changes.
- Approve any high-risk actions.
- Merge manually.

## Role Responsibilities

| Phase | Codex / GPT | Claude Code | Human |
|-------|-------------|-------------|-------|
| OBSERVE | Gather low-token context | N/A | Provide context |
| PLAN | Create or revise task cards | N/A | Approve plan if needed |
| DISPATCH | N/A | N/A | Trigger dispatch or runner |
| EXECUTE | Direction review during progress | Builder edits files | N/A |
| DIRECTION REVIEW | Accept direction, wait, interrupt, revise, or take over | Update progress/report | N/A |
| CHECKER / TEST | Dispatch validation task and review risk | Write/run assigned tests and report evidence | N/A |
| REVIEW | Decide accept/revise/split/reject | N/A | Override if needed |
| LEARN | Capture planning lessons | Capture execution lessons | N/A |
| DONE | N/A | N/A | Merge and close |

## Artifacts

| Artifact | Owner | Location |
|----------|-------|----------|
| Task card | Codex / GPT | `ai/task-cards/*.md` |
| Evidence packet | Claude Code | `ai/evidence-packet-template.md` format |
| Dispatch result | Script | `.worktrees/claude-<timestamp>.*` |
| Checker report | Script / Claude Code | `.worktrees/claude-<timestamp>.checker-report.md` |
| Loop events | Script | `.worktrees/loop-<timestamp>/loop-events.jsonl` |
| Review decision | Codex / GPT | Review output |
| Loop run directory | Script | `.worktrees/loop-<timestamp>/` |

## Decisions

### ACCEPT

The implementation is correct and complete. Human review and merge can proceed.

### REVISE

The implementation is close but needs specific changes. Codex must provide actionable revision instructions, then the loop returns to PLAN for the next iteration.

### SPLIT

The task is too broad or mixes concerns. Codex decomposes it into smaller child task cards. Each child task enters its own loop.

### REJECT

The approach is wrong or unsafe. The loop returns to OBSERVE with rejection reasoning as new context.

## Stop Conditions

A loop stops when any condition is met:

1. Codex returns `ACCEPT`.
2. Codex returns `SPLIT` and child tasks must be planned.
3. Codex returns `REJECT` and replanning is required.
4. Maximum iterations are reached.
5. Token, time, or diff-size budget is exceeded.
6. A high-risk action requires human approval.
7. The human stops the loop.
8. The same failure appears in two consecutive iterations.
9. A fix causes a previously passing check to fail.
10. Failure count does not decrease for two consecutive iterations.
11. The blocker is environmental or external rather than fixable in the repository.

When a stop condition is reached without acceptance, the task is escalated to the human with the latest evidence and review output.

## Codex Direct Intervention Threshold

Codex may directly edit after Claude has made multiple unsuccessful attempts and another revision is unlikely to improve the result. Valid triggers are max iterations reached, the same failure in two consecutive iterations, failure count not decreasing for two consecutive iterations, repeated timeout/unavailability after delegation restoration, or an explicit human request. Codex must record the attempts, takeover reason, touched scope, and validation evidence.

A no-progress or failed Claude iteration is not automatically a takeover trigger
and does not automatically require another Claude card. First classify the round
and run a fresh owner route over the remaining delta. Transport/approval failures
favor restoration; a still-valuable Claude candidate may receive one tighter
same-worktree continuation; a deterministic correction already in Codex context
may route directly. Failure-based takeover remains reserved for threshold hits or
explicit human direction.

A dirty source or stale HEAD dispatch blocker is handled before this threshold audit. Record the Delegation Restoration Gate, restore the base when possible, then re-dispatch Claude.

Threshold evidence is task-scoped. Prior-session failures are useful context, but a new session starts by re-establishing the loop unless the task card carries specific artifact links proving the same current task already hit the threshold.

## Loop Metadata

Each task card can carry:

- Parent task ID
- Iteration number
- Prior decision
- Revision instructions
- Budget and stop conditions
- Required evidence

## Learning Feedback Path

```text
Codex planning lessons -> next OBSERVE / PLAN
Claude execution lessons -> next EXECUTE / VERIFY
Review decisions -> next task card
Failed approaches -> future evidence gathering
```

Lessons should remain concise and reusable. Project-wide rules should only be updated when a lesson repeats or materially reduces future risk.

## Context Lifecycle

Loop context is append-only and file-backed:

- `loop-events.jsonl` records orchestration events without rewriting history.
- `CLAUDE_PROGRESS.md` keeps Goal, Current Phase, Next Check, Blocker, and Last Update near the top so long tasks stay anchored.
- Large logs and diffs remain as artifact files; prompts should reference paths and summaries instead of pasting them wholesale.
- Failed commands and observations are preserved because later recovery depends on that evidence.
