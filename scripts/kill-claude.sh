#!/usr/bin/env bash
# kill-claude.sh  -  Stop the Claude process for a dispatch run using its PID artifact.
#
# Usage: bash ai/kill-claude.sh <claude-<timestamp>> [--kill-after seconds]

set -euo pipefail

# Git for Windows can be launched through bin/bash.exe without the usual Unix tool PATH.
# Prepending these paths is harmless on Unix and makes helper scripts stable on Windows.
PATH="/usr/bin:/bin:/mingw64/bin:${PATH}"
export PATH

if [ $# -lt 1 ]; then
    echo "Usage: $0 <claude-task-id> [--kill-after seconds]" >&2
    exit 1
fi

TASK_ID="$(basename "$1")"
TASK_ID="${TASK_ID%.pid}"
KILL_AFTER=10
shift || true

while [ $# -gt 0 ]; do
    case "$1" in
        --kill-after)
            shift
            KILL_AFTER="${1:-}"
            ;;
        *)
            echo "Error: unknown argument: $1" >&2
            exit 1
            ;;
    esac
    shift || true
done

case "$KILL_AFTER" in
    ''|*[!0-9]*)
        echo "Error: --kill-after must be a non-negative integer." >&2
        exit 1
        ;;
esac

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
PID_FILE="${REPO_ROOT}/.worktrees/${TASK_ID}.pid"
PROGRESS_FILE="${REPO_ROOT}/.worktrees/${TASK_ID}.progress.log"

if [ ! -f "$PID_FILE" ]; then
    echo "Error: PID file not found: $PID_FILE" >&2
    exit 1
fi

PID="$(tr -d '[:space:]' < "$PID_FILE")"
case "$PID" in
    ''|*[!0-9]*)
        echo "Error: invalid PID in $PID_FILE: $PID" >&2
        exit 1
        ;;
esac

log() {
    local message="$1"
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$message" | tee -a "$PROGRESS_FILE"
}

if ! kill -0 "$PID" 2>/dev/null; then
    log "Claude process already stopped: pid=${PID}"
    exit 0
fi

log "Sending TERM to Claude process: pid=${PID}"
kill "$PID" 2>/dev/null || true

elapsed=0
while kill -0 "$PID" 2>/dev/null && [ "$elapsed" -lt "$KILL_AFTER" ]; do
    sleep 1
    elapsed=$((elapsed + 1))
done

if kill -0 "$PID" 2>/dev/null; then
    log "Claude still running after ${KILL_AFTER}s; sending KILL to pid=${PID}"
    kill -9 "$PID" 2>/dev/null || true
else
    log "Claude process stopped after TERM: pid=${PID}"
fi
