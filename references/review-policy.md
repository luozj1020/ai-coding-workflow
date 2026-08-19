# Review Policy

## Bookend Review Boundary

Claude owns implementation convergence; Codex owns semantic acceptance. There
is no mandatory Direction Review between implementation and testing. Builder,
Checker/Test, revision, and validation remain Claude-owned duties under one
frozen contract and may use separate sessions without waking Codex.

Claude may implement, write contract-authorized tests, run assigned validation,
diagnose failures, and make in-scope revisions. It must preserve command, exit
code, key original output, changed paths, assumptions, and unresolved risks.
It cannot change the frozen contract or grant acceptance.

The Workflow control plane verifies mechanical evidence and schedules Codex
only after `DONE_CANDIDATE`, or earlier for a strict `SEMANTIC_BLOCKED` receipt.

Report evidence is claim-bound, not trust-based. The changed-file manifest must
match the diff; claimed test counts must match detected test declarations and
test paths; validation commands require exit-code receipts. Every revision
finding reported `RESOLVED` must name its finding ID, changed file, symbol, and
test. Missing or contradictory evidence is `needs-review`, never acceptance.
The consistency helper accepts either explicit machine claims or the equivalent
standard report sections: a complete `Files Changed` list, an explicit
`Out-of-Scope Confirmation`, and the frozen command plus result under
`Checks Run`. Standard prose remains claim evidence only and never proves the
command actually ran without a deterministic receipt.

Checker/Test Claude owns mechanical validation evidence. It does not make architectural judgments and should not perform broad implementation rewrites.

For test-writing Checker cards, declare exact Write paths, a shell-free
`Per-file validation command` containing `{path}`, and one frozen shell-free
`Exact narrow command`. Runtime enforcement rejects empty or out-of-scope
files, compiles each Python file, runs the per-file command, and then runs the
exact command once before acceptance evidence is considered. Python test files
without an explicit per-file command fall back to single-file pytest. The
receipt is `*.checker-contract.json`; it records both validation layers, and a
violation is isolated and cannot authorize merge.
If the aggregate pytest process exits by signal or reports a Python interpreter
crash, classify it as `environment-crash`. The helper may retry only an
equivalent set of explicit changed test-file targets one file at a time. All
groups must pass to recover the validation gate; an unsplittable broad suite
remains an environment failure, not an assertion failure.
The execution card also records whether the exact command was pre-authorized by
the Checker Bash allowlist. Claude must attempt an authorized command before
claiming a sandbox or permission blocker and must preserve the original denial.
Task-card preflight fails before Claude starts when an assigned validation
command contains shell control syntax, exceeds the bounded command size/count,
or otherwise cannot enter the Bash allowlist. Split it into shell-free entries
or use a workflow validation helper; dispatch must not silently reduce the
frozen validation set.
Checker cards prefer parameterized or table-driven cases and reuse the strict
source-of-truth fixture/layout named in the Context Packet. The dispatcher emits
a non-blocking `*.change-size-advisory.json` when tracked or newly created test
growth is both large and disproportionate to implementation growth. Workflow
control artifacts are excluded and oversized/binary untracked files are skipped.
A warning asks for fixture reuse, parameterization, or a frozen-acceptance
justification; line ratio alone never rejects a semantically necessary test
suite.

When a card declares `Acceptance-to-test IDs`, the Checker report must bind each
ID to a changed test file, test symbol, and literal assertion marker. Static
verification classifies a missing binding as `unverified` and a binding to an
unknown ID, unchanged test file, missing symbol, or missing assertion as a
`conflict`. These bindings verify report-to-diff consistency only; they never
prove semantic acceptance.

An additional Checker/Test Claude session is conditional. The Claude owner or
control plane may start it for assigned test writing, long validation, or large
evidence processing without a Codex handoff. Use deterministic validation when
it closes the mechanical obligation, and record the skip reason when no
additional model is useful.

After the Builder stops writing, `check-worktree.sh` fans independent read-only
commands out with bounded concurrency (default 4) and preserves deterministic
input-order results in one `*.validation-receipt.json`. Its mandatory boundary
precheck covers tracked, staged, and untracked files: untracked content is
checked through virtual no-index patches, so an empty `git diff --check` is
never treated as full evidence. Python syntax/AST/module-boundary checks,
JSON/TOML parsing, abnormal growth, cross-file concatenation, and scope evidence
run before the command fan-out. Any failed branch fails the aggregate receipt;
parallel execution does not weaken the single Codex acceptance decision.

### Claude-Compatible Models  -  Exhaustive Scan

Responsibilities:

- Scan full diffs for subtle issues that mechanical checks miss
- Analyze long test logs for intermittent failures or flaky patterns
- Suggest additional test cases for edge conditions
- Review changes across many files for consistency

The configured cost-efficient Claude-compatible model may be used when the diff is large or the task is complex enough to warrant exhaustive review. It is optional for small, well-scoped changes.

### Codex / GPT  -  Architectural Review

Responsibilities:

