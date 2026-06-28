#!/usr/bin/env bash
# dispatch-to-claude.sh  -  Dispatch a task card to Claude Code in an isolated worktree.
#
# Usage: bash ai/dispatch-to-claude.sh <task-card-path>
#
# This script:
#   1. Validates that git and claude CLI exist.
#   2. Records source repository status (tracked + untracked) before dispatch.
#   3. Creates an isolated git worktree under .worktrees/claude-<timestamp>.
#   4. Copies the task card to TASK_CARD.md in the worktree.
#   5. Invokes claude -p in non-interactive mode.
#   6. Saves result, status, diffstat, diff, untracked files, usage, and report.
#   7. Records worktree status (tracked + untracked) after execution.
#   8. Prints paths to generated result files.
#   9. Does NOT merge automatically.

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <task-card-path>" >&2
    exit 1
fi

TASK_CARD="$1"

if [ ! -f "$TASK_CARD" ]; then
    echo "Error: Task card not found: $TASK_CARD" >&2
    exit 1
fi

if ! command -v git &>/dev/null; then
    echo "Error: git is not installed or not in PATH." >&2
    exit 1
fi

if ! command -v claude &>/dev/null; then
    echo "Error: claude CLI is not installed or not in PATH." >&2
    exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
TASK_ID="claude-${TIMESTAMP}"
WORKTREE_DIR="${REPO_ROOT}/.worktrees/${TASK_ID}"
WORKTREE_ROOT="${REPO_ROOT}/.worktrees"

mkdir -p "$WORKTREE_ROOT"

RESULT_FILE="${WORKTREE_ROOT}/${TASK_ID}.result.json"
STATUS_FILE="${WORKTREE_ROOT}/${TASK_ID}.status.txt"
DIFFSTAT_FILE="${WORKTREE_ROOT}/${TASK_ID}.diffstat.txt"
DIFF_FILE="${WORKTREE_ROOT}/${TASK_ID}.diff"
SOURCE_STATUS_FILE="${WORKTREE_ROOT}/${TASK_ID}.source-status.txt"
WORKTREE_STATUS_FILE="${WORKTREE_ROOT}/${TASK_ID}.worktree-status.txt"
UNTRACKED_FILE="${WORKTREE_ROOT}/${TASK_ID}.untracked.txt"
USAGE_FILE="${WORKTREE_ROOT}/${TASK_ID}.usage.txt"
REPORT_FILE="${WORKTREE_ROOT}/${TASK_ID}.report.md"

for f in "$RESULT_FILE" "$STATUS_FILE" "$DIFFSTAT_FILE" "$DIFF_FILE" \
         "$SOURCE_STATUS_FILE" "$WORKTREE_STATUS_FILE" "$UNTRACKED_FILE" "$USAGE_FILE" "$REPORT_FILE"; do
    mkdir -p "$(dirname "$f")"
done

{
    echo "# Source Repository Status - ${TIMESTAMP}"
    echo "# Recorded before worktree creation"
    echo ""
    echo "## Tracked Changes (git diff --stat)"
    DIFF_OUT="$(git diff --stat 2>/dev/null || true)"
    if [ -z "$DIFF_OUT" ]; then echo "(none)"; else echo "$DIFF_OUT"; fi
    echo ""
    echo "## Staged Changes (git diff --cached --stat)"
    CACHED_OUT="$(git diff --cached --stat 2>/dev/null || true)"
    if [ -z "$CACHED_OUT" ]; then echo "(none)"; else echo "$CACHED_OUT"; fi
    echo ""
    echo "## Untracked Files"
    UNTRACKED_SRC="$(git ls-files --others --exclude-standard 2>/dev/null || true)"
    if [ -z "$UNTRACKED_SRC" ]; then echo "(none)"; else echo "$UNTRACKED_SRC"; fi
} > "$SOURCE_STATUS_FILE"

echo "Source status saved to: $SOURCE_STATUS_FILE"

BRANCH_NAME="claude-task-${TIMESTAMP}"
git worktree add -b "$BRANCH_NAME" "$WORKTREE_DIR" HEAD 2>/dev/null || {
    echo "Error: Failed to create git worktree at $WORKTREE_DIR" >&2
    exit 1
}

