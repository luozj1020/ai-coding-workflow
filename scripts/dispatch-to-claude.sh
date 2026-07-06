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
#   5. Invokes claude -p in non-interactive mode, without inherited proxy env by default.
#   6. Saves result, status, diffstat, diff, untracked files, usage, and report.
#   7. Records worktree status (tracked + untracked) after execution.
#   8. Prints paths to generated result files.
#   9. Does NOT merge automatically.

set -euo pipefail

# Git for Windows can be launched through bin/bash.exe without the usual Unix tool PATH.
# Append common Unix tool paths without overriding caller-provided shims or test fakes.
PATH="${PATH}:/usr/bin:/bin:/mingw64/bin"
export PATH

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

CLAUDE_CODE_PROXY_MODE="${CLAUDE_CODE_PROXY_MODE:-direct}"
if [ "$CLAUDE_CODE_PROXY_MODE" != "direct" ] && [ "$CLAUDE_CODE_PROXY_MODE" != "inherit" ]; then
    echo "Error: CLAUDE_CODE_PROXY_MODE must be 'direct' or 'inherit'." >&2
    exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WATCH_SCRIPT="${SCRIPT_DIR}/watch-claude.sh"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
TASK_ID="claude-${TIMESTAMP}"
WORKTREE_DIR="${REPO_ROOT}/.worktrees/${TASK_ID}"
WORKTREE_ROOT="${REPO_ROOT}/.worktrees"

mkdir -p "$WORKTREE_ROOT"

RESULT_FILE="${WORKTREE_ROOT}/${TASK_ID}.result.json"
STATUS_FILE="${WORKTREE_ROOT}/${TASK_ID}.status.txt"
DIFFSTAT_FILE="${WORKTREE_ROOT}/${TASK_ID}.diffstat.txt"
DIFF_FILE="${WORKTREE_ROOT}/${TASK_ID}.diff"
CHECKER_REPORT_FILE="${WORKTREE_ROOT}/${TASK_ID}.checker-report.md"
CHECKER_LOGS_DIR="${WORKTREE_ROOT}/${TASK_ID}.checker-logs"
SOURCE_STATUS_FILE="${WORKTREE_ROOT}/${TASK_ID}.source-status.txt"
WORKTREE_STATUS_FILE="${WORKTREE_ROOT}/${TASK_ID}.worktree-status.txt"
UNTRACKED_FILE="${WORKTREE_ROOT}/${TASK_ID}.untracked.txt"
USAGE_FILE="${WORKTREE_ROOT}/${TASK_ID}.usage.txt"
REPORT_FILE="${WORKTREE_ROOT}/${TASK_ID}.report.md"
CLAUDE_PROGRESS_FILE="${WORKTREE_ROOT}/${TASK_ID}.claude-progress.md"
PID_FILE="${WORKTREE_ROOT}/${TASK_ID}.pid"
PROGRESS_FILE="${WORKTREE_ROOT}/${TASK_ID}.progress.log"

for f in "$RESULT_FILE" "$STATUS_FILE" "$DIFFSTAT_FILE" "$DIFF_FILE" "$CHECKER_REPORT_FILE" \
         "$SOURCE_STATUS_FILE" "$WORKTREE_STATUS_FILE" "$UNTRACKED_FILE" "$USAGE_FILE" "$REPORT_FILE" \
         "$CLAUDE_PROGRESS_FILE" "$PID_FILE" "$PROGRESS_FILE"; do
    mkdir -p "$(dirname "$f")"
done


TASK_CARD_REL="$(git -C "$REPO_ROOT" ls-files --full-name -- "$TASK_CARD" 2>/dev/null | head -1 || true)"
if [ -z "$TASK_CARD_REL" ]; then
    TASK_CARD_REL="$(git -C "$REPO_ROOT" ls-files --others --exclude-standard --full-name -- "$TASK_CARD" 2>/dev/null | head -1 || true)"
