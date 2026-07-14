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

# Continuation validation runs before the main dispatch setup reaches its
# legacy interpreter detection block, so resolve Python before either path.
PYTHON_CMD=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
fi

# --- Route preference learning ---
# Precedence: explicit caller env > learned preference > direct fallback.
# Track source for logging.  Actual learned-route resolution happens after
# PYTHON_CMD is available (needed to invoke the helper).
if [ -n "${CLAUDE_CODE_PROXY_MODE+x}" ] && [ -n "$CLAUDE_CODE_PROXY_MODE" ]; then
    _ROUTE_SOURCE="explicit"
else
    _ROUTE_SOURCE="default"
    CLAUDE_CODE_PROXY_MODE="direct"
fi
CLAUDE_CODE_TIMEOUT_SECONDS="${CLAUDE_CODE_TIMEOUT_SECONDS:-600}"
CLAUDE_CODE_HEARTBEAT_SECONDS="${CLAUDE_CODE_HEARTBEAT_SECONDS:-30}"
CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS="${CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS:-0}"
CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS="${CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS:-300}"
CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS="${CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS:-300}"
CLAUDE_CODE_ZERO_OUTPUT_PROBE_TIMEOUT_SECONDS="${CLAUDE_CODE_ZERO_OUTPUT_PROBE_TIMEOUT_SECONDS:-60}"
CLAUDE_CODE_RECENT_ACTIVITY_WINDOW_SECONDS="${CLAUDE_CODE_RECENT_ACTIVITY_WINDOW_SECONDS:-120}"
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
CLAUDE_CODE_API_PROBE_MODE="${CLAUDE_CODE_API_PROBE_MODE:-always}"
CLAUDE_CODE_PROBE_ENVIRONMENT="${CLAUDE_CODE_PROBE_ENVIRONMENT:-auto}"
CLAUDE_CODE_FIRST_PROGRESS_ACTION="${CLAUDE_CODE_FIRST_PROGRESS_ACTION:-observe}"
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

# --- External integration gate parsing ---
# Parse the "Claude External Integration Gate" section from the task card.
# These fields control whether MCP config files and plugin directories are
# passed to the Claude CLI invocation.  Default/missing means fail-closed:
# --bare with no MCP/plugin paths.
_EXTERNAL_INTEGRATIONS_ALLOWED="no"
_MCP_CONFIG_PATHS_RAW="none"
_PLUGIN_PATHS_RAW="none"
_STRICT_MCP_ISOLATION="yes"
if [ -f "$TASK_CARD" ]; then
    _eval_gate_field() {
        local field_pattern="$1"
        awk -F'|' -v pat="$field_pattern" '
            function trim(s) { gsub(/^[[:space:]]+|[[:space:]]+$/, "", s); return s }
            /^\|/ && NF >= 3 {
                field = tolower(trim($2))
                value = trim($3)
                if (field ~ pat) { print value; exit }
            }
        ' "$TASK_CARD" 2>/dev/null || true
    }
    _val="$(_eval_gate_field '^external integrations allowed[?]?')"
    if [ -n "$_val" ]; then _EXTERNAL_INTEGRATIONS_ALLOWED="$(printf '%s' "$_val" | tr '[:upper:]' '[:lower:]')"; fi
    _val="$(_eval_gate_field '^mcp config paths[?]?')"
    if [ -n "$_val" ]; then _MCP_CONFIG_PATHS_RAW="$_val"; fi
    _val="$(_eval_gate_field '^plugin paths[?]?')"
    if [ -n "$_val" ]; then _PLUGIN_PATHS_RAW="$_val"; fi
    _val="$(_eval_gate_field '^strict mcp isolation[?]?')"
    if [ -n "$_val" ]; then _STRICT_MCP_ISOLATION="$(printf '%s' "$_val" | tr '[:upper:]' '[:lower:]')"; fi
fi
case "$_EXTERNAL_INTEGRATIONS_ALLOWED" in
    yes|no) ;;
    *) echo "Error: External integrations allowed? must be yes or no." >&2; exit 1 ;;
esac
case "$_STRICT_MCP_ISOLATION" in
    yes|no) ;;
    *) echo "Error: Strict MCP isolation? must be yes or no." >&2; exit 1 ;;
esac
if [ "$_EXTERNAL_INTEGRATIONS_ALLOWED" = "yes" ] && [ "$_STRICT_MCP_ISOLATION" != "yes" ]; then
    echo "Error: Strict MCP isolation? must be yes when external integrations are allowed." >&2
    exit 1
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
case "$CLAUDE_CODE_API_PROBE_MODE" in
    always|failure-only|off) ;;
    *)
        echo "Error: CLAUDE_CODE_API_PROBE_MODE must be 'always', 'failure-only', or 'off'." >&2
        exit 1
        ;;
esac
case "$CLAUDE_CODE_PROBE_ENVIRONMENT" in
    auto|host|sandbox) ;;
    *)
        echo "Error: CLAUDE_CODE_PROBE_ENVIRONMENT must be 'auto', 'host', or 'sandbox'." >&2
        exit 1
        ;;
esac
case "$CLAUDE_CODE_FIRST_PROGRESS_ACTION" in
    observe|stop) ;;
    *)
        echo "Error: CLAUDE_CODE_FIRST_PROGRESS_ACTION must be 'observe' or 'stop'." >&2
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
CLAUDE_CODE_BUILDER_MODE="${CLAUDE_CODE_BUILDER_MODE:-auto}"
case "$CLAUDE_CODE_BUILDER_MODE" in
    auto|standard|execution-only) ;;
    *)
        echo "Error: CLAUDE_CODE_BUILDER_MODE must be 'auto', 'standard', or 'execution-only'." >&2
        exit 1
        ;;
esac
CLAUDE_CODE_TOOL_PROFILE="${CLAUDE_CODE_TOOL_PROFILE:-auto}"
case "$CLAUDE_CODE_TOOL_PROFILE" in
    auto|default|minimal-builder|locator-builder|checker|diagnostic) ;;
    *)
        echo "Error: CLAUDE_CODE_TOOL_PROFILE must be 'auto', 'default', 'minimal-builder', 'locator-builder', 'checker', or 'diagnostic'." >&2
        exit 1
        ;;
esac
CLAUDE_CODE_TASK_VALIDATION_ALLOWLIST="${CLAUDE_CODE_TASK_VALIDATION_ALLOWLIST:-1}"
case "$CLAUDE_CODE_TASK_VALIDATION_ALLOWLIST" in
    0|1) ;;
    *)
        echo "Error: CLAUDE_CODE_TASK_VALIDATION_ALLOWLIST must be 0 or 1." >&2
        exit 1
        ;;
esac
if [ "$CLAUDE_CODE_BUILDER_MODE" = "auto" ]; then
    if [ "$_PARSED_TASK_MODE" = "builder" ] && \
       grep -Eiq '^\|[[:space:]]*Execution-only eligible\?[[:space:]]*\|[[:space:]]*yes([[:space:]]*\||[[:space:]]*$)' "$TASK_CARD" && \
       grep -Eiq '^\|[[:space:]]*Context is sufficient for execution\?[[:space:]]*\|[[:space:]]*yes([[:space:]]*\||[[:space:]]*$)' "$TASK_CARD"; then
        CLAUDE_CODE_BUILDER_MODE="execution-only"
    else
        CLAUDE_CODE_BUILDER_MODE="standard"
    fi
fi
# Execution-only mode is only allowed for task mode builder.
if [ "$CLAUDE_CODE_BUILDER_MODE" = "execution-only" ] && [ "$_PARSED_TASK_MODE" != "builder" ]; then
    echo "Error: CLAUDE_CODE_BUILDER_MODE=execution-only requires task mode 'builder', found '${_PARSED_TASK_MODE:-unknown}'." >&2
    exit 1
fi

# --- Tool profile resolution ---
# Resolve auto after task mode and builder mode are both known.
_TOOL_PROFILE_DERIVATION="explicit"
if [ "$CLAUDE_CODE_TOOL_PROFILE" = "auto" ]; then
    _TOOL_PROFILE_DERIVATION="auto-resolved"
    if [ "$CLAUDE_CODE_BUILDER_MODE" = "execution-only" ]; then
        CLAUDE_CODE_TOOL_PROFILE="minimal-builder"
    elif [ "$_PARSED_TASK_MODE" = "checker-test" ]; then
        CLAUDE_CODE_TOOL_PROFILE="checker"
    elif [ "$CLAUDE_CODE_BUILDER_MODE" = "standard" ]; then
        CLAUDE_CODE_TOOL_PROFILE="locator-builder"
    else
        CLAUDE_CODE_TOOL_PROFILE="default"
    fi
fi

# --- Tool profile CLI flag support detection ---
# Detect --tools / --allowedTools support once per dispatch.
# CLI support requires BOTH --tools AND either --allowedTools or --allowed-tools.
# If unsupported, degrade to legacy/default tools and record unsupported-cli.
_TOOL_PROFILE_SUPPORTED=0
_CLAUDE_HELP_OUTPUT="$(claude --help 2>&1 || true)"
if printf '%s\n' "$_CLAUDE_HELP_OUTPUT" | grep -q -- '--tools' && \
   { printf '%s\n' "$_CLAUDE_HELP_OUTPUT" | grep -q -- '--allowedTools' || \
     printf '%s\n' "$_CLAUDE_HELP_OUTPUT" | grep -q -- '--allowed-tools'; }; then
    _TOOL_PROFILE_SUPPORTED=1
fi

# First-progress timeout: accept both spellings with _SECONDS precedence.
# If CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS is unset and
# CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT is set, use the latter as the value.
if [ -z "${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS+x}" ]; then
    if [ -n "${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT+x}" ] && [ -n "$CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT" ]; then
        CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS="$CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT"
    elif [ "$CLAUDE_CODE_BUILDER_MODE" = "execution-only" ]; then
        CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS=60
    else
        CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS=0
    fi
fi
# Record first-progress timeout alias source for status evidence.
_FIRST_PROGRESS_TIMEOUT_SOURCE="default"
if [ -n "${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS+x}" ] && \
   [ -n "${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT+x}" ] && \
   [ "$CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS" = "$CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT" ]; then
    _FIRST_PROGRESS_TIMEOUT_SOURCE="alias(CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT)"
elif [ -n "${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS+x}" ]; then
    _FIRST_PROGRESS_TIMEOUT_SOURCE="env"
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
                TASK_CARD.md|TASK_CARD_FULL.md|CLAUDE_TASK_CARD.md|CLAUDE_PROMPT.md|CLAUDE_REPORT.md|CLAUDE_PROGRESS.md|ADVISOR_REQUEST.json|advisor-packet.json|advisor-packet.md|advisor-response-*.json|advisor-decision.json)
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

# --- Advisor continuation validation ---
# Validate a prior run's artifacts for safe advisor-continuation reuse.
# Sets _ADVISOR_CONTINUE_TASK_ID, _ADVISOR_CONTINUE_WORKTREE_DIR, _ADVISOR_CONTINUE_BRANCH,
# _ADVISOR_CONTINUE_RESPONSE, _ADVISOR_CONTINUE_RESERVATION_ID on success.
# On any ambiguity, fails closed with an actionable error.
# This path is separate from clean transient retry (retry-in-place).
validate_advisor_continuation() {
    local prior_task_id="$1"
    local prior_root="${WORKTREE_ROOT}/${prior_task_id}"

    local prior_runtime="${prior_root}.runtime.json"
    local prior_dispatcher_pid="${prior_root}.dispatcher.pid"
    local prior_claude_pid="${prior_root}.claude.pid"
    local prior_pid="${prior_root}.pid"
    local prior_checker_pid="${prior_root}.checker.pid"

    # --- 1. Resolve prior runtime ---
    if [ ! -f "$prior_runtime" ]; then
        echo "Error: advisor-continuation: prior runtime.json not found: ${prior_runtime}" >&2
        exit 1
    fi

    local wt source_repo base_commit strategy
    wt="$(sed -n 's/.*"worktree"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"
    source_repo="$(sed -n 's/.*"source_repository"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"
    base_commit="$(sed -n 's/.*"base_commit"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"
    _ADVISOR_CONTINUE_BRANCH="$(sed -n 's/.*"branch"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"

    if [ -z "$wt" ] || [ -z "$source_repo" ] || [ -z "$base_commit" ]; then
        echo "Error: advisor-continuation: prior runtime.json is malformed." >&2
        exit 1
    fi

    # --- 2. Worktree must be under .worktrees/ boundary ---
    case "$wt" in
        "${WORKTREE_ROOT}/"*) ;;
        *)
            echo "Error: advisor-continuation: prior worktree outside .worktrees/ boundary: ${wt}" >&2
            exit 1
            ;;
    esac

    # Worktree must exist and be a valid git worktree
    if [ ! -d "$wt" ]; then
        echo "Error: advisor-continuation: prior worktree missing: ${wt}" >&2
        exit 1
    fi
    if ! git -C "$wt" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        echo "Error: advisor-continuation: not a valid git worktree: ${wt}" >&2
        exit 1
    fi

    # --- 3. Source repository must match ---
    if [ "$source_repo" != "$REPO_ROOT" ]; then
        echo "Error: advisor-continuation: source repository mismatch: recorded=${source_repo} current=${REPO_ROOT}" >&2
        exit 1
    fi

    # --- 4. Require all recorded processes inactive ---
    local pid_val
    for _pid_file in "$prior_dispatcher_pid" "$prior_claude_pid" "$prior_pid" "$prior_checker_pid"; do
        if [ -f "$_pid_file" ]; then
            pid_val="$(tr -d '[:space:]' < "$_pid_file")"
            if [ -n "$pid_val" ] && kill -0 "$pid_val" 2>/dev/null; then
                echo "Error: advisor-continuation: process ${pid_val} (from ${_pid_file}) is still running." >&2
                exit 1
            fi
        fi
    done

    # --- 5. Base commit must match exactly ---
    if [ "$base_commit" != "$BASE_COMMIT" ]; then
        echo "Error: advisor-continuation: recorded base commit does not match current HEAD: recorded=${base_commit} current=${BASE_COMMIT}" >&2
        exit 1
    fi

    # Worktree HEAD must equal recorded base
    local wt_head
    wt_head="$(git -C "$wt" rev-parse HEAD 2>/dev/null || true)"
    if [ "$wt_head" != "$base_commit" ]; then
        echo "Error: advisor-continuation: worktree HEAD does not match recorded base: worktree=${wt_head} base=${base_commit}" >&2
        exit 1
    fi

    # --- 6. Resolve advisor packet and validated response ---
    local advisor_dir="${prior_root}.advisor-request"
    local advisor_packet="${wt}/advisor-packet.json"
    local advisor_response="${advisor_dir}/advisor-response-validated.json"
    local advisor_result="${advisor_dir}/advisor-call-result.json"

    # The advisor packet must exist in the worktree
    if [ ! -f "$advisor_packet" ]; then
        echo "Error: advisor-continuation: advisor-packet.json not found in worktree: ${wt}" >&2
        exit 1
    fi

    # The validated response must exist (from advisor-call or prepare-advisor-continuation)
    if [ ! -f "$advisor_response" ]; then
        # Fall back: check the output dir from advisor-call
        local _advisor_output_dir="${WORKTREE_ROOT}/${prior_task_id}.advisor-output"
        if [ -f "${_advisor_output_dir}/advisor-response-validated.json" ]; then
            advisor_response="${_advisor_output_dir}/advisor-response-validated.json"
        else
            echo "Error: advisor-continuation: validated advisor response not found." >&2
            echo "Expected: ${advisor_response} or ${_advisor_output_dir}/advisor-response-validated.json" >&2
            exit 1
        fi
    fi
    advisor_dir="$(dirname "$advisor_response")"
    advisor_result="${advisor_dir}/advisor-call-result.json"
    if [ ! -f "$advisor_result" ]; then
        echo "Error: advisor-continuation: advisor-call-result.json not found: ${advisor_result}" >&2
        exit 1
    fi

    # Bind the separately stored response to the successful brokered call.
    local _broker_reservation_id
    _broker_reservation_id="$("$PYTHON_CMD" - "$advisor_packet" "$advisor_response" "$advisor_result" "$prior_task_id" <<'PYEOF' 2>/dev/null || echo ""
import json, sys
packet, response, result = [json.load(open(p, encoding="utf-8")) for p in sys.argv[1:4]]
task_id = sys.argv[4]
checks = {
    "ok": result.get("ok") is True,
    "resume_eligible": result.get("resume_eligible") is True,
    "task_id": result.get("task_id") == task_id,
    "request_id": result.get("request_id") == packet.get("request_id") == response.get("request_id"),
    "evidence_hash": result.get("evidence_hash") == packet.get("evidence_hash") == response.get("evidence_hash"),
    "reservation_id": result.get("reservation_id") == response.get("reservation_id"),
    "advisor": result.get("advisor") == response.get("advisor"),
    "decision": result.get("decision") == response.get("decision"),
    "response": result.get("response") == response,
}
if not all(checks.values()):
    raise SystemExit("binding mismatch: " + ",".join(k for k, v in checks.items() if not v))