echo "Created worktree: $WORKTREE_DIR"
echo "Branch: $BRANCH_NAME"

cp "$TASK_CARD" "${WORKTREE_DIR}/TASK_CARD.md"
echo "Task card copied to: ${WORKTREE_DIR}/TASK_CARD.md"

cat > "${WORKTREE_DIR}/CLAUDE_PROMPT.md" <<'EOF'
You are the executor in a Codex/Claude Code workflow.

Execute the task card below. In addition to making the requested edits, create `CLAUDE_REPORT.md` in the worktree before finishing.

`CLAUDE_REPORT.md` must include:
- Task card ID/path and a concise requirements summary.
- Files changed with one-line purpose per file.
- Acceptance criteria mapping: met / not met / partial.
- Checks run and exact outcomes.
- Known risks, assumptions, and open questions.
- Human review checklist.
- Notes that help Codex compare the implementation against the original task.

--- TASK CARD ---
EOF
cat "${WORKTREE_DIR}/TASK_CARD.md" >> "${WORKTREE_DIR}/CLAUDE_PROMPT.md"

echo "Invoking Claude Code..."
cd "$WORKTREE_DIR"

claude -p \
    --permission-mode acceptEdits \
    --output-format json \
    < CLAUDE_PROMPT.md > "$RESULT_FILE" 2>"${STATUS_FILE}" || {
    echo "Warning: claude exited with non-zero status. Check $STATUS_FILE" >&2
}

cd "$WORKTREE_DIR"
git diff --stat > "$DIFFSTAT_FILE" 2>/dev/null || true
git diff > "$DIFF_FILE" 2>/dev/null || true

FILTERED_UNTRACKED="$(git ls-files --others --exclude-standard 2>/dev/null \
    | grep -v -E '^(TASK_CARD|CLAUDE_PROMPT|CLAUDE_REPORT)' || true)"

{
    echo "# Untracked Files in Worktree - ${TIMESTAMP}"
    echo ""
    if [ -z "$FILTERED_UNTRACKED" ]; then
        echo "(none)"
    else
        echo "$FILTERED_UNTRACKED"
        echo ""
        echo "--- Patch Evidence (binary-safe) ---"
        echo "$FILTERED_UNTRACKED" | while IFS= read -r uf; do
            if [ -f "$uf" ] && [ -r "$uf" ]; then
                echo ""
                echo "=== $uf ==="
                ret=0; git diff --no-index -- /dev/null "$uf" 2>/dev/null || ret=$?
                if [ "$ret" -ne 0 ] && [ "$ret" -ne 1 ]; then
                    echo "(diff unavailable for $uf)"
                fi
            fi
        done
    fi
} > "$UNTRACKED_FILE"

{
    cat "$DIFF_FILE"
    echo "$FILTERED_UNTRACKED" | while IFS= read -r uf; do
        [ -z "$uf" ] && continue
        if [ -f "$uf" ] && [ -r "$uf" ]; then
            echo ""
            ret=0; git diff --no-index -- /dev/null "$uf" 2>/dev/null || ret=$?
            if [ "$ret" -ne 0 ] && [ "$ret" -ne 1 ]; then
                echo "(diff unavailable for $uf)"
            fi
        fi
    done
} > "${DIFF_FILE}.combined"
mv "${DIFF_FILE}.combined" "$DIFF_FILE"

PYTHON_CMD=""
if command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
elif command -v python &>/dev/null; then
    PYTHON_CMD="python"
fi

if [ -n "$PYTHON_CMD" ]; then
    "$PYTHON_CMD" - "$RESULT_FILE" "$USAGE_FILE" <<'PYEOF'
import json
import sys

result_file = sys.argv[1]
usage_file = sys.argv[2]

try:
    with open(result_file, "r", encoding="utf-8") as f:
        data = json.load(f)
except (json.JSONDecodeError, FileNotFoundError, OSError) as e:
    with open(usage_file, "w", encoding="utf-8") as f:
        f.write(f"# Usage Summary\n\nError reading result JSON: {e}\n")
    sys.exit(0)

lines = ["# Token / Cost Usage Summary", ""]
for key in ["total_cost_usd", "duration_ms", "duration_api_ms", "num_turns"]:
    if data.get(key) is not None:
        lines.append(f"{key}: {data.get(key)}")
