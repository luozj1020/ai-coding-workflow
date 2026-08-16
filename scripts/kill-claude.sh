#!/usr/bin/env bash
# kill-claude.sh  -  Identity-confirm and stop a dispatch's Claude process tree.
#
# Usage: bash ai/kill-claude.sh <runtime-id> [--kill-after seconds]

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Git for Windows can be launched through bin/bash.exe without the usual Unix tool PATH.
# Prepending these paths is harmless on Unix and makes helper scripts stable on Windows.
PATH="/usr/bin:/bin:/mingw64/bin:${PATH}"
export PATH

if [ $# -lt 1 ]; then
    echo "Usage: $0 <runtime-id> [--kill-after seconds]" >&2
    exit 1
fi

TASK_ID_INPUT="$1"
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
if command -v python3 >/dev/null 2>&1; then PYTHON_CMD=python3; else PYTHON_CMD=python; fi
TASK_ID_HELPER="${SCRIPT_DIR}/claude_task_id.py"
if [ ! -f "$TASK_ID_HELPER" ]; then
    echo "Error: runtime task-id helper is unavailable: $TASK_ID_HELPER" >&2
    exit 1
fi
if ! TASK_ID="$($PYTHON_CMD "$TASK_ID_HELPER" normalize "$TASK_ID_INPUT" --artifact-input 2>/dev/null)"; then
    echo "Error: unsafe or ambiguous runtime task id." >&2
    exit 1
fi

SOURCE_REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
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
PID_FILE="${REPO_ROOT}/.worktrees/${TASK_ID}.pid"
CLAUDE_PID_FILE="${REPO_ROOT}/.worktrees/${TASK_ID}.claude.pid"
IDENTITY_FILE="${REPO_ROOT}/.worktrees/${TASK_ID}.claude.process.json"
PROGRESS_FILE="${REPO_ROOT}/.worktrees/${TASK_ID}.progress.log"
TERMINATION_RECEIPT="${REPO_ROOT}/.worktrees/${TASK_ID}.manual-stop.json"
TERMINATION_HELPER="${SCRIPT_DIR}/prepare-codex-takeover.py"

if [ ! -f "$IDENTITY_FILE" ]; then
    echo "Error: authoritative Claude process identity not found: $IDENTITY_FILE" >&2
    echo "Refusing a PID-only kill because the recorded PID may be stale or reused." >&2
    exit 1
fi

PYTHON_CMD=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
fi
if [ -z "$PYTHON_CMD" ] || [ ! -f "$TERMINATION_HELPER" ]; then
    echo "Error: identity-bound process termination helper is unavailable." >&2
    exit 1
fi

log() {
    local message="$1"
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$message" | tee -a "$PROGRESS_FILE"
}

log "Requesting identity-bound Claude process-tree stop: task=${TASK_ID}, grace=${KILL_AFTER}s"
if ! "$PYTHON_CMD" "$TERMINATION_HELPER" terminate-process \
    --identity "$IDENTITY_FILE" \
    --task-id "$TASK_ID" \
    --role claude \
    --terminate-timeout "$KILL_AFTER" \
    --reason manual-kill-helper \
    --output "$TERMINATION_RECEIPT" >/dev/null; then
    log "Claude process-tree stop failed closed; identity was not authoritative or termination could not be confirmed"
    exit 2
fi

# PID-only receipts are operational hints, not durable identity evidence.
# Remove them only after the identity-bound helper confirms the whole tree is
# inactive. The process identity and termination receipt remain auditable.
rm -f "$PID_FILE" "$CLAUDE_PID_FILE"
log "Claude process tree confirmed inactive; receipt=${TERMINATION_RECEIPT}"
