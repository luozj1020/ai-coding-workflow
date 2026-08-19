# Agents

Project-specific text outside the managed block is preserved by the installer.

<!-- AI-CODING-WORKFLOW:BEGIN managed -->
## AI Coding Workflow Core

**Use Codex only at the bookends.** Codex freezes intent and performs bounded
semantic review. After `aiwf submit`, a durable control plane owns Claude until
the task reaches `review_ready` or strict `semantic_blocked`. Runtime state
changes are not Codex synchronization points.

Classify first: `bypass` for questions/read-only/tiny/urgent work; `direct` for
bounded Codex edits and workflow maintenance; `delegated` for non-trivial work
where durable Claude ownership reduces Codex work. Direct work records
`python ai/aiwf.py direct --reason ... --path ...`. Setup/update loads setup
policy only. Bypass records `workflow bypassed: <reason>`.

For indexed code, use one healthy CodeGraph query first; use
`ai/locate-code.py` for behavior/files and lexical search for text, Shell, and
configuration. Ground only enough to freeze behavior, acceptance, invariants,
forbidden boundaries, validation authority, and concrete risk facts. Unknown
implementation files do not block freeze. Do not browse the web unless asked.

## Production Bookend Path

```text
GROUND -> Codex FREEZE -> aiwf submit -> Codex episode ends
                                      -> Claude CONVERGE
                                      -> tools PROJECT
                                      -> new Codex REVIEW episode
```

For JSON-backed delegation, Codex freezes Task JSON and runs:

```bash
python ai/aiwf.py submit TASK.json
```

Return the durable `bookend-state.json` path, then end the current Codex
episode. Do not block on `monitor-claude.sh`, poll Claude, perform Direction
Review, or dispatch Checker through Codex. `aiwf run` is foreground
compatibility and `aiwf loop` is the legacy per-iteration review loop.
The component composer is for explicit legacy Markdown cards only.

## Claude-Owned Convergence

Claude owns exploration, implementation, assigned tests, diagnosis, revision,
validation, and evidence claims. Builder Claude, Checker/Test Claude, and revision are
internal duties and may use separate Claude sessions under one logical owner.
Compile/test failures, missing code knowledge, timeouts, transport recovery,
session loss, and context exhaustion stay inside Claude/runtime convergence.

Only `semantic_blocked` may wake Codex early. It requires proof that the frozen
contract cannot be completed without a new semantic choice, such as
contradictory acceptance, materially ambiguous external behavior, an
unavoidable forbidden boundary, or an invalid frozen assumption.

## Runtime and Single Writer

The logical Claude owner survives execution epochs; process and write grants do
not. Every next epoch requires identity-bound termination of the old process,
proof of no active writer, a stable state hash, the same contract/base/scope,
and a fresh epoch grant. Unknown visibility fails closed.

The hard timeout expires an execution epoch, not the logical task. A
deterministic `continuation_safe=true` may continue the same owner. Runtime,
authority, and budget failures become `runtime_blocked`, `authority_blocked`,
or `budget_exhausted`; they never request semantic Codex inference.

## Evidence and Review

Tools establish facts; models make claims. Hashes, changed paths, commands,
exit codes, validation, scope, and diff coverage must be machine generated.
Claude supplies semantic assumptions, acceptance implications, and unresolved
risks.

Every changed byte must have exactly one Review Projection classification.
Gaps, overlaps, stale bindings, or unknown classifications invalidate the
projection and expand semantic review. Read a scheduled wake request with:

```bash
python ai/aiwf.py bookend review-input BOOKEND_STATE_OR_DIR
```

At `review_ready`, Codex reviews the frozen contract and remaining semantic
frontier once. A revision becomes a bounded Revision Delta submitted back to
the same Claude owner; Codex ends that episode and returns only for delta
review. Models never merge.

Humans retain destructive, deletion, migration, authentication/permission,
billing, deployment, public-API, secrets, production-data, and merge authority.
Spark remains advisory: it cannot satisfy acceptance or authorize merge;
`preflight-bundle` is diagnostic-only. External MCP/plugins are default-off and
do not widen Bash/Edit authority. The compatibility configuration name remains
`ownership_profile=claude-first`.

## References

Load only the reference for the current operation.

| Need | Reference |
|---|---|
| Bookend roles/states/evidence | `references/operating-model.md` |
| Task contract | `references/task-card-policy.md` |
| Claude epochs/recovery | `references/claude-runtime.md` |
| Review Projection/decisions | `references/review-policy.md` |
| Worktrees/continuation | `references/worktree-and-parallel.md` |
| Retrieval/context | `references/mcp-policy.md` |
| Setup/update/doctor | `references/setup-policy.md` |
| Metrics/pilots | `references/benchmark-policy.md` |
| Skill feedback | `references/feedback-policy.md` |
| Legacy synchronous loop | `references/loop-model.md` |
<!-- AI-CODING-WORKFLOW:END managed -->
