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

if [ -f "$PID_FILE" ]; then
    PID="$(tr -d '[:space:]' < "$PID_FILE")"
    echo "PID: $PID"
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        echo "Process: running"
    else
        echo "Process: not running"
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
