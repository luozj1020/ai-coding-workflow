# Operating Model

## Core Principle

**Codex designs and reviews. Claude edits. Tools gather low-token evidence first.**

This is the default operating principle for all work in this workflow:

1. Codex/GPT is responsible for top-level design, planning, and review.
2. Claude Code is responsible for concrete file modifications.
3. LSP, codegraph, and MCP tools are used before broad file reads or repository scans to reduce token consumption.

## Agent Roles

### Codex / GPT  -  Planner and Reviewer

- Decomposes large features into task cards with clear acceptance criteria.
- Reviews execution evidence and returns structured accept / revise / split / reject decisions with explicit next-loop instructions.
- Evaluates architectural intent, regression risk, and design coherence.
- Gathers context using low-token tools (LSP, codegraph, MCP) during the OBSERVE phase.
- Does NOT write production code directly  -  delegates all implementation to Claude Code.
- Does NOT implement fixes during review  -  only evaluates and decides.

### Claude Code  -  Execution Agent

- Implements task cards in isolated git worktrees.
- Makes concrete file edits.
- Runs mechanical checks: tests, lint, type checks, build verification.
- Produces evidence packets documenting what changed, why, and how it was verified.
- Records assumptions, attempted commands, failed checks, and lessons learned.
- Works within the LSP/codegraph/MCP evidence hierarchy to minimize unnecessary file reads.

### MiMo / DeepSeek  -  High-Token Execution Helpers

- Assist with exhaustive diff scanning, long log analysis, and test suggestion generation.
- Useful for tasks that require processing large amounts of text or code.
- Optional  -  invoked when the task warrants the token cost.

### LSP / Codegraph / MCP  -  Low-Token Project Intelligence

- First-choice information source before reading files or scanning repositories.
- Provides definitions, references, diagnostics, callers, callees, and impact analysis.
- Dramatically reduces token consumption compared to whole-file reads.
- See `mcp-policy.md` for the retrieval order.

## Loop Workflow

The workflow is an explicit loop, not a linear handoff. See `references/loop-model.md` for the full state machine.

```text
OBSERVE -> PLAN -> DISPATCH -> EXECUTE -> VERIFY -> REVIEW
                                                     |
                                                     +-- accept -> DONE
                                                     +-- revise -> PLAN (next iteration)
                                                     +-- split  -> PLAN (child cards)
                                                     +-- reject -> OBSERVE (re-plan)
```

Each iteration:

1. **OBSERVE:** Codex gathers context using low-token tools.
2. **PLAN:** Codex creates or revises a task card.
3. **DISPATCH:** Task card is sent to Claude Code in an isolated worktree.
4. **EXECUTE:** Claude Code implements the changes.
5. **VERIFY:** Claude Code runs checks and produces an evidence packet.
6. **REVIEW:** Codex evaluates the evidence and decides.
7. **LEARN:** Both agents capture lessons from the iteration.

## Task Card and Evidence Packet Handoff Model

### Task Card

A task card is a structured description of a single unit of work. It is created by the planner (Codex/GPT) or by a human, and consumed by the executor (Claude Code).

Fields:

- **Goal**  -  what needs to be accomplished
- **Context**  -  background, related work, constraints
- **Acceptance criteria**  -  how to verify the work is complete
- **Files / modules**  -  the scope of changes expected
- **Dependencies**  -  other task cards, external services, data requirements
- **Evidence**  -  LSP/codegraph/MCP data gathered before implementation
- **Loop context**  -  parent task ID, iteration, prior decision, revision instructions, budget/stop conditions, required evidence

Template: `ai/task-card-template.md`

### Evidence Packet

An evidence packet documents the execution of a task card. It is produced by Claude Code and consumed by the reviewer (Codex/GPT) and human.

Fields:

- **Task card reference**  -  which task card was executed
- **Summary**  -  what was done in one paragraph
- **Changes**  -  list of files modified with a brief description per file
- **Diffstat**  -  file-level change summary
- **Diff**  -  full patch
- **Assumptions**  -  decisions made without explicit guidance
- **Attempted commands**  -  commands run and their outcomes
- **Failed checks**  -  checks that failed and resolution status
- **Tests**  -  what tests were added or modified, pass/fail status
- **Verification**  -  lint, type check, build results
- **Verification evidence**  -  specific output from checks
- **Review feedback**  -  reviewer's decision and instructions (filled after review)
- **Lessons learned**  -  reusable knowledge from this execution
- **Open questions**  -  anything the executor wants the reviewer to consider

Template: `ai/evidence-packet-template.md`

### Handoff Flow

```
Human or Codex/GPT
       |
       v
   Task Card (with loop context)
       |
       v
  Claude Code (executor, isolated worktree)
       |
       v
  Evidence Packet (result JSON + .diff + lessons)
       |
       v
  Codex/GPT (reviewer, structured decision)
       |
       v
  Decision: accept / revise / split / reject
       |
       +-- revise -> new Task Card (next iteration)
       +-- split  -> child Task Cards
       +-- reject -> re-observe
       +-- accept -> Human (final merge)
```

## Evidence Hierarchy

Before reading files or scanning repositories, agents must follow this order:

1. LSP definitions/references/diagnostics
2. Codegraph callers/callees/dependencies/impact radius
3. Targeted search (grep, ripgrep)
4. Targeted snippet reads
5. Whole-file reads only when necessary
6. Full repository scan only with explicit human approval

This applies to both Codex (during OBSERVE) and Claude Code (during EXECUTE).