- Evaluate whether the implementation matches the task card intent
- Assess regression risk  -  what could break, what depends on this
- Review design decisions  -  is this the right abstraction, the right boundary
- Check for security implications
- Check validation evidence quality:
  - Are failed commands, exit codes, key original output lines, and `file:line` locations preserved?
  - Does every changed byte have exactly one Review Projection classification?
  - Are machine facts separated from Claude semantic claims?
- Return a structured decision (see below)

Codex is dormant during Claude convergence and cannot race the owner.

### Contract Delta Stop Rule

After freeze, only `semantic_blocked` may request a new Codex contract decision.
Recommendations and backlog items cannot wake Codex or block Claude convergence.
A delta changes only the contradicted or newly ambiguous contract surface; it
does not reopen repository discovery or create a full planning round.

## Phase Responsibility Matrix

| Phase | Codex | Claude | Control plane |
|---|---|---|---|
| GROUND | Freezeability only | Not active | Locate boundaries/tests/validation |
| FREEZE | Freeze intent and submit | Not active | Hash contract/base and assign owner |
| CONVERGE | Dormant | Explore, implement, test, fix, validate | Single writer, epochs, recovery, budgets |
| PROJECT | Dormant | Supply semantic claims/risks | Prove facts and complete diff coverage |
| REVIEW | Accept/revise/split/reject | Dormant unless resubmitted | Verify wake request and persist decision |

## Runtime Recovery and Takeover

Dirty source, stale HEAD, transport failure, timeout, session loss, missing
reports, and unsuccessful validation are runtime/evidence conditions. The
control plane restores or continues Claude when it can prove the same contract,
owner, base/worktree lineage, write boundary, and absence of another writer.
Otherwise it emits `runtime_blocked`, `authority_blocked`, or
`budget_exhausted`; none wakes Codex.

### No Direction Review Synchronization Point

Testing and revision do not wait for a Codex direction decision. Claude and the
control plane continue while the frozen contract remains satisfiable. A scope
violation is handled by deterministic guards; an implementation defect remains
inside Claude convergence; an unavoidable contract choice produces a strict
`semantic_blocked` receipt.

### Codex Direct Intervention

Codex may directly edit implementation files only after the human explicitly
requests takeover or approves a separately frozen ownership transfer. Timeout,
epoch count, repeated compile/test failure, or missing prose alone does not
transfer ownership.

Before editing, Codex must state the explicit human authority, revoke Claude's
Owner Lease, identity-stop all writer processes, freeze a stable baseline, name
the exact paths, and bind validation. The edit cannot bypass safety approvals.

When two directly linked attempts both have counted classifications, the
dispatcher may issue a `*.takeover-receipt.json` candidate only when the second
is an explicit `retry-in-place` of the first and both receipts bind the same
Claude session UUID, task-card hash, source/execution baselines, source
repository, and physical worktree. A reviewed/advisor continuation or any
fresh session is a new accounting scope, even when it reuses a worktree. Codex
must not edit from that candidate. `aiwf prepare-takeover` performs the atomic
single-writer transfer and produces the actual grant only after old-process
termination and a stable baseline. Codex stays inside the grant's hash-bound
`allowed_write_paths` and runs the bound narrow validation.

No-progress, invalid result JSON, missing prose, or repeated implementation
failure does not transfer ownership. They consume runtime budgets and may end in
a non-semantic blocked state for operator action.

### Evidence Gap Recovery

Missing `result.json`, `CLAUDE_REPORT.md`, or acceptance prose is an evidence gap, not automatically an implementation failure. The control plane first classifies the gap:

- If the diff matches the task card, no stop gate was crossed, and assigned validation is green, deterministic helpers reconstruct the machine-evidence packet.
- If the task card did not assign Claude to write new tests, absence of new tests is not by itself a reason to revise. Codex may still mark residual test risk or add a follow-up task when coverage is materially weak.
- If assigned tests or checks are missing, the same Claude owner receives a narrow internal continuation that preserves the frozen contract.
- If recovery budgets expire, emit `budget_exhausted`; do not draft a Codex revision.
- If Codex decides after seeing the diff that tests are acceptance-critical, it must say that explicitly in the next task card's Testing Responsibility instead of treating the original omission as Claude failure.

Use the acceptance bundle's `review_evidence` block as the bounded starting
point: it exposes the scoped changed-file/status list, patch or recovered-diff
SHA-256, source/execution baselines, report availability, and each exact
validation command with its exit code. Missing prose must not force Codex to
reconstruct these facts from logs, but this summary remains non-authoritative
until the referenced receipts and diff agree.

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

### Coverage-Preserving Review Projection

Final review consumes a Review Projection, not an unconstrained summary. The
projection binds the full diff hash and partitions every changed byte into
exactly one classification. `unclassified_byte_count` must be zero and no
coverage spans may overlap. Any invalid or unknown classification expands to
`semantic-frontier`; it never disappears from review.

Machine-computable fields are deterministic facts. Claude supplies only the
semantic assumptions, acceptance implications, and unresolved risks that tools
cannot prove. The safe baseline is to classify the entire diff as semantic
frontier; later compression must preserve the same total coverage.

