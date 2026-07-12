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

USER_TIMEOUT_SET=0
if [ "${CLAUDE_CODE_TIMEOUT_SECONDS+x}" = "x" ]; then
    USER_TIMEOUT_SET=1
fi
ADAPTIVE_WAIT="${CLAUDE_CODE_ADAPTIVE_WAIT:-1}"
LOOP_FIRST_TIMEOUT_SECONDS="${CLAUDE_CODE_LOOP_FIRST_TIMEOUT_SECONDS:-600}"
LOOP_MIN_TIMEOUT_SECONDS="${CLAUDE_CODE_LOOP_MIN_TIMEOUT_SECONDS:-300}"
LOOP_MAX_TIMEOUT_SECONDS="${CLAUDE_CODE_LOOP_MAX_TIMEOUT_SECONDS:-1800}"
LOOP_TIMEOUT_BUFFER_SECONDS="${CLAUDE_CODE_LOOP_TIMEOUT_BUFFER_SECONDS:-120}"
NEXT_TIMEOUT_SECONDS="$LOOP_FIRST_TIMEOUT_SECONDS"

case "$ADAPTIVE_WAIT" in
    0|1) ;;
    *) echo "Error: CLAUDE_CODE_ADAPTIVE_WAIT must be 0 or 1." >&2; exit 1 ;;
esac
for value_name in LOOP_FIRST_TIMEOUT_SECONDS LOOP_MIN_TIMEOUT_SECONDS LOOP_MAX_TIMEOUT_SECONDS LOOP_TIMEOUT_BUFFER_SECONDS; do
    value="${!value_name}"
    case "$value" in
        ''|*[!0-9]*) echo "Error: ${value_name} must be a non-negative integer." >&2; exit 1 ;;
    esac
done
if [ "$LOOP_MIN_TIMEOUT_SECONDS" -gt "$LOOP_MAX_TIMEOUT_SECONDS" ]; then
    echo "Error: CLAUDE_CODE_LOOP_MIN_TIMEOUT_SECONDS cannot exceed CLAUDE_CODE_LOOP_MAX_TIMEOUT_SECONDS." >&2
    exit 1
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

progress_counts() {
    local file="$1"
    if [ ! -f "$file" ]; then
        echo "0 0"
        return
    fi
    local total done
    total="$(grep -cE '^- \[[ xX]\]' "$file" 2>/dev/null | tr -d '[:space:]' || true)"
    done="$(grep -cE '^- \[[xX]\]' "$file" 2>/dev/null | tr -d '[:space:]' || true)"
    echo "${total:-0} ${done:-0}"
}

elapsed_seconds_from_progress() {
    local file="$1"
    if [ ! -f "$file" ]; then
        echo 0
        return
    fi
    local elapsed
    elapsed="$(grep -Eo 'elapsed_seconds=[0-9]+' "$file" 2>/dev/null | tail -1 | sed 's/elapsed_seconds=//' || true)"
    echo "${elapsed:-0}"
}

clamp_timeout() {
    local value="$1"
    if [ "$value" -lt "$LOOP_MIN_TIMEOUT_SECONDS" ]; then
        echo "$LOOP_MIN_TIMEOUT_SECONDS"
    elif [ "$value" -gt "$LOOP_MAX_TIMEOUT_SECONDS" ]; then
        echo "$LOOP_MAX_TIMEOUT_SECONDS"
    else
        echo "$value"
    fi
}

update_adaptive_timeout() {
    local iteration="$1"
    local progress_file="$2"
    local dispatch_progress="$3"
    local current_timeout="$4"
    local counts total done elapsed remaining per_item proposed

    counts="$(progress_counts "$progress_file")"
    total="${counts%% *}"
    done="${counts##* }"
    elapsed="$(elapsed_seconds_from_progress "$dispatch_progress")"

    if [ "$done" -gt 0 ] && [ "$elapsed" -gt 0 ]; then
        per_item=$(( (elapsed + done - 1) / done ))
        remaining=$(( total - done ))
        if [ "$remaining" -lt 1 ]; then
            remaining=1
        fi
        proposed=$(( per_item * remaining + LOOP_TIMEOUT_BUFFER_SECONDS ))
    elif [ "$elapsed" -gt 0 ]; then
        proposed=$(( current_timeout + LOOP_TIMEOUT_BUFFER_SECONDS ))
        per_item=0
        remaining="${total:-0}"
    else
        proposed="$current_timeout"
        per_item=0
        remaining="${total:-0}"
    fi

    NEXT_TIMEOUT_SECONDS="$(clamp_timeout "$proposed")"
    write_loop_event "adaptive_timeout_observed" "$iteration" "" "elapsed_seconds=${elapsed};progress_done=${done};progress_total=${total};per_item_seconds=${per_item:-0};remaining_items=${remaining:-0};next_timeout_seconds=${NEXT_TIMEOUT_SECONDS}"
}