fi
if [ -z "$TASK_CARD_REL" ]; then
    TASK_CARD_ABS="$(cd "$(dirname "$TASK_CARD")" && pwd)/$(basename "$TASK_CARD")"
    case "$TASK_CARD_ABS" in
        "$REPO_ROOT"/*) TASK_CARD_REL="${TASK_CARD_ABS#"$REPO_ROOT"/}" ;;
        *) TASK_CARD_REL="$TASK_CARD" ;;
    esac
fi

DIRTY_TRACKED="$(git diff --name-only 2>/dev/null || true)"
DIRTY_STAGED="$(git diff --cached --name-only 2>/dev/null || true)"
DIRTY_UNTRACKED="$(git ls-files --others --exclude-standard 2>/dev/null | grep -v -E "^\.worktrees/${TASK_ID}\." | grep -vxF "$TASK_CARD_REL" || true)"

if [ -n "$DIRTY_TRACKED" ] || [ -n "$DIRTY_STAGED" ] || [ -n "$DIRTY_UNTRACKED" ]; then
    if [ "${CLAUDE_CODE_ALLOW_DIRTY_SOURCE:-0}" = "1" ]; then
        echo "Warning: Source worktree is dirty; proceeding because CLAUDE_CODE_ALLOW_DIRTY_SOURCE=1." >&2
    else
        echo "Error: Source worktree is dirty. Claude would run from stale HEAD." >&2
        echo "Commit or stash source changes first, or set CLAUDE_CODE_ALLOW_DIRTY_SOURCE=1 to override." >&2
        echo "The current task card may be untracked and is exempt from the untracked-file check." >&2
        if [ -n "$DIRTY_TRACKED" ]; then
            echo "" >&2
            echo "Tracked changes:" >&2
            echo "$DIRTY_TRACKED" | sed 's/^/  /' >&2
        fi
        if [ -n "$DIRTY_STAGED" ]; then
            echo "" >&2
            echo "Staged changes:" >&2
            echo "$DIRTY_STAGED" | sed 's/^/  /' >&2
        fi
        if [ -n "$DIRTY_UNTRACKED" ]; then
            echo "" >&2
            echo "Unrelated untracked files:" >&2
            echo "$DIRTY_UNTRACKED" | sed 's/^/  /' >&2
        fi
        exit 1
    fi
fi

BRANCH_NAME="claude-task-${TIMESTAMP}"
git worktree add -b "$BRANCH_NAME" "$WORKTREE_DIR" HEAD || {
    echo "Error: Failed to create git worktree at $WORKTREE_DIR" >&2
    exit 1
}

echo "Created worktree: $WORKTREE_DIR"
echo "Branch: $BRANCH_NAME"

{
    echo "# Source Repository Status - ${TIMESTAMP}"
    echo "# Recorded after preflight checks and worktree creation"
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

cp "$TASK_CARD" "${WORKTREE_DIR}/TASK_CARD.md"
echo "Task card copied to: ${WORKTREE_DIR}/TASK_CARD.md"

cat > "${WORKTREE_DIR}/CLAUDE_PROMPT.md" <<'EOF'
You are the executor in a Codex/Claude Code workflow.

Execute the task card below. While working, maintain `CLAUDE_PROGRESS.md` in the worktree so the dispatcher can show user-visible progress without interrupting you.

`CLAUDE_PROGRESS.md` requirements:
- Create it before doing substantial exploration or edits.
- Keep it short and append/update it at natural milestones: context gathered, plan chosen, files being edited, checks running, blocker encountered, finalizing.
- Keep these stable fields near the top so the current goal stays in recent attention:
  - Goal
  - Current Phase
  - Next Check
  - Blocker
  - Last Update
- Before any command or investigation that may take more than a few minutes, write what you are about to do and what result you expect.
- Do not include secrets, large logs, or full diffs.
- Preserve failed commands and observations instead of deleting or rewriting them; later recovery depends on that evidence.


Phase-gate requirements:
- If the task card has an `## Execution Phases` table, follow it as the outer execution contract. You may break down work inside a phase, but do not silently combine phases.
- At each phase boundary, update `CLAUDE_PROGRESS.md` with the current phase, completed evidence, and the next intended action.
- Create or update `CLAUDE_REPORT.md` before running long validation commands, before waiting on potentially slow commands, and before moving to a later phase marked `Stop Before Next Phase? = yes`.
- If validation fails, hangs, or is blocked, stop after recording the exact command, observed output, and proposed next phase instead of continuing broad edits.

Unknowns and decision gates:
- If the task card has `## Execution Readiness Gate`, verify it against the repository before editing. If the task is not implementation-ready, stop after recording why an exploration/prototype task is needed.
- If the task card has `## Unknowns`, perform the requested blindspot pass before implementation and record material findings in `CLAUDE_PROGRESS.md` or `CLAUDE_REPORT.md`.
- If the task card has `## Decision Gates`, obey the listed authority: autonomous decisions may proceed, conservative decisions must choose the least risky compatible path, and stop-and-report decisions must not be crossed silently.
- If the task card has `## Handoff Contract`, treat Must do / Must not do / May decide / Must report / Stop condition as the primary executor contract.
- If implementation reality conflicts with the plan, choose a conservative path when safe, record the deviation under `Deviations From Plan`, and continue only when the task card permits it.

Wait policy requirements:
- If the task card has an `## Wait Policy` table, treat it as the observer contract for how long Codex/humans should give you before reviewing or interrupting.
- Keep `CLAUDE_PROGRESS.md` fresh enough that quiet time reflects real tool/model waiting, not missing progress notes.
- When partial implementation exists but validation is still running or blocked, update `CLAUDE_REPORT.md` with enough file-level summary for Codex to compare the partial diff against the plan.

In addition to making the requested edits, create `CLAUDE_REPORT.md` in the worktree before finishing.

Checker expectations:
- Run project validation before finishing. If `ai/check-worktree.sh` is available, use it.
- Preserve failed command, exit code, key original output, and file:line details.
- Do not weaken, delete, skip, or rewrite checks just to get a green result.
- If a validation blocker is environmental or external, stop and record the blocker instead of guessing.

`CLAUDE_REPORT.md` must include:
- Task card ID/path and a concise requirements summary.
- Files changed with one-line purpose per file.
- Acceptance criteria mapping: met / not met / partial.
- Plan Match: full / partial / off-plan.
- Validation Confidence: high / medium / low.
- Reviewer Should Check: concise list of areas Codex/human should inspect.
- Unknowns resolved, unknown-unknowns discovered, and decision gates crossed.
- Deviations From Plan: original plan, discovered constraint, action taken, and reviewer decision needed.
- Reviewer Briefing: behavior changed, critical paths, risks, and verification guidance.
- Checks run and exact outcomes.
- Known risks, assumptions, and open questions.
- Human review checklist.
- Notes that help Codex compare the implementation against the original task.

--- TASK CARD ---
EOF
cat "${WORKTREE_DIR}/TASK_CARD.md" >> "${WORKTREE_DIR}/CLAUDE_PROMPT.md"

CLAUDE_CODE_TIMEOUT_SECONDS="${CLAUDE_CODE_TIMEOUT_SECONDS:-600}"
CLAUDE_CODE_HEARTBEAT_SECONDS="${CLAUDE_CODE_HEARTBEAT_SECONDS:-30}"
CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS="${CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS:-0}"

case "$CLAUDE_CODE_TIMEOUT_SECONDS" in
    ''|*[!0-9]*)
        echo "Error: CLAUDE_CODE_TIMEOUT_SECONDS must be a non-negative integer." >&2
        exit 1
        ;;
esac
case "$CLAUDE_CODE_HEARTBEAT_SECONDS" in
    ''|*[!0-9]*)
        echo "Error: CLAUDE_CODE_HEARTBEAT_SECONDS must be a positive integer." >&2
        exit 1
        ;;
esac
case "$CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS" in
    ''|*[!0-9]*)
        echo "Error: CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS must be a non-negative integer." >&2
        exit 1
        ;;
esac
if [ "$CLAUDE_CODE_HEARTBEAT_SECONDS" -eq 0 ]; then
    echo "Error: CLAUDE_CODE_HEARTBEAT_SECONDS must be greater than 0." >&2
    exit 1
fi

progress_log() {
    local message="$1"
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$message" | tee -a "$PROGRESS_FILE"
}

file_size() {
    local file="$1"
    if [ -f "$file" ]; then
        wc -c < "$file" 2>/dev/null | tr -d ' ' || echo 0
    else
        echo 0
    fi
}

worktree_change_count() {
    git status --porcelain --untracked-files=all 2>/dev/null \
        | grep -v -E '^(.. )?(TASK_CARD|CLAUDE_PROMPT|CLAUDE_REPORT|CLAUDE_PROGRESS)(\.md)?$' \
        | wc -l 2>/dev/null | tr -d '[:space:]' || echo 0
}

worktree_digest() {
    {
        git status --porcelain --untracked-files=all 2>/dev/null \
            | grep -v -E '^(.. )?(TASK_CARD|CLAUDE_PROMPT|CLAUDE_REPORT|CLAUDE_PROGRESS)(\.md)?$' || true
        git diff --shortstat 2>/dev/null || true
    } | sha1sum 2>/dev/null | awk '{print $1}' || true
}

stop_claude() {
    local reason="$1"
    local elapsed="$2"
    progress_log "Stopping Claude (${reason}) after ${elapsed}s; sending TERM to pid=${CLAUDE_PID}"
    kill "$CLAUDE_PID" 2>/dev/null || true
    sleep 5
    if kill -0 "$CLAUDE_PID" 2>/dev/null; then
        progress_log "Claude still alive after TERM; sending KILL to pid=${CLAUDE_PID}"
        kill -9 "$CLAUDE_PID" 2>/dev/null || true
    fi
}

claude_is_running() {
    if ! kill -0 "$CLAUDE_PID" 2>/dev/null; then
        return 1
    fi
    if command -v ps >/dev/null 2>&1; then
        local state
        state="$(ps -p "$CLAUDE_PID" -o stat= 2>/dev/null | awk '{print $1}' || true)"
        case "$state" in
            Z*) return 1 ;;
        esac
    fi
    return 0
}

run_claude() {
    if [ "$CLAUDE_CODE_PROXY_MODE" = "inherit" ]; then
        claude -p \
            --permission-mode acceptEdits \
            --output-format json \
            < CLAUDE_PROMPT.md > "$RESULT_FILE" 2>"${STATUS_FILE}"
    else
        (
            unset HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY
            unset http_proxy https_proxy all_proxy no_proxy
            claude -p \
                --permission-mode acceptEdits \
                --output-format json \
                < CLAUDE_PROMPT.md > "$RESULT_FILE" 2>"${STATUS_FILE}"
        )
    fi
}

echo "Invoking Claude Code..."
echo "Progress log: $PROGRESS_FILE"
echo "Watch Progress: bash \"$WATCH_SCRIPT\" \"$TASK_ID\""
echo "Watch Details:  bash \"$WATCH_SCRIPT\" \"$TASK_ID\" --details"
cd "$WORKTREE_DIR"

: > "$PROGRESS_FILE"
progress_log "Starting Claude Code: proxy_mode=${CLAUDE_CODE_PROXY_MODE}, timeout_seconds=${CLAUDE_CODE_TIMEOUT_SECONDS}, heartbeat_seconds=${CLAUDE_CODE_HEARTBEAT_SECONDS}, no_output_timeout_seconds=${CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS}"

set +e
run_claude &
CLAUDE_PID=$!
echo "$CLAUDE_PID" > "$PID_FILE"
progress_log "Claude process started: pid=${CLAUDE_PID}"

START_EPOCH="$(date +%s)"
CLAUDE_TIMED_OUT=0
CLAUDE_NO_OUTPUT_TIMED_OUT=0
LAST_ACTIVITY_EPOCH="$START_EPOCH"
LAST_TOTAL_BYTES=0
LAST_WORKTREE_DIGEST="$(worktree_digest)"
while claude_is_running; do
    sleep "$CLAUDE_CODE_HEARTBEAT_SECONDS"
    NOW_EPOCH="$(date +%s)"
    ELAPSED=$((NOW_EPOCH - START_EPOCH))

    if ! claude_is_running; then
        break
    fi

    RESULT_BYTES="$(file_size "$RESULT_FILE")"
    STATUS_BYTES="$(file_size "$STATUS_FILE")"
    REPORT_BYTES="$(file_size "${WORKTREE_DIR}/CLAUDE_REPORT.md")"
    CLAUDE_PROGRESS_BYTES="$(file_size "${WORKTREE_DIR}/CLAUDE_PROGRESS.md")"
    WORKTREE_CHANGES="$(worktree_change_count)"
    CURRENT_WORKTREE_DIGEST="$(worktree_digest)"
    TOTAL_BYTES=$((RESULT_BYTES + STATUS_BYTES + REPORT_BYTES + CLAUDE_PROGRESS_BYTES))
    WORKTREE_CHANGED=0
    if [ "$CURRENT_WORKTREE_DIGEST" != "$LAST_WORKTREE_DIGEST" ]; then
        WORKTREE_CHANGED=1
        LAST_WORKTREE_DIGEST="$CURRENT_WORKTREE_DIGEST"
    fi
    if [ "$TOTAL_BYTES" -ne "$LAST_TOTAL_BYTES" ] || [ "$WORKTREE_CHANGED" -eq 1 ]; then
        LAST_TOTAL_BYTES="$TOTAL_BYTES"
        LAST_ACTIVITY_EPOCH="$NOW_EPOCH"
    fi
    QUIET_SECONDS=$((NOW_EPOCH - LAST_ACTIVITY_EPOCH))
    progress_log "Claude still running: pid=${CLAUDE_PID}, elapsed_seconds=${ELAPSED}, quiet_seconds=${QUIET_SECONDS}, result_bytes=${RESULT_BYTES}, status_bytes=${STATUS_BYTES}, report_bytes=${REPORT_BYTES}, claude_progress_bytes=${CLAUDE_PROGRESS_BYTES}, worktree_changes=${WORKTREE_CHANGES}, worktree_changed=${WORKTREE_CHANGED}"

    if [ "$CLAUDE_CODE_TIMEOUT_SECONDS" -gt 0 ] && [ "$ELAPSED" -ge "$CLAUDE_CODE_TIMEOUT_SECONDS" ]; then
        CLAUDE_TIMED_OUT=1
        stop_claude "runtime timeout" "$ELAPSED"
        break
    fi

    if [ "$CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS" -gt 0 ] && [ "$QUIET_SECONDS" -ge "$CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS" ]; then
        CLAUDE_NO_OUTPUT_TIMED_OUT=1
        stop_claude "no output for ${QUIET_SECONDS}s" "$ELAPSED"
        break
    fi
done

wait "$CLAUDE_PID"
CLAUDE_STATUS=$?
set -e

END_EPOCH="$(date +%s)"
ELAPSED=$((END_EPOCH - START_EPOCH))
if [ "$CLAUDE_NO_OUTPUT_TIMED_OUT" -eq 1 ]; then
    {
        echo ""
        echo "[dispatch] Claude stopped after ${ELAPSED}s because no result/status/report/progress output changed for ${CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS}s."
        echo "[dispatch] No-output timeout seconds: ${CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS}"
        echo "[dispatch] Progress log: ${PROGRESS_FILE}"
    } >> "$STATUS_FILE"
    progress_log "Claude finished by no-output timeout: elapsed_seconds=${ELAPSED}, wait_status=${CLAUDE_STATUS}"
    echo "Warning: claude produced no observable output for ${CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS}s. Check $STATUS_FILE and $PROGRESS_FILE" >&2
elif [ "$CLAUDE_TIMED_OUT" -eq 1 ]; then
    {
        echo ""
        echo "[dispatch] Claude timed out after ${ELAPSED}s."
        echo "[dispatch] Timeout seconds: ${CLAUDE_CODE_TIMEOUT_SECONDS}"
        echo "[dispatch] Progress log: ${PROGRESS_FILE}"
    } >> "$STATUS_FILE"
    progress_log "Claude finished by timeout: elapsed_seconds=${ELAPSED}, wait_status=${CLAUDE_STATUS}"
    echo "Warning: claude timed out after ${ELAPSED}s. Check $STATUS_FILE and $PROGRESS_FILE" >&2
elif [ "$CLAUDE_STATUS" -ne 0 ]; then
    progress_log "Claude exited non-zero: status=${CLAUDE_STATUS}, elapsed_seconds=${ELAPSED}"
    echo "Warning: claude exited with non-zero status $CLAUDE_STATUS. Check $STATUS_FILE" >&2
else
    progress_log "Claude completed successfully: elapsed_seconds=${ELAPSED}"
fi

cd "$WORKTREE_DIR"

CHECK_SCRIPT="${SCRIPT_DIR}/check-worktree.sh"
if [ -f "$CHECK_SCRIPT" ]; then
    progress_log "Starting checker helper: ${CHECK_SCRIPT}"
    set +e
    bash "$CHECK_SCRIPT" --report "$CHECKER_REPORT_FILE" --logs-dir "$CHECKER_LOGS_DIR" >> "$STATUS_FILE" 2>&1
    CHECKER_STATUS=$?
    set -e
    if [ "$CHECKER_STATUS" -eq 0 ]; then
        progress_log "Checker helper completed: ALL GREEN"
    else
        progress_log "Checker helper completed: FAILED status=${CHECKER_STATUS}; report=${CHECKER_REPORT_FILE}"
        echo "Warning: checker helper reported failures. Review $CHECKER_REPORT_FILE" >&2
    fi
else
    {
        echo "# Checker Report"
        echo ""
        echo "FAILED"
        echo ""
        echo "Checker helper not found: ${CHECK_SCRIPT}"
    } > "$CHECKER_REPORT_FILE"
    progress_log "Checker helper unavailable: ${CHECK_SCRIPT}"
fi

git diff --stat > "$DIFFSTAT_FILE" 2>/dev/null || true
git diff > "$DIFF_FILE" 2>/dev/null || true

FILTERED_UNTRACKED="$(git ls-files --others --exclude-standard 2>/dev/null \
    | grep -v -E '^(TASK_CARD|CLAUDE_PROMPT|CLAUDE_REPORT|CLAUDE_PROGRESS)' || true)"

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

if [ -f "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" ]; then
    cp "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" "$CLAUDE_PROGRESS_FILE"
else
    {
        echo "# Claude Progress"
        echo ""
        echo "Claude did not create CLAUDE_PROGRESS.md. Check dispatch progress and status artifacts."
    } > "$CLAUDE_PROGRESS_FILE"
fi

echo "Claude progress saved to: $CLAUDE_PROGRESS_FILE"

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
        echo "- Checker report: $CHECKER_REPORT_FILE"
        echo "- Source status: $SOURCE_STATUS_FILE"
        echo "- Worktree status: $WORKTREE_STATUS_FILE"
        echo "- Untracked files: $UNTRACKED_FILE"
        echo "- Usage summary: $USAGE_FILE"
        echo "- Claude progress: $CLAUDE_PROGRESS_FILE"
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
echo "Checker Report:  $CHECKER_REPORT_FILE"
echo "Source Status:   $SOURCE_STATUS_FILE"
echo "Worktree Status: $WORKTREE_STATUS_FILE"
echo "Untracked Files: $UNTRACKED_FILE"
echo "Usage Summary:   $USAGE_FILE"
echo "Claude Progress: $CLAUDE_PROGRESS_FILE"
echo "Report:          $REPORT_FILE"
echo "Claude PID:      $PID_FILE"
echo "Progress Log:    $PROGRESS_FILE"
echo "Watch Progress:  bash \"$WATCH_SCRIPT\" \"$TASK_ID\""
echo "Watch Details:   bash \"$WATCH_SCRIPT\" \"$TASK_ID\" --details"
echo ""
echo "Changes have NOT been merged. Review the diff and merge manually."
echo "To remove the worktree: git worktree remove $WORKTREE_DIR"
