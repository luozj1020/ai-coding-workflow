#!/usr/bin/env bash
# review-with-codex.sh  -  Send execution evidence to Codex/GPT for review.
#
# Usage: bash ai/review-with-codex.sh <task-card> <result-json> <diff-file> [extra-evidence ...]
#
# This script:
#   1. Validates that codex CLI exists.
#   2. Validates that input files exist.
#   3. Optionally reads extra evidence files (usage summary, source status, report, etc.).
#   4. Invokes codex exec for structured review.
#   5. Persists review output, Codex JSON events, and Codex usage summary when available.

set -euo pipefail

# Git for Windows can be launched through bin/bash.exe without the usual Unix tool PATH.
# Prepending these paths is harmless on Unix and makes helper scripts stable on Windows.
PATH="/usr/bin:/bin:/mingw64/bin:${PATH}"
export PATH

if [ $# -lt 3 ]; then
    echo "Usage: $0 <task-card> <result-json> <diff-file> [extra-evidence ...]" >&2
    echo "" >&2
    echo "Optional extra evidence files (any combination):" >&2
    echo "  usage.txt           - Claude token/cost usage summary" >&2
    echo "  checker-report.md   - Checker-only validation report" >&2
    echo "  source-status.txt   - Source repo state before dispatch" >&2
    echo "  worktree-status.txt - Worktree state after execution" >&2
    echo "  untracked.txt       - Untracked files listing" >&2
    echo "  report.md           - Claude modification report" >&2
    echo "  progress.log        - Claude dispatch heartbeat/progress log" >&2
    echo "  pid                 - Claude subprocess PID artifact" >&2
    exit 1
fi

TASK_CARD="$1"
RESULT_JSON="$2"
DIFF_FILE="$3"
shift 3
EXTRA_FILES=("$@")

for f in "$TASK_CARD" "$RESULT_JSON" "$DIFF_FILE"; do
    if [ ! -f "$f" ]; then
        echo "Error: File not found: $f" >&2
        exit 1
    fi
done

for f in "${EXTRA_FILES[@]+"${EXTRA_FILES[@]}"}"; do
    if [ -n "$f" ] && [ ! -f "$f" ]; then
        echo "Warning: Extra evidence file not found, skipping: $f" >&2
    fi
done

if ! command -v codex &>/dev/null; then
    echo "Error: codex CLI is not installed or not in PATH." >&2
    exit 1
fi

REVIEW_PREFIX="${DIFF_FILE%.diff}"
REVIEW_OUTPUT_FILE="${REVIEW_PREFIX}.review.txt"
CODEX_EVENTS_FILE="${REVIEW_PREFIX}.codex-events.jsonl"
CODEX_USAGE_FILE="${REVIEW_PREFIX}.codex-usage.txt"

TASK_CONTENT="$(cat "$TASK_CARD")"
RESULT_CONTENT="$(cat "$RESULT_JSON")"
DIFF_CONTENT="$(cat "$DIFF_FILE")"

EXTRA_EVIDENCE=""
for f in "${EXTRA_FILES[@]+"${EXTRA_FILES[@]}"}"; do
    if [ -n "$f" ] && [ -f "$f" ]; then
        LABEL="$(basename "$f")"
        EXTRA_EVIDENCE="${EXTRA_EVIDENCE}

## Extra Evidence: ${LABEL}
\`\`\`
$(cat "$f")
\`\`\`"
    fi
done

REVIEW_PROMPT="You are a code reviewer in a multi-agent workflow. Review the following execution evidence and make a structured decision.

## Your Role
- You are reviewing, NOT implementing. Do NOT write code or suggest code edits.
- Do not directly patch implementation files after a Claude run unless a direct-intervention threshold is reached: max iterations, repeated same failure, non-decreasing failures, repeated timeout/unavailability, or explicit human request.
- Claude no-progress, early exit, invalid result JSON, missing report, or one failed attempt is NOT enough for Codex takeover. Prefer a smaller, clearer Claude revision task with required diagnostics and stop-and-report gates.
- Prior-session Claude failures are context only. Do not skip Claude in a fresh session unless the current task card cites matching task IDs and artifact paths proving the same threshold, or the human explicitly asks Codex to take over.
- Missing result/report/acceptance prose is an evidence gap, not automatically an implementation failure. If the diff matches the task card and assigned validation is green, reconstruct minimal review evidence from artifacts, diff, and verification output instead of revising only to obtain prose.
- Treat reports containing AI-CODING-WORKFLOW:DISPATCH-SEEDED-REPORT or AI-CODING-WORKFLOW:DISPATCH-FALLBACK-REPORT as missing valid Claude-owned reports, not as completion evidence.
- Classify Claude evidence explicitly when reviewing: valid report, seeded report only, fallback report, no report but diff accepted, diff without report, acknowledgement only, or no useful progress.
- Treat acknowledgement-only as no implementation progress when there is no code diff, no valid Claude-owned report, and only acknowledgement/proceed text.
- Do not require new tests merely because none were written. Treat missing tests as a revise reason only when the task card assigned Claude to write tests, the user requested tests, or you now explicitly mark tests acceptance-critical for the next iteration.
- When revising only for missing task-card-required tests or evidence, preserve the accepted implementation direction and make the next Claude task narrow: tests/evidence only, no broad rewrite unless a concrete defect is found.
- If one Builder attempt exits after acknowledgement with no code diff and no valid report, tighten and re-dispatch once. If that tightened second attempt again exits after acknowledgement with no code diff and no valid report, direct intervention may be allowed as control-plane salvage. Cite both attempts, preserve the reviewer-accepted first-round direction when present, and limit Codex edits to missing scoped implementation, acceptance tests, and evidence.
- If the first attempt produced useful scoped diff but no valid report/evidence, accept that direction only after running assigned narrow checks. If a tightened retry produces no useful progress, direct intervention may salvage that accepted direction.
- For multi-phase or multi-part tasks, accepting the current Claude result may accept only that phase. If implementation or test-writing phases remain, do not treat ACCEPT as permission for Codex to patch the remainder; produce a next Claude task-card handoff and fill the Delegation Continuity Gate.
- Respect Task Mode. For builder results, first decide whether the implementation direction matches the plan; if yes and tests are needed, dispatch a checker-test Claude task instead of asking Builder Claude to test or letting Codex patch. For checker-test results, review validation evidence and avoid broad implementation changes.
- If a task mixes implementation, test writing, broad validation, and phase stop gates without an explicit mixed-exception rationale, treat apparent stalls as likely orchestration ambiguity. Prefer SPLIT into Builder and Checker/Test task cards before blaming Claude execution.
- Builder tasks should not be marked failed only because they did not write or run acceptance tests. Checker-test tasks should be marked incomplete when assigned tests, validation commands, or reports are missing.
- When Claude appears stuck, classify the primary attribution before deciding: Claude execution, task-card ambiguity, mixed-role task, dirty source/stale HEAD, permission/tool approval blocker, long-running validation, missing progress artifact, or external environment. Check progress log, Claude progress, task-card checklist, report, status output, and partial diff before interrupting or allowing takeover.
- If network diagnostics are present, use them as environment evidence only: proxy mode, optional healthcheck status, and process socket states can support network/auth/model-wait attribution, but they do not expose request contents or prove implementation correctness.
- Treat dirty source/stale HEAD as a delegation restoration problem, not a takeover trigger. Before allowing Codex to patch, require a restoration path: commit the accepted phase, stash or patch source changes, refresh workflow files, re-dispatch from updated HEAD, request explicit dirty-source override, or stop for human input. If restoration is impossible or unsafe, say why and cite the independent takeover threshold or explicit human override.
- Treat permission, sandbox, forbidden-file, network, authentication, missing CLI, or approval blockers as environment/orchestration blockers unless repeated current-task evidence shows Claude ignored an available path forward.
- Check Direction / Boundary Acknowledgement when present. If blocking Codex approval was required and Claude edited before approval, treat that as a process failure. If Claude had material confusion about target, boundaries, acceptance criteria, testing responsibility, or high-risk areas and guessed instead of stopping, normally choose REVISE or REJECT.
- Also check for acknowledgement loops. If Claude already received a proceed decision and asks for the same approval again without a material goal/scope/boundary/risk change, treat it as no-progress and give a concrete next action. Codex review should return one final acknowledgement decision: proceed, narrow-once/re-dispatch, split, or stop.
- If direct intervention is justified, state the failed attempts, why another Claude revision is unlikely to help, the allowed scope, and required validation.
- Compare the implementation against the original task card requirements.
- Use the Claude modification report if present, but verify it against the diff and evidence.
- Evaluate whether the implementation matches the task card intent.
- Review the task card Unknowns and Decision Gates. Decide whether known unknowns were resolved, new unknown-unknowns were surfaced, and any decision gate was crossed with appropriate authority.
- Review the Phase Responsibility Matrix when present. Verify that Codex and Claude stayed inside the active phase ownership boundaries, and do not treat work outside Claude's assigned phase as Claude failure.
- Review any Deviations From Plan. Accept deviations only when the discovered constraint is real, the action taken is conservative or explicitly allowed, and the reviewer briefing makes the behavioral impact clear.
- Check the Handoff Contract if present. Verify Must do, Must not do, May decide, Must report, and Stop condition against the diff and evidence.
- Check Small Change Fast Path Gate if present or if Claude dispatch was skipped. Verify the change touched no more than two small targeted files, had no public API/data/security/migration/permission/concurrency/cross-module contract risk, needed no broad context, stayed within the fast-path scope, and preserved narrow validation evidence or a validation-skip reason.
- Check Codex Spark Gate if present. If Spark was enabled, verify its requested/resolved mode, model, sandbox, artifact, task-size classification, routing recommendation, source-edit permission, isolated worktree use for micro-builder, accepted suggestions, ignored suggestions, and whether strong-model fallback was explicitly prohibited or approved. Treat Spark as auxiliary evidence unless the task card explicitly says it can satisfy acceptance; Spark cannot replace Claude Builder ownership or Codex final review.
- Check Worktree / Large Repo Strategy Gate if present. If worktree reuse or large-repo mode was used, verify it was explicitly allowed, resets/cleans were limited to `.worktrees/reuse/claude-managed`, the source repo was not reset or cleaned, skipped untracked evidence is called out as a review risk, and any `Claude Context Packet` was sufficient to avoid broad repository rediscovery.
- Check Parallel Execution Gate if present. If parallel dispatch was enabled, verify that each task card explicitly allowed it, file/module scopes did not overlap unless intentionally waived, no automatic merge occurred, and review/merge remains serial or manually reconciled.
- Check Spec Gate if present. If a spec was required, verify the spec artifact was reviewed, implementation matched the spec, non-goals were respected, and Claude did not invent product/API/UX decisions outside the spec.
- Check Root Cause Gate for bugfixes, regressions, failing tests, flaky behavior, performance issues, and repeated failed attempts. Verify symptom reproduction or cited evidence, likely root cause, similar-pattern scan, and whether the fix targets the cause rather than the symptom.
- Check Testing Responsibility if present. Verify whether test code changes were user-requested, acceptance-critical, or out of scope; whether Claude was assigned to write/update tests; and whether Claude or Codex/human was responsible for running tests.
- Check Test-First / TDD Contract when present. If TDD was required, require red evidence before production edits and green evidence after implementation. If Builder/Checker ownership was split, verify the evidence came from the assigned owner or request the correct next task.
- Check Finish Branch Gate before saying a whole task or branch is ready for human merge. Require fresh verification, dirty/untracked artifact classification, out-of-scope change review, remaining risks, and human review/merge instructions.
- Check Plan Match, Validation Confidence, and Reviewer Should Check fields when present. If confidence is low or the reviewer briefing is insufficient, normally choose REVISE.
- Assess regression risk and design coherence.
- Check for security implications.
- Review token/cost usage for efficiency anomalies if usage data is present.
- Check repository status evidence for baseline drift if status data is present.
- Treat checker evidence as first-class validation evidence when present.
- If checker evidence is missing, lossy, or contradicts Claude's success claim, call that out and normally choose REVISE unless the task explicitly allowed skipping checks.
- If failed command, exit code, file:line, or key original output is missing from a failure report, require the next loop to preserve it.
- If checker commands mutated the worktree, treat that as a validation failure.
- Your decision drives the next loop iteration.

## Task Card
${TASK_CONTENT}

## Execution Result
${RESULT_CONTENT}

## Diff
\`\`\`
${DIFF_CONTENT}
\`\`\`
${EXTRA_EVIDENCE}

## Required Output

Respond with the following structured format:

### Decision
One of: **ACCEPT**, **REVISE**, **SPLIT**, **REJECT**. For multi-phase tasks, state whether ACCEPT means whole-task accepted or phase accepted with follow-up required.

### Reasoning
A concise explanation of why this decision was made.

### Requirements Comparison
Map the task card acceptance criteria to the observed implementation and evidence.

### Task Mode / Direction Review
State whether this was a builder, checker-test, mixed-exception, or control-plane task. For builder tasks, state whether the implementation direction matches the plan, whether Codex should continue waiting, interrupt/narrow, dispatch checker-test next, or consider takeover. For checker-test tasks, state whether test writing, validation, and reporting were completed.

### Phase Responsibility
State the active phase, what Codex owned, what Claude owned, and whether either side crossed a non-owner boundary. If a missing artifact or test was outside Claude's assigned phase, do not count it as Claude failure; produce the correct next task owner instead.

### Small Change Fast Path
State whether Codex skipped Claude dispatch, whether the fast-path gate was satisfied, files touched, why direct Codex editing was safe, whether scope stayed within the gate, what validation ran or why validation was skipped, and whether the work should have escalated to Claude.

### Stall / Ambiguity Triage
State whether any apparent stall or incomplete evidence is better explained by Claude execution, task-card ambiguity, mixed-role assignment, dirty source/stale HEAD, permission/tool approval blocker, network/proxy/auth/model wait, long-running validation, missing progress artifacts, or external environment. State which artifacts were checked and whether the next action is continue waiting, narrow and re-dispatch, split builder/checker, stop for human, or allow Codex takeover.

### Delegation Restoration
If dirty source, stale HEAD, outdated local workflow files, permission/tool approval, or external environment blocked reliable Claude dispatch, state the restoration path attempted or required. Choose one: commit accepted phase, stash/patch source changes, refresh workflow files, re-dispatch from updated HEAD, request explicit dirty-source override, stop for human, or takeover only after an independent threshold/human override. Dirty source alone is not enough for Codex takeover.

### Direction / Boundary Acknowledgement
State whether acknowledgement was required, whether it was blocking, whether Claude stated understanding/scope/out-of-scope boundaries/acceptance interpretation/testing responsibility/confusions, whether any confusion should have stopped execution, whether Claude waited for required Codex approval, and whether the acknowledgement stayed within the maximum allowed rounds. If deciding the acknowledgement, choose exactly one: proceed, narrow-once/re-dispatch, split, or stop.

### Testing Responsibility
State whether the task card assigned test writing and test execution to Claude, Codex/human, or neither, and whether the evidence matches that assignment.

### Codex Spark Gate
State the fixed Spark fields: enabled/disabled/not recorded, requested mode, resolved mode, model, sandbox, artifact path, exit code, auto-disabled reason, strong-model fallback status, task-size classification, routing recommendation, classification confidence, accepted suggestions, and ignored suggestions. Also state whether source edits were allowed, whether micro-builder work used an isolated worktree, whether strong-model fallback was avoided or explicitly approved, and whether Spark evidence can satisfy any acceptance criterion.

### Claude Evidence Classification
Classify the Claude evidence as one of: valid report, seeded report only, fallback report, no report but diff accepted, diff without report, acknowledgement only, or no useful progress. State whether a valid Claude-owned report exists, whether implementation diff is present, and whether any accepted diff is being accepted with a report/evidence gap.

### Worktree / Large Repo Strategy
State whether fresh or reuse-managed worktree strategy was used, whether `CLAUDE_CODE_LARGE_REPO_MODE=1` skipped untracked scans or untracked patch evidence, whether the task card accepted that evidence tradeoff, whether a Claude Context Packet was provided and sufficient, whether broad repository search was avoided, and whether any reset/clean touched only `.worktrees/reuse/claude-managed`.

### Parallel Execution Gate
State whether experimental parallel execution was enabled, which aggregate artifact was reviewed, whether task scopes overlapped, whether all dispatches completed, whether any automatic merge occurred, and whether the next action is serial review, aggregate review, checker after merge, manual reconcile, or stop.

### Spec Gate
State whether a spec was required, which spec artifact or task-card section was reviewed, whether implementation matched the spec, whether non-goals were respected, and whether any product/API/UX decision was invented outside the spec.

### Root Cause Gate
For bugfix/debugging/regression work, state whether root cause was required, whether the symptom was reproduced or cited, what root cause evidence was provided, whether similar patterns were checked, and whether the fix targets the cause rather than the symptom.

### Test-First / TDD Contract
State whether TDD was required/recommended/not applicable, whether red evidence existed before production edits, whether green evidence exists after implementation, and whether the test owner and production-change owner matched the task card.

### Finish Branch Gate
State whether the whole task or branch is actually ready for human merge. Check fresh verification, evidence packet completeness, dirty/untracked artifact classification, out-of-scope changes, remaining risks, and review/merge instructions. If only a phase is accepted, say that Finish Branch Gate is not yet satisfied.

### Unknowns / Decision Gates
State which unknowns were resolved, which remain open, whether new unknown-unknowns were discovered, and whether any decision gate was crossed appropriately.

### Deviations From Plan
List each deviation, whether it was justified, and whether it requires follow-up.

### Reviewer Understanding
Briefly state the behavior changed, critical paths affected, and the verification evidence that supports your understanding. If the evidence is insufficient to understand the change, choose REVISE.

### Next-Loop Instructions
- For ACCEPT: state either that the whole task is ready for human merge, or that only the current phase is accepted and provide the next Claude task-card handoff for remaining phases.
- For REVISE: provide specific, actionable revision instructions for the next iteration.
- For SPLIT: decompose into smaller task cards with goals and acceptance criteria.
- For REJECT: explain why the approach is wrong and suggest an alternative.

### Codex Direct Intervention
State whether Codex direct intervention is allowed now. If yes, cite the exact threshold reached, files/modules in scope, and validation required. If no, explicitly state that Codex must not patch and give the next Claude task shape.

### Review-to-Next-Task Contract
For REVISE, SPLIT, or REJECT, provide a task-card-ready handoff with:
- Carry Forward Context
- Next Task Mode: builder / checker-test / mixed-exception / control-plane
- Keep
- Change
- Do Not Repeat
- New Acceptance Criteria
- New Unknowns / Decision Gates
- New Spec / Spark / Parallel / Root Cause / TDD / Finish Branch requirements
- New Handoff Contract

For phase-only ACCEPT with remaining implementation/test-writing work, also provide this contract for the next Claude dispatch.

### Reusable Lessons
Record any knowledge that could inform future planning."

PYTHON_CMD=""
if command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
elif command -v python &>/dev/null; then
    PYTHON_CMD="python"
fi

write_codex_usage() {
    if [ -z "$PYTHON_CMD" ]; then
        {
            echo "# Codex Token / Cost Usage Summary"
            echo ""
            echo "Skipped: neither python3 nor python found in PATH."
            echo "Raw review output: $REVIEW_OUTPUT_FILE"
            echo "Codex events: $CODEX_EVENTS_FILE"
        } > "$CODEX_USAGE_FILE"
        return 0
    fi

    "$PYTHON_CMD" - "$CODEX_EVENTS_FILE" "$REVIEW_OUTPUT_FILE" "$CODEX_USAGE_FILE" <<'PYEOF'
import json
import re
import sys
from pathlib import Path

events_path = Path(sys.argv[1])
text_path = Path(sys.argv[2])
usage_path = Path(sys.argv[3])

keys = {
    "input_tokens", "output_tokens", "total_tokens", "cached_input_tokens",
    "cache_read_input_tokens", "cache_creation_input_tokens", "reasoning_tokens",
    "total_cost_usd", "cost_usd", "duration_ms"
}
found = []

def walk(value, prefix=""):
    if isinstance(value, dict):
        for k, v in value.items():
            name = f"{prefix}.{k}" if prefix else k
            if k in keys and isinstance(v, (int, float)):
                found.append((name, v))
            walk(v, name)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            walk(item, f"{prefix}[{i}]")

if events_path.exists() and events_path.stat().st_size:
    for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            walk(json.loads(line))
        except json.JSONDecodeError:
            continue

if not found and text_path.exists():
    text = text_path.read_text(encoding="utf-8", errors="replace")
    patterns = [
        ("input_tokens", r"input[_ ]tokens[:=]\s*([0-9]+)"),
        ("output_tokens", r"output[_ ]tokens[:=]\s*([0-9]+)"),
        ("total_tokens", r"total[_ ]tokens[:=]\s*([0-9]+)"),
        ("total_cost_usd", r"total[_ ]cost[_ ]usd[:=]\s*([0-9.]+)"),
        ("cost_usd", r"cost[_ ]usd[:=]\s*([0-9.]+)"),
    ]
    for name, pat in patterns:
        for m in re.finditer(pat, text, re.I):
            raw = m.group(1)
            found.append((name, float(raw) if "." in raw else int(raw)))

lines = ["# Codex Token / Cost Usage Summary", ""]
if found:
    lines.append("Detected usage fields from Codex review output/events:")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|-------|-------|")
    for name, value in found:
        lines.append(f"| {name} | {value} |")
else:
    lines.append("Usage unavailable: no recognizable token/cost fields were found in Codex output.")
lines.append("")
lines.append(f"Review output: {text_path}")
lines.append(f"Codex events: {events_path}")
usage_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PYEOF
}

echo "Invoking Codex for review..."
if [ ${#EXTRA_FILES[@]} -gt 0 ]; then
    echo "Including ${#EXTRA_FILES[@]} extra evidence file(s)."
fi
echo "Review Output: $REVIEW_OUTPUT_FILE"
echo "Codex Events:  $CODEX_EVENTS_FILE"
echo "Codex Usage:   $CODEX_USAGE_FILE"
echo ""

set +e
codex exec --json "$REVIEW_PROMPT" > "$CODEX_EVENTS_FILE" 2>"${REVIEW_OUTPUT_FILE}.stderr"
CODEX_STATUS=$?
set -e

if [ -s "$CODEX_EVENTS_FILE" ] && [ -n "$PYTHON_CMD" ]; then
    "$PYTHON_CMD" - "$CODEX_EVENTS_FILE" "$REVIEW_OUTPUT_FILE" <<'PYEOF'
import json
import sys
from pathlib import Path

events = Path(sys.argv[1])
out = Path(sys.argv[2])
messages = []
for line in events.read_text(encoding="utf-8", errors="replace").splitlines():
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        continue
    text = None
    for key in ("message", "text", "content", "delta"):
        value = event.get(key) if isinstance(event, dict) else None
        if isinstance(value, str):
            text = value
            break
    if text:
        messages.append(text)
if messages:
    out.write_text("\n".join(messages) + "\n", encoding="utf-8")
else:
    out.write_text(events.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
PYEOF
else
    cat "${REVIEW_OUTPUT_FILE}.stderr" > "$REVIEW_OUTPUT_FILE" 2>/dev/null || true
fi

write_codex_usage

if [ -s "$REVIEW_OUTPUT_FILE" ]; then
    cat "$REVIEW_OUTPUT_FILE"
fi
if [ "$CODEX_STATUS" -ne 0 ]; then
    echo "Warning: codex exited with non-zero status. Check $REVIEW_OUTPUT_FILE and ${REVIEW_OUTPUT_FILE}.stderr" >&2
    exit "$CODEX_STATUS"
fi

echo ""
echo "=== Review Complete ==="
echo "Review Output: $REVIEW_OUTPUT_FILE"
echo "Codex Events:  $CODEX_EVENTS_FILE"
echo "Codex Usage:   $CODEX_USAGE_FILE"