while [ "$ITERATION" -le "$MAX_ITERATIONS" ]; do
    echo "--- Iteration ${ITERATION} ---"
    write_loop_event "iteration_start" "$ITERATION" "" "task_card=${CURRENT_TASK}"

    DISPATCH_OUTPUT="${RUN_DIR}/dispatch-${ITERATION}"
    mkdir -p "$DISPATCH_OUTPUT"

    echo "Dispatching task card to Claude Code..."
    cd "$REPO_ROOT"

    DISPATCH_LOG="${DISPATCH_OUTPUT}/dispatch.log"
    DISPATCH_TIMEOUT_SECONDS="${CLAUDE_CODE_TIMEOUT_SECONDS:-$NEXT_TIMEOUT_SECONDS}"
    if [ "$USER_TIMEOUT_SET" -eq 0 ] && [ "$ADAPTIVE_WAIT" -eq 1 ]; then
        echo "Adaptive dispatch timeout: ${DISPATCH_TIMEOUT_SECONDS}s"
        write_loop_event "adaptive_timeout_applied" "$ITERATION" "" "timeout_seconds=${DISPATCH_TIMEOUT_SECONDS};source=adaptive"
        CLAUDE_CODE_TIMEOUT_SECONDS="$DISPATCH_TIMEOUT_SECONDS" bash "$DISPATCH_SCRIPT" "$CURRENT_TASK" 2>&1 | tee "$DISPATCH_LOG"
    else
        write_loop_event "adaptive_timeout_skipped" "$ITERATION" "" "user_timeout_set=${USER_TIMEOUT_SET};adaptive_wait=${ADAPTIVE_WAIT}"
        bash "$DISPATCH_SCRIPT" "$CURRENT_TASK" 2>&1 | tee "$DISPATCH_LOG"
    fi

    WORKTREE_DIR="$(parse_path "Worktree" "$DISPATCH_LOG")"
    RESULT_FILE="$(parse_path "Result" "$DISPATCH_LOG")"
    RAW_RESULT_FILE="$(parse_path "Raw Result" "$DISPATCH_LOG")"
    STATUS_FILE="$(parse_path "Status" "$DISPATCH_LOG")"
    NETWORK_FILE="$(parse_path "Network Log" "$DISPATCH_LOG")"
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
    if [ "$USER_TIMEOUT_SET" -eq 0 ] && [ "$ADAPTIVE_WAIT" -eq 1 ]; then
        update_adaptive_timeout "$ITERATION" "$CLAUDE_PROGRESS_FILE" "$PROGRESS_FILE" "$DISPATCH_TIMEOUT_SECONDS"
    fi

    for f in "$RESULT_FILE" "$RAW_RESULT_FILE" "$STATUS_FILE" "$DIFFSTAT_FILE" "$DIFF_FILE" "$CHECKER_REPORT_FILE" \
             "$SOURCE_STATUS_FILE" "$WORKTREE_STATUS_FILE" "$UNTRACKED_FILE" "$USAGE_FILE" "$REPORT_FILE" \
             "$CLAUDE_PROGRESS_FILE" "$CLAUDE_PID_FILE" "$PROGRESS_FILE" "$NETWORK_FILE"; do
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
        echo "### Claude Network Diagnostics"
        if [ -n "$NETWORK_FILE" ] && [ -f "$NETWORK_FILE" ]; then
            cat "$NETWORK_FILE"
        else
            echo "Claude network diagnostics unavailable or disabled."
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
        "$CHECKER_REPORT_FILE" "$USAGE_FILE" "$SOURCE_STATUS_FILE" "$WORKTREE_STATUS_FILE" "$UNTRACKED_FILE" "$REPORT_FILE" "$RAW_RESULT_FILE" \
        "$CLAUDE_PROGRESS_FILE" "$PROGRESS_FILE" "$NETWORK_FILE" "$CLAUDE_PID_FILE" 2>&1 | tee "$REVIEW_OUTPUT"
    REVIEW_STATUS=${PIPESTATUS[0]}
    set -e

    REVIEW_ARTIFACT="$(grep '^Review Output:' "$REVIEW_OUTPUT" | sed 's/^Review Output: *//' | tail -1 || true)"
    REVIEW_DECISION_FILE="$(grep '^Review Decision:' "$REVIEW_OUTPUT" | sed 's/^Review Decision: *//' | tail -1 || true)"
    NEXT_TASK_DRAFT_FILE="$(grep '^Next Task Draft:' "$REVIEW_OUTPUT" | sed 's/^Next Task Draft: *//' | tail -1 || true)"
    CODEX_EVENTS="$(grep '^Codex Events:' "$REVIEW_OUTPUT" | sed 's/^Codex Events: *//' | tail -1 || true)"
    CODEX_USAGE="$(grep '^Codex Usage:' "$REVIEW_OUTPUT" | sed 's/^Codex Usage: *//' | tail -1 || true)"

    copy_if_present "$REVIEW_ARTIFACT" "$DISPATCH_OUTPUT"
    copy_if_present "$REVIEW_DECISION_FILE" "$DISPATCH_OUTPUT"
    copy_if_present "$NEXT_TASK_DRAFT_FILE" "$DISPATCH_OUTPUT"
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

    # Extract decision from structured Review Decision JSON
    DECISION=""
    DECISION_SCOPE=""
    if [ -z "$REVIEW_DECISION_FILE" ] || [ ! -f "$REVIEW_DECISION_FILE" ]; then
        write_loop_event "stop" "$ITERATION" "" "missing_review_decision"
        echo "Error: Review Decision file not found. Structured decision required." >&2
        echo "Review output: $REVIEW_OUTPUT" >&2
        echo "Run directory: $RUN_DIR" >&2
        exit 1
    fi

    if [ -n "$PYTHON_CMD" ]; then
        DECISION="$("$PYTHON_CMD" - "$REVIEW_DECISION_FILE" <<'PYEOF'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text(encoding="utf-8"))
    print(data.get("decision", ""))
