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
- Return a decision:
  - **Accept**  -  implementation is correct and complete
  - **Revise**  -  implementation needs changes, with specific instructions
  - **Split**  -  the task should be broken into smaller task cards
  - **Reject**  -  the approach is fundamentally wrong, needs re-planning

Codex/GPT does not write code during review. It evaluates and decides.

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

## Review Workflow

1. Claude Code produces an evidence packet after executing a task card.
2. The evidence packet is sent to Codex/GPT via `ai/review-with-codex.sh`.
3. Codex/GPT reviews and returns a decision.
4. If **accept**: the change is ready for human merge.
5. If **revise**: a new task card is created with revision instructions, and Claude Code re-executes.
6. If **split**: the original task card is decomposed into smaller cards.
7. If **reject**: the task returns to planning.
8. Human performs final merge and any required high-risk approvals.