### State-Backed Incremental Review

When the run uses Workflow State IR and immutable Evidence Objects, use the
Phase 6 review artifacts instead of re-sending the full acceptance surface:

```bash
python ai/build-acceptance-graph.py --state WORKFLOW_STATE.json \
  --store ai/evidence/objects --previous-graph ACCEPTANCE_GRAPH.previous.json \
  --new-diff-ref sha256:<digest> -o ACCEPTANCE_GRAPH.json
python ai/build-delta-review-packet.py --graph ACCEPTANCE_GRAPH.json \
  --previous-graph ACCEPTANCE_GRAPH.previous.json \
  --receipt REVIEW_RECEIPT.previous.json -o DELTA_REVIEW_PACKET.json
python ai/validate-review-receipt.py --receipt REVIEW_RECEIPT.json \
  --graph ACCEPTANCE_GRAPH.json --packet DELTA_REVIEW_PACKET.json
```

The prior Receipt must bind the exact prior Graph and State. Only its accepted,
unchanged items may be omitted. Conditional, rejected, unsupported,
contradictory, reopened, or changed items remain in scope. Use
`--mode revision` to emit only failing/reopened subgraphs. Missing, stale,
unknown, unreadable, permission-denied, or contradictory evidence fails closed;
a bounded lexical candidate cannot support Acceptance by itself.

Feed the graph and delta packet into the terminal acceptance bundle rather than
concatenating their full evidence. Its compact index carries paths, evidence
counts, invariant coverage, unresolved risks, and explicit expansion reasons.
`select-review-tier.py` records deterministic Checker/deep-review skips only
when every indexed item is supported and no semantic-risk delta remains.
Unsupported or uncovered mechanical items use L1 compression; contradictory,
reopened, or unverified semantic items require compact L2 Codex review. Full
evidence stays file-backed and is read only for selected IDs.

Keep the terminal handoff tool-backed. `build-acceptance-bundle.py` and
`build-review-packet.py` print bounded JSON capsules by default while keeping
full evidence in their output files. For a capsule whose
`compression_route.spark_recommended` is true, the outer workflow may execute
its hash-bound `tool_request.argv` once and use Spark's `postflight-bundle`
response as an advisory compression layer. `review-with-codex.sh` does this in
`--spark-compression auto` only when deterministic complexity signals
and at least 8 KiB of estimated transfer savings agree. It stores full Spark
stdout and sends only the bounded Spark capsule to Codex. Pass artifact paths,
never pasted bodies. Verify every capsule with
`verify-evidence-capsule.py`; stale task-card, diff, evidence, or HEAD bindings
fail closed. Spark cannot accept the change, stop Claude, or replace Codex's
evidence-bound semantic decision; an unavailable optional result falls back to
expanding only selected evidence IDs.

Codex output remains bounded by responsibility, not by a hard token limit:
intent freeze contains goal/invariants/acceptance/forbidden paths, planning
review contains blocking findings, and final review contains a decision plus
evidence-bound findings.

When Codex/GPT reviews an evidence packet, it must produce a structured decision with the following fields:

When Claude evidence is useful but not accepted as a complete round, the
optional `evidence_disposition` records review-layer units by exact path,
optional symbol, evidence reference, and `adopted`, `rejected`, or
`needs-revision` disposition. `partially-adopted` requires evidence on both
sides. This never becomes a dispatcher terminal state and never upgrades a
missing report, failed validation, or unreviewed diff into success.

### Decision

One of: **accept**, **revise**, **split**, **reject**.

### Reasoning

A concise explanation of why this decision was made. Reference specific acceptance criteria, evidence, or concerns.

### Next-Loop Instructions

For **accept**: accept the complete frozen logical task represented by the
`DONE_CANDIDATE`. Human merge remains separate.

For **revise**: provide a bounded Revision Delta for the same Claude owner. Be explicit about:
- What needs to change and why
- Which files or modules are affected
- What evidence the next iteration should produce

Bind findings to the contract, projection, affected acceptance IDs, exact
evidence, and validation. Submit the delta, end the Codex episode, and wait for
a new `review_ready` wake request; do not remain in a revision loop.

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

1. A new Codex episode starts only from a valid `codex-wake-request.json`.
2. Verify contract, base, diff, projection, and evidence hashes.
3. Expand only semantic-frontier segments or evidence-integrity conflicts.
4. Return `accept`, `revision-delta`, `split`, or `reject`.
5. `accept` hands the result to the human; it never merges automatically.
6. `revision-delta` is submitted to the same Claude owner and ends this Codex
   episode. A later wake request starts delta review.
7. `split` freezes independent child contracts; each uses its own Bookend task.
8. `reject` invalidates the implementation direction and returns a bounded
   replacement contract, not an interactive monitoring loop.

## Legacy Loop Integration

`references/loop-model.md` and `run-loop.sh` describe a compatibility workflow
for explicit experiments. Their per-iteration direction/final reviews do not
apply to production Bookend tasks.
