#!/usr/bin/env bash
# run-loop.sh  -  Compose dispatch and review in an explicit loop.
#
# Usage: bash ai/run-loop.sh <task-card-path> [max-iterations]
#
# This script dispatches task cards to Claude Code, reviews with Codex/GPT,
# persists all artifacts, records usage summaries, and never merges automatically.

set -euo pipefail

# Git for Windows can be launched through bin/bash.exe without the usual Unix tool PATH.
# Prepending these paths is harmless on Unix and makes helper scripts stable on Windows.
PATH="/usr/bin:/bin:/mingw64/bin:${PATH}"
export PATH

if [ $# -lt 1 ]; then
    echo "Usage: $0 <task-card-path> [max-iterations]" >&2
    exit 1
fi

TASK_CARD="$1"
MAX_ITERATIONS="${2:-5}"

if [ ! -f "$TASK_CARD" ]; then
    echo "Error: Task card not found: $TASK_CARD" >&2
    exit 1
fi

for tool in git codex; do
    if ! command -v "$tool" &>/dev/null; then
        echo "Error: $tool is not installed or not in PATH." >&2
        exit 1
    fi
done

if ! command -v claude &>/dev/null; then
    echo "Error: claude CLI is not installed or not in PATH." >&2
    echo "Dispatch execution requires Claude Code. Planning, task-card generation, doctor checks, and Codex review remain usable." >&2
    echo "Install Claude Code or run the non-dispatch workflow pieces manually; use doctor_workflow.py to verify readiness." >&2
    exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
TASK_ID="loop-${TIMESTAMP}"
RUN_DIR="${REPO_ROOT}/.worktrees/${TASK_ID}"
USAGE_SUMMARY="${RUN_DIR}/loop-usage-summary.md"
QUALITY_SUMMARY="${RUN_DIR}/loop-quality-summary.md"
QUALITY_JSON="${RUN_DIR}/loop-quality-summary.json"
LOOP_EVENTS="${RUN_DIR}/loop-events.jsonl"

mkdir -p "$RUN_DIR"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DISPATCH_SCRIPT="${SCRIPT_DIR}/dispatch-to-claude.sh"
REVIEW_SCRIPT="${SCRIPT_DIR}/review-with-codex.sh"
SUMMARY_SCRIPT="${SCRIPT_DIR}/summarize-loop-run.py"

PYTHON_CMD=""
if command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
elif command -v python &>/dev/null; then
    PYTHON_CMD="python"
fi

if [ ! -f "$DISPATCH_SCRIPT" ]; then
    echo "Error: dispatch-to-claude.sh not found at $DISPATCH_SCRIPT" >&2
    exit 1
fi

if [ ! -f "$REVIEW_SCRIPT" ]; then
    echo "Error: review-with-codex.sh not found at $REVIEW_SCRIPT" >&2
    exit 1
fi

write_quality_summary() {
    if [ -f "$SUMMARY_SCRIPT" ]; then
        if [ -n "$PYTHON_CMD" ]; then
            "$PYTHON_CMD" "$SUMMARY_SCRIPT" "$RUN_DIR" --output "$QUALITY_SUMMARY" --json-output "$QUALITY_JSON" >/dev/null 2>&1 || true
        fi
    fi
}

write_loop_event() {
    local event="$1"
    local iteration="${2:-}"
    local decision="${3:-}"
    local detail="${4:-}"
    if [ -z "$PYTHON_CMD" ]; then
        printf '{"time":"%s","event":"%s","iteration":"%s","decision":"%s","detail":"%s"}\n' \
            "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$event" "$iteration" "$decision" "$detail" >> "$LOOP_EVENTS"
        return 0
    fi
    "$PYTHON_CMD" - "$LOOP_EVENTS" "$event" "$iteration" "$decision" "$detail" <<'PYEOF'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
event, iteration, decision, detail = sys.argv[2:6]
payload = {
    "time": datetime.now(timezone.utc).isoformat(),
    "event": event,
}
if iteration:
    try:
        payload["iteration"] = int(iteration)
    except ValueError:
        payload["iteration"] = iteration
if decision:
    payload["decision"] = decision
if detail:
    payload["detail"] = detail
with path.open("a", encoding="utf-8") as f:
    f.write(json.dumps(payload, sort_keys=True) + "\n")
PYEOF
}

CURRENT_TASK="${RUN_DIR}/task-card-001.md"
cp "$TASK_CARD" "$CURRENT_TASK"

cat > "$USAGE_SUMMARY" <<EOF
# Loop Usage Summary

Run directory: ${RUN_DIR}
Initial task card: ${TASK_CARD}

EOF
write_loop_event "run_start" "" "" "task_card=${TASK_CARD};max_iterations=${MAX_ITERATIONS}"

echo "=== Loop Runner ==="
echo "Run directory: $RUN_DIR"
echo "Max iterations: $MAX_ITERATIONS"
echo "Usage summary: $USAGE_SUMMARY"
echo "Loop events: $LOOP_EVENTS"
echo ""

ITERATION=1
DECISION=""

copy_if_present() {
    local src="$1"
    local dst_dir="$2"
    if [ -n "$src" ] && [ -f "$src" ]; then
        cp "$src" "$dst_dir/" 2>/dev/null || true
    fi
}

parse_path() {
    local label="$1"
    local log="$2"
    grep "^${label}:" "$log" | sed "s/^${label}: *//" | head -1 || true
}

while [ "$ITERATION" -le "$MAX_ITERATIONS" ]; do
    echo "--- Iteration ${ITERATION} ---"
    write_loop_event "iteration_start" "$ITERATION" "" "task_card=${CURRENT_TASK}"

    DISPATCH_OUTPUT="${RUN_DIR}/dispatch-${ITERATION}"
    mkdir -p "$DISPATCH_OUTPUT"

    echo "Dispatching task card to Claude Code..."
    cd "$REPO_ROOT"

    DISPATCH_LOG="${DISPATCH_OUTPUT}/dispatch.log"
    bash "$DISPATCH_SCRIPT" "$CURRENT_TASK" 2>&1 | tee "$DISPATCH_LOG"

    WORKTREE_DIR="$(parse_path "Worktree" "$DISPATCH_LOG")"
    RESULT_FILE="$(parse_path "Result" "$DISPATCH_LOG")"
    STATUS_FILE="$(parse_path "Status" "$DISPATCH_LOG")"
    DIFFSTAT_FILE="$(parse_path "Diffstat" "$DISPATCH_LOG")"
    DIFF_FILE="$(parse_path "Diff" "$DISPATCH_LOG")"
    CHECKER_REPORT_FILE="$(parse_path "Checker Report" "$DISPATCH_LOG")"
    SOURCE_STATUS_FILE="$(parse_path "Source Status" "$DISPATCH_LOG")"
    WORKTREE_STATUS_FILE="$(parse_path "Worktree Status" "$DISPATCH_LOG")"
    UNTRACKED_FILE="$(parse_path "Untracked Files" "$DISPATCH_LOG")"
    USAGE_FILE="$(parse_path "Usage Summary" "$DISPATCH_LOG")"
    REPORT_FILE="$(parse_path "Report" "$DISPATCH_LOG")"
    CLAUDE_PROGRESS_FILE="$(parse_path "Claude Progress" "$DISPATCH_LOG")"
    CLAUDE_PID_FILE="$(parse_path "Claude PID" "$DISPATCH_LOG")"
    PROGRESS_FILE="$(parse_path "Progress Log" "$DISPATCH_LOG")"

    if [ -z "$RESULT_FILE" ] || [ -z "$DIFF_FILE" ]; then
        write_loop_event "dispatch_incomplete" "$ITERATION" "" "dispatch_log=${DISPATCH_LOG}"
        echo "Error: Dispatch did not produce result.json or diff files." >&2
        echo "Check $DISPATCH_OUTPUT/ for details." >&2
        exit 1
    fi
    write_loop_event "dispatch_complete" "$ITERATION" "" "worktree=${WORKTREE_DIR};checker=${CHECKER_REPORT_FILE}"

    for f in "$RESULT_FILE" "$STATUS_FILE" "$DIFFSTAT_FILE" "$DIFF_FILE" "$CHECKER_REPORT_FILE" \
             "$SOURCE_STATUS_FILE" "$WORKTREE_STATUS_FILE" "$UNTRACKED_FILE" "$USAGE_FILE" "$REPORT_FILE" \
             "$CLAUDE_PROGRESS_FILE" "$CLAUDE_PID_FILE" "$PROGRESS_FILE"; do
        copy_if_present "$f" "$DISPATCH_OUTPUT"
    done

    {
        echo "## Iteration ${ITERATION}"
        echo ""
        echo "### Claude Progress"
        if [ -n "$PROGRESS_FILE" ] && [ -f "$PROGRESS_FILE" ]; then
            cat "$PROGRESS_FILE"
        else
            echo "Claude progress unavailable."
        fi
        echo ""
        echo "### Claude Self-Reported Progress"
        if [ -n "$CLAUDE_PROGRESS_FILE" ] && [ -f "$CLAUDE_PROGRESS_FILE" ]; then
            cat "$CLAUDE_PROGRESS_FILE"
        else
            echo "Claude self-reported progress unavailable."
        fi
        echo ""
        echo "### Claude Usage"
        if [ -n "$USAGE_FILE" ] && [ -f "$USAGE_FILE" ]; then
            cat "$USAGE_FILE"
        else
            echo "Claude usage unavailable."
        fi
        echo ""
        echo "### Checker Report"
        if [ -n "$CHECKER_REPORT_FILE" ] && [ -f "$CHECKER_REPORT_FILE" ]; then
            cat "$CHECKER_REPORT_FILE"
        else
            echo "Checker report unavailable."
        fi
        echo ""
    } >> "$USAGE_SUMMARY"

    REVIEW_OUTPUT="${RUN_DIR}/review-${ITERATION}.txt"

    echo ""
    echo "Sending evidence to Codex/GPT for review..."
    set +e
    bash "$REVIEW_SCRIPT" "$CURRENT_TASK" "$RESULT_FILE" "$DIFF_FILE" \
        "$CHECKER_REPORT_FILE" "$USAGE_FILE" "$SOURCE_STATUS_FILE" "$WORKTREE_STATUS_FILE" "$UNTRACKED_FILE" "$REPORT_FILE" \
        "$CLAUDE_PROGRESS_FILE" "$PROGRESS_FILE" "$CLAUDE_PID_FILE" 2>&1 | tee "$REVIEW_OUTPUT"
    REVIEW_STATUS=${PIPESTATUS[0]}
    set -e

    REVIEW_ARTIFACT="$(grep '^Review Output:' "$REVIEW_OUTPUT" | sed 's/^Review Output: *//' | tail -1 || true)"
    CODEX_EVENTS="$(grep '^Codex Events:' "$REVIEW_OUTPUT" | sed 's/^Codex Events: *//' | tail -1 || true)"
    CODEX_USAGE="$(grep '^Codex Usage:' "$REVIEW_OUTPUT" | sed 's/^Codex Usage: *//' | tail -1 || true)"

    copy_if_present "$REVIEW_ARTIFACT" "$DISPATCH_OUTPUT"
    copy_if_present "$CODEX_EVENTS" "$DISPATCH_OUTPUT"
    copy_if_present "$CODEX_USAGE" "$DISPATCH_OUTPUT"

    {
        echo "### Codex Usage"
        if [ -n "$CODEX_USAGE" ] && [ -f "$CODEX_USAGE" ]; then
            cat "$CODEX_USAGE"
        else
            echo "Codex usage unavailable."
        fi
        echo ""
    } >> "$USAGE_SUMMARY"

    if [ "$REVIEW_STATUS" -ne 0 ]; then
        write_loop_event "review_failed" "$ITERATION" "" "status=${REVIEW_STATUS};review=${REVIEW_OUTPUT}"
        echo "Review command failed with status $REVIEW_STATUS. Human intervention required." >&2
        echo "Run directory: $RUN_DIR" >&2
        exit "$REVIEW_STATUS"
    fi
    write_loop_event "review_complete" "$ITERATION" "" "review=${REVIEW_OUTPUT}"

    DECISION="$(grep -iE '^\*\*(ACCEPT|REVISE|SPLIT|REJECT)\*\*|^[-*] \*\*(ACCEPT|REVISE|SPLIT|REJECT)\*\*|(ACCEPT|REVISE|SPLIT|REJECT)' "$REVIEW_OUTPUT" \
        | head -1 \
        | sed 's/.*\(ACCEPT\|REVISE\|SPLIT\|REJECT\).*/\1/i' \
        || true)"

    echo ""
    echo "Decision: ${DECISION:-UNKNOWN}"
    write_loop_event "decision" "$ITERATION" "${DECISION:-UNKNOWN}" "review=${REVIEW_OUTPUT}"

    cat > "${RUN_DIR}/iteration-${ITERATION}-summary.md" <<EOF
# Iteration ${ITERATION}

## Task Card
${CURRENT_TASK}

## Dispatch Output
${DISPATCH_OUTPUT}/

## Review Output
${REVIEW_OUTPUT}

## Usage Summary
${USAGE_SUMMARY}

## Decision
${DECISION:-UNKNOWN}
EOF

    case "$(echo "$DECISION" | tr '[:lower:]' '[:upper:]')" in
        ACCEPT)
            write_loop_event "stop" "$ITERATION" "ACCEPT" "accepted"
            write_quality_summary
            echo ""
            echo "=== Loop Complete: ACCEPTED ==="
            echo "The change is ready for human review and merge."
            echo "Worktree: $WORKTREE_DIR"
            echo "Run directory: $RUN_DIR"
            echo "Usage summary: $USAGE_SUMMARY"
            echo "Quality summary: $QUALITY_SUMMARY"
            echo "Loop events: $LOOP_EVENTS"
            exit 0
            ;;
        REVISE)
            echo ""
            echo "Decision is REVISE. Preparing next iteration..."
            ITERATION=$((ITERATION + 1))

            if [ "$ITERATION" -gt "$MAX_ITERATIONS" ]; then
                write_loop_event "stop" "$((ITERATION - 1))" "REVISE" "max_iterations_reached"
                write_quality_summary
                echo ""
                echo "=== Loop Stopped: Max iterations reached ==="
                echo "Human intervention required."
                echo "Codex direct intervention may now be appropriate if the repeated Claude attempts are documented and another Claude revision is unlikely to help."
                echo "Run directory: $RUN_DIR"
                echo "Usage summary: $USAGE_SUMMARY"
                echo "Quality summary: $QUALITY_SUMMARY"
                echo "Loop events: $LOOP_EVENTS"
                exit 1
            fi

            REVISED_TASK="${RUN_DIR}/task-card-$(printf '%03d' $ITERATION).md"
            cp "$CURRENT_TASK" "$REVISED_TASK"
            cat >> "$REVISED_TASK" <<EOF