print(result["reservation_id"])
PYEOF
)"
    if [ -z "$_broker_reservation_id" ]; then
        echo "Error: advisor-continuation: advisor call result does not match packet/response bindings." >&2
        exit 1
    fi

    # --- 7. Validate response resume eligibility ---
    # Pass expected request/evidence/reservation bindings and original scope
    # constraints so the validator enforces them, not just the shell.
    if [ -n "$PYTHON_CMD" ] && [ -f "${SCRIPT_DIR}/validate-advisor-response.py" ]; then
        # Extract packet scope for validator
        local _packet_allowed_file _packet_forbidden_file
        _packet_allowed_file="${advisor_dir}/packet-allowed-changes.json"
        _packet_forbidden_file="${advisor_dir}/packet-forbidden-changes.json"
        "$PYTHON_CMD" - "$advisor_packet" "$_packet_allowed_file" "$_packet_forbidden_file" <<'PYEOF' 2>/dev/null
import json, sys
pkt = json.load(open(sys.argv[1], encoding="utf-8"))
with open(sys.argv[2], "w") as f:
    json.dump(pkt.get("allowed_changes", []), f)
with open(sys.argv[3], "w") as f:
    json.dump(pkt.get("forbidden_paths", []), f)
PYEOF

        # Extract packet evidence_hash and request_id for validator
        local _pkt_evidence_hash _pkt_request_id
        _pkt_evidence_hash="$("$PYTHON_CMD" - "$advisor_packet" <<'PYEOF' 2>/dev/null || echo ""
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(data.get("evidence_hash", ""))
PYEOF
)"
        _pkt_request_id="$("$PYTHON_CMD" - "$advisor_packet" <<'PYEOF' 2>/dev/null || echo ""
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(data.get("request_id", ""))
PYEOF
)"

        local _validation_output
        _validation_output="$("$PYTHON_CMD" "${SCRIPT_DIR}/validate-advisor-response.py" "$advisor_response" \
            --expected-request-id "$_pkt_request_id" \
            --expected-evidence-hash "$_pkt_evidence_hash" \
            --expected-reservation-id "$_broker_reservation_id" \
            --original-allowed-changes "$_packet_allowed_file" \
            --original-forbidden-changes "$_packet_forbidden_file" \
            --archive-invalid "${advisor_dir}/continuation-validation-invalid.json" 2>&1)" || {
            echo "Error: advisor-continuation: response validation failed:" >&2
            echo "$_validation_output" >&2
            exit 1
        }
        # Check resume_eligible
        local _resume_eligible
        _resume_eligible="$(printf '%s' "$_validation_output" | "$PYTHON_CMD" -c \
            'import json,sys; print(str(json.load(sys.stdin).get("resume_eligible", False)).lower())' \
            2>/dev/null || echo "false")"
        if [ "$_resume_eligible" != "true" ]; then
            echo "Error: advisor-continuation: response is not resume-eligible (resume_eligible=false)." >&2
            exit 1
        fi
    else
        echo "Error: advisor-continuation: response validator unavailable." >&2
        exit 1
    fi

    # --- 8. Recompute canonical state hash and require exact diff_hash match ---
    if [ -n "$PYTHON_CMD" ] && [ -f "${SCRIPT_DIR}/worktree_state_hash.py" ]; then
        local _current_state_hash
        _current_state_hash="$("$PYTHON_CMD" "${SCRIPT_DIR}/worktree_state_hash.py" --worktree "$wt" 2>/dev/null || echo "")"
        if [ -z "$_current_state_hash" ]; then
            echo "Error: advisor-continuation: failed to compute current worktree state hash." >&2
            exit 1
        fi
        local _packet_diff_hash
        _packet_diff_hash="$("$PYTHON_CMD" - "$advisor_packet" <<'PYEOF' 2>/dev/null || echo ""
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(data.get("diff_hash", ""))
PYEOF
)"
        if [ -z "$_packet_diff_hash" ]; then
            echo "Error: advisor-continuation: packet missing diff_hash." >&2
            exit 1
        fi
        if [ "$_current_state_hash" != "$_packet_diff_hash" ]; then
            echo "Error: advisor-continuation: worktree state hash mismatch: current=${_current_state_hash} packet=${_packet_diff_hash}" >&2
            echo "The worktree has changed since the advisor packet was prepared." >&2
            exit 1
        fi
    else
        echo "Error: advisor-continuation: worktree state hash helper unavailable." >&2
        exit 1
    fi

    # --- 9. Validate response bindings: evidence_hash, reservation_id, request_id ---
    local _packet_evidence_hash _response_evidence_hash _response_reservation_id _response_request_id _packet_request_id
    _packet_evidence_hash="$("$PYTHON_CMD" - "$advisor_packet" <<'PYEOF' 2>/dev/null || echo ""
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(data.get("evidence_hash", ""))
PYEOF
)"
    _packet_request_id="$("$PYTHON_CMD" - "$advisor_packet" <<'PYEOF' 2>/dev/null || echo ""
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(data.get("request_id", ""))
PYEOF
)"
    _response_evidence_hash="$("$PYTHON_CMD" - "$advisor_response" <<'PYEOF' 2>/dev/null || echo ""
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(data.get("evidence_hash", ""))
PYEOF
)"
    _response_reservation_id="$("$PYTHON_CMD" - "$advisor_response" <<'PYEOF' 2>/dev/null || echo ""
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(data.get("reservation_id", ""))
PYEOF
)"
    _response_request_id="$("$PYTHON_CMD" - "$advisor_response" <<'PYEOF' 2>/dev/null || echo ""
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(data.get("request_id", ""))
PYEOF
)"

    # Evidence hash must match between packet and response
    if [ -n "$_packet_evidence_hash" ] && [ -n "$_response_evidence_hash" ] && \
       [ "$_packet_evidence_hash" != "$_response_evidence_hash" ]; then
        echo "Error: advisor-continuation: evidence hash mismatch: packet=${_packet_evidence_hash} response=${_response_evidence_hash}" >&2
        exit 1
    fi

    # Request ID must match between packet and response
    if [ -n "$_packet_request_id" ] && [ -n "$_response_request_id" ] && \
       [ "$_packet_request_id" != "$_response_request_id" ]; then
        echo "Error: advisor-continuation: request ID mismatch: packet=${_packet_request_id} response=${_response_request_id}" >&2
        exit 1
    fi

    # Reservation ID must be present in response
    if [ -z "$_response_reservation_id" ]; then
        echo "Error: advisor-continuation: response missing reservation_id." >&2
        exit 1
    fi

    # --- 10. Check changed-path boundaries (pre-execution scope enforcement) ---
    # Enumerate changed paths across unstaged, staged, and untracked state.
    # Writable scope: response allowed_changes ONLY (not union with packet).
    # An advisor "narrow" decision restricts scope; union would defeat that.
    # Forbidden scope: union of response forbidden_changes ∪ packet forbidden_paths.
    local _allowed_changes _forbidden_changes
    _allowed_changes="$("$PYTHON_CMD" - "$advisor_response" <<'PYEOF' 2>/dev/null || echo ""
import json, sys
resp = json.load(open(sys.argv[1], encoding="utf-8"))
# Writable scope is the validated response subset only, never broader than packet.
resp_allowed = sorted(resp.get("allowed_changes", []))
print("\n".join(resp_allowed))
PYEOF
)"
    _forbidden_changes="$("$PYTHON_CMD" - "$advisor_response" "$advisor_packet" <<'PYEOF' 2>/dev/null || echo ""
import json, sys
resp = json.load(open(sys.argv[1], encoding="utf-8"))
pkt = json.load(open(sys.argv[2], encoding="utf-8"))
# Forbidden scope is the union/superset.
resp_forbidden = set(resp.get("forbidden_changes", []))
pkt_forbidden = set(pkt.get("forbidden_paths", []))
all_forbidden = sorted(resp_forbidden | pkt_forbidden)
print("\n".join(all_forbidden))
PYEOF
)"

    # Enumerate all changed paths: unstaged + staged + untracked
    local _unstaged_files _staged_files _untracked_files _changed_files
    _unstaged_files="$(git -C "$wt" diff --name-only 2>/dev/null || true)"
    _staged_files="$(git -C "$wt" diff --cached --name-only 2>/dev/null || true)"
    _untracked_files="$(git -C "$wt" ls-files --others --exclude-standard 2>/dev/null || true)"
    _changed_files="$(printf '%s\n%s\n%s\n' "$_unstaged_files" "$_staged_files" "$_untracked_files" | sort -u | sed '/^$/d')"

    # Check all changed files are inside allowed scope
    if [ -n "$_changed_files" ] && [ -n "$_allowed_changes" ]; then
        local _violation=""
        while IFS= read -r _cf; do
            [ -z "$_cf" ] && continue
            # Skip known control artifacts
            case "$_cf" in
                CLAUDE_PROGRESS.md|CLAUDE_REPORT.md|CLAUDE_TASK_CARD.md|CLAUDE_PROMPT.md|TASK_CARD.md|TASK_CARD_FULL.md|ADVISOR_REQUEST.json|advisor-*.json|advisor-*.md|truncation-manifest.json)
                    continue ;;
            esac
            # Check if changed file is inside any allowed path
            local _allowed=0
            while IFS= read -r _ac; do
                [ -z "$_ac" ] && continue
                if [ "$_cf" = "$_ac" ] || [[ "$_cf" == "${_ac}/"* ]]; then
                    _allowed=1
                    break
                fi
            done <<< "$_allowed_changes"
            if [ "$_allowed" -eq 0 ]; then
                _violation="${_violation}  ${_cf}\n"
            fi
        done <<< "$_changed_files"
        if [ -n "$_violation" ]; then
            echo "Error: advisor-continuation: changed files outside allowed scope:" >&2
            printf '%b' "$_violation" >&2
            exit 1
        fi
    fi

    # Check forbidden paths are not changed
    if [ -n "$_changed_files" ] && [ -n "$_forbidden_changes" ]; then
        local _forbidden_violation=""
        while IFS= read -r _cf; do
            [ -z "$_cf" ] && continue
            # Skip known control artifacts
            case "$_cf" in
                CLAUDE_PROGRESS.md|CLAUDE_REPORT.md|CLAUDE_TASK_CARD.md|CLAUDE_PROMPT.md|TASK_CARD.md|TASK_CARD_FULL.md|ADVISOR_REQUEST.json|advisor-*.json|advisor-*.md|truncation-manifest.json)
                    continue ;;
            esac
            while IFS= read -r _fp; do
                [ -z "$_fp" ] && continue
                if [ "$_cf" = "$_fp" ] || [[ "$_cf" == "${_fp}/"* ]]; then
                    _forbidden_violation="${_forbidden_violation}  ${_cf}\n"
                    break
                fi
            done <<< "$_forbidden_changes"
        done <<< "$_changed_files"
        if [ -n "$_forbidden_violation" ]; then
            echo "Error: advisor-continuation: changed files in forbidden paths:" >&2
            printf '%b' "$_forbidden_violation" >&2
            exit 1
        fi
    fi

    # --- 11. Once-only continuation claim ---
    # Consumed marker is written AFTER all preflight validations pass
    # (hash, scope, bindings, decision).  This ensures scope/hash failures
    # do not consume the continuation.
    local _consumed_marker="${prior_root}.advisor-continue-consumed"
    if [ -f "$_consumed_marker" ]; then
        echo "Error: advisor-continuation: continuation already consumed for task ${prior_task_id}." >&2
        echo "Consumed marker: ${_consumed_marker}" >&2
        exit 1
    fi

    # Ephemeral concurrency lock (prevents concurrent claim)
    _ADVISOR_CONTINUE_RESERVATION_DIR="${WORKTREE_ROOT}/.advisor-continue-lock-${prior_task_id}"
    if ! mkdir "$_ADVISOR_CONTINUE_RESERVATION_DIR" 2>/dev/null; then
        echo "Error: advisor-continuation: reservation already exists for task ${prior_task_id}." >&2
        echo "Another dispatcher may be claiming this advisor continuation." >&2
        exit 1
    fi
    echo "$$" > "${_ADVISOR_CONTINUE_RESERVATION_DIR}/pid"
    # Ephemeral lock is cleaned on exit; consumed marker is NOT.
    trap 'rm -rf "$_ADVISOR_CONTINUE_RESERVATION_DIR"' EXIT

    # Bind consumed marker to request_id + reservation_id (safe digest)
    local _marker_digest
    _marker_digest="$("$PYTHON_CMD" - "$_response_request_id" "$_response_reservation_id" <<'PYEOF' 2>/dev/null || echo ""
import hashlib, sys
rid = sys.argv[1].strip()
resid = sys.argv[2].strip()
digest = hashlib.sha256(f"{rid}:{resid}".encode()).hexdigest()[:16]
print(digest)
PYEOF
)"
    # Also include task_id for diagnostics
    local _consumed_tmp="${_consumed_marker}.tmp.$$"
    {
        echo "{"
        printf '  "task_id": "%s",\n' "$prior_task_id"
        printf '  "request_id": "%s",\n' "$_response_request_id"
        printf '  "reservation_id": "%s",\n' "$_response_reservation_id"
        printf '  "marker_digest": "%s",\n' "$_marker_digest"
        printf '  "consumed_by_pid": "%s",\n' "$$"
        printf '  "consumed_at": "%s"\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date '+%Y-%m-%d %H:%M:%S')"
        echo "}"
    } > "$_consumed_tmp"
    mv "$_consumed_tmp" "$_consumed_marker"

    # --- 12. Extract response data for continuation card ---
    local _response_decision
    _response_decision="$("$PYTHON_CMD" - "$advisor_response" <<'PYEOF' 2>/dev/null || echo "unknown"
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(data.get("decision", "unknown"))
PYEOF
)"
    if [ "$_response_decision" = "stop" ] || [ "$_response_decision" = "split" ]; then
        echo "Error: advisor-continuation: response decision is '${_response_decision}', not resumable." >&2
        exit 1
    fi

    _ADVISOR_CONTINUE_RESPONSE="$advisor_response"
    _ADVISOR_CONTINUE_RESERVATION_ID="$_response_reservation_id"
    _ADVISOR_CONTINUE_TASK_ID="$prior_task_id"
    _ADVISOR_CONTINUE_WORKTREE_DIR="$wt"
    _ADVISOR_CONTINUE_FORBIDDEN_CHANGES="$_forbidden_changes"
    _ADVISOR_CONTINUE_ALLOWED_CHANGES="$_allowed_changes"
    [ -n "$_ADVISOR_CONTINUE_BRANCH" ] || _ADVISOR_CONTINUE_BRANCH="claude-advisor-continue-${prior_task_id}"
}

# --- Post-execution scope enforcement for advisor continuation ---
# Recomputes changed paths after Claude exits and validates against
# the validated allowed/forbidden boundaries.  A violation produces
# non-zero semantic failure, remains isolated, and never reports acceptance/merge.
post_run_scope_enforcement() {
    local _wt="$1"
    local _allowed="$2"
    local _forbidden="$3"
    local _prior_task_id="$4"

    if [ -z "$_wt" ] || [ ! -d "$_wt" ]; then
        echo "Error: post-run scope enforcement: worktree missing: ${_wt}" >&2
        return 1
    fi

    # Enumerate all changed paths after Claude execution
    local _unstaged_files _staged_files _untracked_files _changed_files
    _unstaged_files="$(git -C "$_wt" diff --name-only 2>/dev/null || true)"
    _staged_files="$(git -C "$_wt" diff --cached --name-only 2>/dev/null || true)"
    _untracked_files="$(git -C "$_wt" ls-files --others --exclude-standard 2>/dev/null || true)"
    _changed_files="$(printf '%s\n%s\n%s\n' "$_unstaged_files" "$_staged_files" "$_untracked_files" | sort -u | sed '/^$/d')"

    if [ -z "$_changed_files" ]; then
        return 0  # No changes — clean
    fi

    # Check all changed files are inside allowed scope
    if [ -n "$_allowed" ]; then
        local _violation=""
        while IFS= read -r _cf; do
            [ -z "$_cf" ] && continue
            # Skip known control artifacts
            case "$_cf" in
                CLAUDE_PROGRESS.md|CLAUDE_REPORT.md|CLAUDE_TASK_CARD.md|CLAUDE_PROMPT.md|TASK_CARD.md|TASK_CARD_FULL.md|ADVISOR_REQUEST.json|advisor-*.json|advisor-*.md|truncation-manifest.json)
                    continue ;;
            esac
            local _allowed_match=0
            while IFS= read -r _ac; do
                [ -z "$_ac" ] && continue
                if [ "$_cf" = "$_ac" ] || [[ "$_cf" == "${_ac}/"* ]]; then
                    _allowed_match=1
                    break
                fi
            done <<< "$_allowed"
            if [ "$_allowed_match" -eq 0 ]; then
                _violation="${_violation}  ${_cf}\n"
            fi
        done <<< "$_changed_files"
        if [ -n "$_violation" ]; then
            echo "Error: post-run scope violation: changed files outside allowed scope:" >&2
            printf '%b' "$_violation" >&2
            return 1
        fi
    fi

    # Check forbidden paths are not changed
    if [ -n "$_forbidden" ]; then
        local _forbidden_violation=""
        while IFS= read -r _cf; do
            [ -z "$_cf" ] && continue
            # Skip known control artifacts
            case "$_cf" in
                CLAUDE_PROGRESS.md|CLAUDE_REPORT.md|CLAUDE_TASK_CARD.md|CLAUDE_PROMPT.md|TASK_CARD.md|TASK_CARD_FULL.md|ADVISOR_REQUEST.json|advisor-*.json|advisor-*.md|truncation-manifest.json)
                    continue ;;
            esac
            while IFS= read -r _fp; do
                [ -z "$_fp" ] && continue
                if [ "$_cf" = "$_fp" ] || [[ "$_cf" == "${_fp}/"* ]]; then
                    _forbidden_violation="${_forbidden_violation}  ${_cf}\n"
                    break
                fi
            done <<< "$_forbidden"
        done <<< "$_changed_files"
        if [ -n "$_forbidden_violation" ]; then
            echo "Error: post-run scope violation: changed files in forbidden paths:" >&2
            printf '%b' "$_forbidden_violation" >&2
            return 1
        fi
    fi

    return 0
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
elif [ -n "${CLAUDE_CODE_ADVISOR_CONTINUE_TASK_ID:-}" ]; then
    # --- Advisor continuation setup ---
    # Validate and reuse prior worktree with advisor response artifacts.
    # On failure, exits with actionable error (fail closed).
    _ADVISOR_CONTINUE_TASK_ID=""
    _ADVISOR_CONTINUE_WORKTREE_DIR=""
    _ADVISOR_CONTINUE_BRANCH=""
    _ADVISOR_CONTINUE_RESPONSE=""
    _ADVISOR_CONTINUE_RESERVATION_DIR=""
    _ADVISOR_CONTINUE_RESERVATION_ID=""
    _ADVISOR_CONTINUE_FORBIDDEN_CHANGES=""
    _ADVISOR_CONTINUE_ALLOWED_CHANGES=""
    validate_advisor_continuation "$CLAUDE_CODE_ADVISOR_CONTINUE_TASK_ID"
    # Continuation must receive a new unique TASK_ID; prior ID is for provenance.
    TASK_ID="claude-advisor-${TIMESTAMP}-${RAND_SUFFIX}"
    WORKTREE_DIR="$_ADVISOR_CONTINUE_WORKTREE_DIR"
    BRANCH_NAME="$_ADVISOR_CONTINUE_BRANCH"
    echo "Worktree reuse (advisor-continuation): $WORKTREE_DIR (prior task: $_ADVISOR_CONTINUE_TASK_ID, new task: $TASK_ID)"
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
ATTEMPT_CLASSIFICATION_FILE="${WORKTREE_ROOT}/${TASK_ID}.attempt-classification.json"
INTERACTION_HEALTH_FILE="${WORKTREE_ROOT}/${TASK_ID}.interaction-health.json"
STARTUP_INTERACTION_HEALTH_FILE="${WORKTREE_ROOT}/${TASK_ID}.startup-interaction-health.json"
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
    DIRTY_UNTRACKED="$(git ls-files --others --exclude-standard 2>/dev/null \
        | grep -v -E "^\.worktrees/" \
        | grep -vxF "$TASK_CARD_REL" \
        | grep -vxF ".ai-workflow/model-calls.jsonl" \
        | grep -vxF ".ai-workflow/model-calls.lock" \
        | grep -vxF ".ai-workflow/run-ledger.lock" \
        || true)"
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

