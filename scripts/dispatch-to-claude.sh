#!/usr/bin/env bash
# dispatch-to-claude.sh  -  Dispatch a task card to Claude Code in an isolated worktree.
#
# Usage: bash ai/dispatch-to-claude.sh <task-card-path>
#
# This script:
#   1. Validates that git and claude CLI exist.
#   2. Records source repository status (tracked + untracked) before dispatch.
#   3. Creates an isolated git worktree under .worktrees/claude-<timestamp>.
#   4. Copies the full task card and renders a Claude execution projection.
#   5. Invokes claude -p in non-interactive mode, without inherited proxy env by default.
#   6. Optionally records low-intrusion network diagnostics for the Claude process.
#   7. Saves result, status, diffstat, diff, untracked files, usage, and report.
#   8. Records worktree status (tracked + untracked) after execution.
#   9. Prints paths to generated result files.
#  10. Does NOT merge automatically.

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
CLAUDE_CODE_NETWORK_MONITOR="${CLAUDE_CODE_NETWORK_MONITOR:-0}"
case "$CLAUDE_CODE_NETWORK_MONITOR" in
    0|1) ;;
    *)
        echo "Error: CLAUDE_CODE_NETWORK_MONITOR must be 0 or 1." >&2
        exit 1
        ;;
esac
CLAUDE_CODE_NETWORK_HEALTHCHECK_URL="${CLAUDE_CODE_NETWORK_HEALTHCHECK_URL:-}"
CLAUDE_CODE_NETWORK_HEALTHCHECK_TIMEOUT_SECONDS="${CLAUDE_CODE_NETWORK_HEALTHCHECK_TIMEOUT_SECONDS:-5}"
CLAUDE_CODE_EXECUTION_PROFILE="${CLAUDE_CODE_EXECUTION_PROFILE:-balanced}"
case "$CLAUDE_CODE_EXECUTION_PROFILE" in
    safe)
        DEFAULT_WORKTREE_STRATEGY="fresh"
        DEFAULT_REUSE_WORKTREE_RESET="0"
        DEFAULT_LARGE_REPO_MODE="0"
        DEFAULT_TASK_CARD_VIEW="execution"
        DEFAULT_PROMPT_PROFILE="standard"
        DEFAULT_EVIDENCE_MODE="full"
        DEFAULT_CHECKER_DISCOVER="0"
        ;;
    balanced)
        DEFAULT_WORKTREE_STRATEGY="fresh"
        DEFAULT_REUSE_WORKTREE_RESET="0"
        DEFAULT_LARGE_REPO_MODE="0"
        DEFAULT_TASK_CARD_VIEW="compact"
        DEFAULT_PROMPT_PROFILE="brief"
        DEFAULT_EVIDENCE_MODE="full"
        DEFAULT_CHECKER_DISCOVER="0"
        ;;
    fast-large-repo)
        DEFAULT_WORKTREE_STRATEGY="reuse-managed"
        DEFAULT_REUSE_WORKTREE_RESET="0"
        DEFAULT_LARGE_REPO_MODE="1"
        DEFAULT_TASK_CARD_VIEW="compact"
        DEFAULT_PROMPT_PROFILE="brief"
        DEFAULT_EVIDENCE_MODE="summary"
        DEFAULT_CHECKER_DISCOVER="0"
        ;;
    *)
        echo "Error: CLAUDE_CODE_EXECUTION_PROFILE must be 'safe', 'balanced', or 'fast-large-repo'." >&2
        exit 1
        ;;
esac

# --- Spec item 1: task-mode-aware worktree strategy default ---
# Parse task mode from the task card table to enable smart strategy selection.
# When the user did not explicitly set CLAUDE_CODE_WORKTREE_STRATEGY, select
# reuse-managed only for serial low-risk checker-test cards.  Parallel/DAG,
# Builder/mixed, missing/ambiguous mode, or any risk keyword stays fresh.
_PARSED_TASK_MODE=""
if [ -f "$TASK_CARD" ]; then
    _PARSED_TASK_MODE="$(awk -F'|' '
        /^\|/ && NF >= 3 {
            field = $2; value = $3
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", field)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
            if (tolower(field) == "mode") { print tolower(value); exit }
        }
    ' "$TASK_CARD" 2>/dev/null || true)"
fi

_IS_DAG_DISPATCH=0
if [ -n "${AI_CODING_WORKFLOW_DAG_TASK_ID:-}" ]; then
    _IS_DAG_DISPATCH=1
fi

# Detect explicit risk category rows in the task card.
# Conservative: false positive (staying fresh) is safe; false negative is not.
_HAS_RISK_ROWS=0
if [ -f "$TASK_CARD" ]; then
    if awk -F'|' '
        function trim(s) { gsub(/^[[:space:]]+|[[:space:]]+$/, "", s); return s }
        /^\|/ && NF >= 3 {
            field = tolower(trim($2))
            if (field ~ /public.api|data.model|data.impact|security|migration|permission|concurrency|cross.module|production/) {
                if (field !~ /mode|profile|monitor|strategy|reset|discover|view|proxy|timeout|network|large.repo|evidence|checker|validation|task.card|local.validation/) {
                    exit 0
                }
            }
        }
        END { exit 1 }
    ' "$TASK_CARD" 2>/dev/null; then
        _HAS_RISK_ROWS=1
    fi
fi

# Apply smart default only when the user did not explicitly set the strategy
# and the profile default is fresh (safe/balanced profiles).
if [ -z "${CLAUDE_CODE_WORKTREE_STRATEGY+x}" ] && \
   [ "$DEFAULT_WORKTREE_STRATEGY" = "fresh" ] && \
   [ "$_PARSED_TASK_MODE" = "checker-test" ] && \
   [ "$_IS_DAG_DISPATCH" -eq 0 ] && \
   [ "$_HAS_RISK_ROWS" -eq 0 ]; then
    DEFAULT_WORKTREE_STRATEGY="reuse-managed"
fi

CLAUDE_CODE_WORKTREE_STRATEGY="${CLAUDE_CODE_WORKTREE_STRATEGY:-$DEFAULT_WORKTREE_STRATEGY}"
CLAUDE_CODE_REUSE_WORKTREE_RESET="${CLAUDE_CODE_REUSE_WORKTREE_RESET:-$DEFAULT_REUSE_WORKTREE_RESET}"
CLAUDE_CODE_LARGE_REPO_MODE="${CLAUDE_CODE_LARGE_REPO_MODE:-$DEFAULT_LARGE_REPO_MODE}"
CLAUDE_CODE_TASK_CARD_VIEW="${CLAUDE_CODE_TASK_CARD_VIEW:-$DEFAULT_TASK_CARD_VIEW}"
CLAUDE_CODE_PROMPT_PROFILE="${CLAUDE_CODE_PROMPT_PROFILE:-$DEFAULT_PROMPT_PROFILE}"
CLAUDE_CODE_EVIDENCE_MODE="${CLAUDE_CODE_EVIDENCE_MODE:-$DEFAULT_EVIDENCE_MODE}"
CLAUDE_CODE_CHECKER_DISCOVER="${CLAUDE_CODE_CHECKER_DISCOVER:-$DEFAULT_CHECKER_DISCOVER}"
CLAUDE_CODE_CHECKER_COMMANDS="${CLAUDE_CODE_CHECKER_COMMANDS:-}"
case "$CLAUDE_CODE_NETWORK_HEALTHCHECK_TIMEOUT_SECONDS" in
    ''|*[!0-9]*)
        echo "Error: CLAUDE_CODE_NETWORK_HEALTHCHECK_TIMEOUT_SECONDS must be a non-negative integer." >&2
        exit 1
        ;;
esac
case "$CLAUDE_CODE_WORKTREE_STRATEGY" in
    fresh|reuse-managed) ;;
    *)
        echo "Error: CLAUDE_CODE_WORKTREE_STRATEGY must be 'fresh' or 'reuse-managed'." >&2
        exit 1
        ;;
esac
case "$CLAUDE_CODE_REUSE_WORKTREE_RESET" in
    0|1) ;;
    *)
        echo "Error: CLAUDE_CODE_REUSE_WORKTREE_RESET must be 0 or 1." >&2
        exit 1
        ;;
esac
case "$CLAUDE_CODE_LARGE_REPO_MODE" in
    0|1) ;;
    *)
        echo "Error: CLAUDE_CODE_LARGE_REPO_MODE must be 0 or 1." >&2
        exit 1
        ;;
