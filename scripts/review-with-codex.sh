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
- If direct intervention is justified, state the failed attempts, why another Claude revision is unlikely to help, the allowed scope, and required validation.
- Compare the implementation against the original task card requirements.
- Use the Claude modification report if present, but verify it against the diff and evidence.
- Evaluate whether the implementation matches the task card intent.
- Review the task card Unknowns and Decision Gates. Decide whether known unknowns were resolved, new unknown-unknowns were surfaced, and any decision gate was crossed with appropriate authority.
- Review any Deviations From Plan. Accept deviations only when the discovered constraint is real, the action taken is conservative or explicitly allowed, and the reviewer briefing makes the behavioral impact clear.
- Check the Handoff Contract if present. Verify Must do, Must not do, May decide, Must report, and Stop condition against the diff and evidence.
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
One of: **ACCEPT**, **REVISE**, **SPLIT**, **REJECT**

### Reasoning
A concise explanation of why this decision was made.

### Requirements Comparison
Map the task card acceptance criteria to the observed implementation and evidence.

### Unknowns / Decision Gates
State which unknowns were resolved, which remain open, whether new unknown-unknowns were discovered, and whether any decision gate was crossed appropriately.

### Deviations From Plan
List each deviation, whether it was justified, and whether it requires follow-up.

### Reviewer Understanding
Briefly state the behavior changed, critical paths affected, and the verification evidence that supports your understanding. If the evidence is insufficient to understand the change, choose REVISE.

### Next-Loop Instructions
- For ACCEPT: state that the change is ready for human merge.
- For REVISE: provide specific, actionable revision instructions for the next iteration.
- For SPLIT: decompose into smaller task cards with goals and acceptance criteria.
- For REJECT: explain why the approach is wrong and suggest an alternative.

### Codex Direct Intervention
State whether Codex direct intervention is allowed now. If yes, cite the exact threshold reached, files/modules in scope, and validation required. If no, explicitly state that Codex must not patch and give the next Claude task shape.

### Review-to-Next-Task Contract
For REVISE, SPLIT, or REJECT, provide a task-card-ready handoff with:
- Carry Forward Context
- Keep
- Change
- Do Not Repeat
- New Acceptance Criteria
- New Unknowns / Decision Gates
- New Handoff Contract

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
