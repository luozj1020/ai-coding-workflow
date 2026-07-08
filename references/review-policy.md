# Review Policy

## Division of Labor

### Builder Claude  -  Implementation Direction

Responsibilities:

- Implement the scoped task card change
- Keep edits within the Handoff Contract
- Update `CLAUDE_PROGRESS.md` and `CLAUDE_TASK_CARD.md` progress after each assigned item
- Report changed files, plan match, deviations, assumptions, and risks
- Run only narrow sanity checks explicitly allowed by the task card

Builder Claude does not own acceptance testing. It should not write acceptance tests or run broad suites unless the task card explicitly defines a mixed exception.

### Checker/Test Claude  -  Validation and Tests

Responsibilities:

- Write or update assigned tests
- Run assigned test, lint, type, build, or aggregate validation commands
- Preserve command, exit code, key original output, and `file:line` locations
- Produce evidence packets with diffstat, test results, and report paths
- Make only concrete small fixes explicitly allowed by the task card when validation exposes a clear defect

Checker/Test Claude owns mechanical validation evidence. It does not make architectural judgments and should not perform broad implementation rewrites.

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
- Perform Builder direction review before validation work is dispatched
- Assess regression risk  -  what could break, what depends on this
- Review design decisions  -  is this the right abstraction, the right boundary
- Check for security implications
- Check whether the token budget and delegation policy was followed:
  - Did Codex stay within the context budget during planning?
  - Were high-token reads and multi-file work delegated to Claude?
  - Did Claude return compressed evidence (summaries + artifact paths) instead of pasted large logs?
- Check validation evidence quality:
  - Was `ai/check-worktree.sh` run when available?
  - Are failed commands, exit codes, key original output lines, and `file:line` locations preserved?
  - Did any checker command mutate the worktree?
  - Did the loop stop when failures repeated, regressed, or stopped improving?
- Return a structured decision (see below)

**Codex/GPT does NOT write code during ordinary review.** It evaluates and decides. Implementation is delegated to Claude Code until an intervention threshold is reached.

## Phase Responsibility Matrix

| Phase | Codex responsibilities | Claude responsibilities | Claude must not | Codex must not |
|-------|------------------------|-------------------------|-----------------|----------------|
| OBSERVE | Gather low-token evidence with LSP/CodeGraph/MCP and identify unknowns | N/A unless dispatched for exploration | Edit files without a task card | Perform broad reads when lower-token evidence is enough |
| PLAN | Write the full task card, set Task Mode, Testing Responsibility, Direction/Boundary gates, Stall/Ambiguity Triage, and validation owner | N/A | Decide task boundaries before dispatch | Leave testing responsibility implicit |
| DISPATCH | Render the Claude execution card and preserve the full planning card | Read `CLAUDE_TASK_CARD.md` as the contract | Depend on Codex-only planning sections | Hand-write a second divergent Claude card |
| BUILDER EXECUTE | Observe progress and partial diff direction only | Implement scoped changes, update progress, report direction | Write acceptance tests or run broad suites unless `mixed-exception` or narrow sanity checks are explicit | Patch implementation because the Builder is merely quiet |
| DIRECTION REVIEW | Decide wait, proceed to Checker/Test, revise, split, reject, or threshold-based takeover | Provide progress/report evidence and stop on blockers | Repeatedly ask for the same approval after proceed | Send off-plan work to validation |
| CHECKER/TEST | Dispatch validation task and review evidence quality | Write/update assigned tests, run assigned commands, report failures with exit codes and key output | Perform broad implementation rewrites | Treat missing unassigned tests as Claude failure |
| FINAL REVIEW | Accept/revise/split/reject and optionally run second verification | N/A unless re-dispatched | N/A | Merge automatically or edit directly without threshold |
| TAKEOVER | Edit only after explicit human request or current-task threshold, record scope and validation | N/A | N/A | Use prior-session failures alone as takeover permission |

## Delegation Restoration Before Takeover

Dirty source or stale HEAD means Claude may not see the required context. Treat this as a delegation blocker, not a Claude failure and not a takeover trigger. Before Codex edits implementation files, it must try or explicitly rule out a restoration path:

- commit an accepted phase so HEAD contains the required context
- stash or patch uncommitted source changes
- refresh outdated local workflow files
- re-dispatch Claude from updated clean HEAD
- request explicit dirty-source dispatch approval
- stop for human input when the base cannot be made reliable

Codex takeover after a delegation blocker requires either an independent current-task threshold, an explicit human override, or a recorded reason restoration is impossible or unsafe.

### Direction Review Before Testing

After a Builder Claude task, Codex reviews the partial or final diff before assigning Checker/Test work:

- If the implementation direction matches the plan, Codex waits for Builder completion when still in progress, then dispatches a Checker/Test task when validation is needed.
- If the implementation direction is off-plan, scope-expanding, risky, or violates a stop condition, Codex interrupts or revises with a narrower Builder task.
- If Builder Claude repeatedly runs off-plan, stalls, or exits without useful progress, Codex may enter direct intervention only after citing current-task threshold evidence.
- Codex should not dispatch Checker/Test Claude to validate an implementation direction it has not accepted.

