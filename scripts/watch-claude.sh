#!/usr/bin/env bash
# watch-claude.sh  -  Stream compact Claude dispatch progress for CLI observers.
#
# Usage: bash ai/watch-claude.sh [claude-<timestamp>] [--interval seconds] [--lines count] [--once] [--details] [--stale-after seconds]

set -euo pipefail

# Git for Windows can be launched through bin/bash.exe without the usual Unix tool PATH.
PATH="/usr/bin:/bin:/mingw64/bin:${PATH}"
export PATH

INTERVAL=5
TAIL_LINES=24
ONCE=0
DETAILS=0
PLAIN=0
STALE_AFTER=120
TASK_REF=""

while [ $# -gt 0 ]; do
    case "$1" in
        --interval)
            shift
            INTERVAL="${1:-}"
            ;;
        --lines)
            shift
            TAIL_LINES="${1:-}"
            ;;
        --once)
            ONCE=1
            ;;
        --details)
            DETAILS=1
            ;;
        --plain)
            PLAIN=1
            ;;
        --stale-after)
            shift
            STALE_AFTER="${1:-}"
            ;;
        -h|--help)
            echo "Usage: $0 [claude-<timestamp>] [--interval seconds] [--lines count] [--once] [--details] [--plain] [--stale-after seconds]"
            exit 0
            ;;
        *)
            if [ -n "$TASK_REF" ]; then
                echo "Error: unexpected argument: $1" >&2
                exit 1
            fi
            TASK_REF="$1"
            ;;
    esac
    shift || true
done

for value_name in INTERVAL TAIL_LINES STALE_AFTER; do
    value="${!value_name}"
    case "$value" in
        ''|*[!0-9]*) echo "Error: ${value_name} must be a non-negative integer." >&2; exit 1 ;;
    esac
done
if [ "$INTERVAL" -eq 0 ] || [ "$TAIL_LINES" -eq 0 ]; then
    echo "Error: --interval and --lines must be greater than 0." >&2
    exit 1
fi

resolve_repo() {
    git rev-parse --show-toplevel 2>/dev/null || pwd
}

file_size() {
    local file="$1"
    if [ -f "$file" ]; then
        wc -c < "$file" 2>/dev/null | tr -d '[:space:]' || echo 0
    else
        echo 0
    fi
}

count_matches() {
    local pattern="$1"
    local file="$2"
    if [ ! -f "$file" ]; then
        echo 0
        return
    fi
    grep -cE "$pattern" "$file" 2>/dev/null | tr -d '[:space:]' || true
}