esac
case "$CLAUDE_CODE_TASK_CARD_VIEW" in
    execution|compact) ;;
    *)
        echo "Error: CLAUDE_CODE_TASK_CARD_VIEW must be 'execution' or 'compact'." >&2
        exit 1
        ;;
esac
case "$CLAUDE_CODE_PROMPT_PROFILE" in
    brief|standard) ;;
    *)
        echo "Error: CLAUDE_CODE_PROMPT_PROFILE must be 'brief' or 'standard'." >&2
        exit 1
        ;;
esac
case "$CLAUDE_CODE_EVIDENCE_MODE" in
    full|summary) ;;
    *)
        echo "Error: CLAUDE_CODE_EVIDENCE_MODE must be 'full' or 'summary'." >&2
        exit 1
        ;;
esac
case "$CLAUDE_CODE_CHECKER_DISCOVER" in
    0|1) ;;
    *)
        echo "Error: CLAUDE_CODE_CHECKER_DISCOVER must be 0 or 1." >&2
        exit 1
        ;;
esac
CLAUDE_CODE_VERBOSE="${CLAUDE_CODE_VERBOSE:-0}"
case "$CLAUDE_CODE_VERBOSE" in
    0|1) ;;
    *)
        echo "Error: CLAUDE_CODE_VERBOSE must be 0 or 1." >&2
        exit 1
        ;;
esac
CLAUDE_CODE_APPROVAL_BLOCKED_CONVERGENCE="${CLAUDE_CODE_APPROVAL_BLOCKED_CONVERGENCE:-1}"
case "$CLAUDE_CODE_APPROVAL_BLOCKED_CONVERGENCE" in
    0|1) ;;
    *)
        echo "Error: CLAUDE_CODE_APPROVAL_BLOCKED_CONVERGENCE must be 0 or 1." >&2
        exit 1
        ;;
esac

REPO_ROOT="$(git rev-parse --show-toplevel)"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WATCH_SCRIPT="${SCRIPT_DIR}/watch-claude.sh"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

# Generate a collision-resistant suffix for task identity.
# Even outside DAG mode, two flat dispatches started in the same second
# must not share task IDs, worktree paths, branch names, or artifact paths.
RAND_SUFFIX="${AI_CODING_WORKFLOW_RAND_SUFFIX:-}"
if [ -z "$RAND_SUFFIX" ]; then
    if [ -r /dev/urandom ]; then
        RAND_SUFFIX="$(od -An -tx1 -N4 /dev/urandom | tr -d ' ')"
    else
        RAND_SUFFIX="$$"
    fi
fi

# DAG mode: use caller-provided task ID for collision-resistant identity
# When AI_CODING_WORKFLOW_DAG_TASK_ID is set, build TASK_ID from the DAG
# task identifier rather than just the timestamp, so concurrent dispatches
# in the same second cannot collide.
if [ -n "${AI_CODING_WORKFLOW_DAG_TASK_ID:-}" ]; then
    DAG_GROUP="${AI_CODING_WORKFLOW_DAG_GROUP_ID:-dag}"
    TASK_ID="${DAG_GROUP}-${AI_CODING_WORKFLOW_DAG_TASK_ID}-${TIMESTAMP}-${RAND_SUFFIX}"
else
    TASK_ID="claude-${TIMESTAMP}-${RAND_SUFFIX}"
fi

WORKTREE_ROOT="${REPO_ROOT}/.worktrees"
REUSE_WORKTREE_DIR="${WORKTREE_ROOT}/reuse/claude-managed"
if [ "$CLAUDE_CODE_WORKTREE_STRATEGY" = "reuse-managed" ]; then
    WORKTREE_DIR="$REUSE_WORKTREE_DIR"
else
    WORKTREE_DIR="${WORKTREE_ROOT}/${TASK_ID}"
fi

mkdir -p "$WORKTREE_ROOT"

RESULT_FILE="${WORKTREE_ROOT}/${TASK_ID}.result.json"
RAW_RESULT_FILE="${WORKTREE_ROOT}/${TASK_ID}.result.raw.txt"
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
NETWORK_FILE="${WORKTREE_ROOT}/${TASK_ID}.network.log"
SEEDED_REPORT_MARKER="AI-CODING-WORKFLOW:DISPATCH-SEEDED-REPORT"
SEEDED_PROGRESS_MARKER="AI-CODING-WORKFLOW:DISPATCH-SEEDED-PROGRESS"
FALLBACK_REPORT_MARKER="AI-CODING-WORKFLOW:DISPATCH-FALLBACK-REPORT"

for f in "$RESULT_FILE" "$RAW_RESULT_FILE" "$STATUS_FILE" "$DIFFSTAT_FILE" "$DIFF_FILE" "$CHECKER_REPORT_FILE" \
         "$SOURCE_STATUS_FILE" "$WORKTREE_STATUS_FILE" "$UNTRACKED_FILE" "$USAGE_FILE" "$REPORT_FILE" \
         "$CLAUDE_PROGRESS_FILE" "$PID_FILE" "$PROGRESS_FILE" "$NETWORK_FILE"; do
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
if [ "$CLAUDE_CODE_LARGE_REPO_MODE" = "1" ]; then
    DIRTY_UNTRACKED=""
    DIRTY_UNTRACKED_SKIPPED=1
else
    DIRTY_UNTRACKED="$(git ls-files --others --exclude-standard 2>/dev/null | grep -v -E "^\.worktrees/" | grep -vxF "$TASK_CARD_REL" || true)"
    DIRTY_UNTRACKED_SKIPPED=0
fi

if [ -n "$DIRTY_TRACKED" ] || [ -n "$DIRTY_STAGED" ] || [ -n "$DIRTY_UNTRACKED" ]; then
    if [ "${CLAUDE_CODE_ALLOW_DIRTY_SOURCE:-0}" = "1" ]; then
        echo "Warning: Source worktree is dirty; proceeding because CLAUDE_CODE_ALLOW_DIRTY_SOURCE=1." >&2
    else
        echo "Error: Source worktree is dirty. Claude would run from stale HEAD." >&2
        echo "This is a delegation blocker, not a Codex takeover trigger." >&2
        echo "Restore delegation first: commit accepted changes, stash/patch source changes, or re-dispatch from an updated clean HEAD." >&2
        echo "Set CLAUDE_CODE_ALLOW_DIRTY_SOURCE=1 only with explicit approval when stale-HEAD risk is understood." >&2
        echo "The current task card may be untracked and is exempt from the untracked-file check." >&2
        if [ "$DIRTY_UNTRACKED_SKIPPED" -eq 1 ]; then
            echo "Large-repo mode skipped unrelated untracked-file scanning; tracked/staged dirty checks still ran." >&2
        fi
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

BASE_COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD)"

create_dispatch_worktree() {
    local branch_name="$1"
    if [ "$CLAUDE_CODE_WORKTREE_STRATEGY" = "fresh" ]; then
        git worktree add -b "$branch_name" "$WORKTREE_DIR" "$BASE_COMMIT" || {
            echo "Error: Failed to create git worktree at $WORKTREE_DIR" >&2
            exit 1
        }
        if [ "$CLAUDE_CODE_VERBOSE" = "1" ]; then
            echo "Created worktree: $WORKTREE_DIR"
        fi
        return
    fi

    case "$WORKTREE_DIR" in
        "$WORKTREE_ROOT"/reuse/claude-managed) ;;
        *)
            echo "Error: refusing to reuse unmanaged worktree path: $WORKTREE_DIR" >&2
            exit 1
            ;;
    esac

    mkdir -p "$(dirname "$WORKTREE_DIR")"
    if [ -d "$WORKTREE_DIR" ]; then
        if [ "$CLAUDE_CODE_REUSE_WORKTREE_RESET" != "1" ]; then
            echo "Error: reusable managed worktree already exists: $WORKTREE_DIR" >&2
            echo "Set CLAUDE_CODE_REUSE_WORKTREE_RESET=1 to reset and clean only this managed worktree before reuse." >&2
            echo "This never resets or cleans the source repository." >&2
            exit 1
        fi
        if ! git -C "$WORKTREE_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
            echo "Error: reusable path exists but is not a git worktree: $WORKTREE_DIR" >&2
            exit 1
        fi
        if [ "$CLAUDE_CODE_VERBOSE" = "1" ]; then
            echo "Reusing managed worktree: $WORKTREE_DIR"
        fi
        git -C "$WORKTREE_DIR" reset --hard >/dev/null
        git -C "$WORKTREE_DIR" clean -ffdx >/dev/null
        git -C "$WORKTREE_DIR" checkout -B "$branch_name" "$BASE_COMMIT" >/dev/null
        git -C "$WORKTREE_DIR" reset --hard "$BASE_COMMIT" >/dev/null
        git -C "$WORKTREE_DIR" clean -ffdx >/dev/null
        return
    fi

    git branch -D "$branch_name" >/dev/null 2>&1 || true
    git worktree add -b "$branch_name" "$WORKTREE_DIR" "$BASE_COMMIT" || {
        echo "Error: Failed to create reusable managed git worktree at $WORKTREE_DIR" >&2
        exit 1
    }
    if [ "$CLAUDE_CODE_VERBOSE" = "1" ]; then
        echo "Created reusable managed worktree: $WORKTREE_DIR"
    fi
}

