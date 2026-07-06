#!/usr/bin/env bash
# watch-claude.sh  -  Stream compact Claude dispatch progress for CLI observers.
#
# Usage: bash ai/watch-claude.sh [claude-<timestamp>] [--interval seconds] [--lines count] [--once] [--details] [--stale-after seconds] [--wait-profile small|medium|large] [--startup-grace seconds] [--interrupt-after seconds]

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
STALE_AFTER_EXPLICIT=0
WAIT_PROFILE="${CLAUDE_CODE_WAIT_PROFILE:-medium}"
STARTUP_GRACE=""
INTERRUPT_AFTER=""
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
            STALE_AFTER_EXPLICIT=1
            ;;
        --wait-profile)
            shift
            WAIT_PROFILE="${1:-}"
            ;;
        --startup-grace)
            shift
            STARTUP_GRACE="${1:-}"
            ;;
        --interrupt-after)
            shift
            INTERRUPT_AFTER="${1:-}"
            ;;
        -h|--help)
            echo "Usage: $0 [claude-<timestamp>] [--interval seconds] [--lines count] [--once] [--details] [--plain] [--stale-after seconds] [--wait-profile small|medium|large] [--startup-grace seconds] [--interrupt-after seconds]"
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

case "$WAIT_PROFILE" in
    small)
        DEFAULT_STARTUP_GRACE=30
        DEFAULT_STALE_AFTER=60
        DEFAULT_INTERRUPT_AFTER=240
        ;;
    medium)
        DEFAULT_STARTUP_GRACE=60
        DEFAULT_STALE_AFTER=120
        DEFAULT_INTERRUPT_AFTER=600
        ;;
    large)
        DEFAULT_STARTUP_GRACE=120
        DEFAULT_STALE_AFTER=300
        DEFAULT_INTERRUPT_AFTER=1200
        ;;
    *)
        echo "Error: --wait-profile must be one of: small, medium, large." >&2
        exit 1
        ;;
esac

if [ "$STALE_AFTER_EXPLICIT" -eq 0 ]; then
    STALE_AFTER="$DEFAULT_STALE_AFTER"
fi
STARTUP_GRACE="${STARTUP_GRACE:-$DEFAULT_STARTUP_GRACE}"
INTERRUPT_AFTER="${INTERRUPT_AFTER:-$DEFAULT_INTERRUPT_AFTER}"

for value_name in INTERVAL TAIL_LINES STALE_AFTER STARTUP_GRACE INTERRUPT_AFTER; do
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

worktree_change_count() {
    if [ ! -d "$WORKTREE_DIR" ]; then
        echo 0
        return
    fi
    git -c "safe.directory=$WORKTREE_DIR" -C "$WORKTREE_DIR" status --porcelain --untracked-files=all 2>/dev/null \
        | grep -v -E '^(.. )?(TASK_CARD|CLAUDE_PROMPT|CLAUDE_REPORT|CLAUDE_PROGRESS)(\.md)?$' \
        | wc -l 2>/dev/null | tr -d '[:space:]' || echo 0
}

partial_diffstat() {
    if [ ! -d "$WORKTREE_DIR" ]; then
        echo "(worktree unavailable)"
        return
    fi
    local out
    out="$(git -c "safe.directory=$WORKTREE_DIR" -C "$WORKTREE_DIR" diff --shortstat 2>/dev/null || true)"
    if [ -z "$out" ]; then
        out="$(git -c "safe.directory=$WORKTREE_DIR" -C "$WORKTREE_DIR" status --short --untracked-files=all 2>/dev/null \
            | grep -v -E ' (TASK_CARD|CLAUDE_PROMPT|CLAUDE_REPORT|CLAUDE_PROGRESS)(\.md)?$' \
            | head -5 || true)"
    fi
    if [ -z "$out" ]; then
        echo "(no implementation changes detected)"
    else
        echo "$out"
    fi
}

