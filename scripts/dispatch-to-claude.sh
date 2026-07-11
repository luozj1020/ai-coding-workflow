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

# Spec item 1: verify every relevant risk row explicitly says "no".
# Required categories: public API, data model, security, migration,
# permission, concurrency, cross-module, production impact.
# Missing/unknown/yes means fresh is safer.
_REQUIRED_RISK_CATEGORIES=8
_ALL_RISK_ROWS_SAY_NO=0
if [ -f "$TASK_CARD" ]; then
    _say_no_count="$(awk -F'|' '
        function trim(s) { gsub(/^[[:space:]]+|[[:space:]]+$/, "", s); return s }
        /^\|/ && NF >= 3 {
            field = tolower(trim($2))
            value = tolower(trim($3))
            category = ""
            if (field ~ /^public api( risk| impact)?[?]?$/) category = "public-api"
            else if (field ~ /^data model( risk| impact)?[?]?$/) category = "data-model"
            else if (field ~ /^security( risk| impact)?[?]?$/) category = "security"
            else if (field ~ /^migration( risk| impact)?[?]?$/) category = "migration"
            else if (field ~ /^permission( risk| impact)?[?]?$/) category = "permission"
            else if (field ~ /^concurrency( risk| impact)?[?]?$/) category = "concurrency"
            else if (field ~ /^cross-module( contract)? risk[?]?$/) category = "cross-module"
            else if (field ~ /^production( impact| risk)[?]?$/) category = "production"
            if (category != "") {
                if (!(category in seen)) seen[category] = 1
                if (value != "no") seen[category] = 0
            }
        }
        END {
            required["public-api"] = 1; required["data-model"] = 1; required["security"] = 1
            required["migration"] = 1; required["permission"] = 1; required["concurrency"] = 1
            required["cross-module"] = 1; required["production"] = 1
            count = 0
            for (category in required) if (seen[category] == 1) count++
            print count
        }
    ' "$TASK_CARD" 2>/dev/null || echo 0)"
    if [ "$_say_no_count" -ge "$_REQUIRED_RISK_CATEGORIES" ]; then
        _ALL_RISK_ROWS_SAY_NO=1
    fi
fi

# Apply smart default only when the user did not explicitly set the strategy
# and the profile default is fresh (safe/balanced profiles).
if [ -z "${CLAUDE_CODE_WORKTREE_STRATEGY+x}" ] && \
   [ "$DEFAULT_WORKTREE_STRATEGY" = "fresh" ] && \
   [ "$_PARSED_TASK_MODE" = "checker-test" ] && \
   [ "$_IS_DAG_DISPATCH" -eq 0 ] && \
   [ "$_ALL_RISK_ROWS_SAY_NO" -eq 1 ]; then
    DEFAULT_WORKTREE_STRATEGY="reuse-managed"
fi

# Record whether strategy was explicitly provided by the user or derived from task card.
# Must be captured before the default assignment below overwrites the unset state.
if [ -n "${CLAUDE_CODE_WORKTREE_STRATEGY+x}" ]; then
    _WORKTREE_STRATEGY_DERIVATION="explicit"
else
    _WORKTREE_STRATEGY_DERIVATION="task-derived"
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
CLAUDE_CODE_BUILDER_MODE="${CLAUDE_CODE_BUILDER_MODE:-standard}"
case "$CLAUDE_CODE_BUILDER_MODE" in
    standard|execution-only) ;;
    *)
        echo "Error: CLAUDE_CODE_BUILDER_MODE must be 'standard' or 'execution-only'." >&2
        exit 1
        ;;
esac
# Execution-only mode is only allowed for task mode builder.
if [ "$CLAUDE_CODE_BUILDER_MODE" = "execution-only" ] && [ "$_PARSED_TASK_MODE" != "builder" ]; then
    echo "Error: CLAUDE_CODE_BUILDER_MODE=execution-only requires task mode 'builder', found '${_PARSED_TASK_MODE:-unknown}'." >&2
    exit 1
fi
# First-progress timeout: default 0 (disabled) in standard mode, 120 in execution-only.
if [ -z "${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS+x}" ]; then
    if [ "$CLAUDE_CODE_BUILDER_MODE" = "execution-only" ]; then
        CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS=120
    else
        CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS=0
    fi
fi
case "$CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS" in
    ''|*[!0-9]*)
        echo "Error: CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS must be a non-negative integer." >&2
        exit 1
        ;;
esac
CLAUDE_CODE_WORKTREE_PROGRESS="${CLAUDE_CODE_WORKTREE_PROGRESS:-quiet}"
case "$CLAUDE_CODE_WORKTREE_PROGRESS" in
    quiet|verbose) ;;
    *)
        echo "Error: CLAUDE_CODE_WORKTREE_PROGRESS must be 'quiet' or 'verbose'." >&2
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

