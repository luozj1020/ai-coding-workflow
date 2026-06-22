#!/usr/bin/env bash
# dispatch-to-claude.sh  -  Dispatch a task card to Claude Code in an isolated worktree.
#
# Usage: bash ai/dispatch-to-claude.sh <task-card-path>
#
# This script:
#   1. Validates that git and claude CLI exist.
#   2. Creates an isolated git worktree under .worktrees/claude-<timestamp>.
#   3. Copies the task card to TASK_CARD.md in the worktree.
#   4. Invokes claude -p in non-interactive mode.
#   5. Saves result, status, diffstat, and diff under .worktrees/.
#   6. Prints paths to generated result files.
#   7. Does NOT merge automatically.

set -euo pipefail

# --- Argument validation ---
if [ $# -lt 1 ]; then
    echo "Usage: $0 <task-card-path>" >&2
    exit 1
fi

TASK_CARD="$1"

if [ ! -f "$TASK_CARD" ]; then
    echo "Error: Task card not found: $TASK_CARD" >&2
    exit 1
fi

# --- Check required tools ---
if ! command -v git &>/dev/null; then
    echo "Error: git is not installed or not in PATH." >&2
    exit 1
fi

if ! command -v claude &>/dev/null; then
    echo "Error: claude CLI is not installed or not in PATH." >&2
    exit 1
fi

# --- Setup ---
REPO_ROOT="$(git rev-parse --show-toplevel)"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
TASK_ID="claude-${TIMESTAMP}"
WORKTREE_DIR="${REPO_ROOT}/.worktrees/${TASK_ID}"
WORKTREE_ROOT="${REPO_ROOT}/.worktrees"

mkdir -p "$WORKTREE_ROOT"

# --- Create isolated worktree ---
BRANCH_NAME="claude-task-${TIMESTAMP}"
git worktree add -b "$BRANCH_NAME" "$WORKTREE_DIR" HEAD 2>/dev/null || {
    echo "Error: Failed to create git worktree at $WORKTREE_DIR" >&2
    exit 1
}

echo "Created worktree: $WORKTREE_DIR"
echo "Branch: $BRANCH_NAME"

# --- Copy task card into worktree ---
cp "$TASK_CARD" "${WORKTREE_DIR}/TASK_CARD.md"
echo "Task card copied to: ${WORKTREE_DIR}/TASK_CARD.md"

# --- Define output file paths ---
RESULT_FILE="${WORKTREE_ROOT}/${TASK_ID}.result.json"
STATUS_FILE="${WORKTREE_ROOT}/${TASK_ID}.status.txt"
DIFFSTAT_FILE="${WORKTREE_ROOT}/${TASK_ID}.diffstat.txt"
DIFF_FILE="${WORKTREE_ROOT}/${TASK_ID}.diff"

# Ensure parent directories for all output files exist
mkdir -p "$(dirname "$RESULT_FILE")"
mkdir -p "$(dirname "$STATUS_FILE")"
mkdir -p "$(dirname "$DIFFSTAT_FILE")"
mkdir -p "$(dirname "$DIFF_FILE")"

# --- Invoke Claude Code ---
echo "Invoking Claude Code..."
cd "$WORKTREE_DIR"

claude -p \
    --permission-mode acceptEdits \
    --output-format json \
    < TASK_CARD.md > "$RESULT_FILE" 2>"${STATUS_FILE}" || {
    echo "Warning: claude exited with non-zero status. Check $STATUS_FILE" >&2
}

# --- Generate diffstat and diff ---
cd "$WORKTREE_DIR"
git diff --stat > "$DIFFSTAT_FILE" 2>/dev/null || true
git diff > "$DIFF_FILE" 2>/dev/null || true

# --- Report ---
echo ""
echo "=== Dispatch Complete ==="
echo "Worktree:   $WORKTREE_DIR"
echo "Result:     $RESULT_FILE"
echo "Status:     $STATUS_FILE"
echo "Diffstat:   $DIFFSTAT_FILE"
echo "Diff:       $DIFF_FILE"
echo ""
echo "Changes have NOT been merged. Review the diff and merge manually."
echo "To remove the worktree: git worktree remove $WORKTREE_DIR"
