# Operating Model

## Agent Roles

### Codex / GPT  -  Planner and Reviewer

- Decomposes large features into task cards with clear acceptance criteria.
- Reviews execution evidence and returns accept / revise / split / reject decisions.
- Evaluates architectural intent, regression risk, and design coherence.
- Does not write production code directly  -  delegates implementation.

### Claude Code  -  Execution Agent

- Implements task cards in isolated git worktrees.
- Runs mechanical checks: tests, lint, type checks, build verification.
- Produces evidence packets documenting what changed, why, and how it was verified.
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

Template: `ai/task-card-template.md`

### Evidence Packet

An evidence packet documents the execution of a task card. It is produced by Claude Code and consumed by the reviewer (Codex/GPT) and human.

Fields:

- **Task card reference**  -  which task card was executed
- **Summary**  -  what was done in one paragraph
- **Changes**  -  list of files modified with a brief description per file
- **Diffstat**  -  file-level change summary
- **Diff**  -  full patch
- **Tests**  -  what tests were added or modified, pass/fail status
- **Verification**  -  lint, type check, build results
- **Open questions**  -  anything the executor wants the reviewer to consider

Template: `ai/evidence-packet-template.md`

### Handoff Flow

```
Human or Codex/GPT
       │
       ▼
   Task Card
       │
       ▼
  Claude Code (executor, isolated worktree)
       │
       ▼
  Evidence Packet (result.json + diff.patch)
       │
       ▼
  Codex/GPT (reviewer)
       │
       ▼
  Decision: accept / revise / split / reject
       │
       ▼
  Human (final merge and high-risk approvals)
```