# --- Spec item 3: retry-in-place validation ---
# Validate a prior run's recorded worktree for safe in-place reuse.
# Sets _RETRY_TASK_ID, _RETRY_WORKTREE_DIR, _RETRY_BRANCH on success.
# On any ambiguity, fails closed with an actionable error.
validate_retry_in_place() {
    local prior_task_id="$1"
    local prior_root="${WORKTREE_ROOT}/${prior_task_id}"

    local prior_runtime="${prior_root}.runtime.json"
    local prior_dispatcher_pid="${prior_root}.dispatcher.pid"
    local prior_claude_pid="${prior_root}.claude.pid"
    local prior_pid="${prior_root}.pid"
    local prior_checker_pid="${prior_root}.checker.pid"

    # Load prior runtime identity artifact
    if [ ! -f "$prior_runtime" ]; then
        echo "Error: retry-in-place: prior runtime.json not found: ${prior_runtime}" >&2
        echo "The prior run may not have produced a runtime identity artifact." >&2
        exit 1
    fi

    local wt source_repo base_commit strategy
    wt="$(sed -n 's/.*"worktree"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"
    source_repo="$(sed -n 's/.*"source_repository"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"
    base_commit="$(sed -n 's/.*"base_commit"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"
    strategy="$(sed -n 's/.*"strategy"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"
    _RETRY_BRANCH="$(sed -n 's/.*"branch"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"

    # Validate required fields
    if [ -z "$wt" ] || [ -z "$source_repo" ] || [ -z "$base_commit" ]; then
        echo "Error: retry-in-place: prior runtime.json is malformed (missing worktree, source_repository, or base_commit)." >&2
        exit 1
    fi

    # Safety: worktree must be under .worktrees/ boundary
    case "$wt" in
        "${WORKTREE_ROOT}/"*) ;;
        *)
            echo "Error: retry-in-place: prior worktree is outside .worktrees/ boundary: ${wt}" >&2
            exit 1
            ;;
    esac

    # Reject reuse-managed prior runs: retry-in-place is for fresh worktrees only
    if [ "$strategy" = "reuse-managed" ]; then
        echo "Error: retry-in-place: prior run used reuse-managed strategy. Retry-in-place only supports fresh worktrees." >&2
        exit 1
    fi

    # Worktree must exist
    if [ ! -d "$wt" ]; then
        echo "Error: retry-in-place: prior worktree directory missing: ${wt}" >&2
        exit 1
    fi

    # Must be a git worktree
    if ! git -C "$wt" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        echo "Error: retry-in-place: prior worktree is not a valid git worktree: ${wt}" >&2
        exit 1
    fi

    # Source repository must match
    if [ "$source_repo" != "$REPO_ROOT" ]; then
        echo "Error: retry-in-place: source repository mismatch: recorded=${source_repo} current=${REPO_ROOT}" >&2
        exit 1
    fi

    # No live dispatcher/Claude/checker PIDs
    local pid_val
    if [ -f "$prior_dispatcher_pid" ]; then
        pid_val="$(tr -d '[:space:]' < "$prior_dispatcher_pid")"
        if [ -n "$pid_val" ] && kill -0 "$pid_val" 2>/dev/null; then
            echo "Error: retry-in-place: prior dispatcher PID ${pid_val} is still running." >&2
            exit 1
        fi
    fi
    if [ -f "$prior_claude_pid" ]; then
        pid_val="$(tr -d '[:space:]' < "$prior_claude_pid")"
        if [ -n "$pid_val" ] && kill -0 "$pid_val" 2>/dev/null; then
            echo "Error: retry-in-place: prior Claude PID ${pid_val} is still running." >&2
            exit 1
        fi
    elif [ -f "$prior_pid" ]; then
        pid_val="$(tr -d '[:space:]' < "$prior_pid")"
        if [ -n "$pid_val" ] && kill -0 "$pid_val" 2>/dev/null; then
            echo "Error: retry-in-place: prior Claude PID ${pid_val} is still running." >&2
            exit 1
        fi
    fi
    if [ -f "$prior_checker_pid" ]; then
        pid_val="$(tr -d '[:space:]' < "$prior_checker_pid")"
        if [ -n "$pid_val" ] && kill -0 "$pid_val" 2>/dev/null; then
            echo "Error: retry-in-place: prior checker PID ${pid_val} is still running." >&2
            exit 1
        fi
    fi

    # Worktree must be clean (tracked/staged/untracked)
    local dirty_out
    dirty_out="$(git -C "$wt" diff --name-only 2>/dev/null || true)"
    if [ -n "$dirty_out" ]; then
        echo "Error: retry-in-place: prior worktree has tracked changes:" >&2
        echo "$dirty_out" | sed 's/^/  /' >&2
        exit 1
    fi
    dirty_out="$(git -C "$wt" diff --cached --name-only 2>/dev/null || true)"
    if [ -n "$dirty_out" ]; then
        echo "Error: retry-in-place: prior worktree has staged changes:" >&2
        echo "$dirty_out" | sed 's/^/  /' >&2
        exit 1
    fi
    dirty_out="$(git -C "$wt" ls-files --others --exclude-standard 2>/dev/null || true)"
    if [ -n "$dirty_out" ]; then
        local _unknown_untracked=""
        while IFS= read -r _uf; do
            [ -z "$_uf" ] && continue
            case "$_uf" in
                TASK_CARD.md|TASK_CARD_FULL.md|CLAUDE_TASK_CARD.md|CLAUDE_PROMPT.md|CLAUDE_REPORT.md|CLAUDE_PROGRESS.md)
                    ;; # known dispatcher control file; allowed
                *)
                    _unknown_untracked="${_unknown_untracked}${_uf}\n" ;;
            esac
        done <<< "$dirty_out"
        if [ -n "$_unknown_untracked" ]; then
            echo "Error: retry-in-place: prior worktree has unknown untracked files:" >&2
            printf '%b' "$_unknown_untracked" | sed 's/^/  /' >&2
            exit 1
        fi
    fi

    # Recorded base commit must match current source HEAD
    if [ "$base_commit" != "$BASE_COMMIT" ]; then
        echo "Error: retry-in-place: recorded base commit does not match current HEAD: recorded=${base_commit} current=${BASE_COMMIT}" >&2
        exit 1
    fi

    # Worktree HEAD must equal recorded base
    local wt_head
    wt_head="$(git -C "$wt" rev-parse HEAD 2>/dev/null || true)"
    if [ "$wt_head" != "$base_commit" ]; then
        echo "Error: retry-in-place: worktree HEAD does not match recorded base: worktree=${wt_head} base=${base_commit}" >&2
        exit 1
    fi

    _RETRY_TASK_ID="$prior_task_id"
    _RETRY_WORKTREE_DIR="$wt"
    [ -n "$_RETRY_BRANCH" ] || _RETRY_BRANCH="claude-task-retry-${prior_task_id}"
}

