# Review Policy

## Division of Labor

### Claude Code  -  Mechanical Checks

Responsibilities:

- Run tests and verify pass/fail status
- Run linters and fix obvious violations
- Run type checks and resolve type errors
- Verify build succeeds
- Fix obvious bugs (typos, off-by-one errors, missing imports)
- Produce evidence packets with diffstat, diff, and test results
- Record assumptions, attempted commands, failed checks, and lessons learned

Claude Code handles the mechanical, verifiable aspects of code quality. It does not make architectural judgments.

### MiMo / DeepSeek  -  Exhaustive Scan

Responsibilities:

- Scan full diffs for subtle issues that mechanical checks miss
- Analyze long test logs for intermittent failures or flaky patterns
- Suggest additional test cases for edge conditions
- Review changes across many files for consistency

MiMo/DeepSeek is invoked when the diff is large or the task is complex enough to warrant exhaustive review. It is optional for small, well-scoped changes.

### Codex / GPT  -  Architectural Review

Responsibilities:

- Evaluate whether the implementation matches the task card intent
- Assess regression risk  -  what could break, what depends on this
- Review design decisions  -  is this the right abstraction, the right boundary
- Check for security implications
- Check whether the token budget and delegation policy was followed:
  - Did Codex stay within the context budget during planning?
  - Were high-token reads and multi-file work delegated to Claude?
  - Did Claude return compressed evidence (summaries + artifact paths) instead of pasted large logs?
- Return a structured decision (see below)

**Codex/GPT does NOT write code during review.** It evaluates and decides. Implementation is always delegated to Claude Code.

### Human  -  Final Authority

Responsibilities:

- Merge approved changes
- Approve high-risk changes (see Safety Constraints in SKILL.md)
- Override agent decisions when necessary
- Make architectural decisions that agents cannot

The following always require explicit human approval  -  agents must not perform them autonomously:

- Destructive commands and file deletion
- Database migrations
- Auth / permission changes
- Billing changes
- Deployment changes
- Public API changes
- Secret or credential edits
- Production data changes

## Structured Review Decision

When Codex/GPT reviews an evidence packet, it must produce a structured decision with the following fields:

### Decision

One of: **accept**, **revise**, **split**, **reject**.

### Reasoning

A concise explanation of why this decision was made. Reference specific acceptance criteria, evidence, or concerns.

### Next-Loop Instructions

For **accept**: state that the change is ready for human merge.

For **revise**: provide specific, actionable revision instructions. These instructions become the "Revision instructions" field in the next iteration's task card. Be explicit about:
- What needs to change and why
- Which files or modules are affected
- What evidence the next iteration should produce

For **split**: decompose the task into smaller child task cards. For each child, provide:
- A goal
- Acceptance criteria
- Estimated scope

For **reject**: explain why the approach is fundamentally wrong and suggest an alternative approach. Include:
- What went wrong
- Why the current approach cannot be salvaged
- What alternative approach should be tried

### Reusable Lessons

Record any knowledge gained during review that could inform future planning:
- Patterns that worked well
- Patterns to avoid
- Better approaches discovered during review

## Review Workflow

1. Claude Code produces an evidence packet after executing a task card.
2. The evidence packet is sent to Codex/GPT via `ai/review-with-codex.sh` or `ai/run-loop.sh`.
3. Codex/GPT reviews and returns a structured decision.
4. If **accept**: the change is ready for human merge.
5. If **revise**: a new task card is created with revision instructions (incrementing the loop iteration), and Claude Code re-executes.
6. If **split**: the original task card is decomposed into smaller child cards, each entering its own loop.
7. If **reject**: the task returns to OBSERVE with the rejection reasoning as new context.
8. Human performs final merge and any required high-risk approvals.

## Loop Integration

The review decision drives the loop state machine defined in `references/loop-model.md`:

- Each decision includes next-loop instructions that feed into the next PLAN or OBSERVE phase.
- The task card carries loop metadata (parent task, iteration, prior decision, revision instructions).
- The evidence packet records review feedback for traceability.
- Lessons learned flow from review back into future planning.

See `references/loop-model.md` for the full loop state machine.