if [ "$CLAUDE_CODE_WORKTREE_STRATEGY" = "reuse-managed" ]; then
    BRANCH_NAME="claude-managed-reuse"
elif [ -n "${AI_CODING_WORKFLOW_DAG_BRANCH_NAME:-}" ]; then
    # DAG mode: caller provides a collision-resistant branch name derived
    # from group_id + task_id + timestamp + random suffix.
    BRANCH_NAME="$AI_CODING_WORKFLOW_DAG_BRANCH_NAME"
else
    BRANCH_NAME="claude-task-${TIMESTAMP}-${RAND_SUFFIX}"
fi
_WORKTREE_SETUP_START="$(date +%s)"
create_dispatch_worktree "$BRANCH_NAME"

if [ "$CLAUDE_CODE_VERBOSE" = "1" ]; then
    echo "Worktree strategy: $CLAUDE_CODE_WORKTREE_STRATEGY"
    echo "Branch: $BRANCH_NAME"
fi

{
    echo "# Source Repository Status - ${TIMESTAMP}"
    echo "# Recorded after preflight checks and worktree creation"
    echo ""
    echo "## Worktree Strategy"
    echo ""
    echo "- Execution profile: ${CLAUDE_CODE_EXECUTION_PROFILE}"
    echo "- Strategy: ${CLAUDE_CODE_WORKTREE_STRATEGY}"
    echo "- Worktree: ${WORKTREE_DIR}"
    echo "- Base commit: ${BASE_COMMIT}"
    echo "- Reuse reset allowed: ${CLAUDE_CODE_REUSE_WORKTREE_RESET}"
    echo "- Large repo mode: ${CLAUDE_CODE_LARGE_REPO_MODE}"
    echo "- Claude task card view: ${CLAUDE_CODE_TASK_CARD_VIEW}"
    echo "- Claude prompt profile: ${CLAUDE_CODE_PROMPT_PROFILE}"
    echo "- Evidence mode: ${CLAUDE_CODE_EVIDENCE_MODE}"
    echo "- Checker broad discovery: ${CLAUDE_CODE_CHECKER_DISCOVER}"
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
    if [ "$CLAUDE_CODE_LARGE_REPO_MODE" = "1" ]; then
        echo "(skipped: CLAUDE_CODE_LARGE_REPO_MODE=1 avoids expensive untracked-file scans)"
    else
        UNTRACKED_SRC="$(git ls-files --others --exclude-standard 2>/dev/null || true)"
        if [ -z "$UNTRACKED_SRC" ]; then echo "(none)"; else echo "$UNTRACKED_SRC"; fi
    fi
} > "$SOURCE_STATUS_FILE"

if [ "$CLAUDE_CODE_VERBOSE" = "1" ]; then
    echo "Source status saved to: $SOURCE_STATUS_FILE"
fi

cp "$TASK_CARD" "${WORKTREE_DIR}/TASK_CARD.md"
cp "$TASK_CARD" "${WORKTREE_DIR}/TASK_CARD_FULL.md"

render_claude_task_card() {
    awk -v view="$CLAUDE_CODE_TASK_CARD_VIEW" '
    function section_name(line, s) {
        s = line
        sub(/^##[ \t]+/, "", s)
        sub(/[ \t]+$/, "", s)
        return s
    }
    function codex_only_section(name) {
        return name == "Execution Readiness Gate" \
            || name == "Control-Plane Exception Rationale" \
            || name == "Task Card Views" \
            || name == "Direction Review Gate" \
            || name == "Codex Context Budget" \
            || name == "High-Token Delegation Gate" \
            || name == "Delegation Continuity Gate"
    }
    function compact_skip_section(name) {
        return name == "Goal Loop Contract" \
            || name == "Advisor Gate" \
            || name == "Codex Spark Gate" \
            || name == "Parallel Execution Gate" \
            || name == "Worktree / Large Repo Strategy Gate" \
            || name == "Delegation Restoration Gate" \
            || name == "Spec Gate" \
            || name == "Root Cause Gate" \
            || name == "Test-First / TDD Contract" \
            || name == "Finish Branch Gate"
    }
    BEGIN {
        skip = 0
        print "<!-- Generated by dispatch-to-claude.sh from TASK_CARD_FULL.md. Codex-only planning and control-plane sections are omitted. -->"
        if (view == "compact") {
            print "<!-- Compact view: optional planning gates are omitted. TASK_CARD_FULL.md remains the audit source. -->"
        }
        print ""
    }
    /^##[ \t]+/ {
        name = section_name($0)
        if (codex_only_section(name) || (view == "compact" && compact_skip_section(name))) {
            skip = 1
            next
        }
        skip = 0
    }
    !skip { print }
    ' "$1"
}

render_claude_task_card "${WORKTREE_DIR}/TASK_CARD_FULL.md" > "${WORKTREE_DIR}/CLAUDE_TASK_CARD.md"

if [ "$CLAUDE_CODE_VERBOSE" = "1" ]; then
    echo "Full task card copied to: ${WORKTREE_DIR}/TASK_CARD_FULL.md"
    echo "Compatibility task card copied to: ${WORKTREE_DIR}/TASK_CARD.md"
    echo "Claude execution card rendered to: ${WORKTREE_DIR}/CLAUDE_TASK_CARD.md"
fi

{
    echo "<!-- ${SEEDED_PROGRESS_MARKER} -->"
    echo "# Claude Progress"
    echo ""
    echo "- Goal: Execute ${TASK_CARD}"
    echo "- Current Phase: dispatch-started"
    echo "- Next Check: read CLAUDE_TASK_CARD.md and update this file before exploration or edits"
    echo "- Blocker: none reported yet"
    echo "- Last Update: $(date '+%Y-%m-%d %H:%M:%S')"
    echo ""
    echo "## Milestones"
    echo ""
    echo "- [ ] Context gathered"
    echo "- [ ] Plan chosen"
    echo "- [ ] Assigned edits or checks completed"
    echo "- [ ] Task-card progress checklist updated"
    echo "- [ ] Final report updated"
    echo ""
    echo "Dispatcher created this starter progress file so observers have a baseline even if Claude exits before writing."
} > "${WORKTREE_DIR}/CLAUDE_PROGRESS.md"

{
    echo "<!-- ${SEEDED_REPORT_MARKER} -->"
    echo "# Claude Modification Report"
    echo ""
    echo "Dispatcher-created draft. Claude must remove the seeded-report marker above when it first updates this file."
    echo ""
    echo "## Task Card"
    echo "${WORKTREE_DIR}/CLAUDE_TASK_CARD.md"
    echo ""
    echo "Full Codex planning card: ${WORKTREE_DIR}/TASK_CARD_FULL.md"
    echo ""
    echo "## Current State"
    echo "Claude has not yet reported implementation progress."
} > "${WORKTREE_DIR}/CLAUDE_REPORT.md"

if [ "$CLAUDE_CODE_PROMPT_PROFILE" = "brief" ]; then
cat > "${WORKTREE_DIR}/CLAUDE_PROMPT.md" <<'EOF'
You are the executor in a Codex/Claude Code workflow.

Execute `CLAUDE_TASK_CARD.md`. `TASK_CARD_FULL.md` is retained for audit and may be consulted when the execution card is insufficient, but do not broaden scope beyond the execution card.

Core rules:
- Codex plans/reviews; Claude edits only the assigned scope.
- Update `CLAUDE_PROGRESS.md` before exploration or edits, at phase boundaries, before long commands, and when blocked.
- Remove dispatcher seeded markers when you first update `CLAUDE_PROGRESS.md` or `CLAUDE_REPORT.md`.
- If Direction / Boundary Acknowledgement is blocking, write it and stop for approval. If it is non-blocking and recommendation is `proceed`, continue implementation in the same run.
- Builder tasks implement and report direction. Do not add acceptance tests or broad validation unless explicitly assigned.
- Checker/Test tasks write/update assigned tests, run assigned validation when local validation is allowed, and avoid broad implementation rewrites.
- If one dispatch mixes implementation, test writing, broad validation, and phase stop gates without explicit `mixed-exception`, stop and recommend a split.
- If `Local validation allowed?` is `no`, do not run local validation; report exact commands only.
- If target, scope, testing responsibility, public API/data/security/migration impact, destructive actions, permissions, or production data are unclear, stop-and-report instead of guessing.
- Preserve failures, blockers, exact commands, exit codes, and key output. Do not include secrets, large logs, or full diffs in progress/report files.