# --- Spec item 3: retry-in-place setup ---
# If CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID is set, validate and reuse prior worktree.
# On success, TASK_ID and WORKTREE_DIR are set from prior run's runtime.json.
# On failure, the script exits with an actionable error (fail closed).
BASE_COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD)"

_RETRY_TASK_ID=""
_RETRY_WORKTREE_DIR=""
_RETRY_BRANCH=""
if [ -n "${CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID:-}" ]; then
    validate_retry_in_place "$CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID"
    # Retry must receive a new unique TASK_ID; prior ID is for provenance only.
    TASK_ID="claude-retry-${TIMESTAMP}-${RAND_SUFFIX}"
    WORKTREE_DIR="$_RETRY_WORKTREE_DIR"
    BRANCH_NAME="$_RETRY_BRANCH"
    # Atomic reservation: prevent concurrent claim of the same retry target.
    _RETRY_RESERVATION_DIR="${WORKTREE_ROOT}/.retry-lock-${CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID}"
    if ! mkdir "$_RETRY_RESERVATION_DIR" 2>/dev/null; then
        echo "Error: retry-in-place: reservation already exists for task ${CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID}." >&2
        echo "Another dispatcher may be claiming this retry target." >&2
        exit 1
    fi
    echo "$$" > "${_RETRY_RESERVATION_DIR}/pid"
    trap 'rm -rf "$_RETRY_RESERVATION_DIR"' EXIT
    echo "Worktree reuse (retry-in-place): $WORKTREE_DIR (prior task: $_RETRY_TASK_ID, new task: $TASK_ID)"
else
    # --- Normal worktree setup (fresh or reuse-managed) ---
    if [ -n "${AI_CODING_WORKFLOW_DAG_TASK_ID:-}" ]; then
        DAG_GROUP="${AI_CODING_WORKFLOW_DAG_GROUP_ID:-dag}"
        TASK_ID="${DAG_GROUP}-${AI_CODING_WORKFLOW_DAG_TASK_ID}-${TIMESTAMP}-${RAND_SUFFIX}"
    else
        TASK_ID="claude-${TIMESTAMP}-${RAND_SUFFIX}"
    fi
    if [ "$CLAUDE_CODE_WORKTREE_STRATEGY" = "reuse-managed" ]; then
        WORKTREE_DIR="$REUSE_WORKTREE_DIR"
    else
        WORKTREE_DIR="${WORKTREE_ROOT}/${TASK_ID}"
    fi
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
DISPATCHER_PID_FILE="${WORKTREE_ROOT}/${TASK_ID}.dispatcher.pid"
CLAUDE_PID_FILE="${WORKTREE_ROOT}/${TASK_ID}.claude.pid"
CHECKER_PID_FILE="${WORKTREE_ROOT}/${TASK_ID}.checker.pid"
RUNTIME_JSON="${WORKTREE_ROOT}/${TASK_ID}.runtime.json"
PROGRESS_FILE="${WORKTREE_ROOT}/${TASK_ID}.progress.log"
NETWORK_FILE="${WORKTREE_ROOT}/${TASK_ID}.network.log"
SEEDED_REPORT_MARKER="AI-CODING-WORKFLOW:DISPATCH-SEEDED-REPORT"
SEEDED_PROGRESS_MARKER="AI-CODING-WORKFLOW:DISPATCH-SEEDED-PROGRESS"
FALLBACK_REPORT_MARKER="AI-CODING-WORKFLOW:DISPATCH-FALLBACK-REPORT"

