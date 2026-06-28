#!/usr/bin/env bash
# run-loop.sh  -  Compose dispatch and review in an explicit loop.
#
# Usage: bash ai/run-loop.sh <task-card-path> [max-iterations]
#
# This script dispatches task cards to Claude Code, reviews with Codex/GPT,
# persists all artifacts, records usage summaries, and never merges automatically.

set -euo pipefail

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

for tool in git claude codex; do
    if ! command -v "$tool" &>/dev/null; then
        echo "Error: $tool is not installed or not in PATH." >&2
        exit 1
    fi
done

REPO_ROOT="$(git rev-parse --show-toplevel)"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
TASK_ID="loop-${TIMESTAMP}"
RUN_DIR="${REPO_ROOT}/.worktrees/${TASK_ID}"
USAGE_SUMMARY="${RUN_DIR}/loop-usage-summary.md"

mkdir -p "$RUN_DIR"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DISPATCH_SCRIPT="${SCRIPT_DIR}/dispatch-to-claude.sh"
REVIEW_SCRIPT="${SCRIPT_DIR}/review-with-codex.sh"

if [ ! -f "$DISPATCH_SCRIPT" ]; then
    echo "Error: dispatch-to-claude.sh not found at $DISPATCH_SCRIPT" >&2
    exit 1
fi

if [ ! -f "$REVIEW_SCRIPT" ]; then
    echo "Error: review-with-codex.sh not found at $REVIEW_SCRIPT" >&2
    exit 1
fi

CURRENT_TASK="${RUN_DIR}/task-card-001.md"
cp "$TASK_CARD" "$CURRENT_TASK"

cat > "$USAGE_SUMMARY" <<EOF
# Loop Usage Summary

Run directory: ${RUN_DIR}
Initial task card: ${TASK_CARD}

EOF

echo "=== Loop Runner ==="
echo "Run directory: $RUN_DIR"
echo "Max iterations: $MAX_ITERATIONS"
echo "Usage summary: $USAGE_SUMMARY"
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

while [ "$ITERATION" -le "$MAX_ITERATIONS" ]; do
    echo "--- Iteration ${ITERATION} ---"

    DISPATCH_OUTPUT="${RUN_DIR}/dispatch-${ITERATION}"
    mkdir -p "$DISPATCH_OUTPUT"

    echo "Dispatching task card to Claude Code..."
    cd "$REPO_ROOT"

    DISPATCH_LOG="${DISPATCH_OUTPUT}/dispatch.log"
    bash "$DISPATCH_SCRIPT" "$CURRENT_TASK" 2>&1 | tee "$DISPATCH_LOG"

    WORKTREE_DIR="$(parse_path "Worktree" "$DISPATCH_LOG")"
    RESULT_FILE="$(parse_path "Result" "$DISPATCH_LOG")"
    STATUS_FILE="$(parse_path "Status" "$DISPATCH_LOG")"
    DIFFSTAT_FILE="$(parse_path "Diffstat" "$DISPATCH_LOG")"
    DIFF_FILE="$(parse_path "Diff" "$DISPATCH_LOG")"
    SOURCE_STATUS_FILE="$(parse_path "Source Status" "$DISPATCH_LOG")"
    WORKTREE_STATUS_FILE="$(parse_path "Worktree Status" "$DISPATCH_LOG")"
    UNTRACKED_FILE="$(parse_path "Untracked Files" "$DISPATCH_LOG")"
    USAGE_FILE="$(parse_path "Usage Summary" "$DISPATCH_LOG")"
    REPORT_FILE="$(parse_path "Report" "$DISPATCH_LOG")"

    if [ -z "$RESULT_FILE" ] || [ -z "$DIFF_FILE" ]; then
        echo "Error: Dispatch did not produce result.json or diff files." >&2
        echo "Check $DISPATCH_OUTPUT/ for details." >&2
        exit 1
    fi

    for f in "$RESULT_FILE" "$STATUS_FILE" "$DIFFSTAT_FILE" "$DIFF_FILE" \
             "$SOURCE_STATUS_FILE" "$WORKTREE_STATUS_FILE" "$UNTRACKED_FILE" "$USAGE_FILE" "$REPORT_FILE"; do
        copy_if_present "$f" "$DISPATCH_OUTPUT"
    done

    {
        echo "## Iteration ${ITERATION}"
        echo ""
        echo "### Claude Usage"
        if [ -n "$USAGE_FILE" ] && [ -f "$USAGE_FILE" ]; then
            cat "$USAGE_FILE"
        else
            echo "Claude usage unavailable."
        fi
        echo ""
    } >> "$USAGE_SUMMARY"

    REVIEW_OUTPUT="${RUN_DIR}/review-${ITERATION}.txt"

    echo ""
    echo "Sending evidence to Codex/GPT for review..."
    set +e
    bash "$REVIEW_SCRIPT" "$CURRENT_TASK" "$RESULT_FILE" "$DIFF_FILE" \
        "$USAGE_FILE" "$SOURCE_STATUS_FILE" "$WORKTREE_STATUS_FILE" "$UNTRACKED_FILE" "$REPORT_FILE" 2>&1 | tee "$REVIEW_OUTPUT"
    REVIEW_STATUS=${PIPESTATUS[0]}
    set -e

    REVIEW_ARTIFACT="$(grep '^Review Output:' "$REVIEW_OUTPUT" | sed 's/^Review Output: *//' | tail -1 || true)"
    CODEX_EVENTS="$(grep '^Codex Events:' "$REVIEW_OUTPUT" | sed 's/^Codex Events: *//' | tail -1 || true)"
    CODEX_USAGE="$(grep '^Codex Usage:' "$REVIEW_OUTPUT" | sed 's/^Codex Usage: *//' | tail -1 || true)"

    copy_if_present "$REVIEW_ARTIFACT" "$DISPATCH_OUTPUT"
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
        echo "Review command failed with status $REVIEW_STATUS. Human intervention required." >&2
        echo "Run directory: $RUN_DIR" >&2
        exit "$REVIEW_STATUS"
    fi

    DECISION="$(grep -iE '^\*\*(ACCEPT|REVISE|SPLIT|REJECT)\*\*|^[-*] \*\*(ACCEPT|REVISE|SPLIT|REJECT)\*\*|(ACCEPT|REVISE|SPLIT|REJECT)' "$REVIEW_OUTPUT" \
        | head -1 \
        | sed 's/.*\(ACCEPT\|REVISE\|SPLIT\|REJECT\).*/\1/i' \
        || true)"

    echo ""
    echo "Decision: ${DECISION:-UNKNOWN}"

    cat > "${RUN_DIR}/iteration-${ITERATION}-summary.md" <<EOF
