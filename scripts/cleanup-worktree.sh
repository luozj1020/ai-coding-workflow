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

SOURCE_REPO_ROOT="$(git rev-parse --show-toplevel)"
_COMMON_GIT_DIR="$(git -C "$SOURCE_REPO_ROOT" rev-parse --git-common-dir 2>/dev/null || true)"
case "$_COMMON_GIT_DIR" in
    /*) ;;
    *) _COMMON_GIT_DIR="${SOURCE_REPO_ROOT}/${_COMMON_GIT_DIR}" ;;
esac
_COMMON_GIT_DIR="$(cd "$_COMMON_GIT_DIR" 2>/dev/null && pwd -P || true)"
if [ -n "$_COMMON_GIT_DIR" ] && [ "$(basename "$_COMMON_GIT_DIR")" = ".git" ]; then
    REPO_ROOT="$(dirname "$_COMMON_GIT_DIR")"
else
    REPO_ROOT="$SOURCE_REPO_ROOT"
fi
WORKTREE_DIR="${REPO_ROOT}/.worktrees/${TASK_ID}"
PID_FILE="${REPO_ROOT}/.worktrees/${TASK_ID}.pid"
PROGRESS_FILE="${REPO_ROOT}/.worktrees/${TASK_ID}.progress.log"
PROCESS_IDENTITY_HELPER="${REPO_ROOT}/ai/process-identity.py"
[ -f "$PROCESS_IDENTITY_HELPER" ] || PROCESS_IDENTITY_HELPER="${SCRIPT_DIR:-$(cd "$(dirname "$0")" && pwd)}/process-identity.py"
if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD=python3
else
    PYTHON_CMD=python
fi

if [ ! -d "$WORKTREE_DIR" ]; then
    echo "Error: worktree directory not found: $WORKTREE_DIR" >&2
    exit 1
fi

IDENTITY_FOUND=0
for ROLE in dispatcher claude checker; do
    IDENTITY_FILE="${REPO_ROOT}/.worktrees/${TASK_ID}.${ROLE}.process.json"
    [ -f "$IDENTITY_FILE" ] || continue
    IDENTITY_FOUND=1
    set +e
    "$PYTHON_CMD" "$PROCESS_IDENTITY_HELPER" check \
        --identity "$IDENTITY_FILE" --task-id "$TASK_ID" --role "$ROLE" >/dev/null
    IDENTITY_STATUS=$?
    set -e
    if [ "$IDENTITY_STATUS" -eq 0 ]; then
        echo "Error: ${ROLE} process identity is still active for ${TASK_ID}. Stop it first with kill-claude.sh." >&2
        exit 1
    fi
    if [ "$IDENTITY_STATUS" -ne 1 ]; then
        echo "Error: ${ROLE} process identity cannot be verified for ${TASK_ID}; cleanup fails closed." >&2
        exit 1
    fi
done

# PID-only probing is retained solely for runtime artifacts created before
# process identity receipts existed.
if [ "$IDENTITY_FOUND" -eq 0 ] && [ -f "$PID_FILE" ]; then
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