`CLAUDE_REPORT.md` before finishing must include: requirements summary, files changed, acceptance criteria mapping, out-of-scope confirmation, plan match, validation confidence, reviewer should check, checks run/blocked, deviations, risks, open questions, and human review checklist.

--- CLAUDE EXECUTION CARD ---
EOF
else
cat > "${WORKTREE_DIR}/CLAUDE_PROMPT.md" <<'EOF'
You are the executor in a Codex/Claude Code workflow.

Execute the Claude execution card below. The full Codex planning card is preserved as `TASK_CARD_FULL.md` for audit, but `CLAUDE_TASK_CARD.md` is your execution contract. The dispatcher has already created starter `CLAUDE_PROGRESS.md` and `CLAUDE_REPORT.md` files in the worktree. Update them while working so the dispatcher can show user-visible progress without interrupting you.

`CLAUDE_PROGRESS.md` requirements:
- Update it before doing substantial exploration or edits.
- Remove the dispatcher seeded-progress marker when you first update this file.
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
- If `CLAUDE_TASK_CARD.md` has an `## Execution Progress` checklist, update the checklist after each completed assigned item. Do not edit `TASK_CARD_FULL.md`; it is Codex-owned audit context.


Phase-gate requirements:
- If the task card has an `## Execution Phases` table, follow it as the outer execution contract. You may break down work inside a phase, but do not silently combine phases.
- At each phase boundary, update `CLAUDE_PROGRESS.md` with the current phase, completed evidence, and the next intended action.
- Create or update `CLAUDE_REPORT.md` before running long validation commands, before waiting on potentially slow commands, and before moving to a later phase marked `Stop Before Next Phase? = yes`.
- If validation fails, hangs, or is blocked, stop after recording the exact command, observed output, and proposed next phase instead of continuing broad edits.

Unknowns and decision gates:
- If the task card has `## Execution Readiness Gate`, verify it against the repository before editing. If the task is not implementation-ready, stop after recording why an exploration/prototype task is needed.
- If the task card has `## Phase Responsibility Matrix`, read it before editing and obey the active phase owner/non-owner boundaries. If the matrix conflicts with Task Mode or Testing Responsibility, stop-and-report the conflict instead of guessing.
- If the task card has `## Direction / Boundary Acknowledgement`, complete it before editing when requested. State your understanding, planned scope, explicitly out-of-scope boundaries, likely files/modules, acceptance criteria interpretation, testing responsibility interpretation, confusions/ambiguities, risks, and recommendation.
- If Direction / Boundary Acknowledgement requires blocking Codex approval, write the acknowledgement to `CLAUDE_PROGRESS.md` or `CLAUDE_REPORT.md`, then stop until approval is recorded. Do not edit while waiting for approval.
- If Direction / Boundary Acknowledgement is non-blocking and your recommendation is `proceed`, continue implementation in the same run. Do not stop after acknowledgement unless you record a concrete blocker, stop condition, or explicit need for Codex approval.
- If target, boundaries, acceptance criteria, testing responsibility, public API impact, data model impact, security, migrations, permissions, production data, or destructive actions are unclear, stop-and-report instead of guessing.
- Do not create an acknowledgement loop. Perform at most one blocking acknowledgement per task or phase unless Codex materially changes the goal, scope, boundaries, or risk profile. After Codex records `proceed`, continue execution without asking for the same confirmation again; if Codex records `narrow`, `split`, or `stop`, follow that decision.
- If the task card has `## Unknowns`, perform the requested blindspot pass before implementation and record material findings in `CLAUDE_PROGRESS.md` or `CLAUDE_REPORT.md`.
- If the task card has `## Decision Gates`, obey the listed authority: autonomous decisions may proceed, conservative decisions must choose the least risky compatible path, and stop-and-report decisions must not be crossed silently.
- If the task card has `## Handoff Contract`, treat Must do / Must not do / May decide / Must report / Stop condition as the primary executor contract.
- If implementation reality conflicts with the plan, choose a conservative path when safe, record the deviation under `Deviations From Plan`, and continue only when the task card permits it.

Testing responsibility:
- First identify the task mode: builder, checker-test, mixed-exception, or control-plane.
- If one dispatch assigns implementation, test writing, broad validation, and phase stop gates without explicit `mixed-exception`, treat it as orchestration ambiguity. Stop after recommending a Builder task followed by a Checker/Test task instead of guessing which role to perform.
- Builder tasks implement and report direction. Do not add tests or run broad acceptance suites unless the task card explicitly lists a narrow sanity check.
- Checker/Test tasks write or update tests, run assigned validation, and produce a validation report. Do not perform broad implementation rewrites unless the task card permits a concrete small fix discovered by tests.
- If the task card has `## Testing Responsibility`, follow it exactly.
- Treat writing/updating test code and running test commands as separate responsibilities.
- Add or modify tests when the task card says tests are user-requested, acceptance-critical, or otherwise in scope.
- Do not add or modify tests when test code is out of scope.
- If Claude is assigned to run tests and local validation is allowed, run the listed validation commands or report why they are blocked.
- If `Local validation allowed?` is `no`, do not run local validation; provide the exact commands only for Codex/human/CI to run.
- If Codex/human is assigned to run verification after Claude, finish with implementation evidence and clear commands for that reviewer to run.

Wait policy requirements:
- If the task card has an `## Wait Policy` table, treat it as the observer contract for how long Codex/humans should give you before reviewing or interrupting.
- If the task card has `## Stall / Ambiguity Triage`, use it to classify stalls before stopping: task-card ambiguity, mixed-role assignment, dirty source/stale HEAD, permission/tool approval blocker, long-running validation, missing progress updates, external environment, or true no-progress.
- If a command, file, network call, authentication check, sandbox write, forbidden file, or approval requirement blocks progress, record the exact blocker in `CLAUDE_PROGRESS.md` and `CLAUDE_REPORT.md` and stop instead of waiting silently.
- Keep `CLAUDE_PROGRESS.md` fresh enough that quiet time reflects real tool/model waiting, not missing progress notes.
- When partial implementation exists but validation is still running or blocked, update `CLAUDE_REPORT.md` with enough file-level summary for Codex to compare the partial diff against the plan.

In addition to making the requested edits, update `CLAUDE_REPORT.md` in the worktree before finishing. Remove the dispatcher seeded-report marker when you first update the report.

Checker expectations:
- Run project validation before finishing only when this task mode assigns validation and local validation is allowed. If `ai/check-worktree.sh` is available and assigned exact commands, prefer `bash ai/check-worktree.sh --task-card CLAUDE_TASK_CARD.md --no-discover --command 'label=command'` so broad unrelated checks do not create noise.
- If `Local validation allowed?` is `no`, do not run local validation; report the commands only.
- Preserve failed command, exit code, key original output, and file:line details.
- Do not weaken, delete, skip, or rewrite checks just to get a green result.
- If a validation blocker is environmental or external, stop and record the blocker instead of guessing.

`CLAUDE_REPORT.md` must include:
- Task card ID/path and a concise requirements summary.
- Files changed with one-line purpose per file.
- Acceptance criteria mapping: met / not met / partial.
- Out-of-scope confirmation.
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

--- CLAUDE EXECUTION CARD ---
EOF
fi
cat "${WORKTREE_DIR}/CLAUDE_TASK_CARD.md" >> "${WORKTREE_DIR}/CLAUDE_PROMPT.md"

CLAUDE_CODE_TIMEOUT_SECONDS="${CLAUDE_CODE_TIMEOUT_SECONDS:-600}"
CLAUDE_CODE_HEARTBEAT_SECONDS="${CLAUDE_CODE_HEARTBEAT_SECONDS:-30}"
CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS="${CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS:-0}"

PYTHON_CMD=""
if command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
elif command -v python &>/dev/null; then
    PYTHON_CMD="python"
fi

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

redact_network_value() {
    local value="$1"
    if [ -z "$value" ]; then
        echo "(unset)"
    else
        printf '%s\n' "$value" | sed -E 's#(https?://)[^/@]+@#\1***@#'
    fi
}