for f in "$RESULT_FILE" "$RAW_RESULT_FILE" "$STATUS_FILE" "$DIFFSTAT_FILE" "$DIFF_FILE" "$CHECKER_REPORT_FILE" \
         "$SOURCE_STATUS_FILE" "$WORKTREE_STATUS_FILE" "$UNTRACKED_FILE" "$USAGE_FILE" "$REPORT_FILE" \
         "$CLAUDE_PROGRESS_FILE" "$PID_FILE" "$DISPATCHER_PID_FILE" "$CLAUDE_PID_FILE" "$CHECKER_PID_FILE" \
         "$PROGRESS_FILE" "$NETWORK_FILE"; do
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

# Write runtime PID evidence only after source preflight succeeds. Failed dirty
# source checks must remain artifact-free.
echo "$$" > "$DISPATCHER_PID_FILE"

create_dispatch_worktree() {
    local branch_name="$1"
    if [ "$CLAUDE_CODE_WORKTREE_STRATEGY" = "fresh" ]; then
        if [ "$CLAUDE_CODE_WORKTREE_PROGRESS" = "quiet" ]; then
            git worktree add -b "$branch_name" "$WORKTREE_DIR" "$BASE_COMMIT" >/dev/null || {
                echo "Error: Failed to create git worktree at $WORKTREE_DIR" >&2
                exit 1
            }
        else
            git worktree add -b "$branch_name" "$WORKTREE_DIR" "$BASE_COMMIT" || {
                echo "Error: Failed to create git worktree at $WORKTREE_DIR" >&2
                exit 1
            }
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
        if [ "$CLAUDE_CODE_WORKTREE_PROGRESS" = "quiet" ]; then
            git -C "$WORKTREE_DIR" reset --hard >/dev/null
            git -C "$WORKTREE_DIR" clean -ffdx >/dev/null
            git -C "$WORKTREE_DIR" checkout -B "$branch_name" "$BASE_COMMIT" >/dev/null
            git -C "$WORKTREE_DIR" reset --hard "$BASE_COMMIT" >/dev/null
            git -C "$WORKTREE_DIR" clean -ffdx >/dev/null
        else
            git -C "$WORKTREE_DIR" reset --hard >/dev/null
            git -C "$WORKTREE_DIR" clean -ffdx >/dev/null
            git -C "$WORKTREE_DIR" checkout -B "$branch_name" "$BASE_COMMIT" >/dev/null
            git -C "$WORKTREE_DIR" reset --hard "$BASE_COMMIT" >/dev/null
            git -C "$WORKTREE_DIR" clean -ffdx >/dev/null
        fi
        return
    fi

    git branch -D "$branch_name" >/dev/null 2>&1 || true
    if [ "$CLAUDE_CODE_WORKTREE_PROGRESS" = "quiet" ]; then
        git worktree add -b "$branch_name" "$WORKTREE_DIR" "$BASE_COMMIT" >/dev/null || {
            echo "Error: Failed to create reusable managed git worktree at $WORKTREE_DIR" >&2
            exit 1
        }
    else
        git worktree add -b "$branch_name" "$WORKTREE_DIR" "$BASE_COMMIT" || {
            echo "Error: Failed to create reusable managed git worktree at $WORKTREE_DIR" >&2
            exit 1
        }
    fi
}

# Skip worktree creation if retry-in-place already provided a valid worktree.
if [ -z "${_RETRY_WORKTREE_DIR:-}" ]; then
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
    _WORKTREE_SETUP_END="$(date +%s)"
    _WORKTREE_SETUP_DURATION=$((_WORKTREE_SETUP_END - _WORKTREE_SETUP_START))

    if [ "$CLAUDE_CODE_WORKTREE_PROGRESS" = "quiet" ]; then
        echo "Worktree ready (${CLAUDE_CODE_WORKTREE_STRATEGY}, ${_WORKTREE_SETUP_DURATION}s): $WORKTREE_DIR"
    else
        echo "Worktree strategy: ${CLAUDE_CODE_WORKTREE_STRATEGY}"
        echo "Branch: $BRANCH_NAME"
    fi
fi

