# Bookend Operating Model

## Core Principle

**Codex freezes the contract, Claude owns convergence, tools construct facts
and prove complete diff coverage, and Codex reviews only the remaining semantic
surface.**

The scarce Codex model is not the task process owner. A Codex inference episode
exists only at the two bookends or for a strict semantic contract escalation.

## Semantic Model

```text
GROUND -> FREEZE -> CONVERGE -> PROJECT -> REVIEW
           Codex      Claude       tools      Codex
```

- **GROUND** is deterministic repository grounding: behavioral entry,
  compatibility boundary, relevant tests, validation target, and concrete risk
  facts. Unknown implementation files do not block freeze.
- **FREEZE** produces an immutable Task contract: goal, acceptance, invariants,
  forbidden boundaries, validation authority, and human-approval boundaries.
- **CONVERGE** is one Claude-owned logical task. Exploration, implementation,
  tests, diagnosis, revision, validation, epoch recovery, and session
  replacement remain inside it.
- **PROJECT** constructs machine evidence and a coverage-preserving Review
  Projection. It never summarizes away unclassified changes.
- **REVIEW** is one bounded Codex semantic decision over the frozen contract and
  projection.

`GROUND` and `PROJECT` are control-plane phases, not model synchronization
points.

## Model Call Graph

```text
Codex FREEZE
      |
      | aiwf submit; Codex episode ends
      v
Claude-owned convergence
      |
      | review_ready or semantic_blocked
      v
new Codex REVIEW episode
```

The production target for an accepted ordinary task is two Codex calls. Claude
may use multiple processes, sessions, epochs, or internal roles without
increasing that count.

## Roles

### Codex — Contract Freezer and Semantic Reviewer

Codex:

- grounds only enough repository fact to freeze intent;
- freezes acceptance, invariants, compatibility and forbidden boundaries;
- submits once with `python ai/aiwf.py submit TASK.json` and then exits;
- reads only a hash-bound wake request in a later inference episode;
- returns `accept`, `revision-delta`, `split`, or `reject` at final review;
- makes a bounded contract decision for a true `semantic_blocked` receipt.

Codex does not monitor Claude, review implementation direction mid-run, inspect
ordinary compile/test failures, draft mechanical revisions, or remain blocked
inside the Claude process lifetime.

### Claude — Logical Task Owner

Claude owns the full convergence loop:

```text
explore -> implement -> test -> diagnose -> revise -> validate
```

Builder, test writer, Checker, and recovery are execution responsibilities,
not cross-model synchronization points. Separate Claude sessions may perform
them under the same logical owner and contract.

Claude has implementation authority but not acceptance, merge, destructive,
deployment, migration, authentication/permission, billing, public-API, secret,
or production-data authority.

### Workflow Control Plane

The control plane owns task durability after submission. It:

- stores `logical_task_id`, contract hash, base SHA, owner, worktree, epoch,
  budgets, and evidence bindings;
- enforces a single writer and epoch-scoped write grants;
- handles startup, transport, timeout, session and checkpoint recovery;
- runs deterministic scope and validation gates;
- constructs the Review Projection and emits a wake request;
- never makes a product-semantic choice.

### Tools

Tools establish facts: hashes, changed paths, byte coverage, commands, exit
codes, test receipts, scope, process identity, and artifact provenance. A tool
relationship is not semantic acceptance.

## Runtime State Machine

Runtime transitions are intentionally separate from the five semantic phases:

```text
submitted -> freezing -> converging -> classifying
                       |              |
                       |              +-> recovering -> converging
                       +-----------------> projecting -> review_ready
```

Additional terminal or suspended states are:

| State | Codex wakeup | Owner |
|---|---:|---|
| `review_ready` | yes | Codex final review |
| `semantic_blocked` | yes | Codex contract delta |
| `runtime_blocked` | no | control plane/operator |
| `authority_blocked` | no | human approval |
| `budget_exhausted` | no | policy/human |
| `cancelled` | no | terminal |

Starting, running, validating, retrying, timing out, recovering, resuming, and
changing sessions never imply a model handoff.

## Semantic Block Rule

`semantic_blocked` means Claude cannot complete the frozen contract without a
new semantic decision. Valid examples are contradictory acceptance items,
materially ambiguous externally observable behavior, an unavoidable forbidden
boundary, an invalid frozen assumption, or a required public contract change.

Compile errors, test failures, performance misses, unknown code locations,
incorrect first implementations, tool errors, timeouts, lost sessions, missing
reports, and context exhaustion are not semantic blockers.

## Evidence Types

Every evidence edge is typed:

- `deterministic_fact`
- `model_claim`
- `human_decision`

An acceptance item normally contains both machine facts and a semantic claim:

```text
A3
|- deterministic facts: diff ref, validation receipt, test binding
`- model claim: why those facts imply A3
```

Codex reviews the implication. It does not spend a new inference reproducing
facts already proved by valid receipts.

## Review Projection

A Review Projection is a complete partition of the full diff, not a natural
language summary. Every changed byte must belong to exactly one class, such as
`semantic-frontier`, `mechanically-verified`, or `generated-derived`. Missing,
overlapping, stale, or unreadable coverage invalidates the projection and
expands review; it never authorizes acceptance.

The initial safe implementation may classify the entire diff as semantic
frontier. Compression is an optimization only after complete coverage is
preserved.

## Retrieval

Use one healthy CodeGraph query first for indexed-code relationships, then LSP,
`ai/locate-code.py`, lexical search for text/Shell/configuration, targeted
snippets, and finally whole files only as needed. During FREEZE, stop when the
contract is executable rather than when repository understanding feels
complete. Claude owns implementation discovery during CONVERGE.

## Compatibility

`aiwf run` remains a foreground compatibility lifecycle. `run-loop.sh` remains
available only for explicit legacy experiments that require per-iteration
Codex review. Neither defines the production architecture.