partial_risk_summary() {
    if [ ! -d "$WORKTREE_DIR" ]; then
        echo "files=0 tests_touched=0 high_risk=0"
        return
    fi
    local files file_count test_count high_risk_count
    files="$(git -c "safe.directory=$WORKTREE_DIR" -C "$WORKTREE_DIR" status --porcelain --untracked-files=all 2>/dev/null \
        | grep -v -E ' (TASK_CARD|CLAUDE_PROMPT|CLAUDE_REPORT|CLAUDE_PROGRESS)(\.md)?$' \
        | sed 's/^...//' || true)"
    if [ -z "$files" ]; then
        echo "files=0 tests_touched=0 high_risk=0"
        return
    fi
    file_count="$(printf '%s\n' "$files" | sed '/^$/d' | wc -l 2>/dev/null | tr -d '[:space:]')"
    test_count="$(printf '%s\n' "$files" | grep -Eic '(^|/)(test|tests|spec|__tests__)/|(_test|\.test|\.spec)\.' || true)"
    high_risk_count="$(printf '%s\n' "$files" | grep -Eic '(^|/)(migrations?|infra|deploy|auth|security|billing|payment)(/|$)|package-lock\.json|pnpm-lock\.yaml|yarn\.lock|Cargo\.lock|go\.sum|requirements.*\.txt' || true)"
    echo "files=${file_count:-0} tests_touched=${test_count:-0} high_risk=${high_risk_count:-0}"
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
    local worktree_changes="$8"

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
    elif [ "$worktree_changes" -gt 0 ]; then
        echo "partial-implementation-present: worktree has changes; review partial diff and continue waiting if it aligns with the plan."
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

recommended_action() {
    local running="$1"
    local elapsed="$2"
    local quiet="$3"
    local result_bytes="$4"
    local status_bytes="$5"
    local claude_progress_bytes="$6"
    local worktree_changes="$7"

    if [ "$running" = "no" ]; then
        if [ "$result_bytes" -gt 0 ]; then
            echo "COMPLETE"
        else
            echo "INSPECT_ARTIFACTS"
        fi
        return
    fi

    if [ "$elapsed" -lt "$STARTUP_GRACE" ]; then
        echo "CONTINUE_WAITING"
    elif [ "$quiet" -lt "$STALE_AFTER" ]; then
        echo "CONTINUE_WAITING"
    elif [ "$worktree_changes" -gt 0 ] && [ "$quiet" -lt "$INTERRUPT_AFTER" ]; then
        echo "REVIEW_PARTIAL_DIFF"
    elif [ "$worktree_changes" -gt 0 ]; then
        echo "CONSIDER_INTERRUPT"
    elif [ "$result_bytes" -eq 0 ] && [ "$status_bytes" -eq 0 ] && [ "$claude_progress_bytes" -eq 0 ]; then
        echo "LIKELY_STUCK"
    elif [ "$quiet" -ge "$INTERRUPT_AFTER" ]; then
        echo "LIKELY_STUCK"
    else
        echo "CONSIDER_INTERRUPT"
    fi
}

print_header() {
    if [ "$printed_header" -eq 0 ]; then
        if [ "$PLAIN" -eq 1 ]; then
            echo "# Claude Watch"
            echo "Task ID: $TASK_ID"
            echo "Mode: plain compact (use --details to show full progress tails)"
            echo "Wait policy: profile=${WAIT_PROFILE} startup_grace=${STARTUP_GRACE}s stale_after=${STALE_AFTER}s interrupt_after=${INTERRUPT_AFTER}s"
        else
            echo "============================================================"
            echo " CLAUDE CODE WATCH"
            echo " task: ${TASK_ID}"
            echo " mode: status panel (use --details for full tails, --plain for compact text)"
            echo " wait policy: profile=${WAIT_PROFILE} startup_grace=${STARTUP_GRACE}s stale_after=${STALE_AFTER}s interrupt_after=${INTERRUPT_AFTER}s"
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
    if [ -d "$WORKTREE_DIR" ]; then
        echo ""
        echo "## Partial Worktree"
        echo "Change count: $(worktree_change_count)"
        echo "Diffstat/status:"
        partial_diffstat
    fi
}

print_snapshot() {
    print_header

    local claude_progress_source running last_line elapsed quiet result_bytes status_bytes report_bytes claude_progress_bytes diff_bytes worktree_changes partial_summary risk_summary percent bar milestone reason action digest show_details
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
    worktree_changes="$(field_from_line "$last_line" "worktree_changes")"
    worktree_changes="${worktree_changes:-$(worktree_change_count)}"
    partial_summary="$(partial_diffstat | head -1)"
    risk_summary="$(partial_risk_summary)"
    percent="$(progress_percent "$claude_progress_source")"
    bar="$(progress_bar "$percent")"
    milestone="$(current_milestone "$claude_progress_source")"
    reason="$(stuck_reason "$running" "$quiet" "$result_bytes" "$status_bytes" "$report_bytes" "$claude_progress_bytes" "$claude_progress_source" "$worktree_changes")"
    action="$(recommended_action "$running" "$elapsed" "$quiet" "$result_bytes" "$status_bytes" "$claude_progress_bytes" "$worktree_changes")"

    digest="${running}|${elapsed}|${quiet}|${result_bytes}|${status_bytes}|${report_bytes}|${claude_progress_bytes}|${worktree_changes}|${partial_summary}|${risk_summary}|${percent}|${milestone}|${reason}|${action}"
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
            echo "artifacts: result=${result_bytes}B status=${status_bytes}B report=${report_bytes}B progress=${claude_progress_bytes}B diff=${diff_bytes}B changes=${worktree_changes}"
            echo "partial: ${partial_summary}"
            echo "risk: ${risk_summary}"
            echo "action: ${action}"
            echo "analysis: ${reason}"
            echo ""
        else
            echo "------------------------------------------------------------"
            printf ' STATUS   : %s\n' "$state"
            printf ' TIME     : elapsed=%ss  quiet=%ss\n' "$elapsed" "$quiet"
            printf ' PROGRESS : %s\n' "$bar"
            printf ' CURRENT  : %s\n' "$milestone"
            printf ' FILES    : result=%sB  status=%sB  report=%sB  progress=%sB  diff=%sB  changes=%s\n' "$result_bytes" "$status_bytes" "$report_bytes" "$claude_progress_bytes" "$diff_bytes" "$worktree_changes"
            printf ' PARTIAL  : %s\n' "$partial_summary"
            printf ' RISK     : %s\n' "$risk_summary"
            printf ' ACTION   : %s\n' "$action"
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