{
    echo "# Source Repository Status - ${TIMESTAMP}"
    echo "# Recorded after preflight checks and worktree creation"
    echo ""
    echo "## Worktree Strategy"
    echo ""
    echo "- Execution profile: ${CLAUDE_CODE_EXECUTION_PROFILE}"
    if [ -n "${_RETRY_TASK_ID:-}" ]; then
        echo "- Strategy: retry-in-place (prior: ${CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID})"
    else
        echo "- Strategy: ${CLAUDE_CODE_WORKTREE_STRATEGY}"
    fi
    echo "- Strategy derivation: ${_WORKTREE_STRATEGY_DERIVATION}"
    echo "- Worktree: ${WORKTREE_DIR}"
    echo "- Base commit: ${BASE_COMMIT}"
    echo "- Runtime identity: ${RUNTIME_JSON}"
    if [ -n "${_RETRY_TASK_ID:-}" ]; then
        echo "- Retry provenance: prior task ${_RETRY_TASK_ID} from ${CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID}"
    fi
    echo "- Reuse reset allowed: ${CLAUDE_CODE_REUSE_WORKTREE_RESET}"
    echo "- Large repo mode: ${CLAUDE_CODE_LARGE_REPO_MODE}"
    echo "- Claude task card view: ${CLAUDE_CODE_TASK_CARD_VIEW}"
    echo "- Claude prompt profile: ${CLAUDE_CODE_PROMPT_PROFILE}"
    echo "- Evidence mode: ${CLAUDE_CODE_EVIDENCE_MODE}"
    echo "- Checker broad discovery: ${CLAUDE_CODE_CHECKER_DISCOVER}"
    echo "- Builder mode: ${CLAUDE_CODE_BUILDER_MODE}"
    echo "- First-progress timeout: ${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS}s"
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

echo "Source status saved to: $SOURCE_STATUS_FILE"

# --- Spec item 1: write runtime identity artifact ---
# Write atomically (via temp + mv) so monitors never see a partial file.
_RUNTIME_STRATEGY="${CLAUDE_CODE_WORKTREE_STRATEGY}"
if [ -n "${_RETRY_TASK_ID:-}" ]; then
    _RUNTIME_STRATEGY="retry-in-place"
fi
_RUNTIME_TMP="${RUNTIME_JSON}.tmp.$$"
{
    echo "{"
    echo "  \"schema_version\": 1,"
    printf '  "task_id": "%s",\n' "$TASK_ID"
    printf '  "worktree": "%s",\n' "$WORKTREE_DIR"
    printf '  "strategy": "%s",\n' "$_RUNTIME_STRATEGY"
    printf '  "branch": "%s",\n' "$BRANCH_NAME"
    printf '  "base_commit": "%s",\n' "$BASE_COMMIT"
    printf '  "source_repository": "%s",\n' "$REPO_ROOT"
    if [ -n "${CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID:-}" ]; then
        printf '  "retry_of": "%s",\n' "$CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID"
    fi
    printf '  "pid_files": {\n'
    printf '    "dispatcher": "%s",\n' "$DISPATCHER_PID_FILE"
    printf '    "claude": "%s",\n' "$CLAUDE_PID_FILE"
    printf '    "checker": "%s",\n' "$CHECKER_PID_FILE"
    printf '    "pid": "%s"\n' "$PID_FILE"
    echo "  },"
    printf '  "builder_mode": "%s",\n' "$CLAUDE_CODE_BUILDER_MODE"
    printf '  "first_progress_timeout_seconds": %s\n' "$CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS"
    echo "}"
} > "$_RUNTIME_TMP"
mv "$_RUNTIME_TMP" "$RUNTIME_JSON"
echo "Runtime identity saved to: $RUNTIME_JSON"

cp "$TASK_CARD" "${WORKTREE_DIR}/TASK_CARD.md"
cp "$TASK_CARD" "${WORKTREE_DIR}/TASK_CARD_FULL.md"

render_claude_task_card() {
    awk -v view="$CLAUDE_CODE_TASK_CARD_VIEW" -v builder_mode="$CLAUDE_CODE_BUILDER_MODE" '
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
    function execution_only_keep_section(name) {
        return name == "ID" \
            || name == "Task Mode" \
            || name == "Claude Context Packet" \
            || name == "Goal" \
            || name == "Handoff Contract" \
            || name == "Required Revisions" \
            || name == "Required Changes" \
            || name == "Acceptance Criteria" \
            || name == "Testing Responsibility" \
            || name == "Validation Contract" \
            || name == "Required Report"
    }
    BEGIN {
        skip = 0
        print "<!-- Generated by dispatch-to-claude.sh from TASK_CARD_FULL.md. Codex-only planning and control-plane sections are omitted. -->"
        if (builder_mode == "execution-only") {
            print "<!-- Execution-only view: only execution-relevant sections are included. TASK_CARD_FULL.md remains the audit source. -->"
        } else if (view == "compact") {
            print "<!-- Compact view: optional planning gates are omitted. TASK_CARD_FULL.md remains the audit source. -->"
        }
        print ""
    }
    /^##[ \t]+/ {
        name = section_name($0)
        if (codex_only_section(name)) {
            skip = 1
            next
        }
        if (builder_mode == "execution-only") {
            if (!execution_only_keep_section(name)) {
                skip = 1
                next
            }
            skip = 0
        } else if (view == "compact" && compact_skip_section(name)) {
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

if [ "$CLAUDE_CODE_BUILDER_MODE" = "execution-only" ]; then
cat > "${WORKTREE_DIR}/CLAUDE_PROMPT.md" <<'EOF'
You are the executor in a Codex/Claude Code workflow operating in execution-only Builder mode.

Execute `CLAUDE_TASK_CARD.md`. `TASK_CARD_FULL.md` is the full planning card for audit only.

Rules:
- Read the named target files/sections from the Claude Context Packet.
- Update `CLAUDE_PROGRESS.md` immediately, removing the seeded marker.
- Edit the target files according to the Handoff Contract and Required Changes.
- Do NOT restate or redesign the plan. Do NOT run broad discovery.
- Report a blocker or split when scope is insufficient. Obey the testing boundary.
- Update `CLAUDE_REPORT.md` before finishing with: files changed, acceptance criteria mapping, syntax outcome, deviations, remaining risks.

--- CLAUDE EXECUTION CARD ---
EOF
elif [ "$CLAUDE_CODE_PROMPT_PROFILE" = "brief" ]; then
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

approval_convergence_ready() {
    local report_file="$1"
    local progress_file="$2"
    local combined
    valid_claude_report_file "$report_file" || return 1
    for heading in "Requirements Summary" "Files Changed" "Acceptance Criteria Mapping" \
                   "Out-of-Scope Confirmation" "Plan Match" "Checks Run"; do
        grep -Fqi "## ${heading}" "$report_file" 2>/dev/null || return 1
    done
    combined="$(cat "$report_file" "$progress_file" "$STATUS_FILE" 2>/dev/null || true)"
    printf '%s\n' "$combined" | grep -Eiq \
        '(implementation|assigned|test edits|files changed).{0,60}(complete|completed|done)' || return 1
    printf '%s\n' "$combined" | grep -Eiq \
        '(validation|test|check|command).{0,80}(blocked|denied|requires|waiting).{0,80}(approval|permission|sandbox)|(approval|permission|sandbox).{0,80}(blocked|denied|required).{0,80}(run|execute).{0,40}(validation|test|check|command)' || return 1
    return 0
}

approval_convergence_changes_safe() {
    local line path
    while IFS= read -r line; do
        [ -n "$line" ] || continue
        path="${line:3}"
        case "$path" in
            tests/*|test/*|*/tests/*|*/test/*|*__tests__/*) ;;
            *) return 1 ;;
        esac
    done < <(git status --porcelain --untracked-files=all 2>/dev/null \
        | grep -v -E '^(.. )?(TASK_CARD|TASK_CARD_FULL|CLAUDE_TASK_CARD|CLAUDE_PROMPT|CLAUDE_REPORT|CLAUDE_PROGRESS)(\.md)?$' || true)
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
    local descendants=""
    if command -v pgrep >/dev/null 2>&1; then
        local frontier="$CLAUDE_PID"
        local parent children
        while [ -n "$frontier" ]; do
            local next_frontier=""
            for parent in $frontier; do
                children="$(pgrep -P "$parent" 2>/dev/null || true)"
                if [ -n "$children" ]; then
                    descendants="${descendants} ${children}"
                    next_frontier="${next_frontier} ${children}"
                fi
            done
            frontier="$next_frontier"
        done
    fi
    progress_log "Stopping Claude (${reason}) after ${elapsed}s; sending TERM to pid=${CLAUDE_PID} descendants=${descendants:-none}"
    if [ -n "$descendants" ]; then
        kill $descendants 2>/dev/null || true
    fi
    kill "$CLAUDE_PID" 2>/dev/null || true
    sleep 5
    if [ -n "$descendants" ]; then
        local descendant
        for descendant in $descendants; do
            if kill -0 "$descendant" 2>/dev/null; then
                kill -9 "$descendant" 2>/dev/null || true
            fi
        done
    fi
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

if [ "$CLAUDE_CODE_VERBOSE" = "1" ]; then
    echo "Invoking Claude Code..."
    echo "Progress log: $PROGRESS_FILE"
    echo "Watch Progress: bash \"$WATCH_SCRIPT\" \"$TASK_ID\""
    echo "Watch Details:  bash \"$WATCH_SCRIPT\" \"$TASK_ID\" --details"
fi
cd "$WORKTREE_DIR"

: > "$PROGRESS_FILE"
write_network_header
progress_log "Starting Claude Code: execution_profile=${CLAUDE_CODE_EXECUTION_PROFILE}, prompt_profile=${CLAUDE_CODE_PROMPT_PROFILE}, evidence_mode=${CLAUDE_CODE_EVIDENCE_MODE}, proxy_mode=${CLAUDE_CODE_PROXY_MODE}, timeout_seconds=${CLAUDE_CODE_TIMEOUT_SECONDS}, heartbeat_seconds=${CLAUDE_CODE_HEARTBEAT_SECONDS}, no_output_timeout_seconds=${CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS}, network_monitor=${CLAUDE_CODE_NETWORK_MONITOR}, worktree_strategy=${CLAUDE_CODE_WORKTREE_STRATEGY}, large_repo_mode=${CLAUDE_CODE_LARGE_REPO_MODE}, task_mode=${_PARSED_TASK_MODE:-unknown}, verbose=${CLAUDE_CODE_VERBOSE}, approval_convergence=${CLAUDE_CODE_APPROVAL_BLOCKED_CONVERGENCE}, worktree_progress=${CLAUDE_CODE_WORKTREE_PROGRESS}, builder_mode=${CLAUDE_CODE_BUILDER_MODE}, first_progress_timeout=${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS}"

set +e
run_claude &
CLAUDE_PID=$!
echo "$CLAUDE_PID" > "$PID_FILE"
echo "$CLAUDE_PID" > "$CLAUDE_PID_FILE"
progress_log "Claude process started: pid=${CLAUDE_PID}"

START_EPOCH="$(date +%s)"
CLAUDE_TIMED_OUT=0
CLAUDE_NO_OUTPUT_TIMED_OUT=0
CLAUDE_APPROVAL_CONVERGED=0
CLAUDE_FIRST_PROGRESS_TIMED_OUT=0
_APPROVAL_CONVERGENCE_COUNT=0
_LAST_APPROVAL_FP=""
LAST_ACTIVITY_EPOCH="$START_EPOCH"
LAST_TOTAL_BYTES=0
LAST_WORKTREE_DIGEST="$(worktree_digest)"
FIRST_PROGRESS_DETECTED=0
FIRST_PROGRESS_SIGNAL=""
INITIAL_PROGRESS_HASH="$(sha1sum "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" 2>/dev/null | awk '{print $1}' || true)"
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
    progress_log "Claude still running: pid=${CLAUDE_PID}, elapsed_seconds=${ELAPSED}, quiet_seconds=${QUIET_SECONDS}, result_bytes=${RESULT_BYTES}, status_bytes=${STATUS_BYTES}, report_bytes=${REPORT_BYTES}, claude_progress_bytes=${CLAUDE_PROGRESS_BYTES}, claude_task_bytes=${CLAUDE_TASK_BYTES}, worktree_changes=${WORKTREE_CHANGES}, worktree_changed=${WORKTREE_CHANGED}, first_progress_detected=${FIRST_PROGRESS_DETECTED}, ${NETWORK_SUMMARY}"

    # --- First-substantive-progress detection ---
    # Mark progress when: worktree changes, progress file changed from seed with
    # meaningful content, valid non-seeded report, or blocker/stop/split/approval recorded.
    if [ "$FIRST_PROGRESS_DETECTED" -eq 0 ]; then
        _FP_SIGNAL=""
        if [ "$WORKTREE_CHANGES" -gt 0 ]; then
            _FP_SIGNAL="worktree_change"
        fi
        if [ -z "$_FP_SIGNAL" ]; then
            _CURRENT_PROGRESS_HASH="$(sha1sum "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" 2>/dev/null | awk '{print $1}' || true)"
            if [ -s "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" ] && \
               ! file_contains "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" "$SEEDED_PROGRESS_MARKER"; then
                _FP_SIGNAL="progress_updated"
            fi
        fi
        if [ -z "$_FP_SIGNAL" ] && valid_claude_report_file "${WORKTREE_DIR}/CLAUDE_REPORT.md"; then
            _FP_SIGNAL="valid_report"
        fi
        if [ -z "$_FP_SIGNAL" ]; then
            for _fp_file in "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" "${WORKTREE_DIR}/CLAUDE_REPORT.md"; do
                if [ -f "$_fp_file" ] && \
                   ! file_contains "$_fp_file" "$SEEDED_PROGRESS_MARKER|$SEEDED_REPORT_MARKER" && \
                   grep -Eiq 'blocker|stop|split|permission|approval|waiting' "$_fp_file" 2>/dev/null; then
                    _FP_SIGNAL="blocker_recorded"
                    break
                fi
            done
        fi
        if [ -n "$_FP_SIGNAL" ]; then
            FIRST_PROGRESS_DETECTED=1
            FIRST_PROGRESS_SIGNAL="$_FP_SIGNAL"
            progress_log "First substantive progress detected: signal=${_FP_SIGNAL}, elapsed_seconds=${ELAPSED}"
        elif [ "$CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS" -gt 0 ] && \
             [ "$ELAPSED" -ge "$CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS" ]; then
            CLAUDE_FIRST_PROGRESS_TIMED_OUT=1
            stop_claude "first_progress_timeout after ${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS}s" "$ELAPSED"
            break
        fi
    fi

    # --- Spec item 2: approval-blocked early convergence ---
    # End Claude early when: checker-test mode, valid non-seeded report,
    # approval/permission blocker recorded, and state stable for two heartbeats.
    if [ "${CLAUDE_CODE_APPROVAL_BLOCKED_CONVERGENCE:-1}" = "1" ] && \
       [ "$_PARSED_TASK_MODE" = "checker-test" ]; then
        _ABC_REPORT_VALID=0
        if approval_convergence_ready "${WORKTREE_DIR}/CLAUDE_REPORT.md" "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" && \
           approval_convergence_changes_safe; then
            _ABC_REPORT_VALID=1
        fi

        if [ "$_ABC_REPORT_VALID" -eq 1 ]; then
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

# --- Spec item 4: distinct child exit detection and finalization transition ---
# Log the moment the Claude child is detected as no longer running.
# No extra waiting is introduced; finalization begins immediately.
progress_log "Claude child exited: pid=${CLAUDE_PID}, exit_status=${CLAUDE_STATUS}; transitioning to finalization immediately"

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
elif [ "$CLAUDE_FIRST_PROGRESS_TIMED_OUT" -eq 1 ]; then
    {
        echo ""
        echo "[dispatch] Claude stopped after ${ELAPSED}s: no substantive progress within ${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS}s."
        echo "[dispatch] Convergence type: first_progress_timeout"
        echo "[dispatch] First-progress timed out: yes"
        echo "[dispatch] Builder mode: ${CLAUDE_CODE_BUILDER_MODE}"
        echo "[dispatch] First progress signal: ${FIRST_PROGRESS_SIGNAL:-none}"
        echo "[dispatch] Progress log: ${PROGRESS_FILE}"
    } >> "$STATUS_FILE"
    progress_log "Claude finished by first_progress_timeout: elapsed_seconds=${ELAPSED}, wait_status=${CLAUDE_STATUS}, timeout_seconds=${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS}"
    echo "Warning: claude produced no substantive progress within ${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS}s. Check $STATUS_FILE and $PROGRESS_FILE" >&2
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
            "${CLAUDE_APPROVAL_CONVERGED:-0}" "${CLAUDE_FIRST_PROGRESS_TIMED_OUT:-0}" \
            "${CLAUDE_CODE_BUILDER_MODE:-standard}" "${FIRST_PROGRESS_SIGNAL:-}" \
            "${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS:-0}" <<'PYEOF'
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
    first_progress_timed_out,
    builder_mode,
    first_progress_signal,
    first_progress_timeout,
) = sys.argv[1:16]

payload = {
    "type": "claude_dispatch_fallback",
    "fallback": True,
    "reason": reason,
    "claude_exit_status": int(status),
    "timed_out": timed_out == "1",
    "no_output_timed_out": no_output_timed_out == "1",
    "approval_blocked_early_convergence": approval_converged == "1",
    "first_progress_timeout": first_progress_timed_out == "1",
    "builder_mode": builder_mode,
    "first_progress_signal": first_progress_signal or None,
    "first_progress_timeout_seconds": int(first_progress_timeout),
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
            echo "  \"first_progress_timeout\": $([ "${CLAUDE_FIRST_PROGRESS_TIMED_OUT:-0}" -eq 1 ] && echo true || echo false),"
            echo "  \"builder_mode\": \"${CLAUDE_CODE_BUILDER_MODE:-standard}\","
            echo "  \"first_progress_signal\": \"${FIRST_PROGRESS_SIGNAL:-}\","
            echo "  \"first_progress_timeout_seconds\": ${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS:-0},"
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
    bash "$CHECK_SCRIPT" "${CHECK_ARGS[@]}" >> "$STATUS_FILE" 2>&1 &
    CHECKER_PID=$!
    echo "$CHECKER_PID" > "$CHECKER_PID_FILE"
    progress_log "Checker helper started: pid=${CHECKER_PID}"
    wait "$CHECKER_PID"
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
        echo "- First-progress timed out: $([ "${CLAUDE_FIRST_PROGRESS_TIMED_OUT:-0}" -eq 1 ] && echo yes || echo no)"
        echo "- First-progress signal: ${FIRST_PROGRESS_SIGNAL:-none}"
        echo "- Builder mode: ${CLAUDE_CODE_BUILDER_MODE:-standard}"
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
if [ -n "${_RETRY_TASK_ID:-}" ]; then
    echo "Worktree Strategy: retry-in-place (prior: ${CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID})"
else
    echo "Worktree Strategy: $CLAUDE_CODE_WORKTREE_STRATEGY"
fi
echo "Runtime Identity: $RUNTIME_JSON"
echo "Large Repo Mode: $CLAUDE_CODE_LARGE_REPO_MODE"
echo "Prompt Profile:  $CLAUDE_CODE_PROMPT_PROFILE"
echo "Evidence Mode:   $CLAUDE_CODE_EVIDENCE_MODE"
echo "Builder Mode:    $CLAUDE_CODE_BUILDER_MODE"
echo "First Progress:  ${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS}s timeout"
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
echo "Dispatcher PID:  $DISPATCHER_PID_FILE"
echo "Claude Role PID: $CLAUDE_PID_FILE"
echo "Checker PID:     $CHECKER_PID_FILE"
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