network_log() {
    if [ "$CLAUDE_CODE_NETWORK_MONITOR" != "1" ]; then
        return
    fi
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" >> "$NETWORK_FILE"
}

network_socket_output() {
    local pid="$1"
    local pids="$pid"
    if command -v pgrep >/dev/null 2>&1; then
        local parent children
        for parent in $pids; do
            children="$(pgrep -P "$parent" 2>/dev/null || true)"
            if [ -n "$children" ]; then
                pids="${pids} ${children}"
            fi
        done
        for parent in $pids; do
            children="$(pgrep -P "$parent" 2>/dev/null || true)"
            if [ -n "$children" ]; then
                pids="${pids} ${children}"
            fi
        done
    fi
    pids="$(printf '%s\n' $pids | sed '/^$/d' | sort -n | uniq | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
    if command -v lsof >/dev/null 2>&1; then
        lsof -Pan -p "$(printf '%s' "$pids" | tr ' ' ',')" -iTCP -iUDP 2>/dev/null || true
        return
    fi
    local pid_pattern
    pid_pattern="$(printf '%s' "$pids" | sed 's/[[:space:]][[:space:]]*/|/g')"
    if command -v ss >/dev/null 2>&1; then
        ss -tanp 2>/dev/null | grep -E "pid=(${pid_pattern})," || true
        return
    fi
    if command -v netstat >/dev/null 2>&1; then
        netstat -tanp 2>/dev/null | grep -E "(${pid_pattern})/" || true
        return
    fi
}

network_summary_from_output() {
    local output="$1"
    if [ -z "$output" ]; then
        if command -v lsof >/dev/null 2>&1 || command -v ss >/dev/null 2>&1 || command -v netstat >/dev/null 2>&1; then
            echo "sockets=0 established=0 syn_sent=0 close_wait=0"
        else
            echo "network_tools=unavailable"
        fi
        return
    fi
    local sockets established syn_sent close_wait
    sockets="$(printf '%s\n' "$output" | sed '/^$/d' | wc -l 2>/dev/null | tr -d '[:space:]')"
    established="$(printf '%s\n' "$output" | grep -Eic 'ESTAB|ESTABLISHED' || true)"
    syn_sent="$(printf '%s\n' "$output" | grep -Eic 'SYN-SENT|SYN_SENT' || true)"
    close_wait="$(printf '%s\n' "$output" | grep -Eic 'CLOSE-WAIT|CLOSE_WAIT' || true)"
    echo "sockets=${sockets:-0} established=${established:-0} syn_sent=${syn_sent:-0} close_wait=${close_wait:-0}"
}

write_network_header() {
    if [ "$CLAUDE_CODE_NETWORK_MONITOR" != "1" ]; then
        : > "$NETWORK_FILE"
        return
    fi
    {
        echo "# Claude Network Diagnostics - ${TIMESTAMP}"
        echo ""
        echo "Network monitoring is metadata-only. It records process socket state and optional healthcheck status, not packet contents, request bodies, prompts, or tokens."
        echo ""
        echo "## Configuration"
        echo ""
        echo "- CLAUDE_CODE_NETWORK_MONITOR: ${CLAUDE_CODE_NETWORK_MONITOR}"
        echo "- CLAUDE_CODE_NETWORK_HEALTHCHECK_URL: $(redact_network_value "$CLAUDE_CODE_NETWORK_HEALTHCHECK_URL")"
        echo "- CLAUDE_CODE_NETWORK_HEALTHCHECK_TIMEOUT_SECONDS: ${CLAUDE_CODE_NETWORK_HEALTHCHECK_TIMEOUT_SECONDS}"
        echo "- CLAUDE_CODE_PROXY_MODE: ${CLAUDE_CODE_PROXY_MODE}"
        echo "- HTTP_PROXY: $(redact_network_value "${HTTP_PROXY:-}")"
        echo "- HTTPS_PROXY: $(redact_network_value "${HTTPS_PROXY:-}")"
        echo "- ALL_PROXY: $(redact_network_value "${ALL_PROXY:-}")"
        echo "- NO_PROXY: $(redact_network_value "${NO_PROXY:-}")"
        echo "- http_proxy: $(redact_network_value "${http_proxy:-}")"
        echo "- https_proxy: $(redact_network_value "${https_proxy:-}")"
        echo "- all_proxy: $(redact_network_value "${all_proxy:-}")"
        echo "- no_proxy: $(redact_network_value "${no_proxy:-}")"
        if [ "$CLAUDE_CODE_PROXY_MODE" = "direct" ]; then
            echo "- Effective Claude proxy environment: proxy variables unset inside Claude subprocess"
        else
            echo "- Effective Claude proxy environment: inherited from dispatcher environment"
        fi
        echo ""
        echo "## Tool Availability"
        echo ""
        for tool in lsof ss netstat curl; do
            if command -v "$tool" >/dev/null 2>&1; then
                echo "- ${tool}: available"
            else
                echo "- ${tool}: missing"
            fi
        done
        echo ""
        echo "## Healthcheck"
        echo ""
    } > "$NETWORK_FILE"

    if [ -n "$CLAUDE_CODE_NETWORK_HEALTHCHECK_URL" ]; then
        if command -v curl >/dev/null 2>&1; then
            {
                echo "- Command: curl -I --max-time ${CLAUDE_CODE_NETWORK_HEALTHCHECK_TIMEOUT_SECONDS} <redacted-url>"
                set +e
                curl -I --max-time "$CLAUDE_CODE_NETWORK_HEALTHCHECK_TIMEOUT_SECONDS" "$CLAUDE_CODE_NETWORK_HEALTHCHECK_URL" 2>&1 | tail -20
                rc=$?
                set -e
                echo "- Exit code: ${rc}"
            } >> "$NETWORK_FILE"
        else
            echo "- Skipped: curl is not available." >> "$NETWORK_FILE"
        fi
    else
        echo "- Skipped: CLAUDE_CODE_NETWORK_HEALTHCHECK_URL is unset." >> "$NETWORK_FILE"
    fi
    {
        echo ""
        echo "## Socket Snapshots"
        echo ""
    } >> "$NETWORK_FILE"
}

capture_network_snapshot() {
    local pid="$1"
    local elapsed="$2"
    local quiet="$3"
    if [ "$CLAUDE_CODE_NETWORK_MONITOR" != "1" ]; then
        echo "network_monitor=off"
        return
    fi
    local output summary
    output="$(network_socket_output "$pid")"
    summary="$(network_summary_from_output "$output")"
    {
        echo "### $(date '+%Y-%m-%d %H:%M:%S') pid=${pid} elapsed_seconds=${elapsed} quiet_seconds=${quiet}"
        echo ""
        echo "Summary: ${summary}"
        echo ""
        if [ -z "$output" ]; then
            echo "(no matching socket rows)"
        else
            printf '%s\n' "$output"
        fi
        echo ""
    } >> "$NETWORK_FILE"
    echo "$summary"
}

file_size() {
    local file="$1"
    if [ -f "$file" ]; then
        wc -c < "$file" 2>/dev/null | tr -d ' ' || echo 0
    else
        echo 0
    fi
}

file_contains() {
    local file="$1"
    local pattern="$2"
    [ -f "$file" ] && grep -qE "$pattern" "$file" 2>/dev/null
}

valid_claude_report_file() {
    local file="$1"
    [ -s "$file" ] || return 1
    if file_contains "$file" "$SEEDED_REPORT_MARKER|$FALLBACK_REPORT_MARKER"; then
        return 1
    fi
    if file_contains "$file" "Dispatcher-created draft|fallback report was generated|did not produce a Claude-owned CLAUDE_REPORT.md"; then
        return 1
    fi
    return 0
}

acknowledgement_only_evidence() {
    local progress_file="$1"
    local report_file="$2"
    local changes="$3"
    local valid_report="$4"
    [ "$changes" -eq 0 ] || return 1
    [ "$valid_report" -eq 0 ] || return 1
    {
        [ -f "$progress_file" ] && cat "$progress_file"
        [ -f "$report_file" ] && cat "$report_file"
    } | grep -Eiq 'Direction / Boundary Acknowledgement|My understanding:|Planned scope:|Recommendation:[[:space:]]*(proceed|narrow|split|stop-and-report|stop)' 2>/dev/null
}

