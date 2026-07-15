# M0 Workflow Behavior Inventory

Freeze of the current Markdown-machine-interface state as of commit `9272a90`.
This document inventories every artifact, script dependency, and class of
Markdown/text field currently parsed for machine control. It does **not** claim
to fix any of the issues listed; those are deferred to later milestones.

## 1. Core Artifacts

| Artifact | Location | Purpose |
|----------|----------|---------|
| Task Card Components | `assets/task-card-components/` | Current compact catalog and composable task-card bodies |
| Legacy Task Card Template | `assets/task-card-template.md` | Compatibility-only monolithic template |
| Evidence Packet Template | `assets/evidence-packet-template.md` | Template for review evidence |
| Task Card (per-run) | `TASK_CARD.md` / `TASK_CARD_FULL.md` | Instance of a task card for a specific run |
| Claude Execution Card | `CLAUDE_TASK_CARD.md` | Filtered view of task card for Claude execution |
| Claude Progress | `CLAUDE_PROGRESS.md` | Self-reported progress by Claude during execution |
| Claude Report | `CLAUDE_REPORT.md` | Post-execution report by Claude |
| SKILL.md | `SKILL.md` | Skill definition for Codex discovery |
| AGENTS.md | `assets/AGENTS.md` | Agent rules for Codex |
| CLAUDE.md | `assets/CLAUDE.md` | Agent rules for Claude Code |

## 2. Major Script Dependencies

| Script | Role | Key Parsing |
|--------|------|-------------|
| `scripts/dispatch-to-claude.sh` | Dispatch task card to Claude in isolated worktree | Parses task card table for task mode, risk rows; renders execution/compact views via awk |
| `scripts/run-loop.sh` | Orchestrate dispatch→review loop | Greps review output for ACCEPT/REVISE/SPLIT/REJECT decisions; parses progress checklist counts |
| `scripts/review-with-codex.sh` | Send evidence to Codex/GPT for review | Receives multiple artifact paths; produces review output |
| `scripts/watch-claude.sh` | Monitor Claude process during execution | PID tracking, timeout, kill management |
| `scripts/status-claude.sh` | Show status of running Claude processes | Process discovery |
| `scripts/doctor_workflow.py` | Check workflow readiness | Validates scripts, tool availability |
| `scripts/install_workflow.py` | Bootstrap repository | Copies templates, manages AGENTS.md/CLAUDE.md blocks |
| `scripts/install_for_codex.py` | Install skill for Codex | Copies to ~/.codex/skills |
| `scripts/summarize-loop-run.py` | Summarize loop run | Parses usage/quality JSON |
| `scripts/validate-parallel-plan.py` | Validate parallel execution plans | Parses plan JSON |
| `scripts/assess-parallel-opportunity.py` | Assess parallelization opportunities | Analyzes task structure |

## 3. Markdown Fields Parsed for Machine Control

### 3.1 Task Identity Table

Parsed by `dispatch-to-claude.sh` via awk:

| Field | Parsing | Used For |
|-------|---------|----------|
| `Task mode` | `tolower(field) == "mode"` | Worktree strategy selection, builder-mode gating |
| `Base commit` | Not machine-parsed | Human reference |
| `Local validation allowed?` | Not machine-parsed by scripts | Claude execution decision |
| `Direction acknowledgement` | Not machine-parsed | Claude execution decision |
| `Mixed exception` | Not machine-parsed | Claude execution decision |

### 3.2 Risk Rows (Checker Reuse Risk Gate)

Parsed by `dispatch-to-claude.sh` via awk for worktree strategy:

| Risk Category | Pattern Match | Effect |
|---------------|---------------|--------|
| Public API risk | `public api( risk\| impact)?[?]?` | Must be "no" for reuse-managed |
| Data model risk | `data model( risk\| impact)?[?]?` | Must be "no" for reuse-managed |
| Security risk | `security( risk\| impact)?[?]?` | Must be "no" for reuse-managed |
| Migration risk | `migration( risk\| impact)?[?]?` | Must be "no" for reuse-managed |
| Permission risk | `permission( risk\| impact)?[?]?` | Must be "no" for reuse-managed |
| Concurrency risk | `concurrency( risk\| impact)?[?]?` | Must be "no" for reuse-managed |
| Cross-module risk | `cross-module( contract)? risk[?]?` | Must be "no" for reuse-managed |
| Production impact | `production( impact\| risk)[?]?` | Must be "no" for reuse-managed |

### 3.3 Section Filtering (Task Card Rendering)

`dispatch-to-claude.sh` renders task cards by filtering sections:

**Codex-only sections** (always removed for Claude):
- Execution Readiness Gate
- Control-Plane Exception Rationale
- Task Card Views
- Direction Review Gate
- Codex Context Budget
- High-Token Delegation Gate
- Delegation Continuity Gate

**Compact-view skipped sections** (removed in compact mode):
- Goal Loop Contract
- Advisor Gate
- Codex Spark Gate
- Parallel Execution Gate
- Worktree / Large Repo Strategy Gate
- Delegation Restoration Gate
- Spec Gate
- Root Cause Gate
- Test-First / TDD Contract
- Finish Branch Gate

**Execution-only kept sections** (only these survive in execution-only mode):
- ID
- Task Mode
- Claude Context Packet
- Goal
- Handoff Contract
- Required Revisions
- Required Changes
- Acceptance Criteria
- Testing Responsibility
- Validation Contract
- Required Report

### 3.4 Progress Checklist Parsing

