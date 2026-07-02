#!/usr/bin/env bash
# cleanup-worktree.sh  -  Remove a stopped Claude dispatch worktree while preserving evidence artifacts.
#
# Usage: bash ai/cleanup-worktree.sh <claude-<timestamp>> [--force]

set -euo pipefail

# Git for Windows can be launched through bin/bash.exe without the usual Unix tool PATH.
# Prepending these paths is harmless on Unix and makes helper scripts stable on Windows.
PATH="/usr/bin:/bin:/mingw64/bin:${PATH}"
export PATH

if [ $# -lt 1 ]; then
    echo "Usage: $0 <claude-task-id> [--force]" >&2
    exit 1
fi

TASK_ID="$(basename "$1")"
FORCE=0
shift || true

while [ $# -gt 0 ]; do
    case "$1" in
        --force)
            FORCE=1
            ;;
        *)
            echo "Error: unknown argument: $1" >&2
            exit 1
            ;;
    esac
    shift || true
done

REPO_ROOT="$(git rev-parse --show-toplevel)"
WORKTREE_DIR="${REPO_ROOT}/.worktrees/${TASK_ID}"
PID_FILE="${REPO_ROOT}/.worktrees/${TASK_ID}.pid"
PROGRESS_FILE="${REPO_ROOT}/.worktrees/${TASK_ID}.progress.log"

if [ ! -d "$WORKTREE_DIR" ]; then
    echo "Error: worktree directory not found: $WORKTREE_DIR" >&2
    exit 1
fi

if [ -f "$PID_FILE" ]; then
    PID="$(tr -d '[:space:]' < "$PID_FILE")"
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        echo "Error: Claude process is still running (pid=$PID). Stop it first with kill-claude.sh." >&2
        exit 1
    fi
fi

if [ "$FORCE" -eq 1 ]; then
    git worktree remove --force "$WORKTREE_DIR"
else
    git worktree remove "$WORKTREE_DIR"
fi

{
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Removed worktree: $WORKTREE_DIR"
    echo "Evidence artifacts were preserved under: ${REPO_ROOT}/.worktrees/${TASK_ID}.*"
} | tee -a "$PROGRESS_FILE"