except (json.JSONDecodeError, OSError):
    print("")
PYEOF
)"
        DECISION_SCOPE="$("$PYTHON_CMD" - "$REVIEW_DECISION_FILE" <<'PYEOF'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text(encoding="utf-8"))
    print(data.get("scope", ""))
except (json.JSONDecodeError, OSError):
    print("")
PYEOF
)"
    fi

    if [ -z "$DECISION" ]; then
        write_loop_event "stop" "$ITERATION" "" "invalid_review_decision"
        echo "Error: Could not extract decision from Review Decision JSON." >&2
        echo "Review Decision: $REVIEW_DECISION_FILE" >&2
        echo "Run directory: $RUN_DIR" >&2
        exit 1
    fi

    echo ""
    echo "Decision: ${DECISION} (scope: ${DECISION_SCOPE:-unknown})"
    write_loop_event "decision" "$ITERATION" "${DECISION}" "scope=${DECISION_SCOPE:-unknown};review_decision=${REVIEW_DECISION_FILE}"

    cat > "${RUN_DIR}/iteration-${ITERATION}-summary.md" <<EOF
# Iteration ${ITERATION}

## Task Card
${CURRENT_TASK}

## Dispatch Output
${DISPATCH_OUTPUT}/

## Review Output
${REVIEW_OUTPUT}

## Review Decision
${REVIEW_DECISION_FILE}

## Usage Summary
${USAGE_SUMMARY}