# Initialize runtime evidence only after source preflight succeeds. Failed
# dirty-source checks must remain artifact-free.
: > "$INTERACTION_HEALTH_FILE"
: > "$STARTUP_INTERACTION_HEALTH_FILE"
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

# --- External integration path validation ---
# Validates declared MCP config and plugin paths after the worktree exists.
# Sets: _MCP_CONFIG_PATHS, _PLUGIN_PATHS, _EXTERNAL_INTEGRATION_REJECTION
# Rejects: absolute paths, empty entries, ".." traversal, control characters,
# paths resolving outside worktree, missing files, wrong types/extensions.
validate_external_integration_paths() {
    local wt_dir="$1"
    _MCP_CONFIG_PATHS=()
    _PLUGIN_PATHS=()
    _MCP_CONFIG_PATHS_EVIDENCE="none"
    _PLUGIN_PATHS_EVIDENCE="none"
    _EXTERNAL_INTEGRATION_REJECTION=""

    if [ "$_EXTERNAL_INTEGRATIONS_ALLOWED" != "yes" ]; then
        return 0
    fi

    local _any_valid=0

    # --- Validate MCP config paths ---
    if [ "$_MCP_CONFIG_PATHS_RAW" != "none" ]; then
        local _mcp_list="$_MCP_CONFIG_PATHS_RAW"
        local -a _mcp_parts=()
        IFS=',' read -r -a _mcp_parts <<< "$_mcp_list"
        for _mcp_entry in "${_mcp_parts[@]}"; do
                # Trim whitespace
                _mcp_entry="$(printf '%s' "$_mcp_entry" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
                if [ -z "$_mcp_entry" ]; then
                    _EXTERNAL_INTEGRATION_REJECTION="empty_mcp_path"
                    echo "Error: external integration: empty MCP config path entry." >&2
                    return 1
                fi
                # Reject control characters and newlines
                if printf '%s' "$_mcp_entry" | LC_ALL=C grep -q '[[:cntrl:]]'; then
                    _EXTERNAL_INTEGRATION_REJECTION="invalid_mcp_path_characters"
                    echo "Error: external integration: MCP path contains control characters: ${_mcp_entry}" >&2
                    return 1
                fi
                case "$_mcp_entry" in
                    *'"'*|*'\'*|*'|'*)
                        _EXTERNAL_INTEGRATION_REJECTION="unsafe_mcp_path_characters"
                        echo "Error: external integration: MCP path contains unsupported evidence characters." >&2
                        return 1
                        ;;
                esac
                # Reject absolute paths
                case "$_mcp_entry" in
                    /*|[A-Za-z]:\\*|[A-Za-z]:/*)
                        _EXTERNAL_INTEGRATION_REJECTION="absolute_mcp_path"
                        echo "Error: external integration: absolute MCP path rejected: ${_mcp_entry}" >&2
                        return 1
                        ;;
                esac
                # Reject ".." traversal
                case "$_mcp_entry" in
                    *../*|*/..|..)
                        _EXTERNAL_INTEGRATION_REJECTION="traversal_mcp_path"
                        echo "Error: external integration: MCP path contains '..' traversal: ${_mcp_entry}" >&2
                        return 1
                        ;;
                esac
                # Must end in .json
                case "$_mcp_entry" in
                    *.json) ;;
                    *)
                        _EXTERNAL_INTEGRATION_REJECTION="mcp_not_json"
                        echo "Error: external integration: MCP config must be .json: ${_mcp_entry}" >&2
                        return 1
                        ;;
                esac
                # Resolve and check containment within worktree
                local _mcp_resolved
                _mcp_resolved="$(cd "$wt_dir" && realpath -m "$_mcp_entry" 2>/dev/null || echo "")"
                if [ -z "$_mcp_resolved" ]; then
                    _EXTERNAL_INTEGRATION_REJECTION="mcp_resolve_failed"
                    echo "Error: external integration: cannot resolve MCP path: ${_mcp_entry}" >&2
                    return 1
                fi
                case "$_mcp_resolved" in
                    "${wt_dir}"/*) ;;
                    *)
                        _EXTERNAL_INTEGRATION_REJECTION="mcp_outside_worktree"
                        echo "Error: external integration: MCP path resolves outside worktree: ${_mcp_entry}" >&2
                        return 1
                        ;;
                esac
                # Must be a regular file
                if [ ! -f "$_mcp_resolved" ]; then
                    _EXTERNAL_INTEGRATION_REJECTION="mcp_missing"
                    echo "Error: external integration: MCP config file not found: ${_mcp_entry}" >&2
                    return 1
                fi
                _MCP_CONFIG_PATHS+=("$_mcp_entry")
                _any_valid=1
        done <<< "$_mcp_list"
        _MCP_CONFIG_PATHS_EVIDENCE="$(IFS=,; printf '%s' "${_MCP_CONFIG_PATHS[*]}")"
    fi

    # --- Validate plugin paths ---
    if [ "$_PLUGIN_PATHS_RAW" != "none" ]; then
        local _plugin_list="$_PLUGIN_PATHS_RAW"
        local -a _plugin_parts=()
        IFS=',' read -r -a _plugin_parts <<< "$_plugin_list"
        for _plugin_entry in "${_plugin_parts[@]}"; do
                _plugin_entry="$(printf '%s' "$_plugin_entry" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
                if [ -z "$_plugin_entry" ]; then
                    _EXTERNAL_INTEGRATION_REJECTION="empty_plugin_path"
                    echo "Error: external integration: empty plugin path entry." >&2
                    return 1
                fi
                if printf '%s' "$_plugin_entry" | LC_ALL=C grep -q '[[:cntrl:]]'; then
                    _EXTERNAL_INTEGRATION_REJECTION="invalid_plugin_path_characters"
                    echo "Error: external integration: plugin path contains control characters: ${_plugin_entry}" >&2
                    return 1
                fi
                case "$_plugin_entry" in
                    *'"'*|*'\'*|*'|'*)
                        _EXTERNAL_INTEGRATION_REJECTION="unsafe_plugin_path_characters"
                        echo "Error: external integration: plugin path contains unsupported evidence characters." >&2
                        return 1
                        ;;
                esac
                case "$_plugin_entry" in
                    /*|[A-Za-z]:\\*|[A-Za-z]:/*)
                        _EXTERNAL_INTEGRATION_REJECTION="absolute_plugin_path"
                        echo "Error: external integration: absolute plugin path rejected: ${_plugin_entry}" >&2
                        return 1
                        ;;
                esac
                case "$_plugin_entry" in
                    *../*|*/..|..)
                        _EXTERNAL_INTEGRATION_REJECTION="traversal_plugin_path"
                        echo "Error: external integration: plugin path contains '..' traversal: ${_plugin_entry}" >&2
                        return 1
                        ;;
                esac
                local _plugin_resolved
                _plugin_resolved="$(cd "$wt_dir" && realpath -m "$_plugin_entry" 2>/dev/null || echo "")"
                if [ -z "$_plugin_resolved" ]; then
                    _EXTERNAL_INTEGRATION_REJECTION="plugin_resolve_failed"
                    echo "Error: external integration: cannot resolve plugin path: ${_plugin_entry}" >&2
                    return 1
                fi
                case "$_plugin_resolved" in
                    "${wt_dir}"/*) ;;
                    *)
                        _EXTERNAL_INTEGRATION_REJECTION="plugin_outside_worktree"
                        echo "Error: external integration: plugin path resolves outside worktree: ${_plugin_entry}" >&2
                        return 1
                        ;;
                esac
                # Must be a directory or .zip file
                if [ -d "$_plugin_resolved" ]; then
                    : # directory is valid
                elif [ -f "$_plugin_resolved" ]; then
                    case "$_plugin_resolved" in
                        *.zip) ;;
                        *)
                            _EXTERNAL_INTEGRATION_REJECTION="plugin_not_zip"
                            echo "Error: external integration: plugin file must be .zip: ${_plugin_entry}" >&2
                            return 1
                            ;;
                    esac
                else
                    _EXTERNAL_INTEGRATION_REJECTION="plugin_missing"
                    echo "Error: external integration: plugin path not found: ${_plugin_entry}" >&2
                    return 1
                fi
                _PLUGIN_PATHS+=("$_plugin_entry")
                _any_valid=1
        done <<< "$_plugin_list"
        _PLUGIN_PATHS_EVIDENCE="$(IFS=,; printf '%s' "${_PLUGIN_PATHS[*]}")"
    fi

    # Require at least one valid integration when authorized
    if [ "$_any_valid" -eq 0 ]; then
        _EXTERNAL_INTEGRATION_REJECTION="no_integrations_declared"
        echo "Error: external integration: 'External integrations allowed?' is 'yes' but no valid MCP or plugin paths declared." >&2
        return 1
    fi

    return 0
}

# Skip worktree creation when retry/advisor continuation already supplied a
# validated worktree with preserved implementation progress.
if [ -z "${_RETRY_WORKTREE_DIR:-}" ] && [ -z "${_ADVISOR_CONTINUE_WORKTREE_DIR:-}" ]; then
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

# Validate explicitly declared integrations only after the isolated worktree
# exists, but before writing runtime/source evidence or invoking Claude.
_MCP_CONFIG_PATHS=()
_PLUGIN_PATHS=()
_MCP_CONFIG_PATHS_EVIDENCE="none"
_PLUGIN_PATHS_EVIDENCE="none"
_EXTERNAL_INTEGRATION_REJECTION=""
_EXTERNAL_INTEGRATION_VALID=1
if [ "$_EXTERNAL_INTEGRATIONS_ALLOWED" = "yes" ]; then
    if ! validate_external_integration_paths "$WORKTREE_DIR"; then
        _EXTERNAL_INTEGRATION_VALID=0
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
    echo "- Tool profile: ${CLAUDE_CODE_TOOL_PROFILE} (${_TOOL_PROFILE_DERIVATION})"
    echo "- Tool profile CLI supported: $([ "$_TOOL_PROFILE_SUPPORTED" -eq 1 ] && echo yes || echo no)"
    echo "- Task validation allowlist: $([ "$CLAUDE_CODE_TASK_VALIDATION_ALLOWLIST" -eq 1 ] && echo enabled || echo disabled)"
    echo "- External integrations allowed: ${_EXTERNAL_INTEGRATIONS_ALLOWED}"
    echo "- Strict MCP isolation: ${_STRICT_MCP_ISOLATION}"
    echo "- MCP config paths: ${_MCP_CONFIG_PATHS_EVIDENCE}"
    echo "- Plugin paths: ${_PLUGIN_PATHS_EVIDENCE}"
    echo "- External integration rejection: ${_EXTERNAL_INTEGRATION_REJECTION:-none}"
    echo "- First-progress timeout: ${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS}s (source: ${_FIRST_PROGRESS_TIMEOUT_SOURCE:-default})"
    echo "- First-progress action: ${CLAUDE_CODE_FIRST_PROGRESS_ACTION}"
    echo "- Progress extension seconds: ${CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS}s"
    echo "- Growing progress extension seconds: ${CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS}s"
    echo "- Recent activity window seconds: ${CLAUDE_CODE_RECENT_ACTIVITY_WINDOW_SECONDS}s"
    echo "- API probe mode: ${CLAUDE_CODE_API_PROBE_MODE}"
    echo "- Probe environment: ${CLAUDE_CODE_PROBE_ENVIRONMENT}"
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
    printf '  "tool_profile": "%s",\n' "$CLAUDE_CODE_TOOL_PROFILE"
    printf '  "tool_profile_derivation": "%s",\n' "$_TOOL_PROFILE_DERIVATION"
    printf '  "tool_profile_supported": %s,\n' "$([ "$_TOOL_PROFILE_SUPPORTED" -eq 1 ] && echo true || echo false)"
    printf '  "task_validation_allowlist": %s,\n' "$([ "$CLAUDE_CODE_TASK_VALIDATION_ALLOWLIST" -eq 1 ] && echo true || echo false)"
    printf '  "first_progress_timeout_seconds": %s,\n' "$CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS"
    printf '  "first_progress_timeout_source": "%s",\n' "${_FIRST_PROGRESS_TIMEOUT_SOURCE:-default}"
    printf '  "base_timeout_seconds": %s,\n' "$CLAUDE_CODE_TIMEOUT_SECONDS"
    printf '  "progress_extension_seconds": %s,\n' "$CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS"
    printf '  "growing_progress_extension_seconds": %s,\n' "$CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS"
    printf '  "recent_activity_window_seconds": %s,\n' "$CLAUDE_CODE_RECENT_ACTIVITY_WINDOW_SECONDS"
    printf '  "probe_mode": "%s",\n' "$CLAUDE_CODE_API_PROBE_MODE"
    printf '  "probe_environment": "%s",\n' "$CLAUDE_CODE_PROBE_ENVIRONMENT"
    printf '  "first_progress_action": "%s",\n' "$CLAUDE_CODE_FIRST_PROGRESS_ACTION"
    printf '  "external_integrations_allowed": "%s",\n' "$_EXTERNAL_INTEGRATIONS_ALLOWED"
    printf '  "strict_mcp_isolation": "%s",\n' "$_STRICT_MCP_ISOLATION"
    printf '  "mcp_config_paths": "%s",\n' "${_MCP_CONFIG_PATHS_EVIDENCE}"
    printf '  "plugin_paths": "%s",\n' "${_PLUGIN_PATHS_EVIDENCE}"
    printf '  "external_integration_rejection": "%s",\n' "${_EXTERNAL_INTEGRATION_REJECTION:-none}"
    printf '  "external_integration_valid": %s\n' "$([ "$_EXTERNAL_INTEGRATION_VALID" -eq 1 ] && echo true || echo false)"
    echo "}"
} > "$_RUNTIME_TMP"
mv "$_RUNTIME_TMP" "$RUNTIME_JSON"
echo "Runtime identity saved to: $RUNTIME_JSON"

if [ "$_EXTERNAL_INTEGRATION_VALID" -ne 1 ]; then
    echo "External integration rejection evidence saved to: $RUNTIME_JSON" >&2
    exit 1
fi

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

# --- ADVISOR_REQUEST contract ---
# Append to the generated task card so Claude receives the exact task ID
# and the structured request contract.  This is a control-plane artifact;
# it must not count as implementation progress.
{
    echo ""
    echo "## ADVISOR_REQUEST Contract"
    echo ""
    echo "When blocked and requesting continuation advice, write \`ADVISOR_REQUEST.json\` to the worktree root with this exact structure:"
    echo ""
    echo '```json'
    echo "{"
    echo '  "schema_version": 1,'
    printf '  "task_id": "%s",\n' "$TASK_ID"
    echo '  "direction": "on-plan",'
    echo '  "blocker": {'
    echo '    "kind": "semantic",'
    echo '    "question": "<your blocker question>",'
    echo '    "blocking": true'
    echo '  },'
    echo '  "completed_work": "<summary of work completed>",'
    echo '  "advisor_used": false'
    echo "}"
    echo '```'
    echo ""
    echo "- \`schema_version\` must be integer \`1\`."
    echo "- \`task_id\` must exactly match the dispatch task ID above."
    echo "- \`direction\` must be \`on-plan\` or \`off-plan\`."
    echo "- \`blocker.kind\` must be \`semantic\`, \`transport\`, \`approval\`, \`direction\`, or \`unknown\`."
    echo "- \`blocker.blocking\` must be \`true\` (this file represents an active blocker request)."
    echo "- \`completed_work\` and \`blocker.question\` must be non-empty strings."
    echo "- \`advisor_used\` must be boolean."
    echo "- No extra fields allowed."
    echo ""
    echo "Ordinary completion must not create this file. It is neither acceptance nor continuation authorization."
} >> "${WORKTREE_DIR}/CLAUDE_TASK_CARD.md"

# --- Advisor continuation card generation ---
# When in advisor continuation mode, replace the task card with a minimal
# continuation execution card that says continue from current progress,
# do not re-plan, and includes only validated answer/scope/new validation.
if [ -n "${_ADVISOR_CONTINUE_RESPONSE:-}" ] && [ -f "${_ADVISOR_CONTINUE_RESPONSE:-/dev/null}" ]; then
    _build_advisor_continuation_card() {
        local response_file="$1"
        local task_id="$2"
        local prior_task_id="$3"
        local completed_work="$4"

        local decision answer advisor
        decision="$("$PYTHON_CMD" - "$response_file" <<'PYEOF' 2>/dev/null || echo "continue"
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(data.get("decision", "continue"))
PYEOF
)"
        answer="$("$PYTHON_CMD" - "$response_file" <<'PYEOF' 2>/dev/null || echo ""
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(data.get("answer", ""))
PYEOF
)"
        advisor="$("$PYTHON_CMD" - "$response_file" <<'PYEOF' 2>/dev/null || echo "unknown"
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(data.get("advisor", "unknown"))
PYEOF
)"

        cat <<CARD_EOF
<!-- Advisor continuation card: do not re-plan -->
# Advisor Continuation Card: ${task_id}

**Prior Task:** ${prior_task_id}
**Advisor:** ${advisor}
**Decision:** ${decision}

## Instructions

This is a **same-worktree advisor continuation**. Do not create a new worktree.
Do not re-plan; continue from current progress.

## Advisor Answer

${answer}

## Completed Work (prior run)

${completed_work}

## Allowed Changes
CARD_EOF

        "$PYTHON_CMD" - "$response_file" <<'PYEOF' 2>/dev/null
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
changes = data.get("allowed_changes", [])
if changes:
    for c in changes:
        print(f"- \`{c}\`")
else:
    print("(none)")
PYEOF

        echo ""
        echo "## Forbidden Changes"

        "$PYTHON_CMD" - "$response_file" <<'PYEOF' 2>/dev/null
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
forbidden = data.get("forbidden_changes", [])
if forbidden:
    for f in forbidden:
        print(f"- \`{f}\`")
else:
    print("(none)")
PYEOF

        echo ""
        echo "## New Validation Commands"

        "$PYTHON_CMD" - "$response_file" <<'PYEOF' 2>/dev/null
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
cmds = data.get("new_validation", [])
if cmds:
    for c in cmds:
        print(f"- \`{c}\`")
else:
    print("(none)")
PYEOF

        cat <<'RULES_EOF'

## Rules

- Do **not** repeat planning; continue from current progress.
- Update `CLAUDE_PROGRESS.md` with your continuation status.
- Update `CLAUDE_REPORT.md` when finished.
- Respect the allowed/forbidden changes listed above.

## Continuation Exploration

Declare any search commands run and paths read during this continuation.
Report `none` if no exploration was performed.

- Search commands: `<commands or none>`
- Paths read: `<paths or none>`
RULES_EOF
    }

    # Load completed_work from the advisor packet
    _prior_completed_work="$("$PYTHON_CMD" - "${WORKTREE_DIR}/advisor-packet.json" <<'PYEOF' 2>/dev/null || echo ""
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(data.get("completed_work", ""))
PYEOF
)"

    _build_advisor_continuation_card \
        "$_ADVISOR_CONTINUE_RESPONSE" \
        "$TASK_ID" \
        "$_ADVISOR_CONTINUE_TASK_ID" \
        "$_prior_completed_work" \
        > "${WORKTREE_DIR}/CLAUDE_TASK_CARD.md"

    if [ "$CLAUDE_CODE_VERBOSE" = "1" ]; then
        echo "Advisor continuation card rendered to: ${WORKTREE_DIR}/CLAUDE_TASK_CARD.md"
    fi
fi

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
- When blocked and requesting continuation advice, create `ADVISOR_REQUEST.json` exactly as described in the task card.

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

- If `CLAUDE_CONTEXT_PACKET.md` is present, read it before exploring the codebase. It contains pre-computed target files, symbols, snippets, and constraints.

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

Context Packet:
- If `CLAUDE_CONTEXT_PACKET.md` is present, read it before exploring the codebase. It contains pre-computed target files, symbols, snippets, and constraints relevant to this task.
- The context packet is dispatch evidence and should not be counted as an implementation change.

--- CLAUDE EXECUTION CARD ---
EOF
fi
cat "${WORKTREE_DIR}/CLAUDE_TASK_CARD.md" >> "${WORKTREE_DIR}/CLAUDE_PROMPT.md"

CLAUDE_CODE_TIMEOUT_SECONDS="${CLAUDE_CODE_TIMEOUT_SECONDS:-600}"
CLAUDE_CODE_HEARTBEAT_SECONDS="${CLAUDE_CODE_HEARTBEAT_SECONDS:-30}"
CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS="${CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS:-0}"
CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS="${CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS:-300}"
CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS="${CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS:-300}"

PYTHON_CMD=""
if command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
elif command -v python &>/dev/null; then
    PYTHON_CMD="python"
fi

# --- Route preference learning (continued) ---
# Consult the learned route helper when caller did not explicitly set the mode.
ROUTE_PREFERENCE_HELPER="${SCRIPT_DIR}/claude-route-preference.py"
if [ "$_ROUTE_SOURCE" = "default" ] && [ -n "$PYTHON_CMD" ] && [ -f "$ROUTE_PREFERENCE_HELPER" ]; then
    _LEARNED_ROUTE="$("$PYTHON_CMD" "$ROUTE_PREFERENCE_HELPER" resolve --fallback "" 2>/dev/null || true)"
    if [ "$_LEARNED_ROUTE" = "direct" ] || [ "$_LEARNED_ROUTE" = "inherit" ]; then
        CLAUDE_CODE_PROXY_MODE="$_LEARNED_ROUTE"
        _ROUTE_SOURCE="learned"
    fi
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
case "$CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS" in
    ''|*[!0-9]*)
        echo "Error: CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS must be a non-negative integer." >&2
        exit 1
        ;;
esac
case "$CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS" in
    ''|*[!0-9]*)
        echo "Error: CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS must be a non-negative integer." >&2
        exit 1
        ;;
esac
case "$CLAUDE_CODE_RECENT_ACTIVITY_WINDOW_SECONDS" in
    ''|*[!0-9]*)
        echo "Error: CLAUDE_CODE_RECENT_ACTIVITY_WINDOW_SECONDS must be a non-negative integer." >&2
        exit 1
        ;;
esac
case "$CLAUDE_CODE_ZERO_OUTPUT_PROBE_TIMEOUT_SECONDS" in
    ''|*[!0-9]*|0)
        echo "Error: CLAUDE_CODE_ZERO_OUTPUT_PROBE_TIMEOUT_SECONDS must be a positive integer." >&2
        exit 1
        ;;
esac

progress_log() {
    local message="$1"
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$message" | tee -a "$PROGRESS_FILE"
}

ZERO_OUTPUT_PROBE_CONCLUSION="not-run"
ZERO_OUTPUT_PROBE_AUTHORITATIVE="no"
_LAST_PROBE_CONCLUSION=""
_LAST_PROBE_AUTHORITATIVE="no"
_OBSERVATION_PROBE_RAN=0
_OBSERVATION_PROBE_CONCLUSION=""
_OBSERVATION_PROBE_AUTHORITATIVE="no"

# Unified interaction probe for startup and zero-output phases.
# Accepts phase: "startup", "zero-output", or "observation".
# Writes to the caller-supplied artifact file.
# Sets _LAST_PROBE_CONCLUSION and _LAST_PROBE_AUTHORITATIVE.
# Caller is responsible for promoting to ZERO_OUTPUT_PROBE_* when appropriate.
run_interaction_probe() {
    local phase="$1"
    local artifact_file="$2"
    local helper="${SCRIPT_DIR}/claude-healthcheck.py"
    _LAST_PROBE_CONCLUSION=""
    _LAST_PROBE_AUTHORITATIVE="no"
    if [ -z "$PYTHON_CMD" ] || [ ! -f "$helper" ]; then
        progress_log "Interaction probe (${phase}) skipped: healthcheck helper unavailable"
        return 0
    fi
    local probe_env_args=()
    if [ -n "${CLAUDE_CODE_PROBE_ENVIRONMENT:-}" ] && [ "$CLAUDE_CODE_PROBE_ENVIRONMENT" != "auto" ]; then
        probe_env_args=(--probe-environment "$CLAUDE_CODE_PROBE_ENVIRONMENT")
    fi
    progress_log "Interaction probe (${phase}): checking Claude API with fixed prompt via route=${CLAUDE_CODE_PROXY_MODE}, environment=${CLAUDE_CODE_PROBE_ENVIRONMENT:-auto}"
    "$PYTHON_CMD" "$helper" --interaction-route "$CLAUDE_CODE_PROXY_MODE" \
        --timeout "$CLAUDE_CODE_ZERO_OUTPUT_PROBE_TIMEOUT_SECONDS" --prompt '你好' --json \
        "${probe_env_args[@]}" \
        > "$artifact_file" 2>/dev/null || true
    if [ ! -s "$artifact_file" ]; then
        _LAST_PROBE_CONCLUSION="unavailable-in-current-environment"
        progress_log "Interaction probe (${phase}) returned no diagnostic output"
        return 0
    fi
    _LAST_PROBE_CONCLUSION="$("$PYTHON_CMD" - "$artifact_file" <<'PYEOF' 2>/dev/null || true
import json, sys
try:
    value = json.load(open(sys.argv[1], encoding="utf-8"))
    print(value.get("interaction_conclusion", "unavailable-in-current-environment"))
except (OSError, ValueError, TypeError):
    print("unavailable-in-current-environment")
PYEOF
)"
    if [ "$_LAST_PROBE_CONCLUSION" = "available" ]; then
        _LAST_PROBE_AUTHORITATIVE="yes"
    fi
    progress_log "Interaction probe (${phase}): conclusion=${_LAST_PROBE_CONCLUSION}, authoritative=${_LAST_PROBE_AUTHORITATIVE}, artifact=${artifact_file}"

    # Record diagnostic ledger entry for the real probe attempt.
    # Each real probe attempt is accounted as diagnostic and does not reduce
    # Builder quota or affect takeover/success classification.
    # Diagnostic recording is advisory; failure cannot change dispatch outcome.
    if [ -f "${SCRIPT_DIR}/model-call-broker.py" ]; then
        _DIAG_COUNTS="$("$PYTHON_CMD" - "$artifact_file" \
            "${SCRIPT_DIR}/model-call-broker.py" "${_RETRY_TASK_ID:-$TASK_ID}" \
            "${REPO_ROOT}/.ai-workflow/model-calls.jsonl" <<'PYEOF' 2>/dev/null || true
import json, subprocess, sys

recorded = failed = 0
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    broker, task_id, ledger = sys.argv[2:5]
    for probe in data.get("interaction_probes", []):
        if not isinstance(probe, dict):
            failed += 1
            continue
        cmd = [
            sys.executable, broker,
            "--role", "claude", "--stage", "interaction-healthcheck",
            "--task-id", task_id, "--ledger", ledger, "--diagnostic",
            "--diagnostic-success", str(bool(probe.get("success"))).lower(),
            "--diagnostic-elapsed", str(probe.get("elapsed_seconds", 0)),
            "--diagnostic-route", str(probe.get("route", "unknown")),
        ]
        optional = (
            ("tokens_in", "--diagnostic-tokens-in"),
            ("tokens_out", "--diagnostic-tokens-out"),
            ("model", "--diagnostic-model"),
        )
        for key, flag in optional:
            if probe.get(key) is not None:
                cmd.extend((flag, str(probe[key])))
        cost = probe.get("cost_usd")
        if cost is not None and cost != "unavailable":
            cmd.extend(("--diagnostic-cost-usd", str(cost)))
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            recorded += 1
        else:
            failed += 1
except Exception:
    failed += 1
print(f"{recorded}\t{failed}")
PYEOF
)"
        IFS=$'\t' read -r _DIAG_RECORDED _DIAG_FAILED <<< "${_DIAG_COUNTS:-0\t1}"
        progress_log "Diagnostic ledger records (${phase}): recorded=${_DIAG_RECORDED:-0}, failed=${_DIAG_FAILED:-1}"
    fi
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

# Detect if substantive progress is actively growing.
# Returns 0 (true) if there is meaningful progress growth; 1 otherwise.
# Tracks actual implementation/progress changes, not merely seeded files or a live PID.
progress_is_growing() {
    local now_digest now_report_bytes now_progress_bytes now_report_hash now_progress_hash
    # 1. Worktree changes (diff from base)
    now_digest="$(worktree_digest)"
    if [ "$now_digest" != "${1:-}" ]; then
        return 0
    fi
    # 2. Valid report content changed. Hash comparison catches rewrites that
    # keep or reduce the byte count; size growth remains a cheap positive path.
    now_report_bytes="$(file_size "${WORKTREE_DIR}/CLAUDE_REPORT.md")"
    if valid_claude_report_file "${WORKTREE_DIR}/CLAUDE_REPORT.md"; then
        now_report_hash="$(sha1sum "${WORKTREE_DIR}/CLAUDE_REPORT.md" 2>/dev/null | awk '{print $1}' || true)"
        if [ "$now_report_bytes" -gt "${2:-0}" ] || [ "$now_report_hash" != "${4:-}" ]; then
            return 0
        fi
    fi
    # 3. Non-seeded progress content changed, including shorter rewrites.
    now_progress_bytes="$(file_size "${WORKTREE_DIR}/CLAUDE_PROGRESS.md")"
    if [ -s "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" ] && \
       ! file_contains "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" "$SEEDED_PROGRESS_MARKER"; then
        now_progress_hash="$(sha1sum "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" 2>/dev/null | awk '{print $1}' || true)"
        if [ "$now_progress_bytes" -gt "${3:-0}" ] || [ "$now_progress_hash" != "${5:-}" ]; then
            return 0
        fi
    fi
    return 1
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
        | grep -v -E '^(.. )?(TASK_CARD|TASK_CARD_FULL|CLAUDE_TASK_CARD|CLAUDE_PROMPT|CLAUDE_REPORT|CLAUDE_PROGRESS|ADVISOR_REQUEST)(\.md|\.json)?$' || true)
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
            | grep -v -E '^(.. )?(TASK_CARD|TASK_CARD_FULL|CLAUDE_TASK_CARD|CLAUDE_PROMPT|CLAUDE_REPORT|CLAUDE_PROGRESS|ADVISOR_REQUEST)(\.md|\.json)?$' || true
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
            | grep -v -E '^(.. )?(TASK_CARD|TASK_CARD_FULL|CLAUDE_TASK_CARD|CLAUDE_PROMPT|CLAUDE_REPORT|CLAUDE_PROGRESS|ADVISOR_REQUEST)(\.md|\.json)?$' || true
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
    progress_log "Stopping Claude (${reason}) after ${elapsed}s; draining leaf processes before wrapper pid=${CLAUDE_PID} descendants=${descendants:-none}"
    # Stop leaf workers first. When model-call-broker is present this gives it
    # a chance to observe the child exit and persist a failed reservation,
    # instead of leaving a permanent `running` record that blocks retry.
    local leaves=""
    if [ -n "$descendants" ] && command -v pgrep >/dev/null 2>&1; then
        local descendant
        for descendant in $descendants; do
            if [ -z "$(pgrep -P "$descendant" 2>/dev/null || true)" ]; then
                leaves="${leaves} ${descendant}"
            fi
        done
    fi
    if [ -n "$leaves" ]; then
        kill $leaves 2>/dev/null || true
    else
        kill "$CLAUDE_PID" 2>/dev/null || true
    fi
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
        progress_log "Claude wrapper still alive after drain; sending TERM to pid=${CLAUDE_PID}"
        kill "$CLAUDE_PID" 2>/dev/null || true
        sleep 1
        if kill -0 "$CLAUDE_PID" 2>/dev/null; then
            progress_log "Claude wrapper still alive after TERM; sending KILL to pid=${CLAUDE_PID}"
            kill -9 "$CLAUDE_PID" 2>/dev/null || true
        fi
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
    # Common Claude CLI arguments shared by all invocation paths.
    # Tool profile arrays (_CLAUDE_TOOLS_ARGS, _CLAUDE_ALLOWED_ARGS) are
    # constructed before this function is called and applied identically
    # in direct/inherit and broker/bypass branches.
    # Length check avoids empty-array expansion error under set -u.
    local claude_base_args=(-p --permission-mode acceptEdits --output-format json)
    if [ ${#_CLAUDE_TOOLS_ARGS[@]} -gt 0 ]; then
        claude_base_args+=("${_CLAUDE_TOOLS_ARGS[@]}")
    fi
    if [ ${#_CLAUDE_ALLOWED_ARGS[@]} -gt 0 ]; then
        claude_base_args+=("${_CLAUDE_ALLOWED_ARGS[@]}")
    fi
    # External integration gate: always --bare; add --strict-mcp-config and
    # explicit MCP/plugin paths only when integrations are authorized.
    claude_base_args+=(--bare)
    if [ "$_EXTERNAL_INTEGRATIONS_ALLOWED" = "yes" ] && [ "$_STRICT_MCP_ISOLATION" = "yes" ]; then
        claude_base_args+=(--strict-mcp-config)
        if [ ${#_MCP_CONFIG_PATHS[@]} -gt 0 ]; then
            claude_base_args+=(--mcp-config "${_MCP_CONFIG_PATHS[@]}")
        fi
        if [ ${#_PLUGIN_PATHS[@]} -gt 0 ]; then
            for _pdir in "${_PLUGIN_PATHS[@]}"; do
                claude_base_args+=(--plugin-dir "$_pdir")
            done
        fi
    fi

    if [ "${AI_CODING_WORKFLOW_BYPASS_BROKER:-0}" = "1" ] || [ ! -f "${SCRIPT_DIR}/model-call-broker.py" ]; then
        # Internal bypass for tests/bootstrap to avoid broker recursion.  The
        # missing-helper branch preserves compatibility with old bootstrapped
        # projects and standalone dispatcher fixtures; refreshed installs use
        # the broker by default.
        if [ "$CLAUDE_CODE_PROXY_MODE" = "inherit" ]; then
            claude "${claude_base_args[@]}" \
                < CLAUDE_PROMPT.md > "$RESULT_FILE" 2>"${STATUS_FILE}"
        else
            (
                unset HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY
                unset http_proxy https_proxy all_proxy no_proxy
                claude "${claude_base_args[@]}" \
                    < CLAUDE_PROMPT.md > "$RESULT_FILE" 2>"${STATUS_FILE}"
            )
        fi
    else
        # Broker-mediated execution for quota enforcement and audit.
        local broker_args=(
            --role claude
            --stage builder
            --task-id "${_RETRY_TASK_ID:-$TASK_ID}"
            --ledger "${REPO_ROOT}/.ai-workflow/model-calls.jsonl"
            --input CLAUDE_PROMPT.md
            --output "$RESULT_FILE"
            --stderr "${STATUS_FILE}"
        )
        if [ -f "execution-plan.json" ]; then
            broker_args+=(--plan execution-plan.json)
        elif [ -n "${CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID:-}" ]; then
            # Legacy dispatches have no explicit execution plan. Permit one
            # auditable retry of a failed/cancelled reservation, and no more.
            broker_args+=(--max-calls 2 --retry-failed)
        fi
        if [ "$CLAUDE_CODE_PROXY_MODE" = "inherit" ]; then
            python3 "${SCRIPT_DIR}/model-call-broker.py" "${broker_args[@]}" -- \
                claude "${claude_base_args[@]}"
        else
            (
                unset HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY
                unset http_proxy https_proxy all_proxy no_proxy
                python3 "${SCRIPT_DIR}/model-call-broker.py" "${broker_args[@]}" -- \
                    claude "${claude_base_args[@]}"
            )
        fi
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
progress_log "Starting Claude Code: execution_profile=${CLAUDE_CODE_EXECUTION_PROFILE}, prompt_profile=${CLAUDE_CODE_PROMPT_PROFILE}, evidence_mode=${CLAUDE_CODE_EVIDENCE_MODE}, proxy_mode=${CLAUDE_CODE_PROXY_MODE}, route_source=${_ROUTE_SOURCE}, timeout_seconds=${CLAUDE_CODE_TIMEOUT_SECONDS}, heartbeat_seconds=${CLAUDE_CODE_HEARTBEAT_SECONDS}, no_output_timeout_seconds=${CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS}, network_monitor=${CLAUDE_CODE_NETWORK_MONITOR}, worktree_strategy=${CLAUDE_CODE_WORKTREE_STRATEGY}, large_repo_mode=${CLAUDE_CODE_LARGE_REPO_MODE}, task_mode=${_PARSED_TASK_MODE:-unknown}, verbose=${CLAUDE_CODE_VERBOSE}, approval_convergence=${CLAUDE_CODE_APPROVAL_BLOCKED_CONVERGENCE}, worktree_progress=${CLAUDE_CODE_WORKTREE_PROGRESS}, builder_mode=${CLAUDE_CODE_BUILDER_MODE}, tool_profile=${CLAUDE_CODE_TOOL_PROFILE}, tool_profile_derivation=${_TOOL_PROFILE_DERIVATION}, tool_profile_supported=$([ "$_TOOL_PROFILE_SUPPORTED" -eq 1 ] && echo yes || echo no), task_validation_allowlist=$([ "$CLAUDE_CODE_TASK_VALIDATION_ALLOWLIST" -eq 1 ] && echo yes || echo no), external_integrations_allowed=${_EXTERNAL_INTEGRATIONS_ALLOWED}, strict_mcp_isolation=${_STRICT_MCP_ISOLATION}, mcp_config_paths=${_MCP_CONFIG_PATHS_EVIDENCE}, plugin_paths=${_PLUGIN_PATHS_EVIDENCE}, external_integration_rejection=${_EXTERNAL_INTEGRATION_REJECTION:-none}, first_progress_timeout=${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS}, first_progress_timeout_source=${_FIRST_PROGRESS_TIMEOUT_SOURCE}, first_progress_action=${CLAUDE_CODE_FIRST_PROGRESS_ACTION}, progress_extension_seconds=${CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS}, growing_progress_extension_seconds=${CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS}, api_probe_mode=${CLAUDE_CODE_API_PROBE_MODE}, probe_environment=${CLAUDE_CODE_PROBE_ENVIRONMENT}, startup_probe_conclusion=${_STARTUP_PROBE_CONCLUSION:-not-run}"

# --- Tool profile argument construction ---
# Build arrays for --tools and --allowedTools based on resolved profile.
# These arrays are applied identically in all four Claude invocation paths
# (direct/inherit and broker/bypass).
_CLAUDE_TOOLS_ARGS=()
_CLAUDE_ALLOWED_ARGS=()
_TOOL_PROFILE_AVAILABLE_TOOLS=""
_TOOL_PROFILE_ALLOWLIST_COUNT=0

if [ "$_TOOL_PROFILE_SUPPORTED" -eq 1 ] && [ "$CLAUDE_CODE_TOOL_PROFILE" != "default" ]; then
    case "$CLAUDE_CODE_TOOL_PROFILE" in
        minimal-builder)
            _CLAUDE_TOOLS_ARGS=(--tools "Read,Edit,Write,Bash")
            ;;
        locator-builder)
            _CLAUDE_TOOLS_ARGS=(--tools "Read,Edit,Write,Grep,Glob,Bash")
            ;;
        checker)
            _CLAUDE_TOOLS_ARGS=(--tools "Read,Edit,Write,Grep,Glob,Bash")
            ;;
        diagnostic)
            _CLAUDE_TOOLS_ARGS=(--tools "Read,Grep,Glob,Bash")
            ;;
    esac

    # For non-default profiles, allow Read/Edit/Write when present.
    # Do not auto-allow unrestricted Bash.
    _TOOL_PROFILE_AVAILABLE_TOOLS=""
    case "$CLAUDE_CODE_TOOL_PROFILE" in
        minimal-builder)   _TOOL_PROFILE_AVAILABLE_TOOLS="Read,Edit,Write,Bash" ;;
        locator-builder)   _TOOL_PROFILE_AVAILABLE_TOOLS="Read,Edit,Write,Grep,Glob,Bash" ;;
        checker)           _TOOL_PROFILE_AVAILABLE_TOOLS="Read,Edit,Write,Grep,Glob,Bash" ;;
        diagnostic)        _TOOL_PROFILE_AVAILABLE_TOOLS="Read,Grep,Glob,Bash" ;;
    esac

    # Build allowedTools: allow Read, Edit, Write for profiles that include them.
    _allow_parts=()
    case "$_TOOL_PROFILE_AVAILABLE_TOOLS" in
        *Read*)  _allow_parts+=("Read") ;;
    esac
    case "$_TOOL_PROFILE_AVAILABLE_TOOLS" in
        *Edit*)  _allow_parts+=("Edit") ;;
    esac
    case "$_TOOL_PROFILE_AVAILABLE_TOOLS" in
        *Write*) _allow_parts+=("Write") ;;
    esac

    # Checker profile: extract validation commands from task card.
    # Reports bounded aggregate skip evidence without command bodies or secrets.
    _TOOL_PROFILE_ALLOWLIST_COUNT=0
    _TOOL_PROFILE_ALLOWLIST_UNSAFE=0
    _TOOL_PROFILE_ALLOWLIST_OVERSIZED=0
    _TOOL_PROFILE_ALLOWLIST_OVERFLOW=0
    if [ "$CLAUDE_CODE_TOOL_PROFILE" = "checker" ] && [ "$CLAUDE_CODE_TASK_VALIDATION_ALLOWLIST" = "1" ]; then
        _TASK_CARD_FILE="${WORKTREE_DIR}/CLAUDE_TASK_CARD.md"
        if [ -f "$_TASK_CARD_FILE" ] && [ -n "$PYTHON_CMD" ]; then
            _VALIDATION_SUMMARY_FILE="$(mktemp 2>/dev/null || echo "")"
            _VALIDATION_CMDS="$("$PYTHON_CMD" - "$_TASK_CARD_FILE" "$_VALIDATION_SUMMARY_FILE" <<'PYEOF' 2>/dev/null || echo ""
import json, re, sys

MAX_COMMANDS = 12
MAX_CMD_LEN = 500
# Unsafe shell composition/redirection operators
UNSAFE_RE = re.compile(r'[;&|`><\x00-\x08\x0e-\x1f]')

text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
summary_path = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else None

# Find fenced blocks whose info string contains "validation" or "check"
blocks = re.finditer(r'```[^\n]*(?:validation|check)[^\n]*\n(.*?)```', text, re.I | re.S)

commands = []
unsafe_count = 0
oversized_count = 0
overflow_count = 0
for block in blocks:
    for line in block.group(1).splitlines():
        line = line.strip()
        # Skip empty lines and comments
        if not line or line.startswith('#'):
            continue
        # Reject unsafe commands
        if UNSAFE_RE.search(line):
            unsafe_count += 1
            continue
        # Bound by length
        if len(line) > MAX_CMD_LEN:
            oversized_count += 1
            continue
        if len(commands) >= MAX_COMMANDS:
            overflow_count += 1
            continue
        commands.append(line)

for cmd in commands:
    print(cmd)

# Write aggregate summary (no command bodies or secrets)
if summary_path:
    try:
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump({
                "accepted": len(commands),
                "unsafe": unsafe_count,
                "oversized": oversized_count,
                "overflow": overflow_count,
            }, f)
    except OSError:
        pass
PYEOF
)"

            if [ -n "$_VALIDATION_SUMMARY_FILE" ] && [ -f "$_VALIDATION_SUMMARY_FILE" ]; then
                _VALIDATION_SUMMARY="$(cat "$_VALIDATION_SUMMARY_FILE" 2>/dev/null || echo "")"
                rm -f "$_VALIDATION_SUMMARY_FILE"
                if [ -n "$_VALIDATION_SUMMARY" ] && [ -n "$PYTHON_CMD" ]; then
                    _TOOL_PROFILE_ALLOWLIST_UNSAFE="$("$PYTHON_CMD" -c "import json,sys; print(json.loads(sys.argv[1]).get('unsafe',0))" "$_VALIDATION_SUMMARY" 2>/dev/null || echo 0)"
                    _TOOL_PROFILE_ALLOWLIST_OVERSIZED="$("$PYTHON_CMD" -c "import json,sys; print(json.loads(sys.argv[1]).get('oversized',0))" "$_VALIDATION_SUMMARY" 2>/dev/null || echo 0)"
                    _TOOL_PROFILE_ALLOWLIST_OVERFLOW="$("$PYTHON_CMD" -c "import json,sys; print(json.loads(sys.argv[1]).get('overflow',0))" "$_VALIDATION_SUMMARY" 2>/dev/null || echo 0)"
                fi
            else
                rm -f "$_VALIDATION_SUMMARY_FILE" 2>/dev/null || true
            fi

            if [ -n "$_VALIDATION_CMDS" ]; then
                while IFS= read -r _vcmd; do
                    [ -z "$_vcmd" ] && continue
                    _allow_parts+=("Bash(${_vcmd})")
                    _TOOL_PROFILE_ALLOWLIST_COUNT=$((_TOOL_PROFILE_ALLOWLIST_COUNT + 1))
                done <<< "$_VALIDATION_CMDS"
            fi
        fi
    fi

    if [ ${#_allow_parts[@]} -gt 0 ]; then
        _CLAUDE_ALLOWED_ARGS=(--allowedTools "$(IFS=,; echo "${_allow_parts[*]}")")
    fi
fi

progress_log "Tool profile resolved: profile=${CLAUDE_CODE_TOOL_PROFILE}, derivation=${_TOOL_PROFILE_DERIVATION}, supported=$([ "$_TOOL_PROFILE_SUPPORTED" -eq 1 ] && echo yes || echo no), available_tools=${_TOOL_PROFILE_AVAILABLE_TOOLS:-none}, allowlist_accepted=${_TOOL_PROFILE_ALLOWLIST_COUNT}, allowlist_unsafe=${_TOOL_PROFILE_ALLOWLIST_UNSAFE:-0}, allowlist_oversized=${_TOOL_PROFILE_ALLOWLIST_OVERSIZED:-0}, allowlist_overflow=${_TOOL_PROFILE_ALLOWLIST_OVERFLOW:-0}, allowlist_enabled=$([ "$CLAUDE_CODE_TASK_VALIDATION_ALLOWLIST" -eq 1 ] && echo yes || echo no), external_integrations_allowed=${_EXTERNAL_INTEGRATIONS_ALLOWED}, strict_mcp_isolation=${_STRICT_MCP_ISOLATION}, mcp_config_paths=${_MCP_CONFIG_PATHS_EVIDENCE}, plugin_paths=${_PLUGIN_PATHS_EVIDENCE}, external_integration_rejection=${_EXTERNAL_INTEGRATION_REJECTION:-none}"

# --- Unified interaction probe: startup phase ---
# Advisory only: startup probe failure must not prevent dispatch.
_STARTUP_PROBE_CONCLUSION="not-run"
if [ "$CLAUDE_CODE_API_PROBE_MODE" = "always" ]; then
    run_interaction_probe "startup" "$STARTUP_INTERACTION_HEALTH_FILE"
    _STARTUP_PROBE_CONCLUSION="$_LAST_PROBE_CONCLUSION"
    progress_log "Startup interaction probe: conclusion=${_STARTUP_PROBE_CONCLUSION} (advisory; dispatch continues)"
fi

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
FIRST_PROGRESS_ELAPSED_SECONDS=""
FIRST_WORKTREE_CHANGE_SECONDS=""
_CONTINUATION_THRESHOLD_SECONDS=120
INITIAL_PROGRESS_HASH="$(sha1sum "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" 2>/dev/null | awk '{print $1}' || true)"
# --- Progress-aware timeout extension tracking ---
# When the base timeout fires, if substantive progress is growing, extend once
# by CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS instead of killing immediately.
TIMEOUT_EXTENSION_ACTIVE=0
TIMEOUT_EXTENSION_STARTED_EPOCH=0
TIMEOUT_EXTENSION_DEADLINE=0
TIMEOUT_EXTENSION_REASON=""
# Snapshot of progress indicators at extension start, for detecting further growth.
EXTENSION_START_WORKTREE_DIGEST=""
EXTENSION_START_REPORT_BYTES=0
EXTENSION_START_PROGRESS_BYTES=0
EXTENSION_START_REPORT_HASH=""
EXTENSION_START_PROGRESS_HASH=""
# --- Second active-progress extension tracking ---
# When the first extension deadline fires and progress is still growing,
# grant exactly one more bounded wait round.  Never repeated.
SECOND_EXTENSION_ACTIVE=0
SECOND_EXTENSION_STARTED_EPOCH=0
SECOND_EXTENSION_DEADLINE=0
SECOND_EXTENSION_REASON=""
SECOND_EXTENSION_START_WORKTREE_DIGEST=""
SECOND_EXTENSION_START_REPORT_BYTES=0
SECOND_EXTENSION_START_PROGRESS_BYTES=0
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
        if [ -z "$FIRST_WORKTREE_CHANGE_SECONDS" ]; then
            FIRST_WORKTREE_CHANGE_SECONDS="$ELAPSED"
        fi
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
            FIRST_PROGRESS_ELAPSED_SECONDS="$ELAPSED"
            progress_log "First substantive progress detected: signal=${_FP_SIGNAL}, elapsed_seconds=${ELAPSED}"
        elif [ "$CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS" -gt 0 ] && \
             [ "$ELAPSED" -ge "$CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS" ]; then
            if [ "$CLAUDE_CODE_FIRST_PROGRESS_ACTION" = "observe" ]; then
                # Observation mode: run probe at most once for attribution, record event, continue.
                # Only run the observation probe when probe_mode=always; failure-only
                # defers probing to confirmed zero-output at finalization.
                if [ "$_OBSERVATION_PROBE_RAN" -eq 0 ] && [ "$CLAUDE_CODE_API_PROBE_MODE" = "always" ]; then
                    _OBSERVATION_PROBE_RAN=1
                    run_interaction_probe "observation" "$INTERACTION_HEALTH_FILE"
                    _OBSERVATION_PROBE_CONCLUSION="$_LAST_PROBE_CONCLUSION"
                    _OBSERVATION_PROBE_AUTHORITATIVE="$_LAST_PROBE_AUTHORITATIVE"
                    progress_log "First-progress observation probe: conclusion=${_OBSERVATION_PROBE_CONCLUSION}, artifact=${INTERACTION_HEALTH_FILE}"
                fi
                progress_log "First-progress observation: no substantive progress within ${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS}s; continuing to base timeout (action=${CLAUDE_CODE_FIRST_PROGRESS_ACTION}, probe_mode=${CLAUDE_CODE_API_PROBE_MODE})"
            else
                # Legacy stop mode
                CLAUDE_FIRST_PROGRESS_TIMED_OUT=1
                stop_claude "first_progress_timeout after ${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS}s" "$ELAPSED"
                break
            fi
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
        # --- Second extension deadline check (mutually exclusive: checked first) ---
        # Once active, only this branch handles the second extension.  No rolling reset.
        if [ "$SECOND_EXTENSION_ACTIVE" -eq 1 ] && [ "$NOW_EPOCH" -ge "$SECOND_EXTENSION_DEADLINE" ]; then
            CLAUDE_TIMED_OUT=1
            stop_claude "runtime timeout (second extension expired)" "$ELAPSED"
            break
        fi
        # --- First extension deadline check ---
        # When the first extension deadline is reached, either grant exactly one
        # second extension (if progress is still growing) or terminate.
        if [ "$TIMEOUT_EXTENSION_ACTIVE" -eq 1 ] && [ "$NOW_EPOCH" -ge "$TIMEOUT_EXTENSION_DEADLINE" ]; then
            # Guard: skip if second extension is already active (deadline already set).
            if [ "$SECOND_EXTENSION_ACTIVE" -eq 0 ]; then
                if ! progress_is_growing "$EXTENSION_START_WORKTREE_DIGEST" "$EXTENSION_START_REPORT_BYTES" "$EXTENSION_START_PROGRESS_BYTES" "$EXTENSION_START_REPORT_HASH" "$EXTENSION_START_PROGRESS_HASH"; then
                    CLAUDE_TIMED_OUT=1
                    stop_claude "runtime timeout (extension expired, no progress)" "$ELAPSED"
                    break
                fi
                if [ "$CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS" -gt 0 ]; then
                    SECOND_EXTENSION_ACTIVE=1
                    SECOND_EXTENSION_STARTED_EPOCH="$NOW_EPOCH"
                    SECOND_EXTENSION_DEADLINE=$((NOW_EPOCH + CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS))
                    SECOND_EXTENSION_REASON="growth_at_first_extension_deadline"
                    SECOND_EXTENSION_START_WORKTREE_DIGEST="$CURRENT_WORKTREE_DIGEST"
                    SECOND_EXTENSION_START_REPORT_BYTES="$REPORT_BYTES"
                    SECOND_EXTENSION_START_PROGRESS_BYTES="$CLAUDE_PROGRESS_BYTES"
                    progress_log "Second extension started: seconds=${CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS}, deadline_epoch=${SECOND_EXTENSION_DEADLINE}, reason=${SECOND_EXTENSION_REASON}"
                else
                    CLAUDE_TIMED_OUT=1
                    stop_claude "runtime timeout (extension expired, progress was growing, second extension disabled)" "$ELAPSED"
                    break
                fi
            fi
        fi
        # --- Base timeout: start first extension if recent growth, else terminate ---
        # Fires only once (TIMEOUT_EXTENSION_ACTIVE guard).  Requires activity
        # within a bounded recent window so stale historical progress does not
        # qualify for an extension.
        if [ "$TIMEOUT_EXTENSION_ACTIVE" -eq 0 ] && \
           [ "$CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS" -gt 0 ]; then
            _RECENT_ACTIVITY_SECONDS=$((NOW_EPOCH - LAST_ACTIVITY_EPOCH))
            if [ "$_RECENT_ACTIVITY_SECONDS" -le "$CLAUDE_CODE_RECENT_ACTIVITY_WINDOW_SECONDS" ] && \
               [ "$FIRST_PROGRESS_DETECTED" -eq 1 ]; then
                TIMEOUT_EXTENSION_ACTIVE=1
                TIMEOUT_EXTENSION_STARTED_EPOCH="$NOW_EPOCH"
                TIMEOUT_EXTENSION_DEADLINE=$((NOW_EPOCH + CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS))
                TIMEOUT_EXTENSION_REASON="recent_growth_at_base_deadline"
                EXTENSION_START_WORKTREE_DIGEST="$LAST_WORKTREE_DIGEST"
                EXTENSION_START_REPORT_BYTES="$REPORT_BYTES"
                EXTENSION_START_PROGRESS_BYTES="$CLAUDE_PROGRESS_BYTES"
                EXTENSION_START_REPORT_HASH="$(sha1sum "${WORKTREE_DIR}/CLAUDE_REPORT.md" 2>/dev/null | awk '{print $1}' || true)"
                EXTENSION_START_PROGRESS_HASH="$(sha1sum "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" 2>/dev/null | awk '{print $1}' || true)"
                progress_log "Timeout extension started: base_timeout=${CLAUDE_CODE_TIMEOUT_SECONDS}s, extension=${CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS}s, deadline_epoch=${TIMEOUT_EXTENSION_DEADLINE}, reason=${TIMEOUT_EXTENSION_REASON}, recent_activity_seconds=${_RECENT_ACTIVITY_SECONDS}"
            else
                CLAUDE_TIMED_OUT=1
                TIMEOUT_EXTENSION_REASON="stale_progress_at_base_deadline"
                stop_claude "runtime timeout (stale progress: last activity ${_RECENT_ACTIVITY_SECONDS}s ago)" "$ELAPSED"
                break
            fi
        elif [ "$TIMEOUT_EXTENSION_ACTIVE" -eq 0 ]; then
            # Extension feature disabled or no extension seconds configured.
            CLAUDE_TIMED_OUT=1
            stop_claude "runtime timeout" "$ELAPSED"
            break
        fi
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
# Git Bash can lose visibility of a background wrapper PID while its script
# descendant is still running.  If that makes the monitor leave its loop, use
# the completed run's elapsed time to preserve the first-progress contract.
# In observe mode, do not mark as first-progress timeout (no kill occurred).
if [ "$CLAUDE_FIRST_PROGRESS_TIMED_OUT" -eq 0 ] && \
   [ "$FIRST_PROGRESS_DETECTED" -eq 0 ] && \
   [ "$CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS" -gt 0 ] && \
   [ "$ELAPSED" -ge "$CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS" ] && \
   [ "$CLAUDE_CODE_FIRST_PROGRESS_ACTION" != "observe" ]; then
    CLAUDE_FIRST_PROGRESS_TIMED_OUT=1
    progress_log "First-progress timeout reconciled after child exit: elapsed_seconds=${ELAPSED}, timeout_seconds=${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS}"
fi
progress_log "Claude subprocess ended; dispatcher finalizing artifacts: pid=${CLAUDE_PID}, wait_status=${CLAUDE_STATUS}, elapsed_seconds=${ELAPSED}"
FINAL_NETWORK_SUMMARY="$(capture_network_snapshot "$CLAUDE_PID" "$ELAPSED" 0)"
progress_log "Final network snapshot: ${FINAL_NETWORK_SUMMARY}"
# Git Bash may not provide pgrep, so a timed-out descendant can briefly keep
# the redirected result descriptor open after the recorded wrapper exits.
# Drain that narrow timeout window before replacing invalid output with the
# fallback JSON; normal successful dispatches pay no delay.
if [ "$CLAUDE_TIMED_OUT" -eq 1 ] || [ "$CLAUDE_FIRST_PROGRESS_TIMED_OUT" -eq 1 ] || [ "$CLAUDE_NO_OUTPUT_TIMED_OUT" -eq 1 ]; then
    CLAUDE_CODE_TIMEOUT_DRAIN_SECONDS="${CLAUDE_CODE_TIMEOUT_DRAIN_SECONDS:-6}"
    sleep "$CLAUDE_CODE_TIMEOUT_DRAIN_SECONDS"
fi
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
        if [ "$TIMEOUT_EXTENSION_ACTIVE" -eq 1 ]; then
            echo "[dispatch] Progress extension used: yes"
            echo "[dispatch] Extension seconds: ${CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS}"
            echo "[dispatch] Extension reason: ${TIMEOUT_EXTENSION_REASON}"
            echo "[dispatch] Extension deadline epoch: ${TIMEOUT_EXTENSION_DEADLINE}"
            echo "[dispatch] Extension start worktree digest: ${EXTENSION_START_WORKTREE_DIGEST}"
            echo "[dispatch] Extension start report bytes: ${EXTENSION_START_REPORT_BYTES}"
            echo "[dispatch] Extension start progress bytes: ${EXTENSION_START_PROGRESS_BYTES}"
        else
            echo "[dispatch] Progress extension used: no"
            if [ -n "${TIMEOUT_EXTENSION_REASON:-}" ]; then
                echo "[dispatch] Base timeout reason: ${TIMEOUT_EXTENSION_REASON}"
            fi
        fi
        if [ "$SECOND_EXTENSION_ACTIVE" -eq 1 ]; then
            echo "[dispatch] Second extension used: yes"
            echo "[dispatch] Second extension seconds: ${CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS}"
            echo "[dispatch] Second extension reason: ${SECOND_EXTENSION_REASON}"
            echo "[dispatch] Second extension deadline epoch: ${SECOND_EXTENSION_DEADLINE}"
            echo "[dispatch] Second extension start worktree digest: ${SECOND_EXTENSION_START_WORKTREE_DIGEST}"
            echo "[dispatch] Second extension start report bytes: ${SECOND_EXTENSION_START_REPORT_BYTES}"
            echo "[dispatch] Second extension start progress bytes: ${SECOND_EXTENSION_START_PROGRESS_BYTES}"
        else
            echo "[dispatch] Second extension used: no"
        fi
        echo "[dispatch] Final deadline seconds: $(( TIMEOUT_EXTENSION_ACTIVE == 1 ? CLAUDE_CODE_TIMEOUT_SECONDS + CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS + (SECOND_EXTENSION_ACTIVE == 1 ? CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS : 0) : CLAUDE_CODE_TIMEOUT_SECONDS ))"
        echo "[dispatch] Progress log: ${PROGRESS_FILE}"
    } >> "$STATUS_FILE"
    progress_log "Claude finished by timeout: elapsed_seconds=${ELAPSED}, wait_status=${CLAUDE_STATUS}, extension_active=${TIMEOUT_EXTENSION_ACTIVE}, extension_reason=${TIMEOUT_EXTENSION_REASON:-none}, second_extension_active=${SECOND_EXTENSION_ACTIVE}, second_extension_reason=${SECOND_EXTENSION_REASON:-none}"
    echo "Warning: claude timed out after ${ELAPSED}s. Check $STATUS_FILE and $PROGRESS_FILE" >&2
elif [ "$CLAUDE_STATUS" -ne 0 ]; then
    progress_log "Claude exited non-zero: status=${CLAUDE_STATUS}, elapsed_seconds=${ELAPSED}"
    echo "Warning: claude exited with non-zero status $CLAUDE_STATUS. Check $STATUS_FILE" >&2
else
    progress_log "Claude child exited 0: elapsed_seconds=${ELAPSED}; final outcome pending semantic validation"
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

    # A result written after the dispatcher decided to stop the run is not a
    # successful Claude result.  This matters on Git Bash, where terminating
    # the wrapper may leave a descendant alive long enough to emit valid JSON.
    # Preserve that output as raw evidence and generate the timeout-aware
    # fallback packet below.
    if [ "$CLAUDE_TIMED_OUT" -eq 1 ] || [ "$CLAUDE_FIRST_PROGRESS_TIMED_OUT" -eq 1 ] || [ "$CLAUDE_NO_OUTPUT_TIMED_OUT" -eq 1 ]; then
        valid=0
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
            "${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS:-0}" \
            "${TIMEOUT_EXTENSION_ACTIVE:-0}" "${CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS:-0}" \
            "${TIMEOUT_EXTENSION_REASON:-}" \
            "${CLAUDE_CODE_API_PROBE_MODE:-always}" "${CLAUDE_CODE_PROBE_ENVIRONMENT:-auto}" \
            "${CLAUDE_CODE_FIRST_PROGRESS_ACTION:-observe}" "${_OBSERVATION_PROBE_RAN:-0}" \
            "${SECOND_EXTENSION_ACTIVE:-0}" "${CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS:-0}" \
            "${SECOND_EXTENSION_REASON:-}" \
            "${CLAUDE_CODE_RECENT_ACTIVITY_WINDOW_SECONDS:-120}" \
            <<'PYEOF'
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
    extension_active,
    extension_seconds,
    extension_reason,
    probe_mode,
    probe_environment,
    first_progress_action,
    observation_probe_ran,
    second_extension_active,
    second_extension_seconds,
    second_extension_reason,
    recent_activity_window,
) = sys.argv[1:27]

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
    "first_progress_action": first_progress_action,
    "probe_mode": probe_mode,
    "probe_environment": probe_environment,
    "observation_probe_ran": observation_probe_ran == "1",
    "timeout_extension_used": extension_active == "1",
    "timeout_extension_seconds": int(extension_seconds) if extension_active == "1" else 0,
    "timeout_extension_reason": extension_reason if extension_active == "1" else None,
    "base_timeout_reason": extension_reason or None,
    "recent_activity_window_seconds": int(recent_activity_window),
    "second_extension_used": second_extension_active == "1",
    "second_extension_seconds": int(second_extension_seconds) if second_extension_active == "1" else 0,
    "second_extension_reason": second_extension_reason if second_extension_active == "1" else None,
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
            echo "  \"first_progress_action\": \"${CLAUDE_CODE_FIRST_PROGRESS_ACTION:-observe}\","
            echo "  \"probe_mode\": \"${CLAUDE_CODE_API_PROBE_MODE:-always}\","
            echo "  \"probe_environment\": \"${CLAUDE_CODE_PROBE_ENVIRONMENT:-auto}\","
            echo "  \"observation_probe_ran\": $([ "${_OBSERVATION_PROBE_RAN:-0}" -eq 1 ] && echo true || echo false),"
            echo "  \"timeout_extension_used\": $([ "${TIMEOUT_EXTENSION_ACTIVE:-0}" -eq 1 ] && echo true || echo false),"
            echo "  \"timeout_extension_seconds\": $([ "${TIMEOUT_EXTENSION_ACTIVE:-0}" -eq 1 ] && echo "${CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS:-0}" || echo 0),"
            _ext_reason=""
            if [ "${TIMEOUT_EXTENSION_ACTIVE:-0}" -eq 1 ]; then _ext_reason="${TIMEOUT_EXTENSION_REASON:-}"; fi
            echo "  \"timeout_extension_reason\": \"${_ext_reason}\","
            echo "  \"base_timeout_reason\": \"${TIMEOUT_EXTENSION_REASON:-none}\","
            echo "  \"recent_activity_window_seconds\": ${CLAUDE_CODE_RECENT_ACTIVITY_WINDOW_SECONDS:-120},"
            echo "  \"second_extension_used\": $([ "${SECOND_EXTENSION_ACTIVE:-0}" -eq 1 ] && echo true || echo false),"
            echo "  \"second_extension_seconds\": $([ "${SECOND_EXTENSION_ACTIVE:-0}" -eq 1 ] && echo "${CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS:-0}" || echo 0),"
            _2nd_ext_reason=""
            if [ "${SECOND_EXTENSION_ACTIVE:-0}" -eq 1 ]; then _2nd_ext_reason="${SECOND_EXTENSION_REASON:-}"; fi
            echo "  \"second_extension_reason\": \"${_2nd_ext_reason}\","
            echo "  \"elapsed_seconds\": ${ELAPSED},"
            echo '  "message": "Claude exited without valid JSON result output; dispatcher generated this fallback result."'
            echo "}"
        } > "$RESULT_FILE"
    fi
    progress_log "Generated fallback result JSON: reason=${reason}, raw_result=${RAW_RESULT_FILE}"
}

ensure_result_json "missing_or_invalid_result_json"

# --- Semantic result validation ---
# Detect Claude API errors that produced exit 0 but indicate process failure.
# Records machine-readable classification for orchestrator consumption.
# Does NOT discard raw result, diff, progress, or report evidence.
CLAUDE_SEMANTIC_ERROR=0
CLAUDE_SEMANTIC_ERROR_REASON=""
if [ -s "$RESULT_FILE" ]; then
    if [ -n "$PYTHON_CMD" ]; then
        _SEMANTIC_CHECK="$("$PYTHON_CMD" - "$RESULT_FILE" <<'PYEOF'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        data = json.load(f)
    is_error = data.get("is_error", False)
    result_str = str(data.get("result", ""))
    if is_error is True or (isinstance(is_error, str) and is_error.lower() == "true"):
        if "API Error:" in result_str:
            reason = result_str.split("API Error:", 1)[1].strip()[:120]
            print("1:api_error:" + reason)
        else:
            print("1:is_error_true")
    elif "API Error:" in result_str:
        reason = result_str.split("API Error:", 1)[1].strip()[:120]
        print("1:api_error:" + reason)
    else:
        print("0:")
except Exception:
    print("0:")
PYEOF
)" || _SEMANTIC_CHECK="0:"
        CLAUDE_SEMANTIC_ERROR="${_SEMANTIC_CHECK%%:*}"
        CLAUDE_SEMANTIC_ERROR_REASON="${_SEMANTIC_CHECK#*:}"
    else
        # Without Python, use grep as a fallback for detection
        if grep -qE '"is_error"\s*:\s*true|"API Error:"' "$RESULT_FILE" 2>/dev/null; then
            CLAUDE_SEMANTIC_ERROR=1
            CLAUDE_SEMANTIC_ERROR_REASON="api_error_detected_grep_fallback"
        fi
    fi
fi

if [ "$CLAUDE_SEMANTIC_ERROR" -eq 1 ]; then
    progress_log "Semantic result error detected: reason=${CLAUDE_SEMANTIC_ERROR_REASON}, original_exit_status=${CLAUDE_STATUS}"
    {
        echo ""
        echo "[dispatch] Semantic result error: yes"
        echo "[dispatch] Semantic error reason: ${CLAUDE_SEMANTIC_ERROR_REASON}"
        echo "[dispatch] Original exit status: ${CLAUDE_STATUS}"
    } >> "$STATUS_FILE"
fi

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
        | grep -v -E '^(TASK_CARD|TASK_CARD_FULL|CLAUDE_TASK_CARD|CLAUDE_PROMPT|CLAUDE_REPORT|CLAUDE_PROGRESS|ADVISOR_REQUEST)' || true)"
    FILTERED_UNTRACKED_SKIPPED=0
fi

# --- ADVISOR_REQUEST.json post-Claude validation ---
# Validate after Claude exits. Never infer direction, blocker kind, question,
# or advisor-used from prose/log keywords.
ADVISOR_REQUEST_FILE="${WORKTREE_DIR}/ADVISOR_REQUEST.json"
ADVISOR_REQUEST_VALID=0
ADVISOR_DIRECTION="unknown"
ADVISOR_BLOCKER_KIND="none"
ADVISOR_USED="false"
_ADVISOR_VALIDATOR="${SCRIPT_DIR}/validate-advisor-request.py"
if [ -f "$ADVISOR_REQUEST_FILE" ] && [ -n "$PYTHON_CMD" ] && [ -f "$_ADVISOR_VALIDATOR" ]; then
    _ADVISOR_ARCHIVE_DIR="${WORKTREE_ROOT}/${TASK_ID}.advisor-request"
    mkdir -p "$_ADVISOR_ARCHIVE_DIR"
    if "$PYTHON_CMD" "$_ADVISOR_VALIDATOR" "$ADVISOR_REQUEST_FILE" \
        --expected-task-id "$TASK_ID" \
        --archive-valid "${_ADVISOR_ARCHIVE_DIR}/valid.json" \
        --archive-invalid "${_ADVISOR_ARCHIVE_DIR}/invalid.json" \
        > "${_ADVISOR_ARCHIVE_DIR}/validation-output.json" 2>/dev/null; then
        ADVISOR_REQUEST_VALID=1
        _ADVISOR_VALIDATED_JSON="${_ADVISOR_ARCHIVE_DIR}/validation-output.json"
        ADVISOR_DIRECTION="$("$PYTHON_CMD" - "$_ADVISOR_VALIDATED_JSON" <<'PYEOF' 2>/dev/null || echo "unknown"
import json, sys
v = json.load(open(sys.argv[1], encoding="utf-8"))
print(v.get("direction", "unknown"))
PYEOF
)"
        ADVISOR_BLOCKER_KIND="$("$PYTHON_CMD" - "$_ADVISOR_VALIDATED_JSON" <<'PYEOF' 2>/dev/null || echo "none"
import json, sys
v = json.load(open(sys.argv[1], encoding="utf-8"))
print(v.get("blocker", {}).get("kind", "none"))
PYEOF
)"
        ADVISOR_USED="$("$PYTHON_CMD" - "$_ADVISOR_VALIDATED_JSON" <<'PYEOF' 2>/dev/null || echo "false"
import json, sys
v = json.load(open(sys.argv[1], encoding="utf-8"))
print(str(v.get("advisor_used", False)).lower())
PYEOF
)"
        progress_log "ADVISOR_REQUEST.json validated: direction=${ADVISOR_DIRECTION}, blocker_kind=${ADVISOR_BLOCKER_KIND}, advisor_used=${ADVISOR_USED}"
    else
        progress_log "ADVISOR_REQUEST.json validation failed; using defaults: direction=unknown, blocker_kind=none, advisor_used=false"
    fi
elif [ -f "$ADVISOR_REQUEST_FILE" ]; then
    progress_log "ADVISOR_REQUEST.json found but validator unavailable; using defaults"
else
    progress_log "No ADVISOR_REQUEST.json found; using defaults"
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

# --- Post-execution scope enforcement for advisor continuation ---
# After Claude exits, recompute changed paths/state and enforce the validated
# allowed/forbidden boundaries.  A violation produces non-zero semantic failure,
# remains isolated and never reports acceptance/merge.
ADVISOR_POST_RUN_SCOPE_VIOLATION=0
if [ -n "${_ADVISOR_CONTINUE_TASK_ID:-}" ] && [ -n "${_ADVISOR_CONTINUE_RESPONSE:-}" ]; then
    if ! post_run_scope_enforcement \
        "$WORKTREE_DIR" \
        "${_ADVISOR_CONTINUE_ALLOWED_CHANGES:-}" \
        "${_ADVISOR_CONTINUE_FORBIDDEN_CHANGES:-}" \
        "${_ADVISOR_CONTINUE_TASK_ID}"; then
        ADVISOR_POST_RUN_SCOPE_VIOLATION=1
        progress_log "Post-run scope enforcement FAILED: advisor continuation task ${_ADVISOR_CONTINUE_TASK_ID} violated allowed/forbidden boundaries"
    else
        progress_log "Post-run scope enforcement PASSED: advisor continuation task ${_ADVISOR_CONTINUE_TASK_ID}"
    fi
fi

DISPATCH_EVIDENCE_STATE="$(classify_dispatch_evidence "$IMPLEMENTATION_CHANGES" "$VALID_CLAUDE_REPORT" "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" "${WORKTREE_DIR}/CLAUDE_REPORT.md")"
if [ "$IMPLEMENTATION_CHANGES" -eq 0 ] && [ "$VALID_CLAUDE_REPORT" -eq 0 ] && \
   [ "${FIRST_PROGRESS_DETECTED:-0}" -eq 0 ] && \
   [ "$DISPATCH_EVIDENCE_STATE" != "acknowledgement only" ]; then
    if [ "${_OBSERVATION_PROBE_RAN:-0}" -eq 1 ] && [ -s "$INTERACTION_HEALTH_FILE" ]; then
        # Reuse observation-stage probe result; do not run a second probe.
        progress_log "Reusing observation-stage probe result: artifact=${INTERACTION_HEALTH_FILE}, conclusion=${_OBSERVATION_PROBE_CONCLUSION:-unknown}, reuse=current-dispatch"
        ZERO_OUTPUT_PROBE_CONCLUSION="${_OBSERVATION_PROBE_CONCLUSION:-unknown}"
        ZERO_OUTPUT_PROBE_AUTHORITATIVE="${_OBSERVATION_PROBE_AUTHORITATIVE:-no}"
    elif [ "$CLAUDE_CODE_API_PROBE_MODE" != "off" ]; then
        run_interaction_probe "zero-output" "$INTERACTION_HEALTH_FILE"
        ZERO_OUTPUT_PROBE_CONCLUSION="$_LAST_PROBE_CONCLUSION"
        ZERO_OUTPUT_PROBE_AUTHORITATIVE="$_LAST_PROBE_AUTHORITATIVE"
    fi
fi
# Compute dispatch outcome for orchestrator consumption.
# Allows distinguishing: success, api_error_with_diff, api_error_without_diff,
# approval_blocked, timeout, fallback, no_useful_progress, scope_violation.
DISPATCH_OUTCOME="success"
if [ "${ADVISOR_POST_RUN_SCOPE_VIOLATION:-0}" -eq 1 ]; then
    # Post-run scope violation is a semantic failure; never report acceptance/merge.
    DISPATCH_OUTCOME="scope_violation"
elif [ "$CLAUDE_SEMANTIC_ERROR" -eq 1 ]; then
    if [ "$IMPLEMENTATION_CHANGES" -gt 0 ]; then
        DISPATCH_OUTCOME="api_error_with_diff"
    else
        DISPATCH_OUTCOME="api_error_without_diff"
    fi
elif [ "${CLAUDE_APPROVAL_CONVERGED:-0}" -eq 1 ]; then
    DISPATCH_OUTCOME="approval_blocked"
elif [ "$ZERO_OUTPUT_PROBE_CONCLUSION" = "unavailable-in-current-environment" ] || \
     [ "$ZERO_OUTPUT_PROBE_CONCLUSION" = "inconclusive-restricted-environment" ]; then
    # A failed minimal interaction means this round cannot be attributed to
    # model execution. In a restricted sandbox the evidence is inconclusive,
    # but it still must not count toward takeover.
    DISPATCH_OUTCOME="network_error"
elif [ "$CLAUDE_TIMED_OUT" -eq 1 ] || [ "$CLAUDE_NO_OUTPUT_TIMED_OUT" -eq 1 ] || [ "${CLAUDE_FIRST_PROGRESS_TIMED_OUT:-0}" -eq 1 ]; then
    DISPATCH_OUTCOME="timeout"
elif [ "$RESULT_FALLBACK_GENERATED" -eq 1 ]; then
    DISPATCH_OUTCOME="fallback"
elif [ "$IMPLEMENTATION_CHANGES" -eq 0 ] && [ "$VALID_CLAUDE_REPORT" -eq 0 ]; then
    DISPATCH_OUTCOME="no_useful_progress"
fi

ATTEMPT_FAILURE_CLASS="unavailable"
ATTEMPT_COUNTS_TOWARD_TAKEOVER="unknown"
ATTEMPT_RECOMMENDED_ACTION="inspect-evidence-before-counting"
ATTEMPT_SAME_WORKTREE_RETRY="false"
if [ -n "$PYTHON_CMD" ] && [ -f "${SCRIPT_DIR}/classify-claude-attempt.py" ]; then
    _ATTEMPT_PROGRESS="none"
    if [ "$DISPATCH_EVIDENCE_STATE" = "acknowledgement only" ]; then
        _ATTEMPT_PROGRESS="acknowledgement"
    elif [ "${FIRST_PROGRESS_SIGNAL:-}" = "blocker_recorded" ]; then
        _ATTEMPT_PROGRESS="blocker"
    elif [ "$IMPLEMENTATION_CHANGES" -gt 0 ] || [ "$VALID_CLAUDE_REPORT" -eq 1 ]; then
        _ATTEMPT_PROGRESS="useful"
    fi
    _ATTEMPT_ARGS=(
        --exit-code "$CLAUDE_STATUS" --outcome "$DISPATCH_OUTCOME"
        --diff-changes "$IMPLEMENTATION_CHANGES" --progress "$_ATTEMPT_PROGRESS"
        --direction "$ADVISOR_DIRECTION" --error-text-file "$STATUS_FILE"
        --blocker-kind "$ADVISOR_BLOCKER_KIND"
    )
    if [ "$ADVISOR_USED" = "true" ]; then _ATTEMPT_ARGS+=(--advisor-used); fi
    if [ "$VALID_CLAUDE_REPORT" -eq 1 ]; then _ATTEMPT_ARGS+=(--valid-report); fi
    if [ "$CLAUDE_SEMANTIC_ERROR" -eq 1 ]; then _ATTEMPT_ARGS+=(--semantic-error); fi
    "$PYTHON_CMD" "${SCRIPT_DIR}/classify-claude-attempt.py" "${_ATTEMPT_ARGS[@]}" > "$ATTEMPT_CLASSIFICATION_FILE" || true
    if [ -s "$ATTEMPT_CLASSIFICATION_FILE" ]; then
        IFS=$'\t' read -r ATTEMPT_FAILURE_CLASS ATTEMPT_COUNTS_TOWARD_TAKEOVER ATTEMPT_RECOMMENDED_ACTION ATTEMPT_SAME_WORKTREE_RETRY < <(
            "$PYTHON_CMD" - "$ATTEMPT_CLASSIFICATION_FILE" <<'PYEOF'
import json, sys
v=json.load(open(sys.argv[1], encoding="utf-8"))
print("\t".join(str(v.get(k, "unknown")).lower() if isinstance(v.get(k), bool) else str(v.get(k, "unknown")) for k in ("failure_class", "counts_toward_takeover", "recommended_action", "same_worktree_retry_eligible")))
PYEOF
        )
    fi
fi

progress_log "Dispatch evidence classification: state=${DISPATCH_EVIDENCE_STATE}, implementation_changes=${IMPLEMENTATION_CHANGES}, valid_claude_report=$([ "$VALID_CLAUDE_REPORT" -eq 1 ] && echo yes || echo no), dispatch_outcome=${DISPATCH_OUTCOME}, semantic_error=$([ "$CLAUDE_SEMANTIC_ERROR" -eq 1 ] && echo yes || echo no), probe_mode=${CLAUDE_CODE_API_PROBE_MODE}, probe_environment=${CLAUDE_CODE_PROBE_ENVIRONMENT}, first_progress_action=${CLAUDE_CODE_FIRST_PROGRESS_ACTION}, observation_probe_ran=$([ "${_OBSERVATION_PROBE_RAN:-0}" -eq 1 ] && echo yes || echo no)"
progress_log "API attribution: startup_conclusion=${_STARTUP_PROBE_CONCLUSION:-not-run}, zero_output_conclusion=${ZERO_OUTPUT_PROBE_CONCLUSION}, authoritative=${ZERO_OUTPUT_PROBE_AUTHORITATIVE}"
# Authoritative final outcome — emitted exactly once, after semantic validation.
progress_log "Final dispatch outcome: ${DISPATCH_OUTCOME}, elapsed_seconds=${ELAPSED}, semantic_error=$([ "$CLAUDE_SEMANTIC_ERROR" -eq 1 ] && echo yes || echo no)"
{
    echo ""
    echo "[dispatch] Evidence classification: ${DISPATCH_EVIDENCE_STATE}"
    echo "[dispatch] Implementation changes: ${IMPLEMENTATION_CHANGES}"
    echo "[dispatch] Valid Claude-owned report: $([ "$VALID_CLAUDE_REPORT" -eq 1 ] && echo yes || echo no)"
    echo "[dispatch] Dispatch outcome: ${DISPATCH_OUTCOME}"
    echo "[dispatch] Semantic result error: $([ "$CLAUDE_SEMANTIC_ERROR" -eq 1 ] && echo yes || echo no)"
    echo "[dispatch] Attempt failure class: ${ATTEMPT_FAILURE_CLASS}"
    echo "[dispatch] Counts toward takeover: ${ATTEMPT_COUNTS_TOWARD_TAKEOVER}"
    echo "[dispatch] Recommended action: ${ATTEMPT_RECOMMENDED_ACTION}"
    echo "[dispatch] API probe mode: ${CLAUDE_CODE_API_PROBE_MODE}"
    echo "[dispatch] Probe environment: ${CLAUDE_CODE_PROBE_ENVIRONMENT}"
    echo "[dispatch] First-progress action: ${CLAUDE_CODE_FIRST_PROGRESS_ACTION}"
    echo "[dispatch] First-progress timed out: $([ "${CLAUDE_FIRST_PROGRESS_TIMED_OUT:-0}" -eq 1 ] && echo yes || echo no)"
    echo "[dispatch] Observation probe ran: $([ "${_OBSERVATION_PROBE_RAN:-0}" -eq 1 ] && echo yes || echo no)"
    echo "[dispatch] Startup probe conclusion: ${_STARTUP_PROBE_CONCLUSION:-not-run}"
    echo "[dispatch] Startup interaction health artifact: ${STARTUP_INTERACTION_HEALTH_FILE}"
    echo "[dispatch] Zero-output API probe: ${ZERO_OUTPUT_PROBE_CONCLUSION}"
    echo "[dispatch] Zero-output API probe authoritative: ${ZERO_OUTPUT_PROBE_AUTHORITATIVE}"
    echo "[dispatch] Interaction health artifact: ${INTERACTION_HEALTH_FILE}"
    echo "[dispatch] Same-worktree retry eligible: ${ATTEMPT_SAME_WORKTREE_RETRY}"
    echo "[dispatch] Route source: ${_ROUTE_SOURCE}"
    echo "[dispatch] Route mode: ${CLAUDE_CODE_PROXY_MODE}"
    echo "[dispatch] Advisor request valid: $([ "$ADVISOR_REQUEST_VALID" -eq 1 ] && echo yes || echo no)"
    echo "[dispatch] Advisor direction: ${ADVISOR_DIRECTION}"
    echo "[dispatch] Advisor blocker kind: ${ADVISOR_BLOCKER_KIND}"
    echo "[dispatch] Advisor used: ${ADVISOR_USED}"
    echo "[dispatch] Advisor post-run scope violation: $([ "${ADVISOR_POST_RUN_SCOPE_VIOLATION:-0}" -eq 1 ] && echo yes || echo no)"
    if [ "$CLAUDE_SEMANTIC_ERROR" -eq 1 ]; then
        echo "[dispatch] Semantic error reason: ${CLAUDE_SEMANTIC_ERROR_REASON}"
    fi
} >> "$STATUS_FILE"

# --- Advisor continuation audit ---
# Write machine-readable conservative audit for advisor continuations only.
# Advisory only; never changes dispatch outcome, acceptance, or merge state.
ADVISOR_CONTINUATION_AUDIT_FILE=""
if [ -n "${_ADVISOR_CONTINUE_TASK_ID:-}" ]; then
    ADVISOR_CONTINUATION_AUDIT_FILE="${WORKTREE_ROOT}/${TASK_ID}.advisor-continuation-audit.json"

    # Parse declared searches and paths from continuation report/progress.
    _DECLARED_SEARCHES="unknown"
    _DECLARED_PATHS_READ="unknown"
    _AUDIT_REPORT_SOURCE="${WORKTREE_DIR}/CLAUDE_REPORT.md"
    if [ ! -f "$_AUDIT_REPORT_SOURCE" ] || ! valid_claude_report_file "$_AUDIT_REPORT_SOURCE"; then
        _AUDIT_REPORT_SOURCE="${WORKTREE_DIR}/CLAUDE_PROGRESS.md"
    fi
    if [ -f "$_AUDIT_REPORT_SOURCE" ] && [ -n "$PYTHON_CMD" ]; then
        _DECLARED_SEARCHES="$("$PYTHON_CMD" - "$_AUDIT_REPORT_SOURCE" <<'PYEOF' 2>/dev/null || echo "unknown"
import re, sys
text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
m = re.search(r"Search commands:\s*`([^`]*)`", text, re.I)
val = m.group(1).strip() if m else "unknown"
print(val if val else "none")
PYEOF
)"
        _DECLARED_PATHS_READ="$("$PYTHON_CMD" - "$_AUDIT_REPORT_SOURCE" <<'PYEOF' 2>/dev/null || echo "unknown"
import re, sys
text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
m = re.search(r"Paths read:\s*`([^`]*)`", text, re.I)
val = m.group(1).strip() if m else "unknown"
print(val if val else "none")
PYEOF
)"
    fi

    # Conservative re-exploration classification.
    # yes: explicit unbounded root-wide search, planning-only no-diff, or very late first worktree change
    # no:  first worktree change at or before threshold + scope passed + no broad declaration
    # unknown: otherwise
    _REEXPLORATION_SUSPECTED="unknown"
    _REEXPLORATION_REASON=""
    _CONTINUATION_SUCCEEDED=0
    if [ "$DISPATCH_OUTCOME" = "success" ] && [ "${ADVISOR_POST_RUN_SCOPE_VIOLATION:-0}" -eq 0 ]; then
        _CONTINUATION_SUCCEEDED=1
    fi

    # Missing declarations are distinct from an explicit `none` and must not
    # support a definitive `no` classification.
    _DECLARATIONS_COMPLETE=0
    if [ "${_DECLARED_SEARCHES,,}" != "unknown" ] && \
       [ "${_DECLARED_PATHS_READ,,}" != "unknown" ]; then
        _DECLARATIONS_COMPLETE=1
    fi
    _HAS_BROAD_DECLARATION=0
    if [ -n "$PYTHON_CMD" ]; then
        _HAS_BROAD_DECLARATION="$("$PYTHON_CMD" - "$_DECLARED_SEARCHES" <<'PYEOF' 2>/dev/null || echo "0"
import shlex, sys
searches = sys.argv[1].lower().strip()
if searches in ("unknown", "none", ""):
    print(0)
else:
    broad = False
    for part in searches.replace("&&", ";").replace("||", ";").split(";"):
        part = part.strip()
        if not part:
            continue
        try:
            words = shlex.split(part)
        except ValueError:
            continue
        if not words:
            continue
        if words[0] == "find" and len(words) > 1 and words[1] in (".", "./", "/"):
            broad = True
        elif words[0] == "rg":
            positional = [word for word in words[1:] if not word.startswith("-")]
            if len(positional) == 1 and "--" not in words:
                broad = True
        elif words[:2] == ["git", "grep"]:
            positional = [word for word in words[2:] if not word.startswith("-")]
            if len(positional) == 1 and "--" not in words:
                broad = True
    print(1 if broad else 0)
PYEOF
)"
    fi

    if [ "$_HAS_BROAD_DECLARATION" -eq 1 ]; then
        _REEXPLORATION_SUSPECTED="yes"
        _REEXPLORATION_REASON="broad_search_declaration"
    elif [ "$IMPLEMENTATION_CHANGES" -eq 0 ] && [ "$VALID_CLAUDE_REPORT" -eq 1 ]; then
        _REEXPLORATION_SUSPECTED="yes"
        _REEXPLORATION_REASON="report_only_no_diff"
    elif [ -n "$FIRST_WORKTREE_CHANGE_SECONDS" ] && \
         [ "$FIRST_WORKTREE_CHANGE_SECONDS" -gt "$_CONTINUATION_THRESHOLD_SECONDS" ] && \
         [ "$IMPLEMENTATION_CHANGES" -eq 0 ]; then
        # Very late first worktree change with no implementation changes
        _REEXPLORATION_SUSPECTED="yes"
        _REEXPLORATION_REASON="late_worktree_change_no_diff"
    elif [ -n "$FIRST_WORKTREE_CHANGE_SECONDS" ] && \
         [ "$FIRST_WORKTREE_CHANGE_SECONDS" -le "$_CONTINUATION_THRESHOLD_SECONDS" ] && \
         [ "${ADVISOR_POST_RUN_SCOPE_VIOLATION:-0}" -eq 0 ] && \
         [ "$_HAS_BROAD_DECLARATION" -eq 0 ] && \
         [ "$_DECLARATIONS_COMPLETE" -eq 1 ]; then
        _REEXPLORATION_SUSPECTED="no"
        _REEXPLORATION_REASON="early_change_scope_passed"
    fi

    # full_redispatch_avoided: only when same-worktree continuation succeeded
    _FULL_REDISPATCH_AVOIDED="false"
    if [ "$_CONTINUATION_SUCCEEDED" -eq 1 ]; then
        _FULL_REDISPATCH_AVOIDED="true"
    fi

    # Read model_turn_count from result JSON (num_turns field)
    _MODEL_TURN_COUNT="null"
    if [ -n "$PYTHON_CMD" ] && [ -s "$RESULT_FILE" ]; then
        _MODEL_TURN_COUNT="$("$PYTHON_CMD" - "$RESULT_FILE" <<'PYEOF' 2>/dev/null || echo "null"
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        data = json.load(f)
    val = data.get("num_turns")
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        print(int(val))
    else:
        print("null")
except Exception:
    print("null")
PYEOF
)"
    fi

    # Write audit JSON (atomic via temp + mv).
    # All report-derived text passes through sys.argv; the heredoc is quoted
    # so bash never interpolates dynamic strings into Python source.
    _AUDIT_TMP="${ADVISOR_CONTINUATION_AUDIT_FILE}.tmp.$$"
    if [ -n "$PYTHON_CMD" ]; then
        "$PYTHON_CMD" - \
            "$_AUDIT_TMP" \
            "$TASK_ID" \
            "${_ADVISOR_CONTINUE_TASK_ID}" \
            "${_response_request_id:-unknown}" \
            "${_ADVISOR_CONTINUE_RESERVATION_ID:-unknown}" \
            "$DISPATCH_OUTCOME" \
            "${FIRST_PROGRESS_ELAPSED_SECONDS:-}" \
            "${FIRST_PROGRESS_SIGNAL:-none}" \
            "$IMPLEMENTATION_CHANGES" \
            "$VALID_CLAUDE_REPORT" \
            "$_MODEL_TURN_COUNT" \
            "${ADVISOR_POST_RUN_SCOPE_VIOLATION:-0}" \
            "$_DECLARED_SEARCHES" \
            "$_DECLARED_PATHS_READ" \
            "$_REEXPLORATION_SUSPECTED" \
            "$_REEXPLORATION_REASON" \
            "$_FULL_REDISPATCH_AVOIDED" \
            "$FIRST_WORKTREE_CHANGE_SECONDS" \
            "$_CONTINUATION_SUCCEEDED" \
            <<'PYEOF' 2>/dev/null
import json, sys

(
    out_file,
    task_id,
    prior_task_id,
    request_id,
    reservation_id,
    dispatch_outcome,
    fp_seconds_str,
    fp_signal,
    impl_changes_str,
    valid_report_str,
    model_turn_str,
    scope_violation_str,
    declared_searches,
    declared_paths_read,
    reexploration_suspected,
    reexploration_reason,
    full_redispatch_str,
    wt_change_str,
    continuation_succeeded_str,
) = sys.argv[1:20]

def int_or_none(s):
    try:
        return int(s)
    except (ValueError, TypeError):
        return None

audit = {
    "schema_version": 1,
    "task_id": task_id,
    "prior_task_id": prior_task_id,
    "request_id": request_id,
    "reservation_id": reservation_id,
    "requested": True,
    "accepted": True,
    "succeeded": continuation_succeeded_str == "1",
    "same_worktree": True,
    "dispatch_outcome": dispatch_outcome,
    "first_progress_seconds": int_or_none(fp_seconds_str),
    "first_progress_signal": fp_signal if fp_signal != "none" else None,
    "first_worktree_change_seconds": int_or_none(wt_change_str),
    "implementation_change_count": int_or_none(impl_changes_str) or 0,
    "valid_report": valid_report_str == "1",
    "model_turn_count": int_or_none(model_turn_str),
    "post_run_scope_result": "violation" if scope_violation_str == "1" else "passed",
    "declared_searches": declared_searches,
    "declared_paths_read": declared_paths_read,
    "reexploration_suspected": reexploration_suspected,
    "reexploration_reason": reexploration_reason or None,
    "full_redispatch_avoided": full_redispatch_str == "true",
    "estimated_tokens_avoided": None,
    "estimated_time_avoided": None,
}
with open(out_file, "w", encoding="utf-8") as f:
    json.dump(audit, f, indent=2, sort_keys=True)
PYEOF
    else
        # Never interpolate report-derived strings without a JSON serializer.
        printf '%s\n' \
            '{' \
            '  "schema_version": 1,' \
            '  "requested": true,' \
            '  "accepted": true,' \
            '  "same_worktree": true,' \
            '  "audit_status": "unavailable-python",' \
            '  "reexploration_suspected": "unknown",' \
            '  "estimated_tokens_avoided": null,' \
            '  "estimated_time_avoided": null' \
            '}' > "$_AUDIT_TMP"
    fi
    mv "$_AUDIT_TMP" "$ADVISOR_CONTINUATION_AUDIT_FILE"
    progress_log "Advisor continuation audit written: ${ADVISOR_CONTINUATION_AUDIT_FILE}, reexploration=${_REEXPLORATION_SUSPECTED}, full_redispatch_avoided=${_FULL_REDISPATCH_AVOIDED}"
fi

# --- Route preference recording ---
# Record the route only when interaction was established (proves CLI/provider worked).
# Do NOT record on transient-transport, unavailable, or unclassified-execution-failure.
# Record unconditionally for model-no-progress, external-approval-blocker, direction-deviation.
# Record other classes only with acknowledgement/blocker/useful diff/report evidence.
# Persistence failure is advisory and must not change dispatch outcome.
if [ -n "$PYTHON_CMD" ] && [ -f "$ROUTE_PREFERENCE_HELPER" ]; then
    _SHOULD_RECORD_ROUTE=0
    if [ "$ZERO_OUTPUT_PROBE_CONCLUSION" != "not-run" ]; then
        : # The diagnostic probe never updates learned route preference.
    else
    case "${ATTEMPT_FAILURE_CLASS:-unavailable}" in
        transient-transport|unavailable|unclassified-execution-failure)
            ;; # do not record
        model-no-progress|external-approval-blocker|direction-deviation)
            _SHOULD_RECORD_ROUTE=1
            ;;
        *)
            # Record when interaction was established or useful progress was made
            if [ "$IMPLEMENTATION_CHANGES" -gt 0 ] || \
               [ "$VALID_CLAUDE_REPORT" -eq 1 ] || \
               [ "${_ATTEMPT_PROGRESS:-none}" = "useful" ] || \
               [ "${_ATTEMPT_PROGRESS:-none}" = "acknowledgement" ] || \
               [ "${_ATTEMPT_PROGRESS:-none}" = "blocker" ]; then
                _SHOULD_RECORD_ROUTE=1
            fi
            ;;
    esac
    fi
    if [ "$_SHOULD_RECORD_ROUTE" -eq 1 ]; then
        _RECORD_SOURCE="dispatch-${DISPATCH_OUTCOME}"
        if _RECORD_OUTPUT="$("$PYTHON_CMD" "$ROUTE_PREFERENCE_HELPER" record \
            --route "$CLAUDE_CODE_PROXY_MODE" --source "$_RECORD_SOURCE" 2>&1)"; then
            progress_log "Route preference recorded: route=${CLAUDE_CODE_PROXY_MODE}, source=${_RECORD_SOURCE}, route_source=${_ROUTE_SOURCE}"
        else
            progress_log "Route preference advisory: ${_RECORD_OUTPUT:-persistence failed}"
        fi
    fi
fi

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
        echo "- Progress extension used: $([ "${TIMEOUT_EXTENSION_ACTIVE:-0}" -eq 1 ] && echo yes || echo no)"
        if [ "${TIMEOUT_EXTENSION_ACTIVE:-0}" -eq 1 ]; then
            echo "- Extension seconds: ${CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS:-0}"
            echo "- Extension reason: ${TIMEOUT_EXTENSION_REASON:-}"
        elif [ -n "${TIMEOUT_EXTENSION_REASON:-}" ]; then
            echo "- Base timeout reason: ${TIMEOUT_EXTENSION_REASON}"
        fi
        echo "- Second extension used: $([ "${SECOND_EXTENSION_ACTIVE:-0}" -eq 1 ] && echo yes || echo no)"
        if [ "${SECOND_EXTENSION_ACTIVE:-0}" -eq 1 ]; then
            echo "- Second extension seconds: ${CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS:-0}"
            echo "- Second extension reason: ${SECOND_EXTENSION_REASON:-}"
        fi
        echo "- No-output timed out: $([ "$CLAUDE_NO_OUTPUT_TIMED_OUT" -eq 1 ] && echo yes || echo no)"
        echo "- First-progress timed out: $([ "${CLAUDE_FIRST_PROGRESS_TIMED_OUT:-0}" -eq 1 ] && echo yes || echo no)"
        echo "- First-progress signal: ${FIRST_PROGRESS_SIGNAL:-none}"
        echo "- Builder mode: ${CLAUDE_CODE_BUILDER_MODE:-standard}"
        echo "- API probe mode: ${CLAUDE_CODE_API_PROBE_MODE:-always}"
        echo "- Probe environment: ${CLAUDE_CODE_PROBE_ENVIRONMENT:-auto}"
        echo "- First-progress action: ${CLAUDE_CODE_FIRST_PROGRESS_ACTION:-observe}"
        echo "- Observation probe ran: $([ "${_OBSERVATION_PROBE_RAN:-0}" -eq 1 ] && echo yes || echo no)"
        echo "- Startup probe conclusion: ${_STARTUP_PROBE_CONCLUSION:-not-run}"
        echo "- Approval-blocked early convergence: $([ "${CLAUDE_APPROVAL_CONVERGED:-0}" -eq 1 ] && echo yes || echo no)"
        echo "- Fallback result generated: $([ "$RESULT_FALLBACK_GENERATED" -eq 1 ] && echo yes || echo no)"
        echo "- Dispatch outcome: ${DISPATCH_OUTCOME}"
        echo "- Semantic result error: $([ "$CLAUDE_SEMANTIC_ERROR" -eq 1 ] && echo yes || echo no)"
        if [ "$CLAUDE_SEMANTIC_ERROR" -eq 1 ]; then
            echo "- Semantic error reason: ${CLAUDE_SEMANTIC_ERROR_REASON}"
        fi
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
        echo "- Startup interaction health: $STARTUP_INTERACTION_HEALTH_FILE"
        echo "- Interaction health: $INTERACTION_HEALTH_FILE"
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
echo "External Integrations: ${_EXTERNAL_INTEGRATIONS_ALLOWED}"
echo "Strict MCP Isolation: ${_STRICT_MCP_ISOLATION}"
echo "MCP Config Paths: ${_MCP_CONFIG_PATHS_EVIDENCE}"
echo "Plugin Paths: ${_PLUGIN_PATHS_EVIDENCE}"
if [ -n "${_EXTERNAL_INTEGRATION_REJECTION:-}" ]; then
    echo "Integration Rejection: ${_EXTERNAL_INTEGRATION_REJECTION}"
fi
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
echo "Tool Profile:    $CLAUDE_CODE_TOOL_PROFILE (${_TOOL_PROFILE_DERIVATION})"
echo "First Progress:  ${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS}s timeout"
echo "Progress Ext:    ${CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS}s"
echo "Growth Ext:      ${CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS}s"
echo "Dispatch Outcome:${DISPATCH_OUTCOME}"
echo "Task Card Full:  ${WORKTREE_DIR}/TASK_CARD_FULL.md"
echo "Claude Task:     ${WORKTREE_DIR}/CLAUDE_TASK_CARD.md"
echo "Result:          $RESULT_FILE"
echo "Raw Result:      $RAW_RESULT_FILE"
echo "Status:          $STATUS_FILE"
echo "Network Log:     $NETWORK_FILE"
echo "Attempt Class:   $ATTEMPT_CLASSIFICATION_FILE"
echo "Startup Probe:   $STARTUP_INTERACTION_HEALTH_FILE"
echo "API Probe:       $INTERACTION_HEALTH_FILE"
if [ -n "$ADVISOR_CONTINUATION_AUDIT_FILE" ]; then
echo "Audit:           $ADVISOR_CONTINUATION_AUDIT_FILE"
fi
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