is_running() {
    local pid_file="$1"
    if [ ! -f "$pid_file" ]; then
        return 1
    fi
    local pid
    pid="$(tr -d '[:space:]' < "$pid_file")"
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

field_from_line() {
    local line="$1"
    local key="$2"
    echo "$line" | sed -n "s/.*${key}=\([0-9][0-9]*\).*/\1/p"
}

progress_bar() {
    local percent="$1"
    local width=20
    local filled=$((percent * width / 100))
    local empty=$((width - filled))
    local bar=""
    local i=0
    while [ "$i" -lt "$filled" ]; do bar="${bar}#"; i=$((i + 1)); done
    i=0
    while [ "$i" -lt "$empty" ]; do bar="${bar}-"; i=$((i + 1)); done
    printf '[%s] %s%%' "$bar" "$percent"
}

REPO_ROOT="$(resolve_repo)"
WORKTREE_ROOT="${REPO_ROOT}/.worktrees"

if [ -z "$TASK_REF" ]; then
    latest="$(find "$WORKTREE_ROOT" -maxdepth 1 -type f -name 'claude-*.progress.log' 2>/dev/null | sort | tail -1 || true)"
    if [ -z "$latest" ]; then
        echo "No Claude progress logs found under $WORKTREE_ROOT" >&2
        exit 1
    fi
    TASK_ID="$(basename "$latest" .progress.log)"
elif [ -d "$TASK_REF" ]; then
    TASK_ID="$(basename "$TASK_REF")"
else
    TASK_ID="$(basename "$TASK_REF")"
    TASK_ID="${TASK_ID%.progress.log}"
    TASK_ID="${TASK_ID%.claude-progress.md}"
    TASK_ID="${TASK_ID%.pid}"
fi

PREFIX="${WORKTREE_ROOT}/${TASK_ID}"
WORKTREE_DIR="${WORKTREE_ROOT}/${TASK_ID}"
PID_FILE="${PREFIX}.pid"
PROGRESS_FILE="${PREFIX}.progress.log"
ARCHIVED_CLAUDE_PROGRESS_FILE="${PREFIX}.claude-progress.md"
LIVE_CLAUDE_PROGRESS_FILE="${WORKTREE_DIR}/CLAUDE_PROGRESS.md"
STATUS_FILE="${PREFIX}.status.txt"
REPORT_FILE="${PREFIX}.report.md"
RESULT_FILE="${PREFIX}.result.json"
DIFF_FILE="${PREFIX}.diff"

last_digest=""
printed_header=0

select_claude_progress_file() {
    if [ -f "$LIVE_CLAUDE_PROGRESS_FILE" ]; then
        echo "$LIVE_CLAUDE_PROGRESS_FILE"
    else
        echo "$ARCHIVED_CLAUDE_PROGRESS_FILE"
    fi
}

last_dispatch_line() {
    if [ -f "$PROGRESS_FILE" ]; then
        grep -E 'Claude still running|Claude completed|Claude exited|Claude finished|Stopping Claude' "$PROGRESS_FILE" | tail -1 || true
    fi
}

current_milestone() {
    local file="$1"
    if [ ! -f "$file" ]; then
        echo "No Claude self-reported milestone yet."
        return
    fi
    local line
    line="$(grep -E '^- \[[ xX]\]|^- ' "$file" | tail -1 || true)"
    if [ -z "$line" ]; then
        line="$(grep -v -E '^#|^$' "$file" | tail -1 || true)"
    fi
    if [ -z "$line" ]; then
        echo "Progress file exists, but no milestone text was found."
    else
        echo "$line" | sed 's/^- \[[xX ]\] *//; s/^- *//'
    fi
}

progress_percent() {
    local file="$1"
    if [ ! -f "$file" ]; then
        echo 0
        return
    fi
    local total done
    total="$(count_matches '^- \[[ xX]\]' "$file")"
    done="$(count_matches '^- \[[xX]\]' "$file")"
    total="${total:-0}"
    done="${done:-0}"
    if [ "$total" -gt 0 ]; then
        echo $((done * 100 / total))
    elif [ "$(file_size "$file")" -gt 0 ]; then
        echo 10
    else
        echo 0
    fi
}

stuck_reason() {
    local running="$1"
    local quiet="$2"
    local result_bytes="$3"
    local status_bytes="$4"
    local report_bytes="$5"
    local claude_progress_bytes="$6"
    local claude_progress_source="$7"

    if [ "$running" = "no" ]; then
        if [ "$result_bytes" -gt 0 ]; then
            echo "complete-or-exited: Claude process is not running and result JSON exists."
        else
            echo "exited-without-result: inspect status/report artifacts."
        fi
        return
    fi

    if [ "$quiet" -lt "$STALE_AFTER" ]; then
        echo "none: progress changed recently or stale threshold not reached."
    elif [ "$claude_progress_bytes" -eq 0 ] && [ "$result_bytes" -eq 0 ] && [ "$status_bytes" -eq 0 ]; then
        echo "startup/network/auth wait suspected: no result, stderr, or progress after ${quiet}s."
    elif [ "$claude_progress_bytes" -gt 0 ] && [ -f "$claude_progress_source" ]; then
        echo "long-running current milestone: Claude progress file has not changed for ${quiet}s."
    elif [ "$status_bytes" -gt 0 ]; then
        echo "stderr changed earlier; inspect status tail for tool or CLI errors."
    else
        echo "no artifact growth for ${quiet}s; process may be waiting on Claude CLI/model/tool execution."
    fi
}

print_header() {
    if [ "$printed_header" -eq 0 ]; then
        if [ "$PLAIN" -eq 1 ]; then
            echo "# Claude Watch"
            echo "Task ID: $TASK_ID"
            echo "Mode: plain compact (use --details to show full progress tails)"
            echo "Stale threshold: ${STALE_AFTER}s"
        else
            echo "============================================================"
            echo " CLAUDE CODE WATCH"
            echo " task: ${TASK_ID}"
            echo " mode: status panel (use --details for full tails, --plain for compact text)"
            echo " stale threshold: ${STALE_AFTER}s"
            echo "============================================================"
        fi
        echo ""
        printed_header=1
    fi
}

print_details_if_needed() {
    local show_details="$1"
    local claude_progress_source="$2"
    if [ "$show_details" -ne 1 ]; then
        return
    fi
    echo ""
    echo "## Dispatch Progress Tail"
    if [ -f "$PROGRESS_FILE" ]; then
        tail -n "$TAIL_LINES" "$PROGRESS_FILE"
    else
        echo "(none)"
    fi
    echo ""
    echo "## Claude Progress Tail"
    if [ -f "$claude_progress_source" ]; then
        echo "Source: $claude_progress_source"
        tail -n "$TAIL_LINES" "$claude_progress_source"
    else
        echo "(none)"
    fi
    if [ -f "$STATUS_FILE" ] && [ "$(file_size "$STATUS_FILE")" -gt 0 ]; then
        echo ""
        echo "## Status Tail"
        tail -n "$TAIL_LINES" "$STATUS_FILE"
    fi
}

print_snapshot() {
    print_header

    local claude_progress_source running last_line elapsed quiet result_bytes status_bytes report_bytes claude_progress_bytes diff_bytes percent bar milestone reason digest show_details
    claude_progress_source="$(select_claude_progress_file)"
    if is_running "$PID_FILE"; then running="yes"; else running="no"; fi
    last_line="$(last_dispatch_line)"
    elapsed="$(field_from_line "$last_line" "elapsed_seconds")"; elapsed="${elapsed:-0}"
    quiet="$(field_from_line "$last_line" "quiet_seconds")"; quiet="${quiet:-0}"
    result_bytes="$(file_size "$RESULT_FILE")"
    status_bytes="$(file_size "$STATUS_FILE")"
    report_bytes="$(file_size "$REPORT_FILE")"
    claude_progress_bytes="$(file_size "$claude_progress_source")"
    diff_bytes="$(file_size "$DIFF_FILE")"
    percent="$(progress_percent "$claude_progress_source")"
    bar="$(progress_bar "$percent")"
    milestone="$(current_milestone "$claude_progress_source")"
    reason="$(stuck_reason "$running" "$quiet" "$result_bytes" "$status_bytes" "$report_bytes" "$claude_progress_bytes" "$claude_progress_source")"

    digest="${running}|${elapsed}|${quiet}|${result_bytes}|${status_bytes}|${report_bytes}|${claude_progress_bytes}|${percent}|${milestone}|${reason}"
    if [ "$digest" != "$last_digest" ]; then
        if [ "$running" = "yes" ]; then
            state="RUNNING"
        elif [ "$result_bytes" -gt 0 ]; then
            state="COMPLETE"
        else
            state="STOPPED"
        fi

        if [ "$PLAIN" -eq 1 ]; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] state=${state} elapsed=${elapsed}s quiet=${quiet}s ${bar}"
            echo "current: ${milestone}"
            echo "artifacts: result=${result_bytes}B status=${status_bytes}B report=${report_bytes}B progress=${claude_progress_bytes}B diff=${diff_bytes}B"
            echo "analysis: ${reason}"
            echo ""
        else
            echo "------------------------------------------------------------"
            printf ' STATUS   : %s\n' "$state"
            printf ' TIME     : elapsed=%ss  quiet=%ss\n' "$elapsed" "$quiet"
            printf ' PROGRESS : %s\n' "$bar"
            printf ' CURRENT  : %s\n' "$milestone"
            printf ' FILES    : result=%sB  status=%sB  report=%sB  progress=%sB  diff=%sB\n' "$result_bytes" "$status_bytes" "$report_bytes" "$claude_progress_bytes" "$diff_bytes"
            if [ "$state" = "COMPLETE" ]; then
                printf ' RESULT   : %s\n' "$reason"
            elif echo "$reason" | grep -q '^none:'; then
                printf ' ANALYSIS : %s\n' "$reason"
            else
                printf ' ATTENTION: %s\n' "$reason"
            fi
            echo "------------------------------------------------------------"
            echo ""
        fi
        last_digest="$digest"
    fi

    show_details=0
    if [ "$DETAILS" -eq 1 ]; then
        show_details=1
    elif [ "$running" = "yes" ] && [ "$STALE_AFTER" -gt 0 ] && [ "$quiet" -ge "$STALE_AFTER" ]; then
        show_details=1
    fi
    print_details_if_needed "$show_details" "$claude_progress_source"
}

while true; do
    print_snapshot
    if [ "$ONCE" -eq 1 ]; then
        break
    fi
    if ! is_running "$PID_FILE"; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Claude process is not running; watch complete."
        break
    fi
    sleep "$INTERVAL"
done