Before calling a Builder stalled, Codex classifies the likely cause: task-card ambiguity, mixed builder/checker responsibilities, dirty source/stale HEAD, permission/tool approval blocker, long-running validation, missing progress artifact, external environment, or true Claude no-progress. Permission denials, sandbox write failures, forbidden-file rules, missing CLI/auth, network restrictions, human-approval requirements, dirty source, and stale HEAD are not Claude execution failures unless Claude ignored an available allowed path after restoration.

### Codex Direct Intervention

Codex may directly edit implementation files only when at least one condition is true:

- The loop reached its configured maximum Claude iterations without acceptance.
- The same failure appears in two consecutive Claude iterations.
- Failure count does not decrease for two consecutive Claude iterations.
- Claude Code is unavailable, repeatedly times out, or the blocker is external to execution but fixable in the repository after delegation restoration was attempted or ruled out.
- The human explicitly asks Codex to take over.

Before editing, Codex must state the failed attempts, why another Claude revision is unlikely to help, the files/modules it will touch, and the validation it will run. The edit should be narrow and should not bypass safety approvals.

No-progress evidence, an early Claude exit, invalid result JSON, missing report, or a single failed implementation does not by itself satisfy the threshold. In those cases Codex should produce a smaller revision task with clearer acceptance criteria, stronger stop conditions, and required evidence for Claude.

Failure counts are scoped to the current task/loop. Prior-session Claude failures may justify a sharper task card, narrower scope, or stronger stop gates, but they do not by themselves authorize Codex to skip Claude in a new session. To count prior failures toward takeover, Codex must cite matching task IDs and artifact paths showing the same failure pattern.

If Claude first produced a usable implementation direction but lacked required tests/evidence, and a tightened second Claude task exits with no result/report and no useful progress, Codex may mark the current task as repeated Claude failure. The direct intervention should be a control-plane salvage, not a rewrite: cite both attempts, reuse or mirror the reviewer-accepted first-round direction when possible, add only the missing implementation/tests/evidence, and run the validation named in the task card.

### Evidence Gap Recovery

Missing `result.json`, `CLAUDE_REPORT.md`, or acceptance prose is an evidence gap, not automatically an implementation failure. Codex should first classify the gap:

- If the diff matches the task card, no stop gate was crossed, and the assigned validation is green, Codex may reconstruct a minimal evidence packet from the diff, worktree status, checker output, and its own verification.
- If the task card did not assign Claude to write new tests, absence of new tests is not by itself a reason to revise. Codex may still mark residual test risk or add a follow-up task when coverage is materially weak.
- If the task card assigned Claude to write tests, run checks, or produce specific acceptance evidence, and that evidence cannot be reconstructed, revise with a narrow "tests/evidence only" task. The revision should preserve the accepted implementation direction and should not invite broad rewrites.
- If that narrow tests/evidence-only revision also produces no result/report and no useful progress, stop re-dispatching and move to the control-plane salvage rule above.
- If Codex decides after seeing the diff that tests are acceptance-critical, it must say that explicitly in the next task card's Testing Responsibility instead of treating the original omission as Claude failure.

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

For **accept**: state whether this accepts the whole task or only the completed phase. If implementation or test-writing phases remain, do not mark the whole task ready for merge; create task-card-ready instructions for the next Claude dispatch and fill the Delegation Continuity Gate.

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

1. Builder Claude produces implementation evidence after executing a builder task card.
2. Codex/GPT reviews direction: plan match, scope, risks, deviations, and progress evidence.
3. If direction is acceptable and Builder is complete, Codex dispatches Checker/Test Claude when tests or validation are required.
4. Checker/Test Claude writes/runs assigned tests and produces validation evidence.
5. Codex/GPT reviews validation evidence and returns a structured decision.
6. If **accept** and no phases remain: the change is ready for human merge.
7. If **accept** but unfinished phases remain: Codex plans the next phase and dispatches Claude again. A high-priority subset being accepted is not permission for Codex to implement lower-priority remaining work.
8. If **revise**: a new task card is created with revision instructions (incrementing the loop iteration), and Claude Code re-executes unless an intervention threshold has been reached. If Claude made no useful progress, the next task should be narrower, more diagnostic, and evidence-focused rather than replaced by Codex edits.
9. If **split**: the original task card is decomposed into smaller child cards, each entering its own loop.
10. If **reject**: the task returns to OBSERVE with the rejection reasoning as new context.
11. If an intervention threshold is reached, Codex may perform a scoped direct fix and must produce validation evidence.
12. Human performs final merge and any required high-risk approvals.

## Loop Integration

The review decision drives the loop state machine defined in `references/loop-model.md`:

- Each decision includes next-loop instructions that feed into the next PLAN or OBSERVE phase.
- The task card carries loop metadata (parent task, iteration, prior decision, revision instructions).
- The evidence packet records review feedback for traceability.
- Lessons learned flow from review back into future planning.

See `references/loop-model.md` for the full loop state machine.