---

## Loop Context (auto-generated by run-loop.sh)

- **Iteration:** ${ITERATION}
- **Prior decision:** REVISE
- **Review feedback:** See ${REVIEW_OUTPUT}
- **Review-to-Next-Task Contract:** Copy forward Carry Forward Context, Keep, Change, Do Not Repeat, New Acceptance Criteria, New Unknowns / Decision Gates, and New Handoff Contract from ${REVIEW_OUTPUT}.
- **Usage summary:** See ${USAGE_SUMMARY}

EOF
            CURRENT_TASK="$REVISED_TASK"
            write_loop_event "revision_task_created" "$ITERATION" "REVISE" "task_card=${CURRENT_TASK}"
            echo "Revised task card: $CURRENT_TASK"
            echo ""
            ;;
        SPLIT)
            write_loop_event "stop" "$ITERATION" "SPLIT" "split_requested"
            write_quality_summary
            echo ""
            echo "=== Loop Stopped: SPLIT ==="
            echo "Review the decision in: $REVIEW_OUTPUT"
            echo "Run directory: $RUN_DIR"
            echo "Usage summary: $USAGE_SUMMARY"
            echo "Quality summary: $QUALITY_SUMMARY"
            echo "Loop events: $LOOP_EVENTS"
            exit 0
            ;;
        REJECT)
            write_loop_event "stop" "$ITERATION" "REJECT" "rejected"
            write_quality_summary
            echo ""
            echo "=== Loop Stopped: REJECTED ==="
            echo "Review the decision in: $REVIEW_OUTPUT"
            echo "Run directory: $RUN_DIR"
            echo "Usage summary: $USAGE_SUMMARY"
            echo "Quality summary: $QUALITY_SUMMARY"
            echo "Loop events: $LOOP_EVENTS"
            exit 1
            ;;
        *)
            write_loop_event "stop" "$ITERATION" "${DECISION:-UNKNOWN}" "unknown_decision"
            write_quality_summary
            echo ""
            echo "=== Loop Stopped: Unknown Decision ==="
            echo "Review output: $REVIEW_OUTPUT"
            echo "Run directory: $RUN_DIR"
            echo "Usage summary: $USAGE_SUMMARY"
            echo "Quality summary: $QUALITY_SUMMARY"
            echo "Loop events: $LOOP_EVENTS"
            exit 1
            ;;
    esac
done

echo ""
echo "=== Loop Stopped: Max iterations reached ==="
write_loop_event "stop" "$MAX_ITERATIONS" "${DECISION:-UNKNOWN}" "max_iterations_reached"
write_quality_summary
echo "Human intervention required."
echo "Codex direct intervention may now be appropriate if the repeated Claude attempts are documented and another Claude revision is unlikely to help."
echo "Run directory: $RUN_DIR"
echo "Usage summary: $USAGE_SUMMARY"
echo "Quality summary: $QUALITY_SUMMARY"
echo "Loop events: $LOOP_EVENTS"
exit 1
