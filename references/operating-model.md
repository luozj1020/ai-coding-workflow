# Operating Model

## Core Principle

**Codex freezes intent and reviews. Claude plans, implements, revises, tests, and validates. Tools gather low-token evidence first.**

This is the default operating principle for all work in this workflow:

1. Codex/GPT is responsible for a bounded intent freeze, high-risk decisions, and final semantic review.
2. Claude Code is the default source-writing owner and handles solution planning, exploratory implementation, mechanical batches, revisions, assigned tests, and long validation.
3. LSP, bounded locator search, CodeGraph, and MCP tools are used before broad file reads or repository scans to reduce token consumption and wall-clock stalls.

## Agent Roles

### Codex / GPT  -  Intent Freezer and Reviewer

- Routes every initial, revision, split-child, and next-phase brief before any delegation card.
- Avoids implementation unless the human selects it, confirmed high-risk core semantics require it, or a reviewed correction is deterministic and local.
- Decomposes only positively delegated work into short component cards with clear acceptance criteria.
- Reviews execution evidence and returns structured accept / revise / split / reject decisions with explicit next-loop instructions.
- Evaluates architectural intent, regression risk, and design coherence.
- Gathers context using low-token tools (LSP, `ai/locate-code.py`, bounded CodeGraph, MCP) during the OBSERVE phase.
- May apply reviewer-owned bounded corrections after a fresh route when the accepted context and deterministic delta are already known.

### Claude Code  -  Primary Planner and Execution Agent

- Produces a validated structured solution contract for eligible bounded open multi-phase work, without source edits.
- Implements frozen, exploratory, mechanical, core, and auxiliary task cards in isolated git worktrees.
- Runs only assigned narrow checks, tests, long validation, or evidence processing.
- Produces evidence packets documenting what changed, why, and how it was verified.
- Records assumptions, attempted commands, failed checks, and lessons learned.
- Works within the LSP/locator/CodeGraph/MCP evidence hierarchy to minimize unnecessary file reads.
- Receives source-writing by default under `claude-first`; single-task wall time is advisory when the user runs independent projects in separate terminals.
- Returns compressed evidence: summaries and artifact paths, not pasted large logs or full files.

### Claude-Compatible Models  -  High-Token Execution Helpers

- Assist with exhaustive diff scanning, long log analysis, and test suggestion generation.
- Useful for tasks that require processing large amounts of text or code.
- Optional  -  invoked when the task warrants the token cost.

### LSP / Locator / Codegraph / MCP  -  Low-Token Project Intelligence

- First-choice information source before reading files or scanning repositories, with `ai/locate-code.py` preferred for initial large-repository code location.
- Provides definitions, references, diagnostics, callers, callees, and impact analysis.
- Dramatically reduces token consumption compared to whole-file reads.
- See `mcp-policy.md` for the retrieval order.

## Loop Workflow

The workflow is an explicit loop, not a linear handoff. See `references/loop-model.md` for the full state machine.

```text
OBSERVE -> ROUTE -> PLAN/DIRECT -> EXECUTE -> VERIFY -> REVIEW
                                                     |
                                                     +-- accept -> DONE
                                                     +-- revise -> PLAN (next iteration)
                                                     +-- split  -> PLAN (child cards)
                                                     +-- reject -> OBSERVE (re-plan)
```

Each iteration:

1. **OBSERVE:** Codex gathers context using low-token tools.
2. **ROUTE:** deterministic facts select a Claude role by default; explicit/high-risk core work may select Codex. Spark can replace Codex estimation.
3. **PLAN/DIRECT:** local components compose a short Claude card; Codex reviews only goal, boundaries, acceptance, and critical invariants.
4. **EXECUTE:** Claude produces its assigned durable result in an isolated worktree; Codex direct is exceptional.
5. **VERIFY:** local deterministic tools run by default; Checker/Test Claude is conditional.
6. **REVIEW:** Codex evaluates the evidence and decides.
7. **LEARN:** Both agents capture lessons from the iteration.

## Task Card and Evidence Packet Handoff Model

### Task Card

A task card is a compact execution contract for one Claude unit. Local facts and
components build it after routing; Codex should not write a monolithic card.

Fields:

- **Goal**  -  what needs to be accomplished
- **Context**  -  background, related work, constraints
- **Acceptance criteria**  -  how to verify the work is complete
- **Files / modules**  -  the scope of changes expected
- **Codex context budget**  -  estimated token budget for Codex context gathering; 0 if LSP/locator/CodeGraph evidence is sufficient
- **LSP / locator / CodeGraph evidence**  -  structured low-token evidence gathered before implementation
- **High-token work route**  -  economic owner decision; size alone never forces Claude
- **Evidence compression requirements**  -  instructions for Claude to return summaries + artifact paths, not pasted logs
- **Dependencies**  -  other task cards, external services, data requirements
- **Evidence**  -  LSP/locator/CodeGraph/MCP data gathered before implementation
- **Loop context**  -  parent task ID, iteration, prior decision, revision instructions, budget/stop conditions, required evidence

Default authoring: the deterministic selector chooses a preset from routing
facts, Spark may fill structured gaps, and Codex reviews only material fields.
The monolithic `ai/task-card-template.md` is compatibility-only.

### Evidence Packet

An evidence packet documents routed execution and verification. Local tools, Claude, and Codex may each contribute bounded evidence; Codex and the human consume the final packet.

Fields:

- **Task card reference**  -  which task card was executed
- **Summary**  -  what was done in one paragraph
- **Context budget used**  -  actual token budget consumed by Codex during planning vs task card target
- **High-token work delegated**  -  list of high-token tasks explicitly delegated to Claude
- **Compressed evidence summary**  -  summaries and artifact paths instead of pasted large logs
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
   Deterministic ROUTE
       |
       +-- explicit/high-risk Codex -> implementation
       |
       +-- default Claude role -> short Task Card -> isolated worktree
       |
       v
  Evidence Packet (bounded results + diff/checks when applicable)
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
2. Bounded locator search with `ai/locate-code.py` for large-repository code location
3. CodeGraph callers/callees/dependencies/impact radius for concrete files or symbols
4. Targeted search (grep, ripgrep)
5. Targeted snippet reads
6. Whole-file reads only when necessary
7. Full repository scan only with explicit human approval

This applies to both Codex (during OBSERVE) and Claude Code (during EXECUTE).