`run-loop.sh` parses `CLAUDE_PROGRESS.md` for progress tracking:

```
grep -cE '^- \[[ xX]\]' "$file"   # total items
grep -cE '^- \[[xX]\]' "$file"    # done items
```

Also extracts `elapsed_seconds=N` from progress files for adaptive timeout.

### 3.5 Decision Parsing (REVIEW OUTPUT)

**⚠️ TECHNICAL DEBT — Natural-language grep for next milestone:**

`run-loop.sh` line 381 greps review output for decisions:
```bash
DECISION="$(grep -iE '^\*\*(ACCEPT|REVISE|SPLIT|REJECT)\*\*|^[-*] \*\*(ACCEPT|REVISE|SPLIT|REJECT)\*\*|(ACCEPT|REVISE|SPLIT|REJECT)' "$REVIEW_OUTPUT" \
    | head -1 \
    | sed 's/.*\(ACCEPT\|REVISE\|SPLIT\|REJECT\).*/\1/i' \
    || true)"
```

This is a natural-language pattern match on free-form review text. It is fragile:
- Matches any line containing the keywords, not a structured field
- No validation that the decision is in the expected position
- Could false-match on quoted examples or discussion of decisions
- Head-1 takes the first match, which may not be the actual decision

This is explicitly deferred to the Structured Review milestone.

### 3.6 Path Label Parsing

`run-loop.sh` uses `parse_path` to extract labeled paths from dispatch logs:
```bash
parse_path() {
    local label="$1"
    local log="$2"
    grep "^${label}:" "$log" | sed "s/^${label}: *//" | head -1 || true
}
```

Labels parsed: Worktree, Result, Raw Result, Status, Network Log, Diffstat,
Diff, Checker Report, Source Status, Worktree Status, Untracked Files,
Usage Summary, Report, Claude Progress, Claude PID, Progress Log.

### 3.7 View Modes

| View | Trigger | Effect |
|------|---------|--------|
| `execution` | `CLAUDE_CODE_TASK_CARD_VIEW=execution` or `safe` profile | Removes Codex-only + compact-skip sections |
| `compact` | `CLAUDE_CODE_TASK_CARD_VIEW=compact` (default for balanced/fast) | Removes Codex-only + compact-skip sections |
| `execution-only` | `CLAUDE_CODE_BUILDER_MODE=execution-only` | Keeps only execution-relevant sections |

## 4. Environment Variables Controlling Behavior

| Variable | Default | Effect |
|----------|---------|--------|
| `CLAUDE_CODE_TIMEOUT_SECONDS` | adaptive | Dispatch timeout |
| `CLAUDE_CODE_ADAPTIVE_WAIT` | 1 | Enable adaptive timeout |
| `CLAUDE_CODE_WORKTREE_STRATEGY` | task-derived | fresh or reuse-managed |
| `CLAUDE_CODE_LARGE_REPO_MODE` | 0 | Large repo optimizations |
| `CLAUDE_CODE_TASK_CARD_VIEW` | profile-dependent | compact or execution |
| `CLAUDE_CODE_PROMPT_PROFILE` | profile-dependent | brief or standard |
| `CLAUDE_CODE_EVIDENCE_MODE` | profile-dependent | full or summary |
| `CLAUDE_CODE_BUILDER_MODE` | standard | standard or execution-only |
| `CLAUDE_CODE_NETWORK_MONITOR` | 0 | Network diagnostics |
| `CLAUDE_CODE_CHECKER_DISCOVER` | 0 | Checker discovery |
| `CLAUDE_CODE_VERBOSE` | 0 | Verbose output |
| `CLAUDE_CODE_EXECUTION_PROFILE` | balanced | safe/balanced/fast-large-repo |

## 5. Legacy Template Sections

This frozen inventory originally treated `assets/task-card-template.md` as the
default. Current authoring selects components from the compact catalog and uses
the deterministic composer; the following list documents the retained legacy
template:

1. ID
2. Task Type
3. Executor
4. Task Mode (+ Phase Responsibility Matrix, Stall/Ambiguity Triage)
5. Execution Readiness Gate
6. Control-Plane Exception Rationale
7. Goal Loop Contract
8. Advisor Gate
9. Codex Spark Gate
10. Parallel Execution Gate
11. Task Card Views
12. Direction Review Gate
13. Codex Context Budget
14. High-Token Delegation Gate
15. Delegation Continuity Gate
16. Worktree / Large Repo Strategy Gate
17. Checker Reuse Risk Gate
18. Delegation Restoration Gate
19. Claude Context Packet
20. Goal
21. Handoff Contract
22. Required Revisions
23. Required Changes
24. Acceptance Criteria
25. Testing Responsibility
26. Validation Contract
27. Required Report
28. Spec Gate
29. Root Cause Gate
30. Test-First / TDD Contract
31. Finish Branch Gate

## 6. Current Limitations and Technical Debt

1. **Natural-language decision grep** in `run-loop.sh` — fragile pattern match on review output. Deferred to Structured Review milestone.
2. **No structured task schema** — all task data is Markdown; no JSON schema or validation.
3. **Section filtering is string-based** — awk pattern matching on section headers; no structured metadata.
4. **Risk row parsing uses regex** — could false-match on differently formatted rows.
5. **No profile composition** — task cards are flat Markdown; no inheritance or composition mechanism.
6. **No lint tooling** — task card correctness is verified only by human review and runtime behavior.
7. **No deterministic rendering** — task card views are produced by awk scripts, not a formal renderer.