usage = data.get("usage", {})
if usage:
    lines.extend(["", "## Usage"])
    for key in ["input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"]:
        if usage.get(key) is not None:
            lines.append(f"{key}: {usage.get(key)}")
model_usage = data.get("modelUsage", {})
if model_usage:
    lines.extend(["", "## Per-Model Usage"])
    for model, mu in model_usage.items():
        lines.append(f"### {model}")
        if isinstance(mu, dict):
            for k, v in mu.items():
                lines.append(f"  {k}: {v}")
        else:
            lines.append(f"  {mu}")
with open(usage_file, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
PYEOF
else
    {
        echo "# Token / Cost Usage Summary"
        echo ""
        echo "Skipped: neither python3 nor python found in PATH."
        echo "Raw result file: ${RESULT_FILE}"
    } > "$USAGE_FILE"
fi

echo "Usage summary saved to: $USAGE_FILE"

{
    echo "# Worktree Status After Execution - ${TIMESTAMP}"
    echo ""
    echo "## Tracked Changes (git diff --stat)"
    DIFF_OUT="$(git diff --stat 2>/dev/null || true)"
    if [ -z "$DIFF_OUT" ]; then echo "(none)"; else echo "$DIFF_OUT"; fi
    echo ""
    echo "## Staged Changes (git diff --cached --stat)"
    CACHED_OUT="$(git diff --cached --stat 2>/dev/null || true)"
    if [ -z "$CACHED_OUT" ]; then echo "(none)"; else echo "$CACHED_OUT"; fi
    echo ""
    echo "## Untracked Files (excluding dispatch scaffolding)"
    if [ -z "$FILTERED_UNTRACKED" ]; then echo "(none)"; else echo "$FILTERED_UNTRACKED"; fi
} > "$WORKTREE_STATUS_FILE"

echo "Worktree status saved to: $WORKTREE_STATUS_FILE"

if [ -f "${WORKTREE_DIR}/CLAUDE_REPORT.md" ]; then
    cp "${WORKTREE_DIR}/CLAUDE_REPORT.md" "$REPORT_FILE"
else
    {
        echo "# Claude Modification Report"
        echo ""
        echo "## Task Card"
        echo "$TASK_CARD"
        echo ""
        echo "## Requirements Summary"
        echo "Claude did not create CLAUDE_REPORT.md; this fallback report was generated from workflow artifacts."
        echo ""
        echo "## Changed Files"
        cat "$DIFFSTAT_FILE"
        echo ""
        echo "## Artifact Links"
        echo "- Result JSON: $RESULT_FILE"
        echo "- Status log: $STATUS_FILE"
        echo "- Diffstat: $DIFFSTAT_FILE"
        echo "- Diff: $DIFF_FILE"
        echo "- Source status: $SOURCE_STATUS_FILE"
        echo "- Worktree status: $WORKTREE_STATUS_FILE"
        echo "- Untracked files: $UNTRACKED_FILE"
        echo "- Usage summary: $USAGE_FILE"
        echo ""
        echo "## Human Review Checklist"
        echo "- [ ] Compare diff against task card acceptance criteria."
        echo "- [ ] Check worktree status for untracked implementation files."
        echo "- [ ] Review usage/cost summary for anomalies."
        echo "- [ ] Run project-specific validation before merge."
    } > "$REPORT_FILE"
fi

echo "Report saved to: $REPORT_FILE"

echo ""
echo "=== Dispatch Complete ==="
echo "Worktree:        $WORKTREE_DIR"
echo "Result:          $RESULT_FILE"
echo "Status:          $STATUS_FILE"
echo "Diffstat:        $DIFFSTAT_FILE"
echo "Diff:            $DIFF_FILE"
echo "Source Status:   $SOURCE_STATUS_FILE"
echo "Worktree Status: $WORKTREE_STATUS_FILE"
echo "Untracked Files: $UNTRACKED_FILE"
echo "Usage Summary:   $USAGE_FILE"
echo "Report:          $REPORT_FILE"
echo ""
echo "Changes have NOT been merged. Review the diff and merge manually."
echo "To remove the worktree: git worktree remove $WORKTREE_DIR"