classify_dispatch_evidence() {
    local changes="$1"
    local valid_report="$2"
    local progress_file="$3"
    local report_file="$4"

    if [ "$changes" -gt 0 ] && [ "$valid_report" -eq 1 ]; then
        echo "diff + valid report"
    elif [ "$changes" -gt 0 ]; then
        echo "diff without report"
    elif acknowledgement_only_evidence "$progress_file" "$report_file" "$changes" "$valid_report"; then
        echo "acknowledgement only"
    elif [ -f "$report_file" ] && file_contains "$report_file" "$SEEDED_REPORT_MARKER"; then
        echo "seeded report only"
    elif [ "$valid_report" -eq 1 ]; then
        echo "valid report without diff"
    else
        echo "no valid report"
    fi
}

worktree_change_count() {
    if [ "${CLAUDE_CODE_LARGE_REPO_MODE:-0}" = "1" ]; then
        git status --porcelain --untracked-files=no 2>/dev/null | wc -l 2>/dev/null | tr -d '[:space:]' || echo 0
        return
    fi
    {
        git status --porcelain --untracked-files=all 2>/dev/null \
            | grep -v -E '^(.. )?(TASK_CARD|TASK_CARD_FULL|CLAUDE_TASK_CARD|CLAUDE_PROMPT|CLAUDE_REPORT|CLAUDE_PROGRESS)(\.md)?$' || true
    } | wc -l 2>/dev/null | tr -d '[:space:]' || echo 0
}