## Decision
${DECISION} (scope: ${DECISION_SCOPE:-unknown})
EOF

    case "$(echo "$DECISION" | tr '[:lower:]' '[:upper:]')" in
        ACCEPT)
            if [ "$DECISION_SCOPE" = "whole-task" ]; then
                write_loop_event "stop" "$ITERATION" "ACCEPT" "whole-task_accepted"
                write_quality_summary
                echo ""
                echo "=== Loop Complete: ACCEPTED (whole-task) ==="
                echo "The change is ready for human review and merge."
                echo "Worktree: $WORKTREE_DIR"
                echo "Run directory: $RUN_DIR"
                echo "Usage summary: $USAGE_SUMMARY"
                echo "Quality summary: $QUALITY_SUMMARY"
                echo "Loop events: $LOOP_EVENTS"
                exit 0
            elif [ "$DECISION_SCOPE" = "phase" ]; then
                # Phase accept: check if next_task is present
                HAS_NEXT_TASK="false"
                if [ -n "$NEXT_TASK_DRAFT_FILE" ] && [ -f "$NEXT_TASK_DRAFT_FILE" ] && [ -s "$NEXT_TASK_DRAFT_FILE" ]; then
                    HAS_NEXT_TASK="true"
                fi
                write_loop_event "stop" "$ITERATION" "ACCEPT" "phase_accepted;has_next_task=${HAS_NEXT_TASK}"
                write_quality_summary
                echo ""
                echo "=== Loop Stopped: Phase ACCEPTED ==="
                echo "Phase accepted but whole-task is not complete."
                echo "Review decision: $REVIEW_DECISION_FILE"
                if [ "$HAS_NEXT_TASK" = "true" ]; then
                    echo "Next task draft: $NEXT_TASK_DRAFT_FILE"
                fi
                echo "Run directory: $RUN_DIR"
                echo "Usage summary: $USAGE_SUMMARY"
                echo "Quality summary: $QUALITY_SUMMARY"
                echo "Loop events: $LOOP_EVENTS"
                exit 0
            else
                write_loop_event "stop" "$ITERATION" "ACCEPT" "accepted;scope=${DECISION_SCOPE:-unknown}"
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
            fi
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
- **Review decision:** See ${REVIEW_DECISION_FILE}
- **Review feedback:** See ${REVIEW_OUTPUT}
- **Review-to-Next-Task Contract:** Copy forward Carry Forward Context, Keep, Change, Do Not Repeat, New Acceptance Criteria, New Unknowns / Decision Gates, and New Handoff Contract from ${REVIEW_DECISION_FILE} and ${REVIEW_OUTPUT}.
- **Structured next-task draft:** See ${NEXT_TASK_DRAFT_FILE:-none}
- **Usage summary:** See ${USAGE_SUMMARY}

EOF
            CURRENT_TASK="$REVISED_TASK"
            write_loop_event "revision_task_created" "$ITERATION" "REVISE" "task_card=${CURRENT_TASK};review_decision=${REVIEW_DECISION_FILE};next_task_draft=${NEXT_TASK_DRAFT_FILE:-none}"
            echo "Revised task card: $CURRENT_TASK"
            echo "Review decision: $REVIEW_DECISION_FILE"
            if [ -n "$NEXT_TASK_DRAFT_FILE" ] && [ -f "$NEXT_TASK_DRAFT_FILE" ] && [ -s "$NEXT_TASK_DRAFT_FILE" ]; then
                echo "Next task draft: $NEXT_TASK_DRAFT_FILE"
            fi
            echo ""
            ;;
        SPLIT)
            write_loop_event "stop" "$ITERATION" "SPLIT" "split_requested"
            write_quality_summary
            echo ""
            echo "=== Loop Stopped: SPLIT ==="
            echo "Review decision: $REVIEW_DECISION_FILE"
            echo "Review output: $REVIEW_OUTPUT"
            if [ -n "$NEXT_TASK_DRAFT_FILE" ] && [ -f "$NEXT_TASK_DRAFT_FILE" ] && [ -s "$NEXT_TASK_DRAFT_FILE" ]; then
                echo "Next task draft: $NEXT_TASK_DRAFT_FILE"
            fi
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
            echo "Review decision: $REVIEW_DECISION_FILE"
            echo "Review output: $REVIEW_OUTPUT"
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
            echo "Review decision: $REVIEW_DECISION_FILE"
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
