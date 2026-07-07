# Loop Model

## Overview

The AI coding workflow is an explicit observe/plan/execute/verify/review/learn loop. Each iteration produces concrete artifacts and either completes the task or feeds structured instructions into the next iteration.

Core principle:

**Codex designs and reviews. Claude Code edits. LSP/codegraph/MCP tools gather low-token evidence first.**

## State Machine

```text
OBSERVE -> PLAN -> DISPATCH -> EXECUTE -> VERIFY -> REVIEW -> LEARN
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

## States

## Unknowns Lifecycle

The loop treats unknowns as first-class planning and review evidence:

1. Codex records known facts, known unknowns, assumed-known constraints, and blindspot scan requests in the task card.
2. Codex marks decision gates where architecture, data model, UX, security, or scope could change.
3. Claude checks the map against the codebase, performs the requested blindspot pass, and records new unknowns or plan conflicts.
4. Claude may proceed autonomously only where the task card grants authority; otherwise it chooses a conservative path or stops and reports.
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

### 2. PLAN

**Owner:** Codex / GPT

**Purpose:** Create or revise a task card with clear acceptance criteria, scope, budget, and stop conditions.

**Actions:**

- Create a first-iteration task card from `ai/task-card-template.md`.
- For a revision, create a child task card or update loop context with prior review feedback.
- For a split, decompose into smaller task cards.
- For a reject, replan from updated evidence instead of patching blindly.

**Output:** One or more task cards with acceptance criteria, scoped files/modules, evidence, loop metadata, and stop conditions.

### 3. DISPATCH

**Owner:** Human or workflow script

**Purpose:** Send the task card to Claude Code in an isolated worktree.

**Actions:**

- Create an isolated git worktree.
- Copy the task card into the worktree.
- Invoke Claude Code in non-interactive mode.
- Save result, status, diffstat, and diff artifacts.
- Track heartbeat activity from both artifact growth and implementation worktree changes.
- Do not merge automatically.

**Output:** Worktree plus execution artifacts under `.worktrees/`.

**Partial-progress triage:** If Claude appears quiet during early waiting rounds but the worktree has implementation changes, treat that as progress evidence. Codex or the human should review the partial diff against the task card. Continue waiting when the change direction matches the plan; interrupt only when the partial implementation is off-plan, risky, or no longer making useful progress.

### 4. EXECUTE

**Owner:** Claude Code

**Purpose:** Make the concrete file edits required by the task card.

**Actions:**

- Read the task card fully before editing.
- Prefer LSP/codegraph/MCP evidence before broad file reads.
- Make scoped file changes.
- Run relevant checks after significant changes.
- Record assumptions, attempted commands, and failed checks.
- Keep builder and checker responsibilities separate: edits happen in the builder phase, validation and failure reporting happen in the checker phase.

**Output:** Modified files in the isolated worktree.

### 5. VERIFY

**Owner:** Claude Code

**Purpose:** Confirm that the implementation meets the task card's acceptance criteria.

**Actions:**

- Run tests, lint, type checks, and build checks where applicable.
- Run `ai/check-worktree.sh` when available to produce checker-only validation evidence.
- Compare results against the acceptance criteria.
- Capture verification output, checker report paths, key original failure lines, and known gaps.
- Preserve failed command, exit code, and `file:line` details without lossy summarization.

**Output:** Evidence packet from `ai/evidence-packet-template.md`.

### 6. REVIEW

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

### 7. LEARN

**Owner:** Codex and Claude Code

**Purpose:** Preserve reusable knowledge from the iteration.

**Actions:**

- Record what worked.
- Record what failed.
- Capture assumptions and review feedback.
- Feed lessons into the next OBSERVE or PLAN phase.

**Output:** Lessons in the evidence packet or project-level workflow notes.

### 8. DONE

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
| EXECUTE | N/A | Edit files | N/A |
| VERIFY | N/A | Run checks and produce evidence | N/A |
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

Codex may directly edit after Claude has made multiple unsuccessful attempts and another revision is unlikely to improve the result. Valid triggers are max iterations reached, the same failure in two consecutive iterations, failure count not decreasing for two consecutive iterations, repeated timeout/unavailability, or an explicit human request. Codex must record the attempts, takeover reason, touched scope, and validation evidence.

A no-progress or failed Claude iteration is not automatically a takeover trigger. The default next step is a sharper Claude task card: reduce scope, add diagnostics, require specific artifacts, and set stop-and-report gates. Takeover is reserved for threshold hits or explicit human direction.

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