# Iteration ${ITERATION}

## Task Card
${CURRENT_TASK}

## Dispatch Output
${DISPATCH_OUTPUT}/

## Review Output
${REVIEW_OUTPUT}

## Usage Summary
${USAGE_SUMMARY}

## Decision
${DECISION:-UNKNOWN}
EOF

    case "$(echo "$DECISION" | tr '[:lower:]' '[:upper:]')" in
        ACCEPT)
            echo ""
            echo "=== Loop Complete: ACCEPTED ==="
            echo "The change is ready for human review and merge."
            echo "Worktree: $WORKTREE_DIR"
            echo "Run directory: $RUN_DIR"
            echo "Usage summary: $USAGE_SUMMARY"
            exit 0
            ;;
        REVISE)
            echo ""
            echo "Decision is REVISE. Preparing next iteration..."
            ITERATION=$((ITERATION + 1))

            if [ "$ITERATION" -gt "$MAX_ITERATIONS" ]; then
                echo ""
                echo "=== Loop Stopped: Max iterations reached ==="
                echo "Human intervention required."
                echo "Run directory: $RUN_DIR"
                echo "Usage summary: $USAGE_SUMMARY"
                exit 1
            fi

            REVISED_TASK="${RUN_DIR}/task-card-$(printf '%03d' $ITERATION).md"
            cp "$CURRENT_TASK" "$REVISED_TASK"
            cat >> "$REVISED_TASK" <<EOF

---

## Loop Context (auto-generated by run-loop.sh)

- **Iteration:** ${ITERATION}
- **Prior decision:** REVISE
- **Review feedback:** See ${REVIEW_OUTPUT}
- **Usage summary:** See ${USAGE_SUMMARY}

EOF
            CURRENT_TASK="$REVISED_TASK"
            echo "Revised task card: $CURRENT_TASK"
            echo ""
            ;;
        SPLIT)
            echo ""
            echo "=== Loop Stopped: SPLIT ==="
            echo "Review the decision in: $REVIEW_OUTPUT"
            echo "Run directory: $RUN_DIR"
            echo "Usage summary: $USAGE_SUMMARY"
            exit 0
            ;;
        REJECT)
            echo ""
            echo "=== Loop Stopped: REJECTED ==="
            echo "Review the decision in: $REVIEW_OUTPUT"
            echo "Run directory: $RUN_DIR"
            echo "Usage summary: $USAGE_SUMMARY"
            exit 1
            ;;
        *)
            echo ""
            echo "=== Loop Stopped: Unknown Decision ==="
            echo "Review output: $REVIEW_OUTPUT"
            echo "Run directory: $RUN_DIR"
            echo "Usage summary: $USAGE_SUMMARY"
            exit 1
            ;;
    esac
done

echo ""
echo "=== Loop Stopped: Max iterations reached ==="
echo "Human intervention required."
echo "Run directory: $RUN_DIR"
echo "Usage summary: $USAGE_SUMMARY"
exit 1