worktree_digest() {
    if [ "${CLAUDE_CODE_LARGE_REPO_MODE:-0}" = "1" ]; then
        {
            git status --porcelain --untracked-files=no 2>/dev/null || true
            git diff --shortstat 2>/dev/null || true
            git diff --cached --shortstat 2>/dev/null || true
        } | sha1sum 2>/dev/null | awk '{print $1}' || true
        return
    fi
    {
        git status --porcelain --untracked-files=all 2>/dev/null \
            | grep -v -E '^(.. )?(TASK_CARD|TASK_CARD_FULL|CLAUDE_TASK_CARD|CLAUDE_PROMPT|CLAUDE_REPORT|CLAUDE_PROGRESS)(\.md)?$' || true
        git diff --shortstat 2>/dev/null || true
        git diff --cached --shortstat 2>/dev/null || true
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

_WORKTREE_SETUP_END="$(date +%s)"
_WORKTREE_SETUP_DURATION=$((_WORKTREE_SETUP_END - _WORKTREE_SETUP_START))
echo "Worktree ready (${CLAUDE_CODE_WORKTREE_STRATEGY}, ${_WORKTREE_SETUP_DURATION}s): $WORKTREE_DIR"

if [ "$CLAUDE_CODE_VERBOSE" = "1" ]; then
    echo "Invoking Claude Code..."
    echo "Progress log: $PROGRESS_FILE"
    echo "Watch Progress: bash \"$WATCH_SCRIPT\" \"$TASK_ID\""
    echo "Watch Details:  bash \"$WATCH_SCRIPT\" \"$TASK_ID\" --details"
fi
cd "$WORKTREE_DIR"

: > "$PROGRESS_FILE"
write_network_header
progress_log "Starting Claude Code: execution_profile=${CLAUDE_CODE_EXECUTION_PROFILE}, prompt_profile=${CLAUDE_CODE_PROMPT_PROFILE}, evidence_mode=${CLAUDE_CODE_EVIDENCE_MODE}, proxy_mode=${CLAUDE_CODE_PROXY_MODE}, timeout_seconds=${CLAUDE_CODE_TIMEOUT_SECONDS}, heartbeat_seconds=${CLAUDE_CODE_HEARTBEAT_SECONDS}, no_output_timeout_seconds=${CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS}, network_monitor=${CLAUDE_CODE_NETWORK_MONITOR}, worktree_strategy=${CLAUDE_CODE_WORKTREE_STRATEGY}, large_repo_mode=${CLAUDE_CODE_LARGE_REPO_MODE}, task_mode=${_PARSED_TASK_MODE:-unknown}, verbose=${CLAUDE_CODE_VERBOSE}, approval_convergence=${CLAUDE_CODE_APPROVAL_BLOCKED_CONVERGENCE}"

set +e
run_claude &
CLAUDE_PID=$!
echo "$CLAUDE_PID" > "$PID_FILE"
progress_log "Claude process started: pid=${CLAUDE_PID}"

START_EPOCH="$(date +%s)"
CLAUDE_TIMED_OUT=0
CLAUDE_NO_OUTPUT_TIMED_OUT=0
CLAUDE_APPROVAL_CONVERGED=0
_APPROVAL_CONVERGENCE_COUNT=0
_LAST_APPROVAL_FP=""
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
    CLAUDE_TASK_BYTES="$(file_size "${WORKTREE_DIR}/CLAUDE_TASK_CARD.md")"
    WORKTREE_CHANGES="$(worktree_change_count)"
    CURRENT_WORKTREE_DIGEST="$(worktree_digest)"
    TOTAL_BYTES=$((RESULT_BYTES + STATUS_BYTES + REPORT_BYTES + CLAUDE_PROGRESS_BYTES + CLAUDE_TASK_BYTES))
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
    NETWORK_SUMMARY="$(capture_network_snapshot "$CLAUDE_PID" "$ELAPSED" "$QUIET_SECONDS")"
    progress_log "Claude still running: pid=${CLAUDE_PID}, elapsed_seconds=${ELAPSED}, quiet_seconds=${QUIET_SECONDS}, result_bytes=${RESULT_BYTES}, status_bytes=${STATUS_BYTES}, report_bytes=${REPORT_BYTES}, claude_progress_bytes=${CLAUDE_PROGRESS_BYTES}, claude_task_bytes=${CLAUDE_TASK_BYTES}, worktree_changes=${WORKTREE_CHANGES}, worktree_changed=${WORKTREE_CHANGED}, ${NETWORK_SUMMARY}"

    # --- Spec item 2: approval-blocked early convergence ---
    # End Claude early when: checker-test mode, valid non-seeded report,
    # approval/permission blocker recorded, and state stable for two heartbeats.
    if [ "${CLAUDE_CODE_APPROVAL_BLOCKED_CONVERGENCE:-1}" = "1" ] && \
       [ "$_PARSED_TASK_MODE" = "checker-test" ]; then
        _ABC_REPORT_VALID=0
        if valid_claude_report_file "${WORKTREE_DIR}/CLAUDE_REPORT.md"; then
            _ABC_REPORT_VALID=1
        fi

        _ABC_BLOCKER=0
        if [ "$_ABC_REPORT_VALID" -eq 1 ]; then
            if grep -qiE \
                'permission.*(block|denied|requir)|approval.*(requir|block|wait)|waiting.*(approv|permiss)|sandbox.*(block|deni)|blocked.*(permission|approval)' \
                "$STATUS_FILE" "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" 2>/dev/null; then
                _ABC_BLOCKER=1
            fi
        fi

        if [ "$_ABC_REPORT_VALID" -eq 1 ] && [ "$_ABC_BLOCKER" -eq 1 ]; then
            _REPORT_HASH="$(sha1sum "${WORKTREE_DIR}/CLAUDE_REPORT.md" 2>/dev/null | awk '{print $1}' || true)"
            _PROGRESS_HASH="$(sha1sum "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" 2>/dev/null | awk '{print $1}' || true)"
            _ABC_FP="$(printf '%s:%s:%s:%s' \
                "$_ABC_REPORT_VALID" "$WORKTREE_CHANGES" "$_REPORT_HASH" "$_PROGRESS_HASH" \
                | sha1sum | awk '{print $1}')"

            if [ "$_ABC_FP" = "$_LAST_APPROVAL_FP" ]; then
                _APPROVAL_CONVERGENCE_COUNT=$((_APPROVAL_CONVERGENCE_COUNT + 1))
                if [ "$_APPROVAL_CONVERGENCE_COUNT" -ge 2 ]; then
                    progress_log "Approval-blocked early convergence: stable for ${_APPROVAL_CONVERGENCE_COUNT} heartbeats after ${ELAPSED}s"
                    stop_claude "approval-blocked early convergence" "$ELAPSED"
                    CLAUDE_APPROVAL_CONVERGED=1
                    break
                fi
            else
                _APPROVAL_CONVERGENCE_COUNT=1
                _LAST_APPROVAL_FP="$_ABC_FP"
            fi
        else
            _APPROVAL_CONVERGENCE_COUNT=0
            _LAST_APPROVAL_FP=""
        fi
    fi

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
progress_log "Claude subprocess ended; dispatcher finalizing artifacts: pid=${CLAUDE_PID}, wait_status=${CLAUDE_STATUS}, elapsed_seconds=${ELAPSED}"
FINAL_NETWORK_SUMMARY="$(capture_network_snapshot "$CLAUDE_PID" "$ELAPSED" 0)"
progress_log "Final network snapshot: ${FINAL_NETWORK_SUMMARY}"
if [ "${CLAUDE_APPROVAL_CONVERGED:-0}" -eq 1 ]; then
    {
        echo ""
        echo "[dispatch] Claude stopped for approval-blocked early convergence after ${ELAPSED}s."
        echo "[dispatch] Convergence type: approval_blocked_early_convergence"
        echo "[dispatch] Task mode: checker-test"
        echo "[dispatch] Progress log: ${PROGRESS_FILE}"
    } >> "$STATUS_FILE"
    progress_log "Claude finished by approval-blocked early convergence: elapsed_seconds=${ELAPSED}, wait_status=${CLAUDE_STATUS}"
elif [ "$CLAUDE_NO_OUTPUT_TIMED_OUT" -eq 1 ]; then
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

RESULT_FALLBACK_GENERATED=0
ensure_result_json() {
    local reason="$1"
    local valid=0
    if [ -s "$RESULT_FILE" ] && [ -n "$PYTHON_CMD" ]; then
        if "$PYTHON_CMD" - "$RESULT_FILE" >/dev/null 2>&1 <<'PYEOF'
import json
import sys
with open(sys.argv[1], "r", encoding="utf-8") as f:
    json.load(f)
PYEOF
        then
            valid=1
        fi
    elif [ -s "$RESULT_FILE" ] && [ -z "$PYTHON_CMD" ]; then
        valid=1
    fi

    if [ "$valid" -eq 1 ]; then
        return 0
    fi

    RESULT_FALLBACK_GENERATED=1
    if [ -s "$RESULT_FILE" ]; then
        cp "$RESULT_FILE" "$RAW_RESULT_FILE" 2>/dev/null || true
    else
        : > "$RAW_RESULT_FILE"
    fi

    if [ -n "$PYTHON_CMD" ]; then
        "$PYTHON_CMD" - "$RESULT_FILE" "$RAW_RESULT_FILE" "$STATUS_FILE" "$PROGRESS_FILE" "$REPORT_FILE" \
            "$CLAUDE_STATUS" "$CLAUDE_TIMED_OUT" "$CLAUDE_NO_OUTPUT_TIMED_OUT" "$ELAPSED" "$reason" \
            "${CLAUDE_APPROVAL_CONVERGED:-0}" <<'PYEOF'
import json
import sys
from pathlib import Path

(
    result_file,
    raw_result_file,
    status_file,
    progress_file,
    report_file,
    status,
    timed_out,
    no_output_timed_out,
    elapsed,
    reason,
    approval_converged,
) = sys.argv[1:12]

payload = {
    "type": "claude_dispatch_fallback",
    "fallback": True,
    "reason": reason,
    "claude_exit_status": int(status),
    "timed_out": timed_out == "1",
    "no_output_timed_out": no_output_timed_out == "1",
    "approval_blocked_early_convergence": approval_converged == "1",
    "elapsed_seconds": int(elapsed),
    "raw_result_file": raw_result_file,
    "status_file": status_file,
    "progress_file": progress_file,
    "report_file": report_file,
    "message": "Claude exited without valid JSON result output; dispatcher generated this fallback result.",
}
Path(result_file).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PYEOF
    else
        {
            echo "{"
            echo '  "type": "claude_dispatch_fallback",'
            echo '  "fallback": true,'
            echo "  \"reason\": \"${reason}\","
            echo "  \"claude_exit_status\": ${CLAUDE_STATUS},"
            echo "  \"timed_out\": $([ "$CLAUDE_TIMED_OUT" -eq 1 ] && echo true || echo false),"
            echo "  \"no_output_timed_out\": $([ "$CLAUDE_NO_OUTPUT_TIMED_OUT" -eq 1 ] && echo true || echo false),"
            echo "  \"approval_blocked_early_convergence\": $([ "${CLAUDE_APPROVAL_CONVERGED:-0}" -eq 1 ] && echo true || echo false),"
            echo "  \"elapsed_seconds\": ${ELAPSED},"
            echo '  "message": "Claude exited without valid JSON result output; dispatcher generated this fallback result."'
            echo "}"
        } > "$RESULT_FILE"
    fi
    progress_log "Generated fallback result JSON: reason=${reason}, raw_result=${RAW_RESULT_FILE}"
}

ensure_result_json "missing_or_invalid_result_json"

cd "$WORKTREE_DIR"

CHECK_SCRIPT="${SCRIPT_DIR}/check-worktree.sh"
if [ -f "$CHECK_SCRIPT" ]; then
    progress_log "Starting checker helper: ${CHECK_SCRIPT}"
    CHECK_ARGS=(--report "$CHECKER_REPORT_FILE" --logs-dir "$CHECKER_LOGS_DIR" --task-card "${WORKTREE_DIR}/CLAUDE_TASK_CARD.md")
    if [ "$CLAUDE_CODE_CHECKER_DISCOVER" = "1" ]; then
        CHECK_ARGS+=(--discover)
    else
        CHECK_ARGS+=(--no-discover)
    fi
    if [ -n "$CLAUDE_CODE_CHECKER_COMMANDS" ]; then
        while IFS= read -r checker_command; do
            [ -z "$checker_command" ] && continue
            CHECK_ARGS+=(--command "$checker_command")
        done <<EOF_CHECKER_COMMANDS
$CLAUDE_CODE_CHECKER_COMMANDS
EOF_CHECKER_COMMANDS
    fi
    set +e
    bash "$CHECK_SCRIPT" "${CHECK_ARGS[@]}" >> "$STATUS_FILE" 2>&1
    CHECKER_STATUS=$?
    set -e
    if [ "$CHECKER_STATUS" -eq 0 ]; then
        if grep -Eq '^SKIPPED by policy$|^SKIPPED$|Local validation is disabled by the task card' "$CHECKER_REPORT_FILE" 2>/dev/null; then
            progress_log "Checker helper completed: artifact collection OK; validation skipped by policy"
        elif grep -Eq '^ALL GREEN$' "$CHECKER_REPORT_FILE" 2>/dev/null; then
            progress_log "Checker helper completed: artifact collection OK; validation ALL GREEN"
        else
            progress_log "Checker helper completed: artifact collection OK; validation status unknown"
        fi
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

if [ "$CLAUDE_CODE_LARGE_REPO_MODE" = "1" ]; then
    FILTERED_UNTRACKED=""
    FILTERED_UNTRACKED_SKIPPED=1
else
    FILTERED_UNTRACKED="$(git ls-files --others --exclude-standard 2>/dev/null \
        | grep -v -E '^(TASK_CARD|TASK_CARD_FULL|CLAUDE_TASK_CARD|CLAUDE_PROMPT|CLAUDE_REPORT|CLAUDE_PROGRESS)' || true)"
    FILTERED_UNTRACKED_SKIPPED=0
fi

write_untracked_patches() {
    echo "$FILTERED_UNTRACKED" | while IFS= read -r uf; do
        [ -z "$uf" ] && continue
        if [ -f "$uf" ] && [ -r "$uf" ]; then
            echo ""
            echo "### Untracked File: $uf"
            ret=0; git diff --no-index -- /dev/null "$uf" 2>/dev/null || ret=$?
            if [ "$ret" -ne 0 ] && [ "$ret" -ne 1 ]; then
                echo "(diff unavailable for $uf)"
            fi
        fi
    done
}

{
    echo "# Diffstat - ${TIMESTAMP}"
    echo ""
    echo "## Unstaged Changes"
    DIFF_OUT="$(git diff --stat 2>/dev/null || true)"
    if [ -z "$DIFF_OUT" ]; then echo "(none)"; else echo "$DIFF_OUT"; fi
    echo ""
    echo "## Staged Changes"
    CACHED_OUT="$(git diff --cached --stat 2>/dev/null || true)"
    if [ -z "$CACHED_OUT" ]; then echo "(none)"; else echo "$CACHED_OUT"; fi
    echo ""
    echo "## Untracked Files"
    if [ "$FILTERED_UNTRACKED_SKIPPED" -eq 1 ]; then
        echo "(skipped: CLAUDE_CODE_LARGE_REPO_MODE=1 avoids expensive untracked-file scans)"
    elif [ -z "$FILTERED_UNTRACKED" ]; then echo "(none)"; else echo "$FILTERED_UNTRACKED"; fi
} > "$DIFFSTAT_FILE"

{
    echo "# Combined Diff - ${TIMESTAMP}"
    echo ""
    if [ "$CLAUDE_CODE_EVIDENCE_MODE" = "summary" ]; then
        echo "Evidence mode: summary"
        echo ""
        echo "Full patch generation was skipped to reduce large-repository I/O and review-token cost."
        echo "Review the implementation in the preserved worktree when patch-level evidence is needed:"
        echo "$WORKTREE_DIR"
        echo ""
        echo "## Unstaged Name Status"
        NAME_STATUS="$(git diff --name-status 2>/dev/null || true)"
        if [ -z "$NAME_STATUS" ]; then echo "(none)"; else echo "$NAME_STATUS"; fi
        echo ""
        echo "## Staged Name Status"
        CACHED_NAME_STATUS="$(git diff --cached --name-status 2>/dev/null || true)"
        if [ -z "$CACHED_NAME_STATUS" ]; then echo "(none)"; else echo "$CACHED_NAME_STATUS"; fi
        echo ""
        echo "## Untracked Files"
        if [ "$FILTERED_UNTRACKED_SKIPPED" -eq 1 ]; then
            echo "(skipped: CLAUDE_CODE_LARGE_REPO_MODE=1 avoids expensive untracked-file scans)"
        elif [ -z "$FILTERED_UNTRACKED" ]; then
            echo "(none)"
        else
            echo "$FILTERED_UNTRACKED"
        fi
    else
        echo "## Unstaged Diff"
        UNSTAGED_DIFF="$(git diff 2>/dev/null || true)"
        if [ -z "$UNSTAGED_DIFF" ]; then echo "(none)"; else echo "$UNSTAGED_DIFF"; fi
        echo ""
        echo "## Staged Diff"
        STAGED_DIFF="$(git diff --cached 2>/dev/null || true)"
        if [ -z "$STAGED_DIFF" ]; then echo "(none)"; else echo "$STAGED_DIFF"; fi
        echo ""
        echo "## Untracked File Patches"
        if [ "$FILTERED_UNTRACKED_SKIPPED" -eq 1 ]; then
            echo "(skipped: CLAUDE_CODE_LARGE_REPO_MODE=1 avoids expensive untracked-file patch generation)"
        elif [ -z "$FILTERED_UNTRACKED" ]; then
            echo "(none)"
        else
            write_untracked_patches
        fi
    fi
} > "$DIFF_FILE"

{
    echo "# Untracked Files in Worktree - ${TIMESTAMP}"
    echo ""
    if [ "$FILTERED_UNTRACKED_SKIPPED" -eq 1 ]; then
        echo "(skipped: CLAUDE_CODE_LARGE_REPO_MODE=1 avoids expensive untracked-file scans)"
    elif [ -z "$FILTERED_UNTRACKED" ]; then
        echo "(none)"
    elif [ "$CLAUDE_CODE_EVIDENCE_MODE" = "summary" ]; then
        echo "$FILTERED_UNTRACKED"
        echo ""
        echo "--- Patch Evidence ---"
        echo "(skipped: CLAUDE_CODE_EVIDENCE_MODE=summary avoids untracked-file patch generation)"
    else
        echo "$FILTERED_UNTRACKED"
        echo ""
        echo "--- Patch Evidence (binary-safe) ---"
        write_untracked_patches
    fi
} > "$UNTRACKED_FILE"

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
    if [ "$FILTERED_UNTRACKED_SKIPPED" -eq 1 ]; then
        echo "(skipped: CLAUDE_CODE_LARGE_REPO_MODE=1 avoids expensive untracked-file scans)"
    elif [ -z "$FILTERED_UNTRACKED" ]; then echo "(none)"; else echo "$FILTERED_UNTRACKED"; fi
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

IMPLEMENTATION_CHANGES="$(worktree_change_count)"
VALID_CLAUDE_REPORT=0
if valid_claude_report_file "${WORKTREE_DIR}/CLAUDE_REPORT.md"; then
    VALID_CLAUDE_REPORT=1
fi
DISPATCH_EVIDENCE_STATE="$(classify_dispatch_evidence "$IMPLEMENTATION_CHANGES" "$VALID_CLAUDE_REPORT" "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" "${WORKTREE_DIR}/CLAUDE_REPORT.md")"
progress_log "Dispatch evidence classification: state=${DISPATCH_EVIDENCE_STATE}, implementation_changes=${IMPLEMENTATION_CHANGES}, valid_claude_report=$([ "$VALID_CLAUDE_REPORT" -eq 1 ] && echo yes || echo no)"
{
    echo ""
    echo "[dispatch] Evidence classification: ${DISPATCH_EVIDENCE_STATE}"
    echo "[dispatch] Implementation changes: ${IMPLEMENTATION_CHANGES}"
    echo "[dispatch] Valid Claude-owned report: $([ "$VALID_CLAUDE_REPORT" -eq 1 ] && echo yes || echo no)"
} >> "$STATUS_FILE"

if [ "$VALID_CLAUDE_REPORT" -eq 1 ]; then
    cp "${WORKTREE_DIR}/CLAUDE_REPORT.md" "$REPORT_FILE"
else
    {
        echo "<!-- ${FALLBACK_REPORT_MARKER} -->"
        echo "# Claude Modification Report"
        echo ""
        echo "## Task Card"
        echo "$TASK_CARD"
        echo ""
        echo "- Full task card artifact: ${WORKTREE_DIR}/TASK_CARD_FULL.md"
        echo "- Claude execution card artifact: ${WORKTREE_DIR}/CLAUDE_TASK_CARD.md"
        echo ""
        echo "## Requirements Summary"
        echo "Claude did not produce a valid Claude-owned CLAUDE_REPORT.md; this fallback report was generated from workflow artifacts."
        echo ""
        echo "This fallback report is not a valid Claude report."
        echo ""
        echo "## Dispatch Outcome"
        echo ""
        echo "- Evidence classification: ${DISPATCH_EVIDENCE_STATE}"
        echo "- Implementation changes: ${IMPLEMENTATION_CHANGES}"
        echo "- Valid Claude-owned report: no"
        echo "- Claude exit status: ${CLAUDE_STATUS}"
        echo "- Elapsed seconds: ${ELAPSED}"
        echo "- Runtime timed out: $([ "$CLAUDE_TIMED_OUT" -eq 1 ] && echo yes || echo no)"
        echo "- No-output timed out: $([ "$CLAUDE_NO_OUTPUT_TIMED_OUT" -eq 1 ] && echo yes || echo no)"
        echo "- Approval-blocked early convergence: $([ "${CLAUDE_APPROVAL_CONVERGED:-0}" -eq 1 ] && echo yes || echo no)"
        echo "- Fallback result generated: $([ "$RESULT_FALLBACK_GENERATED" -eq 1 ] && echo yes || echo no)"
        echo "- Raw result artifact: $RAW_RESULT_FILE"
        echo ""
        echo "## Changed Files"
        cat "$DIFFSTAT_FILE"
        echo ""
        echo "## Artifact Links"
        echo "- Result JSON: $RESULT_FILE"
        echo "- Full task card: ${WORKTREE_DIR}/TASK_CARD_FULL.md"
        echo "- Claude execution card: ${WORKTREE_DIR}/CLAUDE_TASK_CARD.md"
        echo "- Status log: $STATUS_FILE"
        echo "- Network log: $NETWORK_FILE"
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
echo "Execution Profile: $CLAUDE_CODE_EXECUTION_PROFILE"
echo "Worktree Strategy: $CLAUDE_CODE_WORKTREE_STRATEGY"
echo "Large Repo Mode: $CLAUDE_CODE_LARGE_REPO_MODE"
echo "Prompt Profile:  $CLAUDE_CODE_PROMPT_PROFILE"
echo "Evidence Mode:   $CLAUDE_CODE_EVIDENCE_MODE"
echo "Task Card Full:  ${WORKTREE_DIR}/TASK_CARD_FULL.md"
echo "Claude Task:     ${WORKTREE_DIR}/CLAUDE_TASK_CARD.md"
echo "Result:          $RESULT_FILE"
echo "Raw Result:      $RAW_RESULT_FILE"
echo "Status:          $STATUS_FILE"
echo "Network Log:     $NETWORK_FILE"
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
if [ "$CLAUDE_CODE_WORKTREE_STRATEGY" = "reuse-managed" ]; then
    echo "Reusable managed worktree kept for future dispatches: $WORKTREE_DIR"
    echo "To discard it: git worktree remove $WORKTREE_DIR"
else
    echo "To remove the worktree: git worktree remove $WORKTREE_DIR"
fi
