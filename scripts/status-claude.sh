#!/usr/bin/env bash
# status-claude.sh  -  Show status for a Claude Code dispatch run.
#
# Usage: bash ai/status-claude.sh [claude-<timestamp>|/path/to/worktree]

set -euo pipefail

# Git for Windows can be launched through bin/bash.exe without the usual Unix tool PATH.
# Prepending these paths is harmless on Unix and makes helper scripts stable on Windows.
PATH="/usr/bin:/bin:/mingw64/bin:${PATH}"
export PATH

resolve_repo() {
    git rev-parse --show-toplevel 2>/dev/null || pwd
}

REPO_ROOT="$(resolve_repo)"
WORKTREE_ROOT="${REPO_ROOT}/.worktrees"
TASK_REF="${1:-}"
WAIT_PROFILE="${CLAUDE_CODE_WAIT_PROFILE:-medium}"

case "$WAIT_PROFILE" in
    small)
        STARTUP_GRACE=30
        STALE_AFTER=60
        INTERRUPT_AFTER=240
        ;;
    medium)
        STARTUP_GRACE=60
        STALE_AFTER=120
        INTERRUPT_AFTER=600
        ;;
    large)
        STARTUP_GRACE=120
        STALE_AFTER=300
        INTERRUPT_AFTER=1200
        ;;
    *)
        WAIT_PROFILE="medium"
        STARTUP_GRACE=60
        STALE_AFTER=120
        INTERRUPT_AFTER=600
        ;;
esac

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
    TASK_ID="${TASK_ID%.pid}"
fi

PREFIX="${WORKTREE_ROOT}/${TASK_ID}"
WORKTREE_DIR="${WORKTREE_ROOT}/${TASK_ID}"
PID_FILE="${PREFIX}.pid"
PROGRESS_FILE="${PREFIX}.progress.log"
RESULT_FILE="${PREFIX}.result.json"
STATUS_FILE="${PREFIX}.status.txt"
DIFF_FILE="${PREFIX}.diff"
REPORT_FILE="${PREFIX}.report.md"
CLAUDE_PROGRESS_FILE="${PREFIX}.claude-progress.md"
WORKTREE_STATUS_FILE="${PREFIX}.worktree-status.txt"

file_size() {
    local file="$1"
    if [ -f "$file" ]; then
        wc -c < "$file" 2>/dev/null | tr -d '[:space:]' || echo 0
    else
        echo 0
    fi
}

field_from_line() {
    local line="$1"
    local key="$2"
    echo "$line" | sed -n "s/.*${key}=\([0-9][0-9]*\).*/\1/p"
}

last_dispatch_line() {
    if [ -f "$PROGRESS_FILE" ]; then
        grep -E 'Claude still running|Claude completed|Claude exited|Claude finished|Stopping Claude' "$PROGRESS_FILE" | tail -1 || true
    fi
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
            | head -20 || true)"
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

print_file() {
    local label="$1"
    local file="$2"
    if [ -e "$file" ]; then
        echo "$label: $file ($(file_size "$file") bytes)"
    else
        echo "$label: missing ($file)"
    fi
}

echo "# Claude Dispatch Status"
echo "Task ID: $TASK_ID"
echo "Worktree: $WORKTREE_DIR"
echo "Wait Policy: profile=${WAIT_PROFILE} startup_grace=${STARTUP_GRACE}s stale_after=${STALE_AFTER}s interrupt_after=${INTERRUPT_AFTER}s"

PROCESS_STATE="unknown"
if [ -f "$PID_FILE" ]; then
    PID="$(tr -d '[:space:]' < "$PID_FILE")"
    echo "PID: $PID"
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        echo "Process: running"
        PROCESS_STATE="running"
    else
        echo "Process: not running"
        PROCESS_STATE="not-running"
    fi
else
    echo "PID: missing"
    echo "Process: unknown"
fi

echo ""
echo "## Artifacts"
print_file "Progress" "$PROGRESS_FILE"
print_file "Result" "$RESULT_FILE"
print_file "Status" "$STATUS_FILE"
print_file "Diff" "$DIFF_FILE"
print_file "Report" "$REPORT_FILE"
print_file "Claude Progress" "$CLAUDE_PROGRESS_FILE"
print_file "Worktree Status" "$WORKTREE_STATUS_FILE"

echo ""
echo "## Partial Worktree Triage"
if [ -d "$WORKTREE_DIR" ]; then
    CHANGE_COUNT="$(worktree_change_count)"
    LAST_LINE="$(last_dispatch_line)"
    ELAPSED="$(field_from_line "$LAST_LINE" "elapsed_seconds")"; ELAPSED="${ELAPSED:-0}"
    QUIET="$(field_from_line "$LAST_LINE" "quiet_seconds")"; QUIET="${QUIET:-0}"
    RESULT_BYTES="$(file_size "$RESULT_FILE")"
    STATUS_BYTES="$(file_size "$STATUS_FILE")"
    CLAUDE_PROGRESS_BYTES="$(file_size "$CLAUDE_PROGRESS_FILE")"
    if [ "$PROCESS_STATE" = "running" ]; then RUNNING="yes"; else RUNNING="no"; fi
    ACTION="$(recommended_action "$RUNNING" "$ELAPSED" "$QUIET" "$RESULT_BYTES" "$STATUS_BYTES" "$CLAUDE_PROGRESS_BYTES" "$CHANGE_COUNT")"
    RISK_SUMMARY="$(partial_risk_summary)"
    echo "Action: $ACTION"
    echo "Elapsed: ${ELAPSED}s"
    echo "Quiet: ${QUIET}s"
    echo "Implementation change count: $CHANGE_COUNT"
    echo "Risk summary: $RISK_SUMMARY"
    echo "Diffstat/status:"
    partial_diffstat
    if [ "$CHANGE_COUNT" -gt 0 ]; then
        echo ""
        echo "Recommendation: review the partial diff against the task card before interrupting Claude. Continue waiting if the changes match the plan; stop Claude only if the implementation is off-plan, risky, or no longer making useful progress."
    else
        echo ""
        echo "Recommendation: no implementation changes are visible yet. If Claude is running and artifacts stay stale beyond the expected startup/auth window, inspect status/progress before deciding whether to stop it."
    fi
else
    echo "(worktree unavailable)"
fi

echo ""
echo "## Progress Tail"
if [ -f "$PROGRESS_FILE" ]; then
    tail -20 "$PROGRESS_FILE"
else
    echo "(none)"
fi

echo ""
echo "## Claude Progress Tail"
if [ -f "$CLAUDE_PROGRESS_FILE" ]; then
    tail -40 "$CLAUDE_PROGRESS_FILE"
else
    echo "(none)"
fi

echo ""
echo "## Status Tail"
if [ -f "$STATUS_FILE" ]; then
    tail -20 "$STATUS_FILE"
else
    echo "(none)"
fi

if [ -d "$WORKTREE_DIR" ]; then
    echo ""
    echo "## Worktree Git Status"
    git -c "safe.directory=$WORKTREE_DIR" -C "$WORKTREE_DIR" status --short --untracked-files=all 2>/dev/null || echo "(git status unavailable)"
fi
