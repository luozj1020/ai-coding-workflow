#!/usr/bin/env bash
# dispatch-to-claude.sh  -  Dispatch a task card to Claude Code in an isolated worktree.
#
# Usage: bash ai/dispatch-to-claude.sh <task-card-path>
#        [--empty-api-config-env NAME] [--execution-env auto|sandbox|host]
#        [--dirty-source-mode block|snapshot]
#        [--tool-profile auto|default|editor-only|minimal-builder|locator-builder|checker|diagnostic]
#        [--retry-in-place-task-id TASK_ID | --reviewed-continuation APPROVAL]
#        [--context-lease LEASE --continuation-kind KIND]
#        [--recovery-classification ATTEMPT_CLASSIFICATION]
#        [--context-compile-strategy coverage|anchors-only]
#        [--force-fresh-session] [--rehydrate-from CAPSULE]
#        [--preflight-task-id TASK_ID]
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Git for Windows can be launched through bin/bash.exe without the usual Unix tool PATH.
# Append common Unix tool paths without overriding caller-provided shims or test fakes.
PATH="${PATH}:/usr/bin:/bin:/mingw64/bin"
export PATH

# Workflow helpers are executed from the source tree and import sibling
# modules.  Never let those control-plane imports create __pycache__ entries:
# they would make a clean source worktree appear dirty during a later dispatch.
PYTHONDONTWRITEBYTECODE=1
export PYTHONDONTWRITEBYTECODE

if [ $# -lt 1 ]; then
    echo "Usage: $0 <task-card-path> [--empty-api-config-env NAME] [--execution-env auto|sandbox|host] [--dirty-source-mode block|snapshot] [--tool-profile PROFILE] [--retry-in-place-task-id TASK_ID | --reviewed-continuation APPROVAL | --bookend-continuation RECEIPT | --context-lease LEASE --continuation-kind KIND] [--recovery-classification ATTEMPT_CLASSIFICATION] [--context-compile-strategy coverage|anchors-only] [--force-fresh-session] [--rehydrate-from CAPSULE] [--preflight-task-id TASK_ID]" >&2
    exit 1
fi

TASK_CARD="$1"
shift
EMPTY_API_CONFIG_ENV=""
DISPATCH_EXECUTION_ENV="auto"
DIRTY_SOURCE_MODE_OPTION=""
TOOL_PROFILE_OPTION=""
RETRY_IN_PLACE_TASK_ID_OPTION=""
REVIEWED_CONTINUATION_OPTION=""
BOOKEND_CONTINUATION_OPTION=""
CONTEXT_LEASE_OPTION=""
CONTINUATION_KIND_OPTION=""
FORCE_FRESH_SESSION_OPTION=0
REHYDRATE_FROM_OPTION=""
RECOVERY_CLASSIFICATION_OPTION=""
CONTEXT_COMPILE_STRATEGY_OPTION=""
PREFLIGHT_TASK_ID_OPTION=""
while [ $# -gt 0 ]; do
    case "$1" in
        --empty-api-config-env)
            [ $# -ge 2 ] || {
                echo "Error: --empty-api-config-env requires a value." >&2
                exit 1
            }
            EMPTY_API_CONFIG_ENV="$2"
            shift 2
            ;;
        --execution-env)
            [ $# -ge 2 ] || {
                echo "Error: --execution-env requires auto, sandbox, or host." >&2
                exit 1
            }
            DISPATCH_EXECUTION_ENV="$2"
            case "$DISPATCH_EXECUTION_ENV" in
                auto|sandbox|host) ;;
                *)
                    echo "Error: --execution-env must be auto, sandbox, or host." >&2
                    exit 1
                    ;;
            esac
            shift 2
            ;;
        --dirty-source-mode)
            [ $# -ge 2 ] || {
                echo "Error: --dirty-source-mode requires block or snapshot." >&2
                exit 1
            }
            DIRTY_SOURCE_MODE_OPTION="$2"
            case "$DIRTY_SOURCE_MODE_OPTION" in
                block|snapshot) ;;
                *)
                    echo "Error: --dirty-source-mode must be block or snapshot." >&2
                    exit 1
                    ;;
            esac
            shift 2
            ;;
        --tool-profile)
            [ $# -ge 2 ] || {
                echo "Error: --tool-profile requires a value." >&2
                exit 1
            }
            TOOL_PROFILE_OPTION="$2"
            case "$TOOL_PROFILE_OPTION" in
                auto|default|editor-only|minimal-builder|locator-builder|checker|diagnostic) ;;
                *)
                    echo "Error: --tool-profile must be auto, default, editor-only, minimal-builder, locator-builder, checker, or diagnostic." >&2
                    exit 1
                    ;;
            esac
            shift 2
            ;;
        --retry-in-place-task-id)
            [ $# -ge 2 ] || {
                echo "Error: --retry-in-place-task-id requires a task id." >&2
                exit 1
            }
            RETRY_IN_PLACE_TASK_ID_OPTION="$2"
            shift 2
            ;;
        --reviewed-continuation)
            [ $# -ge 2 ] || {
                echo "Error: --reviewed-continuation requires an approval path." >&2
                exit 1
            }
            REVIEWED_CONTINUATION_OPTION="$2"
            shift 2
            ;;
        --bookend-continuation)
            [ $# -ge 2 ] || {
                echo "Error: --bookend-continuation requires a receipt path." >&2
                exit 1
            }
            BOOKEND_CONTINUATION_OPTION="$2"
            shift 2
            ;;
        --context-lease)
            [ $# -ge 2 ] || {
                echo "Error: --context-lease requires a lease path." >&2
                exit 1
            }
            CONTEXT_LEASE_OPTION="$2"
            shift 2
            ;;
        --continuation-kind)
            [ $# -ge 2 ] || {
                echo "Error: --continuation-kind requires next-slice, revision, or checker-followup." >&2
                exit 1
            }
            CONTINUATION_KIND_OPTION="$2"
            case "$CONTINUATION_KIND_OPTION" in
                next-slice|revision|checker-followup) ;;
                *)
                    echo "Error: --continuation-kind must be next-slice, revision, or checker-followup." >&2
                    exit 1
                    ;;
            esac
            shift 2
            ;;
        --force-fresh-session)
            FORCE_FRESH_SESSION_OPTION=1
            shift
            ;;
        --rehydrate-from)
            [ $# -ge 2 ] || {
                echo "Error: --rehydrate-from requires a capsule path." >&2
                exit 1
            }
            REHYDRATE_FROM_OPTION="$2"
            shift 2
            ;;
        --recovery-classification)
            [ $# -ge 2 ] || {
                echo "Error: --recovery-classification requires an attempt-classification path." >&2
                exit 1
            }
            RECOVERY_CLASSIFICATION_OPTION="$2"
            shift 2
            ;;
        --context-compile-strategy)
            [ $# -ge 2 ] || {
                echo "Error: --context-compile-strategy requires coverage or anchors-only." >&2
                exit 1
            }
            CONTEXT_COMPILE_STRATEGY_OPTION="$2"
            case "$CONTEXT_COMPILE_STRATEGY_OPTION" in
                coverage|anchors-only) ;;
                *)
                    echo "Error: --context-compile-strategy must be coverage or anchors-only." >&2
                    exit 1
                    ;;
            esac
            shift 2
            ;;
        --preflight-task-id)
            [ $# -ge 2 ] || {
                echo "Error: --preflight-task-id requires a task id." >&2
                exit 1
            }
            PREFLIGHT_TASK_ID_OPTION="$2"
            case "$PREFLIGHT_TASK_ID_OPTION" in
                *[!A-Za-z0-9._-]*)
                    echo "Error: --preflight-task-id contains unsafe characters." >&2
                    exit 1
                    ;;
            esac
            shift 2
            ;;
        *)
            echo "Error: unknown option: $1" >&2
            exit 1
            ;;
    esac
done

if [ -n "$CONTEXT_LEASE_OPTION" ]; then
    if [ -n "$REVIEWED_CONTINUATION_OPTION" ] || [ -n "${CLAUDE_CODE_REVIEWED_CONTINUATION:-}" ]; then
        echo "Error: --context-lease and --reviewed-continuation are mutually exclusive." >&2
        exit 1
    fi
    if [ -z "$CONTINUATION_KIND_OPTION" ]; then
        echo "Error: --context-lease requires --continuation-kind." >&2
        exit 1
    fi
    REVIEWED_CONTINUATION_OPTION="$CONTEXT_LEASE_OPTION"
elif [ -n "$CONTINUATION_KIND_OPTION" ] || [ "$FORCE_FRESH_SESSION_OPTION" -eq 1 ] || [ -n "$REHYDRATE_FROM_OPTION" ]; then
    echo "Error: --continuation-kind, --force-fresh-session, and --rehydrate-from require --context-lease." >&2
    exit 1
fi

if [ -n "$RECOVERY_CLASSIFICATION_OPTION" ]; then
    if [ -n "$RETRY_IN_PLACE_TASK_ID_OPTION" ]; then
        echo "Error: --recovery-classification cannot be combined with --retry-in-place-task-id; transport retry must preserve the exact prior task." >&2
        exit 1
    fi
    if [ ! -f "$RECOVERY_CLASSIFICATION_OPTION" ]; then
        echo "Error: --recovery-classification file does not exist: ${RECOVERY_CLASSIFICATION_OPTION}" >&2
        exit 1
    fi
fi

if [ -n "$RETRY_IN_PLACE_TASK_ID_OPTION" ]; then
    case "$RETRY_IN_PLACE_TASK_ID_OPTION" in
        *[!A-Za-z0-9._-]*)
            echo "Error: --retry-in-place-task-id contains unsafe characters." >&2
            exit 1
            ;;
    esac
    CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID="$RETRY_IN_PLACE_TASK_ID_OPTION"
    export CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID
fi
if [ -n "$REVIEWED_CONTINUATION_OPTION" ]; then
    CLAUDE_CODE_REVIEWED_CONTINUATION="$REVIEWED_CONTINUATION_OPTION"
    export CLAUDE_CODE_REVIEWED_CONTINUATION
fi
if [ -n "$BOOKEND_CONTINUATION_OPTION" ]; then
    CLAUDE_CODE_BOOKEND_CONTINUATION="$BOOKEND_CONTINUATION_OPTION"
    export CLAUDE_CODE_BOOKEND_CONTINUATION
fi
if [ -n "$DIRTY_SOURCE_MODE_OPTION" ]; then
    CLAUDE_CODE_DIRTY_SOURCE_MODE="$DIRTY_SOURCE_MODE_OPTION"
    export CLAUDE_CODE_DIRTY_SOURCE_MODE
fi
if [ -n "$TOOL_PROFILE_OPTION" ]; then
    CLAUDE_CODE_TOOL_PROFILE="$TOOL_PROFILE_OPTION"
    export CLAUDE_CODE_TOOL_PROFILE
fi
if [ -n "$CONTEXT_COMPILE_STRATEGY_OPTION" ]; then
    CLAUDE_CODE_CONTEXT_COMPILE_STRATEGY="$CONTEXT_COMPILE_STRATEGY_OPTION"
    export CLAUDE_CODE_CONTEXT_COMPILE_STRATEGY
fi
if [ -n "$EMPTY_API_CONFIG_ENV" ]; then
    case "$EMPTY_API_CONFIG_ENV" in
        *[!A-Z0-9_]*|[0-9]*|"")
            echo "Error: --empty-api-config-env must be an uppercase environment name ending in _API_CONFIG_FILE." >&2
            exit 1
            ;;
    esac
    case "$EMPTY_API_CONFIG_ENV" in
        *_API_CONFIG_FILE) ;;
        *)
            echo "Error: --empty-api-config-env must end in _API_CONFIG_FILE." >&2
            exit 1
            ;;
    esac
    printf -v "$EMPTY_API_CONFIG_ENV" '%s' /dev/null
    export "$EMPTY_API_CONFIG_ENV"
fi

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

RUNTIME_TASK_ID_HELPER="${SCRIPT_DIR}/claude_task_id.py"
if [ -z "$PYTHON_CMD" ] || [ ! -f "$RUNTIME_TASK_ID_HELPER" ]; then
    echo "Error: runtime task-id helper is unavailable beside the dispatcher." >&2
    exit 1
fi
normalize_runtime_task_id() {
    "$PYTHON_CMD" "$RUNTIME_TASK_ID_HELPER" normalize "$1"
}
if [ -n "$PREFLIGHT_TASK_ID_OPTION" ]; then
    if ! PREFLIGHT_TASK_ID_OPTION="$(normalize_runtime_task_id "$PREFLIGHT_TASK_ID_OPTION" 2>/dev/null)"; then
        echo "Error: --preflight-task-id is not a valid runtime task id." >&2
        exit 1
    fi
fi
if [ -n "${CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID:-}" ]; then
    if ! CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID="$(normalize_runtime_task_id "$CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID" 2>/dev/null)"; then
        echo "Error: --retry-in-place-task-id is not a valid runtime task id." >&2
        exit 1
    fi
    RETRY_IN_PLACE_TASK_ID_OPTION="$CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID"
    export CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID
fi

# Context Lease identity is derived before worktree selection. Empty values
# remain explicitly unbound for legacy providers; non-empty values are checked
# on every warm continuation so a model/provider switch cannot silently reuse
# the prior conversation.
_CONTEXT_MODEL_HINT="${ANTHROPIC_MODEL:-}"
case "$_CONTEXT_MODEL_HINT" in
    ""|*[!A-Za-z0-9._:/@+-]*)
        if [ -n "$_CONTEXT_MODEL_HINT" ]; then
            echo "Error: ANTHROPIC_MODEL contains unsupported identity characters." >&2
            exit 1
        fi
        ;;
esac
_CONTEXT_PROVIDER_ROUTE_SHA256=""
if [ -n "$PYTHON_CMD" ] && [ -n "${ANTHROPIC_BASE_URL:-}" ]; then
    _CONTEXT_PROVIDER_ROUTE_SHA256="$("$PYTHON_CMD" - "${ANTHROPIC_BASE_URL}" <<'PYEOF' 2>/dev/null || true
import hashlib, sys
from urllib.parse import urlsplit

value = sys.argv[1]
parsed = urlsplit(value)
origin = "{}://{}".format(parsed.scheme.lower(), parsed.netloc.lower()) if parsed.netloc else value
print("sha256:" + hashlib.sha256(origin.encode("utf-8")).hexdigest())
PYEOF
)"
fi
_CONTEXT_LEASE_ROUTE=""
_CONTEXT_LEASE_ID=""
_CONTEXT_LEASE_CALLS_USED=0
_CONTEXT_LEASE_MAX_WARM_CALLS=0
_CONTEXT_CHECKPOINT_REQUIRED=0
_CONTEXT_FORCE_FRESH_SESSION=0

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
CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS="${CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS:-}"
CLAUDE_CODE_HARD_TIMEOUT_SECONDS="${CLAUDE_CODE_HARD_TIMEOUT_SECONDS:-1500}"
CLAUDE_CODE_HEARTBEAT_SECONDS="${CLAUDE_CODE_HEARTBEAT_SECONDS:-30}"
CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS="${CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS:-0}"
CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS="${CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS:-300}"
CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS="${CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS:-300}"
CLAUDE_CODE_TIMEOUT_ADVISOR="${CLAUDE_CODE_TIMEOUT_ADVISOR:-auto}"
CLAUDE_CODE_TIMEOUT_ADVISOR_LEAD_SECONDS="${CLAUDE_CODE_TIMEOUT_ADVISOR_LEAD_SECONDS:-60}"
CLAUDE_CODE_TIMEOUT_ADVISOR_CALL_TIMEOUT_SECONDS="${CLAUDE_CODE_TIMEOUT_ADVISOR_CALL_TIMEOUT_SECONDS:-90}"
CLAUDE_CODE_TIMEOUT_ADVISOR_MAX_ATTEMPTS="${CLAUDE_CODE_TIMEOUT_ADVISOR_MAX_ATTEMPTS:-2}"
CLAUDE_CODE_TIMEOUT_ADVISOR_RETRY_SECONDS="${CLAUDE_CODE_TIMEOUT_ADVISOR_RETRY_SECONDS:-30}"
CLAUDE_CODE_ZERO_OUTPUT_PROBE_TIMEOUT_SECONDS="${CLAUDE_CODE_ZERO_OUTPUT_PROBE_TIMEOUT_SECONDS:-60}"
CLAUDE_CODE_RECENT_ACTIVITY_WINDOW_SECONDS="${CLAUDE_CODE_RECENT_ACTIVITY_WINDOW_SECONDS:-120}"
case "$CLAUDE_CODE_RECENT_ACTIVITY_WINDOW_SECONDS" in
    ''|*[!0-9]*)
        echo "Error: CLAUDE_CODE_RECENT_ACTIVITY_WINDOW_SECONDS must be a non-negative integer." >&2
        exit 1
        ;;
esac
CLAUDE_CODE_APPROVAL_CONVERGENCE_HEARTBEATS="${CLAUDE_CODE_APPROVAL_CONVERGENCE_HEARTBEATS:-2}"
case "$CLAUDE_CODE_APPROVAL_CONVERGENCE_HEARTBEATS" in ''|*[!0-9]*|0) echo "Error: CLAUDE_CODE_APPROVAL_CONVERGENCE_HEARTBEATS must be a positive integer." >&2; exit 1 ;; esac
CLAUDE_CODE_TERMINAL_DRAIN_SECONDS="${CLAUDE_CODE_TERMINAL_DRAIN_SECONDS:-1}"
case "$CLAUDE_CODE_TERMINAL_DRAIN_SECONDS" in ''|*[!0-9]*) echo "Error: CLAUDE_CODE_TERMINAL_DRAIN_SECONDS must be a non-negative integer." >&2; exit 1 ;; esac
CLAUDE_CODE_TERMINATE_GRACE_SECONDS="${CLAUDE_CODE_TERMINATE_GRACE_SECONDS:-5}"
case "$CLAUDE_CODE_TERMINATE_GRACE_SECONDS" in ''|*[!0-9]*|0) echo "Error: CLAUDE_CODE_TERMINATE_GRACE_SECONDS must be a positive integer." >&2; exit 1 ;; esac
CLAUDE_CODE_DIRTY_SOURCE_MODE="${CLAUDE_CODE_DIRTY_SOURCE_MODE:-block}"
case "$CLAUDE_CODE_DIRTY_SOURCE_MODE" in
    block|snapshot) ;;
    *) echo "Error: CLAUDE_CODE_DIRTY_SOURCE_MODE must be 'block' or 'snapshot'." >&2; exit 1 ;;
esac
CLAUDE_CODE_CHECKER_RUNTIME_ENFORCEMENT="${CLAUDE_CODE_CHECKER_RUNTIME_ENFORCEMENT:-1}"
case "$CLAUDE_CODE_CHECKER_RUNTIME_ENFORCEMENT" in 0|1) ;; *) echo "Error: CLAUDE_CODE_CHECKER_RUNTIME_ENFORCEMENT must be 0 or 1." >&2; exit 1 ;; esac
CLAUDE_CODE_CHECKER_FILE_TIMEOUT_SECONDS="${CLAUDE_CODE_CHECKER_FILE_TIMEOUT_SECONDS:-120}"
case "$CLAUDE_CODE_CHECKER_FILE_TIMEOUT_SECONDS" in ''|*[!0-9]*|0) echo "Error: CLAUDE_CODE_CHECKER_FILE_TIMEOUT_SECONDS must be a positive integer." >&2; exit 1 ;; esac
CLAUDE_CODE_EDIT_READY_GRACE_SECONDS="${CLAUDE_CODE_EDIT_READY_GRACE_SECONDS:-120}"
CLAUDE_CODE_PRODUCT_IDLE_TIMEOUT_SECONDS="${CLAUDE_CODE_PRODUCT_IDLE_TIMEOUT_SECONDS:-600}"
CLAUDE_CODE_PRODUCT_IDLE_CONFIRMATIONS="${CLAUDE_CODE_PRODUCT_IDLE_CONFIRMATIONS:-2}"
CLAUDE_CODE_TAIL_TIMEOUT_SECONDS="${CLAUDE_CODE_TAIL_TIMEOUT_SECONDS:-90}"
CLAUDE_CODE_COMPLETION_READY_TIMEOUT_SECONDS="${CLAUDE_CODE_COMPLETION_READY_TIMEOUT_SECONDS:-20}"
CLAUDE_CODE_CODEGRAPH_POLICY="${CLAUDE_CODE_CODEGRAPH_POLICY:-fallback}"
CLAUDE_CODE_CODEGRAPH_TIMEOUT_SECONDS="${CLAUDE_CODE_CODEGRAPH_TIMEOUT_SECONDS:-180}"
case "$CLAUDE_CODE_CODEGRAPH_POLICY" in fallback|repair|off) ;; *) echo "Error: CLAUDE_CODE_CODEGRAPH_POLICY must be fallback, repair, or off." >&2; exit 1 ;; esac
case "$CLAUDE_CODE_CODEGRAPH_TIMEOUT_SECONDS" in ''|*[!0-9]*|0) echo "Error: CLAUDE_CODE_CODEGRAPH_TIMEOUT_SECONDS must be a positive integer." >&2; exit 1 ;; esac
for _idle_name in CLAUDE_CODE_EDIT_READY_GRACE_SECONDS CLAUDE_CODE_PRODUCT_IDLE_TIMEOUT_SECONDS CLAUDE_CODE_TAIL_TIMEOUT_SECONDS CLAUDE_CODE_COMPLETION_READY_TIMEOUT_SECONDS; do
    _idle_value="${!_idle_name}"
    case "$_idle_value" in ''|*[!0-9]*) echo "Error: ${_idle_name} must be a non-negative integer." >&2; exit 1 ;; esac
done
case "$CLAUDE_CODE_PRODUCT_IDLE_CONFIRMATIONS" in ''|*[!0-9]*|0) echo "Error: CLAUDE_CODE_PRODUCT_IDLE_CONFIRMATIONS must be a positive integer." >&2; exit 1 ;; esac
case "$CLAUDE_CODE_TIMEOUT_ADVISOR" in auto|on|off) ;; *) echo "Error: CLAUDE_CODE_TIMEOUT_ADVISOR must be auto, on, or off." >&2; exit 1 ;; esac
for _advisor_name in CLAUDE_CODE_TIMEOUT_ADVISOR_LEAD_SECONDS CLAUDE_CODE_TIMEOUT_ADVISOR_CALL_TIMEOUT_SECONDS CLAUDE_CODE_TIMEOUT_ADVISOR_RETRY_SECONDS; do
    _advisor_value="${!_advisor_name}"
    case "$_advisor_value" in ''|*[!0-9]*) echo "Error: ${_advisor_name} must be a non-negative integer." >&2; exit 1 ;; esac
done
case "$CLAUDE_CODE_TIMEOUT_ADVISOR_CALL_TIMEOUT_SECONDS" in 0) echo "Error: CLAUDE_CODE_TIMEOUT_ADVISOR_CALL_TIMEOUT_SECONDS must be greater than 0." >&2; exit 1 ;; esac
case "$CLAUDE_CODE_TIMEOUT_ADVISOR_MAX_ATTEMPTS" in ''|*[!0-9]*|0) echo "Error: CLAUDE_CODE_TIMEOUT_ADVISOR_MAX_ATTEMPTS must be a positive integer." >&2; exit 1 ;; esac
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
CLAUDE_CODE_API_PROBE_MODE="${CLAUDE_CODE_API_PROBE_MODE:-adaptive}"
CLAUDE_CODE_PROBE_ENVIRONMENT="${CLAUDE_CODE_PROBE_ENVIRONMENT:-auto}"
CLAUDE_CODE_HOST_AUTHORITY="${CLAUDE_CODE_HOST_AUTHORITY:-0}"
case "$DISPATCH_EXECUTION_ENV" in
    host)
        CLAUDE_CODE_HOST_AUTHORITY=1
        CLAUDE_CODE_PROBE_ENVIRONMENT=host
        ;;
    sandbox)
        CLAUDE_CODE_HOST_AUTHORITY=0
        CLAUDE_CODE_PROBE_ENVIRONMENT=sandbox
        ;;
esac
CLAUDE_CODE_STARTUP_PREFLIGHT_REQUIRED="${CLAUDE_CODE_STARTUP_PREFLIGHT_REQUIRED:-1}"
CLAUDE_CODE_API_AVAILABILITY_TTL_SECONDS="${CLAUDE_CODE_API_AVAILABILITY_TTL_SECONDS:-86400}"
case "$CLAUDE_CODE_HOST_AUTHORITY" in 0|1) ;; *) echo "Error: CLAUDE_CODE_HOST_AUTHORITY must be 0 or 1." >&2; exit 1 ;; esac
case "$CLAUDE_CODE_STARTUP_PREFLIGHT_REQUIRED" in 0|1) ;; *) echo "Error: CLAUDE_CODE_STARTUP_PREFLIGHT_REQUIRED must be 0 or 1." >&2; exit 1 ;; esac
case "$CLAUDE_CODE_API_AVAILABILITY_TTL_SECONDS" in ''|*[!0-9]*|0) echo "Error: CLAUDE_CODE_API_AVAILABILITY_TTL_SECONDS must be a positive integer." >&2; exit 1 ;; esac
if [ "$CLAUDE_CODE_HOST_AUTHORITY" = "1" ]; then
    # This flag is an assertion made by an already-authorized outer caller.
    # Unsetting the marker does not grant network access inside a sandbox.
    [ "$CLAUDE_CODE_PROBE_ENVIRONMENT" = "auto" ] && CLAUDE_CODE_PROBE_ENVIRONMENT="host"
    unset CODEX_SANDBOX_NETWORK_DISABLED
fi
if [ -n "${CLAUDE_CODE_FIRST_PROGRESS_ACTION+x}" ]; then
    _FIRST_PROGRESS_ACTION_EXPLICIT=1
else
    _FIRST_PROGRESS_ACTION_EXPLICIT=0
    CLAUDE_CODE_FIRST_PROGRESS_ACTION=observe
fi
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
if [ -z "$CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS" ]; then
    CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS="$CLAUDE_CODE_TIMEOUT_SECONDS"
fi

# --- Spec item 1: task-mode-aware worktree strategy default ---
# Parse task mode from the task card table to enable smart strategy selection.
# When the user did not explicitly set CLAUDE_CODE_WORKTREE_STRATEGY, select
# reuse-managed only for serial low-risk checker-test cards.  Parallel/DAG,
# Builder/mixed, missing/ambiguous mode, or any risk keyword stays fresh.
_PARSED_TASK_MODE=""
_DECLARED_TASK_MODE=""
_TASK_CARD_BUILDER_MODE=""
_TASK_MODE_BUILDER_HINT=""
_TASK_MODE_NORMALIZED=0
_TASK_MODE_NORMALIZATION_REASON="none"
_TASK_MODE_ROLE_ALIAS="none"
_REVIEWED_INHERITED_BUILDER_MODE=""
_REVIEWED_INHERITED_TOOL_PROFILE=""
_REVIEWED_PRIOR_CONTEXT_LEASE_ID=""
if [ -n "${AI_WORKFLOW_TASK_MODE:-}" ]; then
    _PARSED_TASK_MODE="$(printf '%s' "$AI_WORKFLOW_TASK_MODE" | tr '[:upper:]' '[:lower:]')"
elif [ -f "$TASK_CARD" ]; then
    _PARSED_TASK_MODE="$(awk -F'|' '
        /aiwf-execution-card-v1/ {
            value = $0
            sub(/^.*task-mode=/, "", value)
            sub(/[;[:space:]>].*$/, "", value)
            if (value != "") { print tolower(value); exit }
        }
        /^\|/ && NF >= 3 {
            field = $2; value = $3
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", field)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
            if (tolower(field) == "mode") { print tolower(value); exit }
        }
    ' "$TASK_CARD" 2>/dev/null || true)"
fi
if [ -f "$TASK_CARD" ]; then
    _TASK_CARD_BUILDER_MODE="$(awk -F'|' '
        /aiwf-execution-card-v1/ {
            value = $0
            sub(/^.*builder-mode=/, "", value)
            sub(/[;[:space:]>].*$/, "", value)
            if (value != "") { print tolower(value); exit }
        }
        /^\|/ && NF >= 3 {
            field = $2; value = $3
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", field)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
            if (tolower(field) == "builder mode") { print tolower(value); exit }
        }
    ' "$TASK_CARD" 2>/dev/null || true)"
fi
_DECLARED_TASK_MODE="$_PARSED_TASK_MODE"
# Routing roles/presets are not runtime task modes. Normalize known legacy or
# hand-written aliases before capability negotiation, while retaining the
# declared value in receipts. Unknown and conflicting combinations fail early.
case "$_PARSED_TASK_MODE" in
    solution-planner)
        _PARSED_TASK_MODE="builder"
        _TASK_MODE_BUILDER_HINT="solution-planning"
        _TASK_MODE_ROLE_ALIAS="solution-planner"
        _TASK_MODE_NORMALIZED=1
        _TASK_MODE_NORMALIZATION_REASON="role-alias-to-runtime-mode"
        ;;
    execution-builder)
        _PARSED_TASK_MODE="builder"
        _TASK_MODE_BUILDER_HINT="execution-only"
        _TASK_MODE_ROLE_ALIAS="execution-builder"
        _TASK_MODE_NORMALIZED=1
        _TASK_MODE_NORMALIZATION_REASON="role-alias-to-runtime-mode"
        ;;
    batch-builder)
        _PARSED_TASK_MODE="builder"
        _TASK_MODE_BUILDER_HINT="batch"
        _TASK_MODE_ROLE_ALIAS="batch-builder"
        _TASK_MODE_NORMALIZED=1
        _TASK_MODE_NORMALIZATION_REASON="role-alias-to-runtime-mode"
        ;;
    exploratory-builder)
        _PARSED_TASK_MODE="builder"
        _TASK_MODE_BUILDER_HINT="exploratory"
        _TASK_MODE_ROLE_ALIAS="exploratory-builder"
        _TASK_MODE_NORMALIZED=1
        _TASK_MODE_NORMALIZATION_REASON="role-alias-to-runtime-mode"
        ;;
    checker)
        _PARSED_TASK_MODE="checker-test"
        _TASK_MODE_ROLE_ALIAS="checker"
        _TASK_MODE_NORMALIZED=1
        _TASK_MODE_NORMALIZATION_REASON="role-alias-to-runtime-mode"
        ;;
    revision)
        _PARSED_TASK_MODE="builder"
        _TASK_MODE_NORMALIZED=1
        _TASK_MODE_NORMALIZATION_REASON="revision-to-builder"
        ;;
    builder|checker-test|mixed-exception|control-plane|"") ;;
    *)
        echo "Error: task card Mode '${_DECLARED_TASK_MODE}' is unknown; use builder, checker-test, mixed-exception, control-plane, or revision. Routing roles such as solution-planner are normalized only when recognized." >&2
        exit 1
        ;;
esac
case "$_TASK_CARD_BUILDER_MODE" in
    ""|auto) ;;
    standard|execution-only|solution-planning|batch|exploratory)
        if [ -n "$_TASK_MODE_BUILDER_HINT" ] && [ "$_TASK_MODE_BUILDER_HINT" != "$_TASK_CARD_BUILDER_MODE" ]; then
            echo "Error: task card Mode '${_DECLARED_TASK_MODE}' implies Builder mode '${_TASK_MODE_BUILDER_HINT}', but the card declares '${_TASK_CARD_BUILDER_MODE}'." >&2
            exit 1
        fi
        _TASK_MODE_BUILDER_HINT="$_TASK_CARD_BUILDER_MODE"
        ;;
    *)
        echo "Error: task card Builder mode '${_TASK_CARD_BUILDER_MODE}' is unknown." >&2
        exit 1
        ;;
esac
if [ -n "${CLAUDE_CODE_REVIEWED_CONTINUATION:-}" ] && \
   [ -n "$PYTHON_CMD" ] && [ -f "$CLAUDE_CODE_REVIEWED_CONTINUATION" ]; then
    IFS=$'\t' read -r _REVIEWED_INHERITED_BUILDER_MODE \
        _REVIEWED_INHERITED_TOOL_PROFILE _REVIEWED_PRIOR_CONTEXT_LEASE_ID < <(
        "$PYTHON_CMD" - "$CLAUDE_CODE_REVIEWED_CONTINUATION" <<'PYEOF'
import json, sys
try:
    value = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    value = {}
print("\t".join(str(value.get(key) or "") for key in (
    "inherited_builder_mode", "inherited_tool_profile", "prior_context_lease_id",
)))
PYEOF
    )
fi
if [ -n "$_REVIEWED_INHERITED_BUILDER_MODE" ] && \
   [ "$_PARSED_TASK_MODE" = "builder" ]; then
    case "$_REVIEWED_INHERITED_BUILDER_MODE" in
        standard|execution-only|solution-planning|batch|exploratory) ;;
        *)
            echo "Error: reviewed-continuation approval has an unknown inherited Builder mode." >&2
            exit 1
            ;;
    esac
    if [ -n "$_TASK_MODE_BUILDER_HINT" ] && \
       [ "$_TASK_MODE_BUILDER_HINT" != "$_REVIEWED_INHERITED_BUILDER_MODE" ]; then
        echo "Error: reviewed-continuation Builder mode conflicts with the next task card." >&2
        exit 1
    fi
    _TASK_MODE_BUILDER_HINT="$_REVIEWED_INHERITED_BUILDER_MODE"
fi
if [ -n "$_TASK_MODE_BUILDER_HINT" ] && \
   [ "$_TASK_MODE_BUILDER_HINT" != "standard" ] && \
   [ "$_PARSED_TASK_MODE" != "builder" ]; then
    echo "Error: task card Builder mode '${_TASK_MODE_BUILDER_HINT}' requires effective task Mode 'builder', found '${_PARSED_TASK_MODE:-unknown}'." >&2
    exit 1
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
CLAUDE_CODE_CHECKER_JOBS="${CLAUDE_CODE_CHECKER_JOBS:-4}"
CLAUDE_CODE_AUTO_BOOTSTRAP_CAPSULE="${CLAUDE_CODE_AUTO_BOOTSTRAP_CAPSULE:-1}"
CLAUDE_CODE_CONTEXT_COMPILE_STRATEGY="${CLAUDE_CODE_CONTEXT_COMPILE_STRATEGY:-coverage}"
case "$CLAUDE_CODE_NETWORK_HEALTHCHECK_TIMEOUT_SECONDS" in
    ''|*[!0-9]*)
        echo "Error: CLAUDE_CODE_NETWORK_HEALTHCHECK_TIMEOUT_SECONDS must be a non-negative integer." >&2
        exit 1
        ;;
esac
case "$CLAUDE_CODE_API_PROBE_MODE" in
    adaptive|always|failure-only|off) ;;
    *)
        echo "Error: CLAUDE_CODE_API_PROBE_MODE must be 'adaptive', 'always', 'failure-only', or 'off'." >&2
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
case "$CLAUDE_CODE_CHECKER_JOBS" in
    ''|*[!0-9]*|0)
        echo "Error: CLAUDE_CODE_CHECKER_JOBS must be a positive integer." >&2
        exit 1
        ;;
esac
if [ "$CLAUDE_CODE_CHECKER_JOBS" -gt 8 ]; then
    echo "Error: CLAUDE_CODE_CHECKER_JOBS must not exceed 8." >&2
    exit 1
fi
case "$CLAUDE_CODE_AUTO_BOOTSTRAP_CAPSULE" in
    0|1) ;;
    *)
        echo "Error: CLAUDE_CODE_AUTO_BOOTSTRAP_CAPSULE must be 0 or 1." >&2
        exit 1
        ;;
esac
case "$CLAUDE_CODE_CONTEXT_COMPILE_STRATEGY" in
    coverage|anchors-only) ;;
    *)
        echo "Error: CLAUDE_CODE_CONTEXT_COMPILE_STRATEGY must be coverage or anchors-only." >&2
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
    auto|standard|execution-only|solution-planning|batch|exploratory) ;;
    *)
        echo "Error: CLAUDE_CODE_BUILDER_MODE must be 'auto', 'standard', 'execution-only', 'solution-planning', 'batch', or 'exploratory'." >&2
        exit 1
        ;;
esac
if [ -n "$_TASK_MODE_BUILDER_HINT" ]; then
    if [ "$CLAUDE_CODE_BUILDER_MODE" = "auto" ]; then
        CLAUDE_CODE_BUILDER_MODE="$_TASK_MODE_BUILDER_HINT"
    elif [ "$CLAUDE_CODE_BUILDER_MODE" != "$_TASK_MODE_BUILDER_HINT" ]; then
        echo "Error: task card role/mode requires CLAUDE_CODE_BUILDER_MODE=${_TASK_MODE_BUILDER_HINT}, but '${CLAUDE_CODE_BUILDER_MODE}' was requested." >&2
        exit 1
    fi
fi
CLAUDE_CODE_TOOL_PROFILE="${CLAUDE_CODE_TOOL_PROFILE:-auto}"
if [ -n "$_REVIEWED_INHERITED_TOOL_PROFILE" ]; then
    if [ "$CLAUDE_CODE_TOOL_PROFILE" = "auto" ]; then
        CLAUDE_CODE_TOOL_PROFILE="$_REVIEWED_INHERITED_TOOL_PROFILE"
    elif [ "$CLAUDE_CODE_TOOL_PROFILE" != "$_REVIEWED_INHERITED_TOOL_PROFILE" ]; then
        echo "Error: reviewed-continuation tool profile conflicts with the requested profile." >&2
        exit 1
    fi
fi
case "$CLAUDE_CODE_TOOL_PROFILE" in
    auto|default|editor-only|minimal-builder|locator-builder|checker|diagnostic) ;;
    *)
        echo "Error: CLAUDE_CODE_TOOL_PROFILE must be 'auto', 'default', 'editor-only', 'minimal-builder', 'locator-builder', 'checker', or 'diagnostic'." >&2
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
CLAUDE_CODE_WRITE_SCOPE_ENFORCEMENT="${CLAUDE_CODE_WRITE_SCOPE_ENFORCEMENT:-auto}"
case "$CLAUDE_CODE_WRITE_SCOPE_ENFORCEMENT" in
    auto|required|off) ;;
    *)
        echo "Error: CLAUDE_CODE_WRITE_SCOPE_ENFORCEMENT must be auto, required, or off." >&2
        exit 1
        ;;
esac
_TASK_WRITE_SCOPE_POLICY="$(awk -F'|' '
    /^\|/ && NF >= 3 {
        field=$2; value=$3
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", field)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
        if (tolower(field) == "write scope enforcement") { print tolower(value); exit }
    }
' "$TASK_CARD" 2>/dev/null || true)"
if [ "$_TASK_WRITE_SCOPE_POLICY" = "required" ]; then
    CLAUDE_CODE_WRITE_SCOPE_ENFORCEMENT="required"
fi
_WRITE_SCOPE_REQUESTED="$CLAUDE_CODE_WRITE_SCOPE_ENFORCEMENT"
if [ "$CLAUDE_CODE_BUILDER_MODE" = "auto" ]; then
    if [ "$_PARSED_TASK_MODE" = "builder" ] && \
       grep -Eiq '^\|[[:space:]]*Planning owner[[:space:]]*\|[[:space:]]*Claude([[:space:]]*\||[[:space:]]*$)' "$TASK_CARD"; then
        CLAUDE_CODE_BUILDER_MODE="solution-planning"
    elif [ "$_PARSED_TASK_MODE" = "builder" ] && \
       grep -Eiq '^\|[[:space:]]*Transformation rule[[:space:]]*\|' "$TASK_CARD" && \
       grep -Eiq '^\|[[:space:]]*Independent write units[[:space:]]*\|' "$TASK_CARD"; then
        CLAUDE_CODE_BUILDER_MODE="batch"
    elif [ "$_PARSED_TASK_MODE" = "builder" ] && \
       grep -Eiq '^\|[[:space:]]*Builder mode[[:space:]]*\|[[:space:]]*exploratory([[:space:]]*\||[[:space:]]*$)' "$TASK_CARD"; then
        CLAUDE_CODE_BUILDER_MODE="exploratory"
    elif [ "$_PARSED_TASK_MODE" = "builder" ] && \
       grep -Eiq '^\|[[:space:]]*Execution-only (eligible|safe)\?[[:space:]]*\|[[:space:]]*yes([[:space:]]*\||[[:space:]]*$)' "$TASK_CARD" && \
       grep -Eiq '^\|[[:space:]]*Context (is )?sufficient for execution\?[[:space:]]*\|[[:space:]]*yes([[:space:]]*\||[[:space:]]*$)' "$TASK_CARD"; then
        CLAUDE_CODE_BUILDER_MODE="execution-only"
    else
        CLAUDE_CODE_BUILDER_MODE="standard"
    fi
fi

_CHECKER_WRITES_TESTS=0
if [ "$_PARSED_TASK_MODE" = "checker-test" ] && \
   grep -Eiq '^\|[[:space:]]*Test writing[[:space:]]*\|[[:space:]]*Claude([[:space:]]*\||[[:space:]]*$)' "$TASK_CARD" 2>/dev/null; then
    _CHECKER_WRITES_TESTS=1
fi
if [ "$_FIRST_PROGRESS_ACTION_EXPLICIT" -eq 0 ] && \
   { [ "$CLAUDE_CODE_BUILDER_MODE" = "execution-only" ] || \
     [ "$CLAUDE_CODE_BUILDER_MODE" = "batch" ] || \
     [ "$_CHECKER_WRITES_TESTS" -eq 1 ]; }; then
    CLAUDE_CODE_FIRST_PROGRESS_ACTION=stop
fi
# Execution-only mode is only allowed for task mode builder.
if { [ "$CLAUDE_CODE_BUILDER_MODE" = "execution-only" ] || \
     [ "$CLAUDE_CODE_BUILDER_MODE" = "solution-planning" ] || \
     [ "$CLAUDE_CODE_BUILDER_MODE" = "batch" ] || \
     [ "$CLAUDE_CODE_BUILDER_MODE" = "exploratory" ]; } && [ "$_PARSED_TASK_MODE" != "builder" ]; then
    echo "Error: CLAUDE_CODE_BUILDER_MODE=${CLAUDE_CODE_BUILDER_MODE} requires task mode 'builder', found '${_PARSED_TASK_MODE:-unknown}'." >&2
    exit 1
fi

# --- Tool profile resolution ---
# Resolve auto after task mode and builder mode are both known.
_TOOL_PROFILE_DERIVATION="explicit"
if [ "$CLAUDE_CODE_TOOL_PROFILE" = "auto" ]; then
    _TOOL_PROFILE_DERIVATION="auto-resolved"
    if grep -Eiq '^\|[[:space:]]*(Tool profile|Shell access)[[:space:]]*\|[[:space:]]*(editor-only|forbidden|none)([[:space:]]*\||[[:space:]]*$)' "$TASK_CARD" 2>/dev/null; then
        CLAUDE_CODE_TOOL_PROFILE="editor-only"
        _TOOL_PROFILE_DERIVATION="task-card-hard-restriction"
    elif [ "$CLAUDE_CODE_BUILDER_MODE" = "execution-only" ] || \
         [ "$CLAUDE_CODE_BUILDER_MODE" = "solution-planning" ] || \
         [ "$CLAUDE_CODE_BUILDER_MODE" = "batch" ]; then
        CLAUDE_CODE_TOOL_PROFILE="minimal-builder"
    elif [ "$_PARSED_TASK_MODE" = "checker-test" ]; then
        CLAUDE_CODE_TOOL_PROFILE="checker"
    elif [ "$CLAUDE_CODE_BUILDER_MODE" = "standard" ] || \
         [ "$CLAUDE_CODE_BUILDER_MODE" = "exploratory" ]; then
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
if [ "$CLAUDE_CODE_TOOL_PROFILE" = "editor-only" ] && [ "$_TOOL_PROFILE_SUPPORTED" -ne 1 ]; then
    echo "Error: editor-only requires Claude CLI --tools and --allowedTools support; refusing to expose Bash." >&2
    exit 1
fi

# First-progress timeout: accept both spellings with _SECONDS precedence.
# If CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS is unset and
# CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT is set, use the latter as the value.
_FIRST_PROGRESS_TIMEOUT_EXPLICIT=0
if [ -n "${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS+x}" ]; then
    _FIRST_PROGRESS_TIMEOUT_EXPLICIT=1
fi
if [ -z "${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS+x}" ]; then
    if [ -n "${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT+x}" ] && [ -n "$CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT" ]; then
        CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS="$CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT"
    elif [ "$CLAUDE_CODE_BUILDER_MODE" = "execution-only" ] || \
         [ "$CLAUDE_CODE_BUILDER_MODE" = "batch" ] || \
         [ "$_CHECKER_WRITES_TESTS" -eq 1 ]; then
        # A role-specific first-progress stop must not pre-empt the context
        # acquisition window. Keep both deadlines aligned by default while
        # preserving explicit FIRST_PROGRESS overrides for callers that need
        # a different policy.
        CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS="$CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS"
    else
        CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS=0
    fi
fi
# Record first-progress timeout alias source for status evidence.
_FIRST_PROGRESS_TIMEOUT_SOURCE="default"
if [ "$_FIRST_PROGRESS_TIMEOUT_EXPLICIT" -eq 0 ] && \
   [ -n "${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT+x}" ] && \
   [ "$CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS" = "$CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT" ]; then
    _FIRST_PROGRESS_TIMEOUT_SOURCE="alias(CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT)"
elif [ "$_FIRST_PROGRESS_TIMEOUT_EXPLICIT" -eq 1 ]; then
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
resolve_runtime_repository_root() {
    local source_root="$1"
    local common_raw common_abs primary
    common_raw="$(git -C "$source_root" rev-parse --git-common-dir 2>/dev/null || true)"
    if [ -n "$common_raw" ]; then
        case "$common_raw" in
            /*) common_abs="$common_raw" ;;
            *) common_abs="${source_root}/${common_raw}" ;;
        esac
        common_abs="$(cd "$common_abs" 2>/dev/null && pwd -P || true)"
        if [ -n "$common_abs" ] && [ "$(basename "$common_abs")" = ".git" ]; then
            dirname "$common_abs"
            return 0
        fi
    fi
    primary="$(git -C "$source_root" worktree list --porcelain 2>/dev/null \
        | sed -n 's/^worktree //p' | head -1)"
    if [ -n "$primary" ] && [ -d "$primary" ]; then
        (cd "$primary" && pwd -P)
        return 0
    fi
    return 1
}
RUNTIME_REPO_ROOT="$(resolve_runtime_repository_root "$REPO_ROOT" || true)"
if [ -z "$RUNTIME_REPO_ROOT" ] || [ ! -d "$RUNTIME_REPO_ROOT" ]; then
    echo "Error: could not resolve the Git common repository root." >&2
    exit 1
fi
MONITOR_SCRIPT="${SCRIPT_DIR}/monitor-claude.sh"
HANDOFF_RECORDER="${SCRIPT_DIR}/record-handoff-event.py"
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
if [ -n "$PREFLIGHT_TASK_ID_OPTION" ]; then
    TASK_ID="$PREFLIGHT_TASK_ID_OPTION"
elif [ -n "${AI_CODING_WORKFLOW_DAG_TASK_ID:-}" ]; then
    DAG_GROUP="${AI_CODING_WORKFLOW_DAG_GROUP_ID:-dag}"
    TASK_ID="${DAG_GROUP}-${AI_CODING_WORKFLOW_DAG_TASK_ID}-${TIMESTAMP}-${RAND_SUFFIX}"
else
    TASK_ID="claude-${TIMESTAMP}-${RAND_SUFFIX}"
fi
if ! TASK_ID="$(normalize_runtime_task_id "$TASK_ID" 2>/dev/null)"; then
    echo "Error: generated runtime task id is unsafe or ambiguous." >&2
    exit 1
fi

WORKTREE_ROOT="${RUNTIME_REPO_ROOT}/.worktrees"
REUSE_WORKTREE_DIR="${WORKTREE_ROOT}/reuse/claude-managed"
_MANAGED_RUNTIME_PROTOCOL="aiwf-task-runtime-v1"
_EXACT_WRITE_PROTOCOL="aiwf-exact-write-v3"
_VALIDATION_RUNNER_PROTOCOL="aiwf-validation-runner-v1"

# Validate managed-package consistency before any connectivity/model probe.
# Historical execution worktrees are intentionally not inspected here.
_MANAGED_RUNTIME_PREFLIGHT_FILE="${WORKTREE_ROOT}/${TASK_ID}.managed-runtime-preflight.json"
_MANAGED_RUNTIME_PREFLIGHT_ERROR=""
for _runtime_helper_name in write-approved-file.py run-approved-validation.py; do
    if [ ! -f "${SCRIPT_DIR}/${_runtime_helper_name}" ]; then
        _MANAGED_RUNTIME_PREFLIGHT_ERROR="missing-runtime-helper:${_runtime_helper_name}"
        break
    fi
done
_EARLY_WRITER_PROTOCOL=""
_EARLY_VALIDATION_PROTOCOL=""
if [ -z "$_MANAGED_RUNTIME_PREFLIGHT_ERROR" ]; then
    if [ -z "$PYTHON_CMD" ]; then
        _MANAGED_RUNTIME_PREFLIGHT_ERROR="python-unavailable"
    else
        _EARLY_WRITER_PROTOCOL="$("$PYTHON_CMD" \
            "${SCRIPT_DIR}/write-approved-file.py" --runtime-protocol 2>/dev/null || true)"
        if [ "$_EARLY_WRITER_PROTOCOL" != "$_EXACT_WRITE_PROTOCOL" ]; then
            _MANAGED_RUNTIME_PREFLIGHT_ERROR="exact-write-protocol-mismatch"
        fi
        _EARLY_VALIDATION_PROTOCOL="$("$PYTHON_CMD" \
            "${SCRIPT_DIR}/run-approved-validation.py" --runtime-protocol 2>/dev/null || true)"
        if [ -z "$_MANAGED_RUNTIME_PREFLIGHT_ERROR" ] && \
           [ "$_EARLY_VALIDATION_PROTOCOL" != "$_VALIDATION_RUNNER_PROTOCOL" ]; then
            _MANAGED_RUNTIME_PREFLIGHT_ERROR="validation-runner-protocol-mismatch"
        fi
    fi
fi
if [ -n "$_MANAGED_RUNTIME_PREFLIGHT_ERROR" ]; then
    mkdir -p "$WORKTREE_ROOT"
    if [ -n "$PYTHON_CMD" ]; then
        "$PYTHON_CMD" - "$_MANAGED_RUNTIME_PREFLIGHT_FILE" "$TASK_ID" \
            "$_MANAGED_RUNTIME_PROTOCOL" "$_EXACT_WRITE_PROTOCOL" \
            "${_EARLY_WRITER_PROTOCOL:-missing}" \
            "$_VALIDATION_RUNNER_PROTOCOL" \
            "${_EARLY_VALIDATION_PROTOCOL:-missing}" \
            "$_MANAGED_RUNTIME_PREFLIGHT_ERROR" <<'PYEOF'
import json, os, sys, tempfile
(path, task_id, bundle_protocol, expected, observed,
 validation_expected, validation_observed, reason) = sys.argv[1:]
value = {
    "schema_version": 1,
    "status": "blocked",
    "task_id": task_id,
    "failure_category": "workflow-runtime-mismatch",
    "reason": reason,
    "bundle_protocol": bundle_protocol,
    "expected_exact_write_protocol": expected,
    "observed_exact_write_protocol": observed,
    "expected_validation_runner_protocol": validation_expected,
    "observed_validation_runner_protocol": validation_observed,
    "builder_started": False,
    "model_interaction_started": False,
    "model_round_consumed": False,
    "counts_toward_takeover": False,
}
fd, temporary = tempfile.mkstemp(
    prefix=".managed-runtime-preflight-", dir=os.path.dirname(path)
)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(value, handle, indent=2, sort_keys=True)
    handle.write("\n")
os.replace(temporary, path)
PYEOF
    fi
    echo "Error: managed workflow runtime is internally inconsistent; refusing all model probes." >&2
    echo "failure_category=workflow-runtime-mismatch" >&2
    echo "runtime_preflight_receipt=$_MANAGED_RUNTIME_PREFLIGHT_FILE" >&2
    exit 1
fi

# Return 0 only when the recorded PID still identifies the same process.
# Return 1 for exited/reused/foreign PIDs and 2 when identity is inconclusive.
recorded_process_is_running() {
    local pid_file="$1"
    local identity_file="$2"
    local task_id="${3:-}"
    local role="${4:-}"
    local helper="${SCRIPT_DIR}/process-identity.py"
    if [ -n "$PYTHON_CMD" ] && [ -f "$helper" ] && [ -f "$identity_file" ]; then
        set +e
        "$PYTHON_CMD" "$helper" check --identity "$identity_file" \
            --task-id "$task_id" --role "$role" >/dev/null 2>&1
        local identity_status=$?
        set -e
        return "$identity_status"
    fi
    if [ -f "$pid_file" ]; then
        local pid_value
        pid_value="$(tr -d '[:space:]' < "$pid_file")"
        [ -n "$pid_value" ] && kill -0 "$pid_value" 2>/dev/null && return 0
    fi
    return 1
}

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
    local prior_dispatcher_identity="${prior_root}.dispatcher.process.json"
    local prior_claude_identity="${prior_root}.claude.process.json"
    local prior_checker_identity="${prior_root}.checker.process.json"

    # Load prior runtime identity artifact
    if [ ! -f "$prior_runtime" ]; then
        echo "Error: retry-in-place: prior runtime.json not found: ${prior_runtime}" >&2
        echo "The prior run may not have produced a runtime identity artifact." >&2
        exit 1
    fi

    local wt source_repo source_base_commit execution_base_commit strategy retry_ordinal
    local prior_snapshot_commit prior_snapshot_tree prior_snapshot_receipt
    wt="$(sed -n 's/.*"worktree"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"
    source_repo="$(sed -n 's/.*"source_repository"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"
    source_base_commit="$(sed -n 's/.*"source_base_commit"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"
    [ -n "$source_base_commit" ] || source_base_commit="$(sed -n 's/.*"base_commit"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"
    execution_base_commit="$(sed -n 's/.*"execution_base_commit"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"
    [ -n "$execution_base_commit" ] || execution_base_commit="$(sed -n 's/.*"worktree_start_commit"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"
    [ -n "$execution_base_commit" ] || execution_base_commit="$source_base_commit"
    prior_snapshot_commit="$(sed -n 's/.*"dirty_snapshot_commit"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"
    prior_snapshot_tree="$(sed -n 's/.*"dirty_snapshot_tree"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"
    prior_snapshot_receipt="$(sed -n 's/.*"dirty_snapshot_receipt"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"
    strategy="$(sed -n 's/.*"strategy"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"
    retry_ordinal="$(sed -n 's/.*"retry_ordinal"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$prior_runtime" | head -1)"
    [ -n "$retry_ordinal" ] || retry_ordinal=0
    _RETRY_BRANCH="$(sed -n 's/.*"branch"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"

    # Validate required fields
    if [ -z "$wt" ] || [ -z "$source_repo" ] || [ -z "$source_base_commit" ] || [ -z "$execution_base_commit" ]; then
        echo "Error: retry-in-place: prior runtime.json is malformed (missing worktree, source repository, or baseline commit)." >&2
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

    if [ "$retry_ordinal" -ge 1 ]; then
        echo "Error: retry-in-place: retry budget exhausted (prior retry_ordinal=${retry_ordinal}, maximum=1)." >&2
        echo "Use fallback-local, reroute, or request human intervention instead of another same-worktree retry." >&2
        exit 1
    fi

    # No live dispatcher/Claude/checker PIDs
    local pid_val identity_status
    if recorded_process_is_running "$prior_dispatcher_pid" "$prior_dispatcher_identity" "$prior_task_id" dispatcher; then
        pid_val="$(tr -d '[:space:]' < "$prior_dispatcher_pid" 2>/dev/null || true)"
        echo "Error: retry-in-place: prior dispatcher identity is still running (PID ${pid_val})." >&2
        exit 1
    else
        identity_status=$?
        if [ "$identity_status" -eq 2 ]; then
            echo "Error: retry-in-place: prior dispatcher identity is visibility-unknown." >&2
            exit 1
        fi
    fi
    if [ -f "$prior_claude_pid" ]; then
        if recorded_process_is_running "$prior_claude_pid" "$prior_claude_identity" "$prior_task_id" claude; then
            pid_val="$(tr -d '[:space:]' < "$prior_claude_pid" 2>/dev/null || true)"
            echo "Error: retry-in-place: prior Claude identity is still running (PID ${pid_val})." >&2
            exit 1
        elif [ "$?" -eq 2 ]; then
            echo "Error: retry-in-place: prior Claude identity is visibility-unknown." >&2
            exit 1
        fi
    elif recorded_process_is_running "$prior_pid" "$prior_claude_identity" "$prior_task_id" claude; then
        echo "Error: retry-in-place: prior Claude identity is still running." >&2
        exit 1
    fi
    if recorded_process_is_running "$prior_checker_pid" "$prior_checker_identity" "$prior_task_id" checker; then
        echo "Error: retry-in-place: prior checker identity is still running." >&2
        exit 1
    elif [ "$?" -eq 2 ]; then
        echo "Error: retry-in-place: prior checker identity is visibility-unknown." >&2
        exit 1
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
            # A regular zero-byte placeholder carries no implementation or
            # report evidence. Preserve it in the same worktree but do not
            # force manual deletion before retry; if Claude writes content,
            # the normal path/scope checks apply to the resulting file.
            if [ -f "$wt/$_uf" ] && [ ! -s "$wt/$_uf" ]; then
                continue
            fi
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
    if [ "$source_base_commit" != "$BASE_COMMIT" ]; then
        echo "Error: retry-in-place: recorded source base does not match current HEAD: recorded=${source_base_commit} current=${BASE_COMMIT}" >&2
        exit 1
    fi

    # Worktree HEAD binds the execution baseline. In dirty-snapshot mode this
    # intentionally differs from the source repository's original HEAD.
    local wt_head
    wt_head="$(git -C "$wt" rev-parse HEAD 2>/dev/null || true)"
    if [ "$wt_head" != "$execution_base_commit" ]; then
        echo "Error: retry-in-place: worktree HEAD does not match recorded execution base: worktree=${wt_head} execution_base=${execution_base_commit}" >&2
        exit 1
    fi
    if [ -n "$prior_snapshot_commit" ] && [ "$prior_snapshot_commit" != "$execution_base_commit" ]; then
        echo "Error: retry-in-place: dirty snapshot provenance does not match recorded execution base." >&2
        exit 1
    fi

    _RETRY_TASK_ID="$prior_task_id"
    _RETRY_ROOT_TASK_ID="$(sed -n 's/.*"lineage_root_task_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"
    [ -n "$_RETRY_ROOT_TASK_ID" ] || _RETRY_ROOT_TASK_ID="$prior_task_id"
    _RETRY_ORDINAL=$((retry_ordinal + 1))
    _RETRY_WORKTREE_DIR="$wt"
    _RETRY_SOURCE_BASE_COMMIT="$source_base_commit"
    _RETRY_EXECUTION_BASE_COMMIT="$execution_base_commit"
    if [ -n "$prior_snapshot_commit" ]; then
        DIRTY_SNAPSHOT_COMMIT="$prior_snapshot_commit"
        DIRTY_SNAPSHOT_TREE="$prior_snapshot_tree"
        _INHERITED_DIRTY_SNAPSHOT_RECEIPT="$prior_snapshot_receipt"
    fi
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

    local wt source_repo source_base_commit execution_base_commit strategy
    local prior_snapshot_commit prior_snapshot_tree prior_snapshot_receipt
    wt="$(sed -n 's/.*"worktree"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"
    source_repo="$(sed -n 's/.*"source_repository"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"
    source_base_commit="$(sed -n 's/.*"source_base_commit"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"
    [ -n "$source_base_commit" ] || source_base_commit="$(sed -n 's/.*"base_commit"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"
    execution_base_commit="$(sed -n 's/.*"execution_base_commit"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"
    [ -n "$execution_base_commit" ] || execution_base_commit="$(sed -n 's/.*"worktree_start_commit"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"
    [ -n "$execution_base_commit" ] || execution_base_commit="$source_base_commit"
    prior_snapshot_commit="$(sed -n 's/.*"dirty_snapshot_commit"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"
    prior_snapshot_tree="$(sed -n 's/.*"dirty_snapshot_tree"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"
    prior_snapshot_receipt="$(sed -n 's/.*"dirty_snapshot_receipt"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"
    _ADVISOR_CONTINUE_BRANCH="$(sed -n 's/.*"branch"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$prior_runtime" | head -1)"

    if [ -z "$wt" ] || [ -z "$source_repo" ] || [ -z "$source_base_commit" ] || [ -z "$execution_base_commit" ]; then
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
    if [ "$source_base_commit" != "$BASE_COMMIT" ]; then
        echo "Error: advisor-continuation: recorded source base does not match current HEAD: recorded=${source_base_commit} current=${BASE_COMMIT}" >&2
        exit 1
    fi

    # Worktree HEAD must equal the execution baseline, which may be a dirty
    # snapshot commit rather than the source repository HEAD.
    local wt_head
    wt_head="$(git -C "$wt" rev-parse HEAD 2>/dev/null || true)"
    if [ "$wt_head" != "$execution_base_commit" ]; then
        echo "Error: advisor-continuation: worktree HEAD does not match recorded execution base: worktree=${wt_head} execution_base=${execution_base_commit}" >&2
        exit 1
    fi
    if [ -n "$prior_snapshot_commit" ] && [ "$prior_snapshot_commit" != "$execution_base_commit" ]; then
        echo "Error: advisor-continuation: dirty snapshot provenance does not match recorded execution base." >&2
        exit 1
    fi
    _ADVISOR_SOURCE_BASE_COMMIT="$source_base_commit"
    _ADVISOR_EXECUTION_BASE_COMMIT="$execution_base_commit"
    if [ -n "$prior_snapshot_commit" ]; then
        DIRTY_SNAPSHOT_COMMIT="$prior_snapshot_commit"
        DIRTY_SNAPSHOT_TREE="$prior_snapshot_tree"
        _INHERITED_DIRTY_SNAPSHOT_RECEIPT="$prior_snapshot_receipt"
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
WORKTREE_START_COMMIT="$BASE_COMMIT"
DIRTY_SNAPSHOT_COMMIT=""
DIRTY_SNAPSHOT_TREE=""
_INHERITED_DIRTY_SNAPSHOT_RECEIPT=""

_RETRY_TASK_ID=""
_RETRY_WORKTREE_DIR=""
_RETRY_BRANCH=""
_RETRY_ROOT_TASK_ID=""
_RETRY_ORDINAL=0
_RETRY_SOURCE_BASE_COMMIT=""
_RETRY_EXECUTION_BASE_COMMIT=""
_REVIEWED_CONTINUATION_TASK_ID=""
_REVIEWED_CONTINUATION_WORKTREE_DIR=""
_REVIEWED_CONTINUATION_APPROVAL=""
_REVIEWED_CONTINUATION_APPROVAL_ID=""
_REVIEWED_CONTINUATION_BASELINE_HASH=""
_REVIEWED_CONTINUATION_NEXT_ROLE=""
_REVIEWED_CONTINUATION_LEASE_DIR=""
_REVIEWED_CONTINUATION_CONSUMED_DIR=""

_continuation_selector_count=0
[ -n "${CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID:-}" ] && _continuation_selector_count=$((_continuation_selector_count + 1))
[ -n "${CLAUDE_CODE_ADVISOR_CONTINUE_TASK_ID:-}" ] && _continuation_selector_count=$((_continuation_selector_count + 1))
[ -n "${CLAUDE_CODE_REVIEWED_CONTINUATION:-}" ] && _continuation_selector_count=$((_continuation_selector_count + 1))
[ -n "${CLAUDE_CODE_BOOKEND_CONTINUATION:-}" ] && _continuation_selector_count=$((_continuation_selector_count + 1))
if [ "$_continuation_selector_count" -gt 1 ]; then
    echo "Error: retry-in-place, advisor continuation, reviewed continuation, and bookend continuation are mutually exclusive." >&2
    exit 1
fi

if [ -n "${CLAUDE_CODE_REVIEWED_CONTINUATION:-}" ]; then
    _reviewed_helper="${SCRIPT_DIR}/prepare-worktree-continuation.py"
    if [ -z "$PYTHON_CMD" ] || [ ! -f "$_reviewed_helper" ]; then
        echo "Error: reviewed-continuation: Python helper is unavailable." >&2
        exit 1
    fi
    if [ -n "$CONTEXT_LEASE_OPTION" ]; then
        _context_helper="${SCRIPT_DIR}/context-lease.py"
        if [ ! -f "$_context_helper" ]; then
            echo "Error: context-lease helper is unavailable; refresh the bootstrapped workflow." >&2
            exit 1
        fi
        _context_args=(
            validate
            --context-lease "$CONTEXT_LEASE_OPTION"
            --next-task-card "$TASK_CARD"
            --continuation-kind "$CONTINUATION_KIND_OPTION"
            --tool-profile "$CLAUDE_CODE_TOOL_PROFILE"
        )
        [ -z "$_CONTEXT_MODEL_HINT" ] || _context_args+=(--model "$_CONTEXT_MODEL_HINT")
        [ -z "$_CONTEXT_PROVIDER_ROUTE_SHA256" ] || \
            _context_args+=(--provider-route-sha256 "$_CONTEXT_PROVIDER_ROUTE_SHA256")
        [ "$FORCE_FRESH_SESSION_OPTION" -eq 0 ] || _context_args+=(--force-fresh-session)
        [ -z "$REHYDRATE_FROM_OPTION" ] || _context_args+=(--rehydrate-from "$REHYDRATE_FROM_OPTION")
        _context_args+=(--allow-auto-rehydrate)
        if ! _context_validation="$("$PYTHON_CMD" "$_context_helper" "${_context_args[@]}")"; then
            echo "Error: Context Lease validation failed." >&2
            exit 1
        fi
        IFS=$'\t' read -r _CONTEXT_LEASE_ID _CONTEXT_LEASE_ROUTE \
            _CONTEXT_LEASE_CALLS_USED _CONTEXT_LEASE_MAX_WARM_CALLS \
            _CONTEXT_CHECKPOINT_REQUIRED < <(
            printf '%s' "$_context_validation" | "$PYTHON_CMD" -c \
                'import json,sys; v=json.load(sys.stdin); print("\t".join(str(v.get(k, "")) for k in ("lease_id","route","calls_used","max_warm_calls")) + "\t" + ("1" if v.get("checkpoint_required") else "0"))'
        )
        case "$_CONTEXT_LEASE_ROUTE" in
            warm-resume) ;;
            capsule-rehydrate|cold-fresh) _CONTEXT_FORCE_FRESH_SESSION=1 ;;
            *)
                echo "Error: Context Lease returned an unsupported session route." >&2
                exit 1
                ;;
        esac
    elif ! "$PYTHON_CMD" "$_reviewed_helper" validate \
        --approval "$CLAUDE_CODE_REVIEWED_CONTINUATION" \
        --next-task-card "$TASK_CARD" >/dev/null; then
        echo "Error: reviewed-continuation approval validation failed." >&2
        exit 1
    fi
    IFS=$'\t' read -r _REVIEWED_CONTINUATION_APPROVAL_ID _REVIEWED_CONTINUATION_TASK_ID \
        _REVIEWED_CONTINUATION_WORKTREE_DIR _REVIEWED_CONTINUATION_BASELINE_HASH \
        _REVIEWED_CONTINUATION_NEXT_ROLE < <(
        "$PYTHON_CMD" - "$CLAUDE_CODE_REVIEWED_CONTINUATION" <<'PYEOF'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
fields = ("approval_id", "prior_task_id", "worktree", "worktree_state_hash", "next_role")
print("\t".join(str(value.get(field, "")) for field in fields))
PYEOF
    )
    if [ -z "$_REVIEWED_CONTINUATION_APPROVAL_ID" ] || \
       [ -z "$_REVIEWED_CONTINUATION_TASK_ID" ] || \
       [ -z "$_REVIEWED_CONTINUATION_WORKTREE_DIR" ]; then
        echo "Error: reviewed-continuation approval is missing dispatcher fields." >&2
        exit 1
    fi
    case "$_REVIEWED_CONTINUATION_APPROVAL_ID" in
        *[!A-Za-z0-9._-]*) echo "Error: unsafe reviewed-continuation approval id." >&2; exit 1 ;;
    esac
    _REVIEWED_CONTINUATION_LEASE_DIR="${WORKTREE_ROOT}/.reviewed-continuation-lease-${_REVIEWED_CONTINUATION_APPROVAL_ID}"
    _REVIEWED_CONTINUATION_CONSUMED_DIR="${WORKTREE_ROOT}/.reviewed-continuation-consumed-${_REVIEWED_CONTINUATION_APPROVAL_ID}"
    if [ -e "$_REVIEWED_CONTINUATION_CONSUMED_DIR" ] || \
       ! mkdir "$_REVIEWED_CONTINUATION_LEASE_DIR" 2>/dev/null; then
        echo "Error: reviewed-continuation approval was already consumed or is active." >&2
        exit 1
    fi
    printf '%s\n' "$$" > "${_REVIEWED_CONTINUATION_LEASE_DIR}/dispatcher.pid"
    trap '[ -z "${_REVIEWED_CONTINUATION_LEASE_DIR:-}" ] || rm -rf "$_REVIEWED_CONTINUATION_LEASE_DIR"' EXIT
    _REVIEWED_CONTINUATION_APPROVAL="$(cd "$(dirname "$CLAUDE_CODE_REVIEWED_CONTINUATION")" && pwd)/$(basename "$CLAUDE_CODE_REVIEWED_CONTINUATION")"
    TASK_ID="claude-reviewed-${TIMESTAMP}-${RAND_SUFFIX}"
    WORKTREE_DIR="$_REVIEWED_CONTINUATION_WORKTREE_DIR"
    BRANCH_NAME="$(git -C "$WORKTREE_DIR" symbolic-ref --short HEAD 2>/dev/null || true)"
    WORKTREE_START_COMMIT="$(git -C "$WORKTREE_DIR" rev-parse HEAD 2>/dev/null || true)"
    echo "Worktree reuse (reviewed-continuation): $WORKTREE_DIR (prior task: $_REVIEWED_CONTINUATION_TASK_ID, new task: $TASK_ID)"
elif [ -n "${CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID:-}" ]; then
    validate_retry_in_place "$CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID"
    # Retry must receive a new unique TASK_ID; prior ID is for provenance only.
    TASK_ID="claude-retry-${TIMESTAMP}-${RAND_SUFFIX}"
    WORKTREE_DIR="$_RETRY_WORKTREE_DIR"
    BRANCH_NAME="$_RETRY_BRANCH"
    WORKTREE_START_COMMIT="$_RETRY_EXECUTION_BASE_COMMIT"
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
    WORKTREE_START_COMMIT="$_ADVISOR_EXECUTION_BASE_COMMIT"
    echo "Worktree reuse (advisor-continuation): $WORKTREE_DIR (prior task: $_ADVISOR_CONTINUE_TASK_ID, new task: $TASK_ID)"
elif [ -n "${CLAUDE_CODE_BOOKEND_CONTINUATION:-}" ]; then
    # --- Bookend convergence continuation ---
    # Reuses a dirty product worktree from a prior Bookend epoch.  Unlike
    # retry-in-place (which requires a clean worktree), this allows dirty
    # state because the frozen contract is the authority — no Codex review
    # is needed for ordinary compile/test failures.
    _BOOKEND_RECEIPT="$CLAUDE_CODE_BOOKEND_CONTINUATION"
    if [ ! -f "$_BOOKEND_RECEIPT" ]; then
        echo "Error: bookend-continuation: receipt file not found: $_BOOKEND_RECEIPT" >&2
        exit 1
    fi
    if [ -z "$PYTHON_CMD" ]; then
        echo "Error: bookend-continuation: Python is required for receipt validation." >&2
        exit 1
    fi
    # Validate receipt and extract fields
    IFS=$'\t' read -r _BOOKEND_WORKTREE_DIR _BOOKEND_STATE_HASH _BOOKEND_EPOCH < <(
        "$PYTHON_CMD" - "$_BOOKEND_RECEIPT" <<'PYEOF'
import json, sys
try:
    r = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    print("\t\t")
    sys.exit(0)
fields = ("product_worktree", "worktree_state_hash", "epoch")
print("\t".join(str(r.get(f, "")) for f in fields))
PYEOF
    )
    if [ -z "$_BOOKEND_WORKTREE_DIR" ] || [ -z "$_BOOKEND_STATE_HASH" ]; then
        echo "Error: bookend-continuation: receipt is missing product_worktree or worktree_state_hash." >&2
        exit 1
    fi
    # Validate receipt structure
    if ! "$PYTHON_CMD" - "$_BOOKEND_RECEIPT" "$REPO_ROOT" <<'PYEOF' >/dev/null 2>&1; then
import json, sys
r = json.load(open(sys.argv[1], encoding="utf-8"))
errors = []
if r.get("schema_version") != 1:
    errors.append("bad schema")
if r.get("kind") != "bookend-convergence-continuation":
    errors.append("bad kind")
if not r.get("logical_task_id"):
    errors.append("missing logical_task_id")
if not r.get("contract_hash"):
    errors.append("missing contract_hash")
if r.get("owner") != "claude":
    errors.append("owner must be claude")
if r.get("prior_write_grant_revoked") is not True:
    errors.append("prior_write_grant_revoked must be true")
if r.get("no_active_writer") is not True:
    errors.append("no_active_writer must be true")
sys.exit(1 if errors else 0)
PYEOF
        echo "Error: bookend-continuation: receipt structure validation failed." >&2
        exit 1
    fi
    # Worktree must exist and be a git worktree
    if [ ! -d "$_BOOKEND_WORKTREE_DIR" ]; then
        echo "Error: bookend-continuation: product worktree missing: $_BOOKEND_WORKTREE_DIR" >&2
        exit 1
    fi
    if ! git -C "$_BOOKEND_WORKTREE_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        echo "Error: bookend-continuation: product worktree is not a git worktree: $_BOOKEND_WORKTREE_DIR" >&2
        exit 1
    fi
    # Verify worktree is under .worktrees boundary
    case "$_BOOKEND_WORKTREE_DIR" in
        "${WORKTREE_ROOT}/"*) ;;
        *)
            echo "Error: bookend-continuation: worktree is outside .worktrees boundary: $_BOOKEND_WORKTREE_DIR" >&2
            exit 1
            ;;
    esac
    # Verify state hash has not drifted
    if [ -n "$PYTHON_CMD" ] && [ -f "${SCRIPT_DIR}/worktree_state_hash.py" ]; then
        _current_hash="$("$PYTHON_CMD" "${SCRIPT_DIR}/worktree_state_hash.py" --worktree "$_BOOKEND_WORKTREE_DIR" 2>/dev/null || echo "")"
        if [ -n "$_current_hash" ] && [ "$_current_hash" != "$_BOOKEND_STATE_HASH" ]; then
            echo "Error: bookend-continuation: worktree state hash drifted since receipt: current=${_current_hash} receipt=${_BOOKEND_STATE_HASH}" >&2
            exit 1
        fi
    fi
    # No live processes from prior epoch
    _bookend_prior_pid_files=("$_BOOKEND_WORKTREE_DIR"/*.pid)
    for _pid_file in "${_bookend_prior_pid_files[@]}"; do
        [ -f "$_pid_file" ] || continue
        _pid_val="$(tr -d '[:space:]' < "$_pid_file" 2>/dev/null || true)"
        [ -n "$_pid_val" ] || continue
        if kill -0 "$_pid_val" 2>/dev/null; then
            echo "Error: bookend-continuation: prior process still active (PID $_pid_val) in worktree." >&2
            exit 1
        fi
    done
    TASK_ID="claude-bookend-${TIMESTAMP}-${RAND_SUFFIX}"
    WORKTREE_DIR="$_BOOKEND_WORKTREE_DIR"
    BRANCH_NAME="$(git -C "$WORKTREE_DIR" symbolic-ref --short HEAD 2>/dev/null || true)"
    WORKTREE_START_COMMIT="$(git -C "$WORKTREE_DIR" rev-parse HEAD 2>/dev/null || true)"
    echo "Worktree reuse (bookend-continuation): $WORKTREE_DIR (epoch: $_BOOKEND_EPOCH, new task: $TASK_ID)"
else
    # --- Normal worktree setup (fresh or reuse-managed) ---
    if [ -n "$PREFLIGHT_TASK_ID_OPTION" ]; then
        TASK_ID="$PREFLIGHT_TASK_ID_OPTION"
    elif [ -n "${AI_CODING_WORKFLOW_DAG_TASK_ID:-}" ]; then
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

if ! TASK_ID="$(normalize_runtime_task_id "$TASK_ID" 2>/dev/null)"; then
    echo "Error: final runtime task id is unsafe or ambiguous." >&2
    exit 1
fi

mkdir -p "$WORKTREE_ROOT"

RESULT_FILE="${WORKTREE_ROOT}/${TASK_ID}.result.json"
RAW_RESULT_FILE="${WORKTREE_ROOT}/${TASK_ID}.result.raw.txt"
STATUS_FILE="${WORKTREE_ROOT}/${TASK_ID}.status.txt"
DIFFSTAT_FILE="${WORKTREE_ROOT}/${TASK_ID}.diffstat.txt"
DIFF_FILE="${WORKTREE_ROOT}/${TASK_ID}.diff"
CHECKER_REPORT_FILE="${WORKTREE_ROOT}/${TASK_ID}.checker-report.md"
CHECKER_VALIDATION_RECEIPT_FILE="${WORKTREE_ROOT}/${TASK_ID}.validation-receipt.json"
SCOPED_HANDOFF_MANIFEST_FILE="${WORKTREE_ROOT}/${TASK_ID}.scoped-handoff.json"
SCOPED_HANDOFF_PATCH_FILE="${WORKTREE_ROOT}/${TASK_ID}.scoped.patch"
CHECKER_LOGS_DIR="${WORKTREE_ROOT}/${TASK_ID}.checker-logs"
SOURCE_STATUS_FILE="${WORKTREE_ROOT}/${TASK_ID}.source-status.txt"
WORKTREE_STATUS_FILE="${WORKTREE_ROOT}/${TASK_ID}.worktree-status.txt"
UNTRACKED_FILE="${WORKTREE_ROOT}/${TASK_ID}.untracked.txt"
USAGE_FILE="${WORKTREE_ROOT}/${TASK_ID}.usage.txt"
REPORT_FILE="${WORKTREE_ROOT}/${TASK_ID}.report.md"
REPORT_ARTIFACT_VALIDATION_FILE="${WORKTREE_ROOT}/${TASK_ID}.report-artifact-validation.json"
REPORT_CONSISTENCY_FILE="${WORKTREE_ROOT}/${TASK_ID}.report-consistency.json"
OUTCOME_FILE="${WORKTREE_ROOT}/${TASK_ID}.outcome.json"
ACCEPTANCE_BUNDLE_FILE="${WORKTREE_ROOT}/${TASK_ID}.acceptance-bundle.json"
ACCEPTANCE_CAPSULE_FILE="${WORKTREE_ROOT}/${TASK_ID}.acceptance-capsule.json"
RECOVERED_COMPLETION_FILE="${WORKTREE_ROOT}/${TASK_ID}.recovered-completion.json"
WRITE_SCOPE_RECEIPT_FILE="${WORKTREE_ROOT}/${TASK_ID}.write-scope-enforcement.json"
PRODUCT_BASELINE_FILE="${WORKTREE_ROOT}/${TASK_ID}.product-baseline.json"
PRODUCT_LIVE_STATE_FILE="${WORKTREE_ROOT}/${TASK_ID}.product-state.live.json"
PRODUCT_STATE_FILE="${WORKTREE_ROOT}/${TASK_ID}.product-state.json"
CHANGE_SIZE_ADVISORY_FILE="${WORKTREE_ROOT}/${TASK_ID}.change-size-advisory.json"
EXTENSION_CAPSULE_FILE="${WORKTREE_ROOT}/${TASK_ID}.extension-capsule.json"
EXTENSION_ADVISOR_OUTPUT_FILE="${WORKTREE_ROOT}/${TASK_ID}.extension-advisor.txt"
EXTENSION_ADVISOR_STDERR_FILE="${WORKTREE_ROOT}/${TASK_ID}.extension-advisor.stderr.txt"
EXTENSION_ADVISOR_RECEIPT_FILE="${WORKTREE_ROOT}/${TASK_ID}.extension-advisor.json"
VALIDATION_CAPABILITY_FILE="${WORKTREE_ROOT}/${TASK_ID}.validation-capability.json"
MANAGED_RUNTIME_BUNDLE_FILE="${WORKTREE_ROOT}/${TASK_ID}.managed-runtime-bundle.json"
REVISION_CARD_VALIDATION_FILE="${WORKTREE_ROOT}/${TASK_ID}.revision-card-validation.json"
EXECUTION_CAPSULE_RECEIPT_FILE="${WORKTREE_ROOT}/${TASK_ID}.execution-capsule.json"
SKILL_CONTEXT_PACKET_FILE="${WORKTREE_ROOT}/${TASK_ID}.skill-context.md"
SKILL_CONTEXT_COMPILATION_FILE="${WORKTREE_ROOT}/${TASK_ID}.skill-context.json"
CONTEXT_CHECKPOINT_FILE="${WORKTREE_ROOT}/${TASK_ID}.context-checkpoint.md"
CONTEXT_CHECKPOINT_RECEIPT_FILE="${WORKTREE_ROOT}/${TASK_ID}.context-checkpoint.json"
RECOVERY_DELTA_FILE="${WORKTREE_ROOT}/${TASK_ID}.recovery-delta.md"
RECOVERY_DELTA_RECEIPT_FILE="${WORKTREE_ROOT}/${TASK_ID}.recovery-delta.json"
_CONTEXT_CHECKPOINT_MODE="none"
_CONTEXT_CHECKPOINT_RECEIPT_BOUND=0
_RECOVERY_DELTA_MODE="none"
_AUTO_BOOTSTRAP_CAPSULE=0
_REQUIRE_COMPLETE_EXECUTION_CONTRACT=0
if [ -n "$CONTEXT_LEASE_OPTION" ] && [ -n "$REHYDRATE_FROM_OPTION" ]; then
    _CONTEXT_CHECKPOINT_MODE="caller-supplied"
fi
if [ "$_CONTEXT_CHECKPOINT_REQUIRED" -eq 1 ]; then
    if [ -n "$REHYDRATE_FROM_OPTION" ]; then
        echo "Error: Context Lease requested automatic checkpoint while caller supplied one." >&2
        exit 1
    fi
    _CONTEXT_CHECKPOINT_HELPER="${SCRIPT_DIR}/build-context-checkpoint.py"
    if [ -z "$PYTHON_CMD" ] || [ ! -f "$_CONTEXT_CHECKPOINT_HELPER" ]; then
        echo "Error: Context Lease rehydrate checkpoint helper is unavailable; refresh the bootstrapped workflow." >&2
        exit 1
    fi
    if ! "$PYTHON_CMD" "$_CONTEXT_CHECKPOINT_HELPER" \
        --context-lease "$CONTEXT_LEASE_OPTION" \
        --next-task-card "$TASK_CARD" \
        --output "$CONTEXT_CHECKPOINT_FILE" \
        --receipt "$CONTEXT_CHECKPOINT_RECEIPT_FILE" >/dev/null; then
        echo "Error: deterministic Context Lease checkpoint generation failed." >&2
        exit 1
    fi
    REHYDRATE_FROM_OPTION="$CONTEXT_CHECKPOINT_FILE"
    _CONTEXT_CHECKPOINT_MODE="automatic"
    _CONTEXT_CHECKPOINT_RECEIPT_BOUND=1
fi
_EXECUTION_CAPSULE_MODE="legacy"
_EXECUTION_CAPSULE_KIND="initial"
if [ -n "$CONTEXT_LEASE_OPTION" ]; then
    _EXECUTION_CAPSULE_MODE="delta"
    _EXECUTION_CAPSULE_KIND="$CONTINUATION_KIND_OPTION"
elif [ -n "${CLAUDE_CODE_REVIEWED_CONTINUATION:-}" ]; then
    # Native JSON/rendered cards have executable sections that can be reduced
    # to a delta capsule. Minimal legacy table cards remain on their compatible
    # view instead of silently dropping prose outside named sections.
    if grep -qE 'aiwf-execution-card-v1|^[[:space:]]*\{|^##[[:space:]]+(Goal|Handoff Contract|Required Changes|Required Revisions)' \
        "$TASK_CARD" 2>/dev/null; then
        _EXECUTION_CAPSULE_MODE="delta"
        _EXECUTION_CAPSULE_KIND="revision"
    fi
elif [ "$CLAUDE_CODE_BUILDER_MODE" = "execution-only" ] || [ -n "$RECOVERY_CLASSIFICATION_OPTION" ]; then
    _EXECUTION_CAPSULE_MODE="bootstrap"
fi
CLAUDE_PROGRESS_FILE="${WORKTREE_ROOT}/${TASK_ID}.claude-progress.md"
PID_FILE="${WORKTREE_ROOT}/${TASK_ID}.pid"
DISPATCHER_PID_FILE="${WORKTREE_ROOT}/${TASK_ID}.dispatcher.pid"
CLAUDE_PID_FILE="${WORKTREE_ROOT}/${TASK_ID}.claude.pid"
CHECKER_PID_FILE="${WORKTREE_ROOT}/${TASK_ID}.checker.pid"
RUNTIME_JSON="${WORKTREE_ROOT}/${TASK_ID}.runtime.json"
PREFLIGHT_JSON="${WORKTREE_ROOT}/${TASK_ID}.dispatch-preflight.json"
DIRTY_PATHS_FILE="${WORKTREE_ROOT}/${TASK_ID}.dirty-paths.txt"
CONTROL_ARCHIVE_FILE="${WORKTREE_ROOT}/${TASK_ID}.control-archive.json"
CONTROL_ARCHIVE_DIR="${WORKTREE_ROOT}/control-archive/${TASK_ID}"
DISPATCHER_IDENTITY_FILE="${WORKTREE_ROOT}/${TASK_ID}.dispatcher.process.json"
CLAUDE_IDENTITY_FILE="${WORKTREE_ROOT}/${TASK_ID}.claude.process.json"
CHECKER_IDENTITY_FILE="${WORKTREE_ROOT}/${TASK_ID}.checker.process.json"
PROCESS_TERMINATION_FILE="${WORKTREE_ROOT}/${TASK_ID}.process-termination.json"
ABNORMAL_EXIT_FILE="${WORKTREE_ROOT}/${TASK_ID}.dispatcher-abnormal-exit.json"
PHASE_METRICS_FILE="${WORKTREE_ROOT}/${TASK_ID}.phase-metrics.json"
ACTIVITY_OBSERVATION_FILE="${WORKTREE_ROOT}/${TASK_ID}.activity-observation.json"
PROGRESS_FILE="${WORKTREE_ROOT}/${TASK_ID}.progress.log"
MONITOR_EVENT_LOG="${WORKTREE_ROOT}/${TASK_ID}.monitor-events.log"
PHASE_EVENT_LOG="${WORKTREE_ROOT}/${TASK_ID}.phase-events.jsonl"
NETWORK_FILE="${WORKTREE_ROOT}/${TASK_ID}.network.log"
ATTEMPT_CLASSIFICATION_FILE="${WORKTREE_ROOT}/${TASK_ID}.attempt-classification.json"
TAKEOVER_RECEIPT_FILE="${WORKTREE_ROOT}/${TASK_ID}.takeover-receipt.json"
DIRTY_SNAPSHOT_RECEIPT_FILE="${WORKTREE_ROOT}/${TASK_ID}.dirty-snapshot.json"
CHECKER_CONTRACT_RECEIPT_FILE="${WORKTREE_ROOT}/${TASK_ID}.checker-contract.json"
CODEGRAPH_WORKTREE_RECEIPT_FILE="${WORKTREE_ROOT}/${TASK_ID}.codegraph-worktree.json"
INTERACTION_HEALTH_FILE="${WORKTREE_ROOT}/${TASK_ID}.interaction-health.json"
STARTUP_INTERACTION_HEALTH_FILE="${WORKTREE_ROOT}/${TASK_ID}.startup-interaction-health.json"
API_AVAILABILITY_STATE_FILE="${RUNTIME_REPO_ROOT}/.ai-workflow/claude-api-availability.json"
SEEDED_REPORT_MARKER="AI-CODING-WORKFLOW:DISPATCH-SEEDED-REPORT"
SEEDED_PROGRESS_MARKER="AI-CODING-WORKFLOW:DISPATCH-SEEDED-PROGRESS"
FALLBACK_REPORT_MARKER="AI-CODING-WORKFLOW:DISPATCH-FALLBACK-REPORT"

for f in "$RESULT_FILE" "$RAW_RESULT_FILE" "$STATUS_FILE" "$DIFFSTAT_FILE" "$DIFF_FILE" "$CHECKER_REPORT_FILE" \
         "$SOURCE_STATUS_FILE" "$WORKTREE_STATUS_FILE" "$UNTRACKED_FILE" "$USAGE_FILE" "$REPORT_FILE" \
         "$CLAUDE_PROGRESS_FILE" "$PID_FILE" "$DISPATCHER_PID_FILE" "$CLAUDE_PID_FILE" "$CHECKER_PID_FILE" \
         "$PROGRESS_FILE" "$MONITOR_EVENT_LOG" "$PHASE_EVENT_LOG" "$NETWORK_FILE"; do
    mkdir -p "$(dirname "$f")"
done
TASK_CARD_ABS="$(cd "$(dirname "$TASK_CARD")" && pwd -P)/$(basename "$TASK_CARD")"
TASK_CARD_REL=""
TASK_CARD_EXTERNAL=1
case "$TASK_CARD_ABS" in
    "$REPO_ROOT"/*)
        TASK_CARD_REL="${TASK_CARD_ABS#"$REPO_ROOT"/}"
        TASK_CARD_EXTERNAL=0
        ;;
esac

_ARCHIVED_CONTROL_PATHS=""
if [ -n "$PYTHON_CMD" ] && [ -f "${SCRIPT_DIR}/archive-control-files.py" ]; then
    "$PYTHON_CMD" "${SCRIPT_DIR}/archive-control-files.py" \
        --repo "$REPO_ROOT" --archive-dir "$CONTROL_ARCHIVE_DIR" \
        --output "$CONTROL_ARCHIVE_FILE" >/dev/null 2>&1 || true
    if [ -s "$CONTROL_ARCHIVE_FILE" ]; then
        _ARCHIVED_CONTROL_PATHS="$($PYTHON_CMD - "$CONTROL_ARCHIVE_FILE" <<'PYEOF' 2>/dev/null || true
import json, sys
for path in json.load(open(sys.argv[1], encoding="utf-8")).get("archived_paths", []):
    print(path)
PYEOF
)"
    fi
    if [ -z "$_ARCHIVED_CONTROL_PATHS" ]; then
        rm -f "$CONTROL_ARCHIVE_FILE"
        rmdir "$CONTROL_ARCHIVE_DIR" 2>/dev/null || true
    fi
fi
DIRTY_TRACKED="$(git diff --name-only 2>/dev/null || true)"
DIRTY_STAGED="$(git diff --cached --name-only 2>/dev/null || true)"
if [ "$CLAUDE_CODE_LARGE_REPO_MODE" = "1" ]; then
    DIRTY_UNTRACKED=""
    DIRTY_UNTRACKED_SKIPPED=1
else
    DIRTY_UNTRACKED="$(git ls-files --others --exclude-standard 2>/dev/null \
        | grep -v -E "^\.worktrees/" \
        | { if [ -n "$TASK_CARD_REL" ]; then grep -vxF "$TASK_CARD_REL"; else cat; fi; } \
        | grep -vxF ".ai-workflow/model-calls.jsonl" \
        | grep -vxF ".ai-workflow/model-calls.lock" \
        | grep -vxF ".ai-workflow/model-usage.jsonl" \
        | grep -vxF ".ai-workflow/run-ledger.lock" \
        | grep -vxF ".ai-workflow/claude-api-availability.json" \
        | { if [ -n "$_ARCHIVED_CONTROL_PATHS" ]; then grep -vxFf <(printf '%s\n' "$_ARCHIVED_CONTROL_PATHS"); else cat; fi; } \
        || true)"
    DIRTY_UNTRACKED_SKIPPED=0
fi

if [ -n "$DIRTY_TRACKED" ] || [ -n "$DIRTY_STAGED" ] || [ -n "$DIRTY_UNTRACKED" ]; then
    if [ -n "${CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID:-}${CLAUDE_CODE_ADVISOR_CONTINUE_TASK_ID:-}${CLAUDE_CODE_REVIEWED_CONTINUATION:-}" ]; then
        echo "Source worktree remains dirty; validated continuation reuses its recorded execution baseline ${WORKTREE_START_COMMIT} without re-snapshotting source state."
    elif [ "$CLAUDE_CODE_DIRTY_SOURCE_MODE" = "snapshot" ]; then
        if [ "$CLAUDE_CODE_WORKTREE_STRATEGY" != "fresh" ] || \
           [ -n "${CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID:-}${CLAUDE_CODE_ADVISOR_CONTINUE_TASK_ID:-}${CLAUDE_CODE_REVIEWED_CONTINUATION:-}" ] || \
           [ -n "${AI_CODING_WORKFLOW_DAG_TASK_ID:-}" ]; then
            echo "Error: dirty-snapshot mode supports only a single fresh worktree dispatch." >&2
            exit 1
        fi
        _SNAPSHOT_HELPER="${SCRIPT_DIR}/create-dirty-snapshot.py"
        if [ -z "$PYTHON_CMD" ] || [ ! -f "$_SNAPSHOT_HELPER" ]; then
            echo "Error: dirty-snapshot mode requires Python and create-dirty-snapshot.py." >&2
            exit 1
        fi
        _SNAPSHOT_ARGS=(--repo "$REPO_ROOT" --output "$DIRTY_SNAPSHOT_RECEIPT_FILE")
        if [ -n "$TASK_CARD_REL" ]; then
            _SNAPSHOT_ARGS+=(--exclude "$TASK_CARD_REL")
        fi
        while IFS= read -r _archived_control; do
            [ -n "$_archived_control" ] && _SNAPSHOT_ARGS+=(--exclude "$_archived_control")
        done <<< "$_ARCHIVED_CONTROL_PATHS"
        DIRTY_SNAPSHOT_COMMIT="$("$PYTHON_CMD" "$_SNAPSHOT_HELPER" "${_SNAPSHOT_ARGS[@]}")" || {
            echo "Error: failed to create dirty-source snapshot." >&2
            exit 1
        }
        DIRTY_SNAPSHOT_TREE="$("$PYTHON_CMD" - "$DIRTY_SNAPSHOT_RECEIPT_FILE" <<'PYEOF'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8")).get("snapshot_tree", ""))
PYEOF
)"
        case "$DIRTY_SNAPSHOT_COMMIT" in
            ''|*[!0-9a-f]*) echo "Error: dirty snapshot returned an invalid commit id." >&2; exit 1 ;;
        esac
        WORKTREE_START_COMMIT="$DIRTY_SNAPSHOT_COMMIT"
        echo "Dirty source captured as isolated snapshot: ${DIRTY_SNAPSHOT_COMMIT}"
        echo "Snapshot receipt: ${DIRTY_SNAPSHOT_RECEIPT_FILE}"
    elif [ "${CLAUDE_CODE_ALLOW_DIRTY_SOURCE:-0}" = "1" ]; then
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
if [ -z "$PYTHON_CMD" ] || [ ! -f "${SCRIPT_DIR}/process-identity.py" ]; then
    echo "Error: dispatcher process-identity helper is unavailable; refusing PID-only execution." >&2
    exit 1
fi
if ! "$PYTHON_CMD" "${SCRIPT_DIR}/process-identity.py" capture \
    --pid "$$" --task-id "$TASK_ID" --role dispatcher \
    --output "$DISPATCHER_IDENTITY_FILE" >/dev/null 2>&1; then
    echo "Error: dispatcher process identity could not be captured; refusing PID-only execution." >&2
    exit 1
fi
echo "$$" > "$DISPATCHER_PID_FILE"

# Resolve a learned route and test the real interaction boundary before making
# a full worktree.  This probe intentionally requests no card-specific tool
# filter: its init event records the executable's observed inventory, while the
# later profile gate remains responsible for comparing required capabilities.
_EARLY_STARTUP_PROBE_CONCLUSION="not-run"
_EARLY_STARTUP_PROBE_SOURCE="not-run"
if [ "$_ROUTE_SOURCE" = "default" ] && [ -n "$PYTHON_CMD" ] && \
   [ -f "${SCRIPT_DIR}/claude-route-preference.py" ]; then
    _EARLY_LEARNED_ROUTE="$("$PYTHON_CMD" "${SCRIPT_DIR}/claude-route-preference.py" resolve --fallback "" 2>/dev/null || true)"
    if [ "$_EARLY_LEARNED_ROUTE" = direct ] || [ "$_EARLY_LEARNED_ROUTE" = inherit ]; then
        CLAUDE_CODE_PROXY_MODE="$_EARLY_LEARNED_ROUTE"
        _ROUTE_SOURCE="learned"
    fi
fi
if [ "$CLAUDE_CODE_STARTUP_PREFLIGHT_REQUIRED" = "1" ] && [ -n "$PYTHON_CMD" ]; then
    _EARLY_CACHE_HIT=0
    if [ "$CLAUDE_CODE_API_PROBE_MODE" != "always" ] && \
       [ -f "${SCRIPT_DIR}/claude-api-availability.py" ]; then
        if "$PYTHON_CMD" "${SCRIPT_DIR}/claude-api-availability.py" check \
            --state "$API_AVAILABILITY_STATE_FILE" --repository "$REPO_ROOT" \
            --route "$CLAUDE_CODE_PROXY_MODE" --environment "$CLAUDE_CODE_PROBE_ENVIRONMENT" \
            --claude-command "$(command -v claude 2>/dev/null || true)" \
            --tool-profile "${CLAUDE_CODE_TOOL_PROFILE:-default}" \
            --ttl "$CLAUDE_CODE_API_AVAILABILITY_TTL_SECONDS" \
            > "$STARTUP_INTERACTION_HEALTH_FILE" 2>/dev/null; then
            _EARLY_CACHE_HIT=1
            _EARLY_STARTUP_PROBE_CONCLUSION="available"
            _EARLY_STARTUP_PROBE_SOURCE="early-cache"
        fi
    fi
    if [ "$_EARLY_CACHE_HIT" -eq 0 ] && [ -f "${SCRIPT_DIR}/claude-healthcheck.py" ]; then
        _EARLY_PROBE_ENV_ARGS=()
        if [ "$CLAUDE_CODE_PROBE_ENVIRONMENT" != auto ]; then
            _EARLY_PROBE_ENV_ARGS=(--probe-environment "$CLAUDE_CODE_PROBE_ENVIRONMENT")
        fi
        "$PYTHON_CMD" "${SCRIPT_DIR}/claude-healthcheck.py" \
            --interaction-route "$CLAUDE_CODE_PROXY_MODE" \
            --timeout "$CLAUDE_CODE_ZERO_OUTPUT_PROBE_TIMEOUT_SECONDS" \
            --prompt '你好' --json "${_EARLY_PROBE_ENV_ARGS[@]}" \
            > "$STARTUP_INTERACTION_HEALTH_FILE" 2>/dev/null || true
        _EARLY_STARTUP_PROBE_CONCLUSION="$("$PYTHON_CMD" - "$STARTUP_INTERACTION_HEALTH_FILE" <<'PYEOF' 2>/dev/null || echo unavailable-in-current-environment
import json, sys
try:
    print(json.load(open(sys.argv[1], encoding="utf-8")).get("interaction_conclusion", "unavailable-in-current-environment"))
except (OSError, ValueError, TypeError):
    print("unavailable-in-current-environment")
PYEOF
)"
        _EARLY_STARTUP_PROBE_SOURCE="early-live"
        if [ "$_EARLY_STARTUP_PROBE_CONCLUSION" = available ] && \
           [ -f "${SCRIPT_DIR}/claude-api-availability.py" ]; then
            _EARLY_INVENTORY_ARGS=()
            while IFS= read -r _EARLY_TOOL; do
                [ -n "$_EARLY_TOOL" ] && _EARLY_INVENTORY_ARGS+=(--tool-inventory "$_EARLY_TOOL")
            done < <("$PYTHON_CMD" - "$STARTUP_INTERACTION_HEALTH_FILE" <<'PYEOF' 2>/dev/null || true
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
successful = [p for p in value.get("interaction_probes", []) if p.get("success")]
for tool in (successful[-1].get("tool_inventory", []) if successful else []):
    print(tool)
PYEOF
)
            "$PYTHON_CMD" "${SCRIPT_DIR}/claude-api-availability.py" record \
                --state "$API_AVAILABILITY_STATE_FILE" --repository "$REPO_ROOT" \
                --route "$CLAUDE_CODE_PROXY_MODE" --environment "$CLAUDE_CODE_PROBE_ENVIRONMENT" \
                --claude-command "$(command -v claude 2>/dev/null || true)" \
                --tool-profile "${CLAUDE_CODE_TOOL_PROFILE:-default}" \
                --source early-startup-probe --tool-inventory-verified \
            "${_EARLY_INVENTORY_ARGS[@]}" >/dev/null 2>&1 || true
        fi
    fi
    _EARLY_TOOL_INVENTORY_MISSING=""
    _EARLY_REQUESTED_TOOLS=""
    case "${CLAUDE_CODE_TOOL_PROFILE:-default}" in
        editor-only)     _EARLY_REQUESTED_TOOLS="Read,Edit,Write,Grep,Glob" ;;
        minimal-builder) _EARLY_REQUESTED_TOOLS="Read,Edit,Write,Bash" ;;
        locator-builder) _EARLY_REQUESTED_TOOLS="Read,Edit,Write,Grep,Glob,Bash" ;;
        checker)         _EARLY_REQUESTED_TOOLS="Read,Edit,Write,Grep,Glob,Bash" ;;
        diagnostic)      _EARLY_REQUESTED_TOOLS="Read,Grep,Glob,Bash" ;;
    esac
    if [ "$_EARLY_STARTUP_PROBE_CONCLUSION" = available ] && \
       [ -n "$_EARLY_REQUESTED_TOOLS" ]; then
        _EARLY_WRITE_SCOPE_EXPECTED="off"
        if { [ "${_PARSED_TASK_MODE:-}" = builder ] || \
             [ "${_PARSED_TASK_MODE:-}" = checker-test ]; } && \
           [ "${CLAUDE_CODE_TOOL_PROFILE:-}" != diagnostic ] && \
           { [ "${CLAUDE_CODE_WRITE_SCOPE_ENFORCEMENT:-auto}" = auto ] || \
             [ "${CLAUDE_CODE_WRITE_SCOPE_ENFORCEMENT:-auto}" = required ]; }; then
            _EARLY_WRITE_SCOPE_EXPECTED="required"
        fi
        [ -z "${_REVIEWED_CONTINUATION_TASK_ID:-}" ] || _EARLY_WRITE_SCOPE_EXPECTED="required"
        _EARLY_TOOL_INVENTORY_MISSING="$({
            "$PYTHON_CMD" - "$STARTUP_INTERACTION_HEALTH_FILE" \
                "$_EARLY_REQUESTED_TOOLS" "$_EARLY_WRITE_SCOPE_EXPECTED" <<'PYEOF'
import json, sys
health, requested, write_scope = sys.argv[1:]
value = json.load(open(health, encoding="utf-8"))
if "tool_inventory" in value:
    inventory = value.get("tool_inventory", [])
    verified = value.get("tool_inventory_verified") is True
else:
    successful = [item for item in value.get("interaction_probes", []) if item.get("success")]
    latest = successful[-1] if successful else {}
    inventory = latest.get("tool_inventory", [])
    verified = latest.get("tool_inventory_verified") is True
if not verified:
    print("inventory-unverified")
    raise SystemExit
available = set(item for item in inventory if isinstance(item, str))
missing = set(filter(None, requested.split(","))) - available
if write_scope == "required" and "Bash" in available:
    missing.difference_update({"Edit", "Write"})
print(",".join(sorted(missing)))
PYEOF
        } 2>/dev/null)"
        if [ -n "$_EARLY_TOOL_INVENTORY_MISSING" ]; then
            _EARLY_STARTUP_PROBE_CONCLUSION="tool-capability-mismatch"
            _EARLY_STARTUP_PROBE_SOURCE="early-capability-check"
        fi
    fi
    if [ "$_EARLY_STARTUP_PROBE_CONCLUSION" != available ]; then
        _EARLY_NEEDS_HOST=0
        _EARLY_EXIT_STATUS=75
        if [ "$_EARLY_STARTUP_PROBE_CONCLUSION" = inconclusive-restricted-environment ] && \
           [ "$CLAUDE_CODE_HOST_AUTHORITY" != 1 ]; then
            _EARLY_NEEDS_HOST=1
        fi
        _EARLY_CATEGORY="$("$PYTHON_CMD" - "$STARTUP_INTERACTION_HEALTH_FILE" <<'PYEOF' 2>/dev/null || echo interaction-unavailable
import json, sys
try:
    value = json.load(open(sys.argv[1], encoding="utf-8"))
    probes = value.get("interaction_probes", [])
    print(probes[-1].get("failure_category", "interaction-unavailable") if probes else "interaction-unavailable")
except (OSError, ValueError, TypeError, IndexError):
    print("interaction-unavailable")
PYEOF
)"
        [ "$_EARLY_NEEDS_HOST" -eq 0 ] || _EARLY_CATEGORY="sandbox-network-host-handoff"
        if [ "$_EARLY_STARTUP_PROBE_CONCLUSION" = tool-capability-mismatch ]; then
            _EARLY_CATEGORY="tool-capability-mismatch"
            _EARLY_EXIT_STATUS=1
        fi
        {
            echo "Claude dispatch preflight blocked before worktree creation."
            echo "failure_category=${_EARLY_CATEGORY}"
            echo "interaction_conclusion=${_EARLY_STARTUP_PROBE_CONCLUSION}"
            if [ -n "$_EARLY_TOOL_INVENTORY_MISSING" ]; then
                echo "missing_runtime_tools=${_EARLY_TOOL_INVENTORY_MISSING}"
            fi
            echo "builder_started=false"
        } > "$STATUS_FILE"
        if [ -f "${SCRIPT_DIR}/classify-claude-attempt.py" ]; then
            _EARLY_ATTEMPT_OUTCOME="preflight_error"
            [ "$_EARLY_NEEDS_HOST" -eq 0 ] || _EARLY_ATTEMPT_OUTCOME="network_error"
            "$PYTHON_CMD" "${SCRIPT_DIR}/classify-claude-attempt.py" \
                --exit-code "$_EARLY_EXIT_STATUS" --outcome "$_EARLY_ATTEMPT_OUTCOME" --progress none \
                --direction unknown --error-text-file "$STATUS_FILE" \
                --retry-ordinal "${_RETRY_ORDINAL:-0}" \
                > "$ATTEMPT_CLASSIFICATION_FILE" 2>/dev/null || true
        fi
        "$PYTHON_CMD" - "$RESULT_FILE" "$OUTCOME_FILE" "$TASK_ID" "$_EARLY_CATEGORY" \
            "$_EARLY_NEEDS_HOST" "$DISPATCH_EXECUTION_ENV" "$CLAUDE_CODE_HOST_AUTHORITY" \
            "$STARTUP_INTERACTION_HEALTH_FILE" "$ATTEMPT_CLASSIFICATION_FILE" \
            "$TASK_CARD" "$CLAUDE_CODE_DIRTY_SOURCE_MODE" \
            "${_REVIEWED_CONTINUATION_APPROVAL:-}" \
            "${_EARLY_TOOL_INVENTORY_MISSING:-}" "${TOOL_PROFILE_OPTION:-}" \
            "$CONTEXT_LEASE_OPTION" "$CONTINUATION_KIND_OPTION" \
            "$FORCE_FRESH_SESSION_OPTION" "$REHYDRATE_FROM_OPTION" <<'PYEOF'
import json, os, sys
result, outcome, task_id, category, needs_host, requested_env, authority, health, classification, task_card, dirty_mode, reviewed, missing_tools, tool_profile, context_lease, continuation_kind, force_fresh, rehydrate_from = sys.argv[1:]
needs_host_execution = needs_host == "1"
host_retry_args = None
host_retry_environment = None
if needs_host_execution:
    host_retry_environment = {"CLAUDE_CODE_HOST_AUTHORITY": "1"}
    host_retry_args = [task_card, "--execution-env", "host"]
    if dirty_mode == "snapshot":
        host_retry_environment["CLAUDE_CODE_DIRTY_SOURCE_MODE"] = "snapshot"
        host_retry_args += ["--dirty-source-mode", "snapshot"]
    if tool_profile:
        host_retry_environment["CLAUDE_CODE_TOOL_PROFILE"] = tool_profile
        host_retry_args += ["--tool-profile", tool_profile]
    if context_lease:
        host_retry_args += [
            "--context-lease", context_lease,
            "--continuation-kind", continuation_kind,
        ]
        if force_fresh == "1":
            host_retry_args += ["--force-fresh-session"]
        if rehydrate_from:
            host_retry_args += ["--rehydrate-from", rehydrate_from]
    elif reviewed:
        host_retry_environment["CLAUDE_CODE_REVIEWED_CONTINUATION"] = reviewed
        host_retry_args += ["--reviewed-continuation", reviewed]
    else:
        host_retry_args += ["--preflight-task-id", task_id]
common = {
    "schema_version": 1, "task_id": task_id,
    "dispatch_outcome": "preflight-blocked", "failure_category": category,
    "builder_started": False, "claude_first_satisfied": False,
    "workflow_execution_status": "failed-to-dispatch",
    "completion_state": "failed-to-dispatch", "needs_host_execution": needs_host_execution,
    "host_handoff_required": needs_host_execution,
    "host_handoff_action": (
        "rerun-identical-dispatch-on-authorized-host-once"
        if needs_host_execution else None
    ),
    "host_retry_environment": host_retry_environment,
    "host_retry_environment_legacy": needs_host_execution,
    "host_retry_args": host_retry_args,
    "host_retry_args_authoritative": needs_host_execution,
    "host_retry_command_form": "stable-cli" if needs_host_execution else None,
    "host_requested": requested_env == "host",
    "host_authorized": authority == "1", "host_effective": False,
    "interaction_health": health, "worktree_created": False, "merge_authorized": False,
    "missing_runtime_tools": sorted(filter(None, missing_tools.split(","))),
    "attempt_classification": classification if os.path.isfile(classification) else None,
}
with open(result, "w", encoding="utf-8") as handle:
    json.dump(common, handle, indent=2, sort_keys=True); handle.write("\n")
with open(outcome, "w", encoding="utf-8") as handle:
    json.dump({**common, "dispatch_success": False, "artifact_valid": False,
               "report_consistency": "not-applicable", "validation_success": "not-run",
               "semantic_acceptance": "not-reviewed"}, handle, indent=2, sort_keys=True)
    handle.write("\n")
PYEOF
        echo "Error: Claude dispatch preflight failed (${_EARLY_CATEGORY}) before worktree creation." >&2
        if [ "$_EARLY_NEEDS_HOST" -ne 0 ]; then
            echo "needs_host_execution=true" >&2
            printf 'host_retry_command=bash %q %q --execution-env host' "$0" "$TASK_CARD" >&2
            [ "$CLAUDE_CODE_DIRTY_SOURCE_MODE" != snapshot ] || printf ' --dirty-source-mode snapshot' >&2
            [ -z "${TOOL_PROFILE_OPTION:-}" ] || printf ' --tool-profile %q' "$TOOL_PROFILE_OPTION" >&2
            if [ -n "$CONTEXT_LEASE_OPTION" ]; then
                printf ' --context-lease %q --continuation-kind %q' \
                    "$CONTEXT_LEASE_OPTION" "$CONTINUATION_KIND_OPTION" >&2
                [ "$FORCE_FRESH_SESSION_OPTION" -eq 0 ] || printf ' --force-fresh-session' >&2
                [ -z "$REHYDRATE_FROM_OPTION" ] || printf ' --rehydrate-from %q' "$REHYDRATE_FROM_OPTION" >&2
                printf '\n' >&2
            elif [ -n "${_REVIEWED_CONTINUATION_APPROVAL:-}" ]; then
                printf ' --reviewed-continuation %q\n' "$_REVIEWED_CONTINUATION_APPROVAL" >&2
            else
                printf ' --preflight-task-id %q\n' "$TASK_ID" >&2
            fi
        fi
        rm -f "$DISPATCHER_PID_FILE"
        exit "$_EARLY_EXIT_STATUS"
    fi
fi

create_dispatch_worktree() {
    local branch_name="$1"
    if [ "$CLAUDE_CODE_WORKTREE_STRATEGY" = "fresh" ]; then
        if [ "$WORKTREE_DIR" != "${WORKTREE_ROOT}/${TASK_ID}" ]; then
            echo "Error: refusing recursive or non-flat fresh worktree target: $WORKTREE_DIR" >&2
            echo "Expected direct child of the Git common runtime root: ${WORKTREE_ROOT}/${TASK_ID}" >&2
            exit 1
        fi
        if [ "$CLAUDE_CODE_WORKTREE_PROGRESS" = "quiet" ]; then
            git worktree add -b "$branch_name" "$WORKTREE_DIR" "$WORKTREE_START_COMMIT" >/dev/null || {
                echo "Error: Failed to create git worktree at $WORKTREE_DIR" >&2
                exit 1
            }
        else
            git worktree add -b "$branch_name" "$WORKTREE_DIR" "$WORKTREE_START_COMMIT" || {
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
_WORKTREE_SETUP_DURATION=""
if [ -z "${_RETRY_WORKTREE_DIR:-}" ] && \
   [ -z "${_ADVISOR_CONTINUE_WORKTREE_DIR:-}" ] && \
   [ -z "${_REVIEWED_CONTINUATION_WORKTREE_DIR:-}" ]; then
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

# No continuation selector may bypass a Codex single-writer marker by naming an
# older task id that points at the same physical worktree.
_CODEX_OWNER_MARKER="$("$PYTHON_CMD" - "$WORKTREE_ROOT" "$WORKTREE_DIR" <<'PYEOF' 2>/dev/null || echo "__marker-check-failed__"
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
worktree = pathlib.Path(sys.argv[2]).resolve()
for marker in root.glob("*.codex-write-owner.json"):
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
        candidate = pathlib.Path(str(value.get("worktree", ""))).resolve()
    except (OSError, ValueError, TypeError):
        print("__invalid_marker__")
        raise SystemExit
    if candidate == worktree:
        print(str(marker.resolve()))
        raise SystemExit
PYEOF
)"
case "$_CODEX_OWNER_MARKER" in
    "")
        ;;
    "__marker-check-failed__"|"__invalid_marker__")
        echo "Error: cannot authoritatively validate Codex worktree ownership markers." >&2
        exit 1
        ;;
    *)
        echo "Error: worktree ownership was transferred to Codex; Claude dispatch is forbidden: ${_CODEX_OWNER_MARKER}" >&2
        exit 1
        ;;
esac

# Give Claude a task-scoped scratch directory outside the repository. This
# makes the default temporary-file policy actionable instead of prompt-only.
_SYSTEM_TMP_ROOT="${AI_WORKFLOW_SYSTEM_TMP_ROOT:-/tmp}"
if [ ! -d "$_SYSTEM_TMP_ROOT" ]; then
    echo "Error: task scratch root is unavailable: ${_SYSTEM_TMP_ROOT}" >&2
    exit 1
fi
TASK_TMPDIR="$(mktemp -d "${_SYSTEM_TMP_ROOT%/}/aiwf-${TASK_ID}.XXXXXX")" || {
    echo "Error: failed to create task scratch directory under ${_SYSTEM_TMP_ROOT}." >&2
    exit 1
}
export TMPDIR="$TASK_TMPDIR"
export TEMP="$TASK_TMPDIR"
export TMP="$TASK_TMPDIR"

# Model-session continuity is distinct from worktree continuity. Every initial
# run gets an explicit UUID; same-owner continuations resume it when a valid
# prior runtime receipt exists. Missing identity falls back to a new named
# session while recording that only file-backed context was preserved.
CLAUDE_SESSION_MODE_EFFECTIVE="new"
CLAUDE_SESSION_RESUME_STATUS="not-requested"
CLAUDE_SESSION_PRIOR_TASK_ID=""
_CLAUDE_RESUME_FALLBACK_USED=0
CLAUDE_SESSION_ID="${CLAUDE_CODE_RESUME_SESSION_ID:-}"
if [ "$_CONTEXT_FORCE_FRESH_SESSION" -eq 1 ]; then
    CLAUDE_SESSION_ID=""
    CLAUDE_SESSION_MODE_EFFECTIVE="new"
    CLAUDE_SESSION_RESUME_STATUS="context-lease-${_CONTEXT_LEASE_ROUTE}"
elif [ -z "$PYTHON_CMD" ]; then
    CLAUDE_SESSION_ID=""
    CLAUDE_SESSION_MODE_EFFECTIVE="implicit"
    CLAUDE_SESSION_RESUME_STATUS="python-unavailable-file-backed-only"
elif [ -n "$CLAUDE_SESSION_ID" ]; then
    CLAUDE_SESSION_MODE_EFFECTIVE="resume"
    CLAUDE_SESSION_RESUME_STATUS="explicit"
else
    if [ -n "${_REVIEWED_CONTINUATION_TASK_ID:-}" ]; then
        CLAUDE_SESSION_PRIOR_TASK_ID="$_REVIEWED_CONTINUATION_TASK_ID"
    elif [ -n "${_RETRY_TASK_ID:-}" ]; then
        CLAUDE_SESSION_PRIOR_TASK_ID="$_RETRY_TASK_ID"
    elif [ -n "${_ADVISOR_CONTINUE_TASK_ID:-}" ]; then
        CLAUDE_SESSION_PRIOR_TASK_ID="$_ADVISOR_CONTINUE_TASK_ID"
    fi
    if [ -n "$CLAUDE_SESSION_PRIOR_TASK_ID" ] && [ -s "${WORKTREE_ROOT}/${CLAUDE_SESSION_PRIOR_TASK_ID}.runtime.json" ]; then
        CLAUDE_SESSION_ID="$("$PYTHON_CMD" - "${WORKTREE_ROOT}/${CLAUDE_SESSION_PRIOR_TASK_ID}.runtime.json" <<'PYEOF' 2>/dev/null || true
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8")).get("claude_session_id", ""))
PYEOF
)"
        if [ -n "$CLAUDE_SESSION_ID" ]; then
            CLAUDE_SESSION_MODE_EFFECTIVE="resume"
            CLAUDE_SESSION_RESUME_STATUS="prior-runtime"
        fi
    fi
fi
if [ "$_CONTEXT_FORCE_FRESH_SESSION" -eq 1 ]; then
    CLAUDE_SESSION_ID="$("$PYTHON_CMD" - "$TASK_ID" "$_CONTEXT_LEASE_ROUTE" <<'PYEOF'
import sys, uuid
print(uuid.uuid5(uuid.NAMESPACE_URL, "ai-coding-workflow:{}:{}".format(sys.argv[1], sys.argv[2])))
PYEOF
)"
elif [ -z "$PYTHON_CMD" ]; then
    :
elif [ -n "$CLAUDE_SESSION_ID" ]; then
    if ! "$PYTHON_CMD" - "$CLAUDE_SESSION_ID" <<'PYEOF' >/dev/null 2>&1
import sys, uuid
uuid.UUID(sys.argv[1])
PYEOF
    then
        echo "Error: Claude session id is not a valid UUID." >&2
        exit 1
    fi
else
    CLAUDE_SESSION_ID="$("$PYTHON_CMD" - "$TASK_ID" <<'PYEOF'
import sys, uuid
print(uuid.uuid5(uuid.NAMESPACE_URL, "ai-coding-workflow:" + sys.argv[1]))
PYEOF
)"
    if [ -n "$CLAUDE_SESSION_PRIOR_TASK_ID" ]; then
        CLAUDE_SESSION_RESUME_STATUS="unavailable-file-backed-fallback"
    fi
fi

# Dirty-source authority never implies that a stale fresh worktree is usable.
# Bind every task-mentioned dirty path to the isolated execution copy before
# invoking Claude. Unrelated dirty paths remain recorded but do not block.
if [ "$CLAUDE_CODE_DIRTY_SOURCE_MODE" != "snapshot" ] && \
   [ "${CLAUDE_CODE_ALLOW_DIRTY_SOURCE:-0}" = "1" ] && \
   { [ -n "$DIRTY_TRACKED" ] || [ -n "$DIRTY_STAGED" ] || [ -n "$DIRTY_UNTRACKED" ]; }; then
    {
        printf '%s\n' "$DIRTY_TRACKED"
        printf '%s\n' "$DIRTY_STAGED"
        printf '%s\n' "$DIRTY_UNTRACKED"
    } | sed '/^[[:space:]]*$/d' | sort -u > "$DIRTY_PATHS_FILE"
    _PREFLIGHT_HELPER="${SCRIPT_DIR}/dispatch-preflight.py"
    if [ -z "$PYTHON_CMD" ] || [ ! -f "$_PREFLIGHT_HELPER" ]; then
        echo "Error: dirty-source execution requires dispatch-preflight.py." >&2
        exit 1
    fi
    _TASK_CARD_SOURCE="$(cd "$(dirname "$TASK_CARD")" && pwd)/$(basename "$TASK_CARD")"
    set +e
    "$PYTHON_CMD" "$_PREFLIGHT_HELPER" \
        --source "$REPO_ROOT" --worktree "$WORKTREE_DIR" \
        --task-card "$_TASK_CARD_SOURCE" --dirty-paths "$DIRTY_PATHS_FILE" \
        --output "$PREFLIGHT_JSON" >/dev/null
    _PREFLIGHT_STATUS=$?
    set -e
    if [ "$_PREFLIGHT_STATUS" -ne 0 ]; then
        echo "Error: dispatch preflight blocked stale or missing task-relevant dirty source." >&2
        echo "Evidence: ${PREFLIGHT_JSON}" >&2
        echo "Create an accepted clean baseline or explicitly use CLAUDE_CODE_DIRTY_SOURCE_MODE=snapshot; do not dispatch from stale HEAD." >&2
        exit 1
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

# CodeGraph indexes are worktree-bound. Never let an index discovered through
# the source repository or a stale MCP session become execution evidence for a
# different isolated worktree. Default to deterministic local fallback; an
# explicit repair policy may sync/reindex the execution worktree.
CODEGRAPH_EXECUTION_STATUS="unavailable"
CODEGRAPH_EXECUTION_ACTION="fallback-local"
CODEGRAPH_EXECUTION_REASON="guard-unavailable"
CODEGRAPH_SAFE_TO_USE="no"
_CODEGRAPH_GUARD="${SCRIPT_DIR}/codegraph-worktree-guard.py"
if [ -n "$PYTHON_CMD" ] && [ -f "$_CODEGRAPH_GUARD" ]; then
    "$PYTHON_CMD" "$_CODEGRAPH_GUARD" \
        --source "$REPO_ROOT" --worktree "$WORKTREE_DIR" \
        --output "$CODEGRAPH_WORKTREE_RECEIPT_FILE" \
        --policy "$CLAUDE_CODE_CODEGRAPH_POLICY" \
        --timeout "$CLAUDE_CODE_CODEGRAPH_TIMEOUT_SECONDS" >/dev/null || true
    if [ -s "$CODEGRAPH_WORKTREE_RECEIPT_FILE" ]; then
        readarray -t _CODEGRAPH_FIELDS < <("$PYTHON_CMD" - "$CODEGRAPH_WORKTREE_RECEIPT_FILE" <<'PYEOF'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
print(value.get("status", "unavailable"))
print(value.get("action", "fallback-local"))
print(value.get("reason", "unknown"))
print("yes" if value.get("safe_to_use") is True else "no")
PYEOF
)
        CODEGRAPH_EXECUTION_STATUS="${_CODEGRAPH_FIELDS[0]:-unavailable}"
        CODEGRAPH_EXECUTION_ACTION="${_CODEGRAPH_FIELDS[1]:-fallback-local}"
        CODEGRAPH_EXECUTION_REASON="${_CODEGRAPH_FIELDS[2]:-unknown}"
        CODEGRAPH_SAFE_TO_USE="${_CODEGRAPH_FIELDS[3]:-no}"
    fi
fi
echo "CodeGraph execution guard: status=${CODEGRAPH_EXECUTION_STATUS}, action=${CODEGRAPH_EXECUTION_ACTION}, reason=${CODEGRAPH_EXECUTION_REASON}, receipt=${CODEGRAPH_WORKTREE_RECEIPT_FILE}"

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
    echo "- Context compilation strategy: ${CLAUDE_CODE_CONTEXT_COMPILE_STRATEGY}"
    if [ -n "${_RETRY_TASK_ID:-}" ]; then
        echo "- Retry provenance: prior task ${_RETRY_TASK_ID} from ${CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID}"
    fi
    echo "- Reuse reset allowed: ${CLAUDE_CODE_REUSE_WORKTREE_RESET}"
    echo "- Large repo mode: ${CLAUDE_CODE_LARGE_REPO_MODE}"
    echo "- Claude task card view: ${CLAUDE_CODE_TASK_CARD_VIEW}"
    echo "- Claude prompt profile: ${CLAUDE_CODE_PROMPT_PROFILE}"
    echo "- Auto bootstrap capsule: ${CLAUDE_CODE_AUTO_BOOTSTRAP_CAPSULE}"
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
    echo "- CodeGraph policy: ${CLAUDE_CODE_CODEGRAPH_POLICY}"
    echo "- CodeGraph execution status: ${CODEGRAPH_EXECUTION_STATUS}"
    echo "- CodeGraph execution action: ${CODEGRAPH_EXECUTION_ACTION}"
    echo "- CodeGraph execution reason: ${CODEGRAPH_EXECUTION_REASON}"
    echo "- CodeGraph receipt: ${CODEGRAPH_WORKTREE_RECEIPT_FILE}"
    echo "- First-progress timeout: ${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS}s (source: ${_FIRST_PROGRESS_TIMEOUT_SOURCE:-default})"
    echo "- First-progress action: ${CLAUDE_CODE_FIRST_PROGRESS_ACTION}"
    echo "- Context-acquisition timeout: ${CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS}s"
    echo "- Active-execution window: ${CLAUDE_CODE_TIMEOUT_SECONDS}s"
    echo "- Hard timeout: ${CLAUDE_CODE_HARD_TIMEOUT_SECONDS}s"
    echo "- Progress extension seconds: ${CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS}s"
    echo "- Renewable product-growth extension seconds: ${CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS}s"
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
_LINEAGE_ROOT_TASK_ID="${_RETRY_ROOT_TASK_ID:-$TASK_ID}"
_REUSE_COUNT=0
if [ -n "${_REVIEWED_CONTINUATION_TASK_ID:-}" ]; then
    _RUNTIME_STRATEGY="reviewed-continuation"
    _LINEAGE_ROOT_TASK_ID="$(sed -n 's/.*"lineage_root_task_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "${WORKTREE_ROOT}/${_REVIEWED_CONTINUATION_TASK_ID}.runtime.json" 2>/dev/null | head -1)"
    [ -n "$_LINEAGE_ROOT_TASK_ID" ] || _LINEAGE_ROOT_TASK_ID="$_REVIEWED_CONTINUATION_TASK_ID"
    _PRIOR_REUSE_COUNT="$(sed -n 's/.*"reuse_count"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "${WORKTREE_ROOT}/${_REVIEWED_CONTINUATION_TASK_ID}.runtime.json" 2>/dev/null | head -1)"
    case "$_PRIOR_REUSE_COUNT" in
        ''|*[!0-9]*) _PRIOR_REUSE_COUNT=0 ;;
    esac
    _REUSE_COUNT=$((_PRIOR_REUSE_COUNT + 1))
elif [ -n "${_RETRY_TASK_ID:-}" ]; then
    _RUNTIME_STRATEGY="retry-in-place"
elif [ -n "${_ADVISOR_CONTINUE_TASK_ID:-}" ]; then
    _LINEAGE_ROOT_TASK_ID="$(sed -n 's/.*"lineage_root_task_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "${WORKTREE_ROOT}/${_ADVISOR_CONTINUE_TASK_ID}.runtime.json" 2>/dev/null | head -1)"
    [ -n "$_LINEAGE_ROOT_TASK_ID" ] || _LINEAGE_ROOT_TASK_ID="$_ADVISOR_CONTINUE_TASK_ID"
fi
_RUNTIME_TMP="${RUNTIME_JSON}.tmp.$$"
{
    echo "{"
    echo "  \"schema_version\": 1,"
    printf '  "task_id": "%s",\n' "$TASK_ID"
    printf '  "runtime_id": "%s",\n' "$TASK_ID"
    printf '  "worktree": "%s",\n' "$WORKTREE_DIR"
    printf '  "strategy": "%s",\n' "$_RUNTIME_STRATEGY"
    printf '  "branch": "%s",\n' "$BRANCH_NAME"
    printf '  "base_commit": "%s",\n' "$BASE_COMMIT"
    printf '  "worktree_start_commit": "%s",\n' "$WORKTREE_START_COMMIT"
    printf '  "source_base_commit": "%s",\n' "$BASE_COMMIT"
    printf '  "execution_base_commit": "%s",\n' "$WORKTREE_START_COMMIT"
    printf '  "dirty_source_mode": "%s",\n' "$CLAUDE_CODE_DIRTY_SOURCE_MODE"
    if [ -n "$DIRTY_SNAPSHOT_COMMIT" ]; then
        printf '  "dirty_snapshot_commit": "%s",\n' "$DIRTY_SNAPSHOT_COMMIT"
        printf '  "dirty_snapshot_tree": "%s",\n' "$DIRTY_SNAPSHOT_TREE"
        printf '  "dirty_snapshot_receipt": "%s",\n' "${_INHERITED_DIRTY_SNAPSHOT_RECEIPT:-$DIRTY_SNAPSHOT_RECEIPT_FILE}"
    fi
    printf '  "source_repository": "%s",\n' "$REPO_ROOT"
    printf '  "runtime_repository_root": "%s",\n' "$RUNTIME_REPO_ROOT"
    printf '  "worktree_root": "%s",\n' "$WORKTREE_ROOT"
    printf '  "worktree_layout": "flat-common-root",\n'
    printf '  "task_card_external_to_source": %s,\n' "$([ "$TASK_CARD_EXTERNAL" -eq 1 ] && echo true || echo false)"
    printf '  "lineage_root_task_id": "%s",\n' "$_LINEAGE_ROOT_TASK_ID"
    printf '  "retry_ordinal": %s,\n' "${_RETRY_ORDINAL:-0}"
    if [ -n "${_WORKTREE_SETUP_DURATION:-}" ]; then
        printf '  "worktree_setup_seconds": %s,\n' "$_WORKTREE_SETUP_DURATION"
    else
        echo '  "worktree_setup_seconds": null,'
    fi
    printf '  "task_tmpdir": "%s",\n' "$TASK_TMPDIR"
    printf '  "claude_session_id": "%s",\n' "$CLAUDE_SESSION_ID"
    printf '  "claude_session_mode": "%s",\n' "$CLAUDE_SESSION_MODE_EFFECTIVE"
    printf '  "claude_session_resume_status": "%s",\n' "$CLAUDE_SESSION_RESUME_STATUS"
    printf '  "claude_session_prior_task_id": "%s",\n' "$CLAUDE_SESSION_PRIOR_TASK_ID"
    printf '  "claude_session_generation": 0,\n'
    printf '  "model_hint": "%s",\n' "$_CONTEXT_MODEL_HINT"
    printf '  "provider_route_sha256": "%s",\n' "$_CONTEXT_PROVIDER_ROUTE_SHA256"
    if [ -n "${CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID:-}" ]; then
        printf '  "retry_of": "%s",\n' "$CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID"
    fi
    if [ -n "${_REVIEWED_CONTINUATION_TASK_ID:-}" ]; then
        printf '  "reviewed_continuation_of": "%s",\n' "$_REVIEWED_CONTINUATION_TASK_ID"
        printf '  "reviewed_continuation_approval_id": "%s",\n' "$_REVIEWED_CONTINUATION_APPROVAL_ID"
        printf '  "reviewed_continuation_baseline_hash": "%s",\n' "$_REVIEWED_CONTINUATION_BASELINE_HASH"
        printf '  "reviewed_continuation_builder_mode": "%s",\n' "${_REVIEWED_INHERITED_BUILDER_MODE:-$CLAUDE_CODE_BUILDER_MODE}"
        printf '  "reviewed_continuation_tool_profile": "%s",\n' "${_REVIEWED_INHERITED_TOOL_PROFILE:-$CLAUDE_CODE_TOOL_PROFILE}"
        printf '  "reviewed_continuation_prior_context_lease_id": "%s",\n' "$_REVIEWED_PRIOR_CONTEXT_LEASE_ID"
        printf '  "provenance_root_strategy": "fresh",\n'
        printf '  "reuse_count": %s,\n' "$_REUSE_COUNT"
    fi
    if [ -n "$CONTEXT_LEASE_OPTION" ]; then
        printf '  "context_lease_id": "%s",\n' "$_CONTEXT_LEASE_ID"
        printf '  "context_lease_continuation_kind": "%s",\n' "$CONTINUATION_KIND_OPTION"
        printf '  "context_lease_route": "%s",\n' "$_CONTEXT_LEASE_ROUTE"
        printf '  "context_lease_calls_used": %s,\n' "$_CONTEXT_LEASE_CALLS_USED"
        printf '  "context_lease_max_warm_calls": %s,\n' "$_CONTEXT_LEASE_MAX_WARM_CALLS"
        printf '  "context_checkpoint_mode": "%s",\n' "$_CONTEXT_CHECKPOINT_MODE"
        if [ -n "$REHYDRATE_FROM_OPTION" ]; then
            printf '  "context_checkpoint": "%s",\n' "$REHYDRATE_FROM_OPTION"
        fi
        if [ "$_CONTEXT_CHECKPOINT_RECEIPT_BOUND" -eq 1 ]; then
            printf '  "context_checkpoint_receipt": "%s",\n' "$CONTEXT_CHECKPOINT_RECEIPT_FILE"
        fi
    fi
    printf '  "pid_files": {\n'
    printf '    "dispatcher": "%s",\n' "$DISPATCHER_PID_FILE"
    printf '    "claude": "%s",\n' "$CLAUDE_PID_FILE"
    printf '    "checker": "%s",\n' "$CHECKER_PID_FILE"
    printf '    "pid": "%s"\n' "$PID_FILE"
    echo "  },"
    printf '  "process_identity_files": {\n'
    printf '    "dispatcher": "%s",\n' "$DISPATCHER_IDENTITY_FILE"
    printf '    "claude": "%s",\n' "$CLAUDE_IDENTITY_FILE"
    printf '    "checker": "%s"\n' "$CHECKER_IDENTITY_FILE"
    echo "  },"
    printf '  "process_termination_receipt": "%s",\n' "$PROCESS_TERMINATION_FILE"
    printf '  "dispatcher_abnormal_exit_receipt": "%s",\n' "$ABNORMAL_EXIT_FILE"
    printf '  "builder_mode": "%s",\n' "$CLAUDE_CODE_BUILDER_MODE"
    printf '  "context_compile_strategy": "%s",\n' "$CLAUDE_CODE_CONTEXT_COMPILE_STRATEGY"
    printf '  "task_mode": "%s",\n' "${_PARSED_TASK_MODE:-unknown}"
    printf '  "declared_task_mode": "%s",\n' "${_DECLARED_TASK_MODE:-unknown}"
    printf '  "task_mode_normalized": %s,\n' "$([ "$_TASK_MODE_NORMALIZED" -eq 1 ] && echo true || echo false)"
    printf '  "task_mode_normalization_reason": "%s",\n' "$_TASK_MODE_NORMALIZATION_REASON"
    printf '  "task_mode_role_alias": "%s",\n' "$_TASK_MODE_ROLE_ALIAS"
    printf '  "task_card_builder_mode": "%s",\n' "${_TASK_CARD_BUILDER_MODE:-auto}"
    printf '  "execution_capsule_mode": "%s",\n' "$_EXECUTION_CAPSULE_MODE"
    printf '  "execution_capsule_receipt": "%s",\n' "$EXECUTION_CAPSULE_RECEIPT_FILE"
    printf '  "auto_bootstrap_capsule": %s,\n' "$([ "$_AUTO_BOOTSTRAP_CAPSULE" -eq 1 ] && echo true || echo false)"
    printf '  "recovery_delta_mode": "%s",\n' "$_RECOVERY_DELTA_MODE"
    printf '  "recovery_delta": "%s",\n' "$RECOVERY_DELTA_FILE"
    printf '  "recovery_delta_receipt": "%s",\n' "$RECOVERY_DELTA_RECEIPT_FILE"
    printf '  "skill_context_packet": "%s",\n' "$SKILL_CONTEXT_PACKET_FILE"
    printf '  "skill_context_compilation": "%s",\n' "$SKILL_CONTEXT_COMPILATION_FILE"
    printf '  "tool_profile": "%s",\n' "$CLAUDE_CODE_TOOL_PROFILE"
    printf '  "tool_profile_derivation": "%s",\n' "$_TOOL_PROFILE_DERIVATION"
    printf '  "tool_profile_supported": %s,\n' "$([ "$_TOOL_PROFILE_SUPPORTED" -eq 1 ] && echo true || echo false)"
    printf '  "tool_profile_evidence": "cli-flag-support-only",\n'
    printf '  "runtime_tool_inventory_verified": false,\n'
    printf '  "write_scope_enforcement": "%s",\n' "$CLAUDE_CODE_WRITE_SCOPE_ENFORCEMENT"
    printf '  "write_scope_receipt": "%s",\n' "$WRITE_SCOPE_RECEIPT_FILE"
    printf '  "product_baseline_receipt": "%s",\n' "$PRODUCT_BASELINE_FILE"
    printf '  "product_live_state_receipt": "%s",\n' "$PRODUCT_LIVE_STATE_FILE"
    printf '  "product_state_receipt": "%s",\n' "$PRODUCT_STATE_FILE"
    printf '  "activity_observation_receipt": "%s",\n' "$ACTIVITY_OBSERVATION_FILE"
    printf '  "extension_capsule_receipt": "%s",\n' "$EXTENSION_CAPSULE_FILE"
    printf '  "extension_advisor_receipt": "%s",\n' "$EXTENSION_ADVISOR_RECEIPT_FILE"
    printf '  "task_validation_allowlist": %s,\n' "$([ "$CLAUDE_CODE_TASK_VALIDATION_ALLOWLIST" -eq 1 ] && echo true || echo false)"
    printf '  "checker_runtime_enforcement": %s,\n' "$([ "$CLAUDE_CODE_CHECKER_RUNTIME_ENFORCEMENT" -eq 1 ] && echo true || echo false)"
    printf '  "checker_file_timeout_seconds": %s,\n' "$CLAUDE_CODE_CHECKER_FILE_TIMEOUT_SECONDS"
    printf '  "validation_fanout_jobs": %s,\n' "$CLAUDE_CODE_CHECKER_JOBS"
    printf '  "validation_receipt": "%s",\n' "$CHECKER_VALIDATION_RECEIPT_FILE"
    printf '  "scoped_handoff_manifest": "%s",\n' "$SCOPED_HANDOFF_MANIFEST_FILE"
    printf '  "scoped_handoff_patch": "%s",\n' "$SCOPED_HANDOFF_PATCH_FILE"
    printf '  "edit_ready_grace_seconds": %s,\n' "$CLAUDE_CODE_EDIT_READY_GRACE_SECONDS"
    printf '  "product_idle_timeout_seconds": %s,\n' "$CLAUDE_CODE_PRODUCT_IDLE_TIMEOUT_SECONDS"
    printf '  "product_idle_confirmations": %s,\n' "$CLAUDE_CODE_PRODUCT_IDLE_CONFIRMATIONS"
    printf '  "codegraph_policy": "%s",\n' "$CLAUDE_CODE_CODEGRAPH_POLICY"
    printf '  "codegraph_execution_status": "%s",\n' "$CODEGRAPH_EXECUTION_STATUS"
    printf '  "codegraph_execution_action": "%s",\n' "$CODEGRAPH_EXECUTION_ACTION"
    printf '  "codegraph_execution_reason": "%s",\n' "$CODEGRAPH_EXECUTION_REASON"
    printf '  "codegraph_safe_to_use": %s,\n' "$([ "$CODEGRAPH_SAFE_TO_USE" = yes ] && echo true || echo false)"
    printf '  "codegraph_worktree_receipt": "%s",\n' "$CODEGRAPH_WORKTREE_RECEIPT_FILE"
    printf '  "first_progress_timeout_seconds": %s,\n' "$CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS"
    printf '  "first_progress_timeout_source": "%s",\n' "${_FIRST_PROGRESS_TIMEOUT_SOURCE:-default}"
    printf '  "base_timeout_seconds": %s,\n' "$CLAUDE_CODE_TIMEOUT_SECONDS"
    printf '  "context_acquisition_timeout_seconds": %s,\n' "$CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS"
    printf '  "hard_timeout_seconds": %s,\n' "$CLAUDE_CODE_HARD_TIMEOUT_SECONDS"
    printf '  "active_window_refresh_limit": 0,\n'
    printf '  "active_window_refresh_policy": "canonical-product-growth-until-hard-timeout",\n'
    printf '  "growth_extension_limit": 0,\n'
    printf '  "growth_extension_policy": "renewable-product-growth-until-hard-timeout",\n'
    printf '  "progress_extension_seconds": %s,\n' "$CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS"
    printf '  "growing_progress_extension_seconds": %s,\n' "$CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS"
    printf '  "recent_activity_window_seconds": %s,\n' "$CLAUDE_CODE_RECENT_ACTIVITY_WINDOW_SECONDS"
    printf '  "timeout_advisor": "%s",\n' "$CLAUDE_CODE_TIMEOUT_ADVISOR"
    printf '  "timeout_advisor_lead_seconds": %s,\n' "$CLAUDE_CODE_TIMEOUT_ADVISOR_LEAD_SECONDS"
    printf '  "timeout_advisor_call_timeout_seconds": %s,\n' "$CLAUDE_CODE_TIMEOUT_ADVISOR_CALL_TIMEOUT_SECONDS"
    printf '  "timeout_advisor_max_attempts": %s,\n' "$CLAUDE_CODE_TIMEOUT_ADVISOR_MAX_ATTEMPTS"
    printf '  "probe_mode": "%s",\n' "$CLAUDE_CODE_API_PROBE_MODE"
    printf '  "probe_environment": "%s",\n' "$CLAUDE_CODE_PROBE_ENVIRONMENT"
    printf '  "host_requested": %s,\n' "$([ "$DISPATCH_EXECUTION_ENV" = host ] && echo true || echo false)"
    printf '  "host_authorized": %s,\n' "$([ "$CLAUDE_CODE_HOST_AUTHORITY" = 1 ] && echo true || echo false)"
    echo '  "host_effective": false,'
    printf '  "api_availability_ttl_seconds": %s,\n' "$CLAUDE_CODE_API_AVAILABILITY_TTL_SECONDS"
    printf '  "api_availability_state": "%s",\n' "$API_AVAILABILITY_STATE_FILE"
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

if [ -n "${_REVIEWED_CONTINUATION_TASK_ID:-}" ]; then
    _REVIEWED_ARCHIVE_DIR="${WORKTREE_ROOT}/${TASK_ID}.prior-control"
    mkdir -p "$_REVIEWED_ARCHIVE_DIR"
    for _control_name in TASK_CARD.md TASK_CARD_FULL.md CLAUDE_TASK_CARD.md CLAUDE_PROMPT.md CLAUDE_PROGRESS.md CLAUDE_REPORT.md; do
        if [ -f "${WORKTREE_DIR}/${_control_name}" ]; then
            cp "${WORKTREE_DIR}/${_control_name}" "${_REVIEWED_ARCHIVE_DIR}/${_control_name}"
        fi
    done
    "$PYTHON_CMD" - "$_REVIEWED_CONTINUATION_APPROVAL" "${WORKTREE_ROOT}/${TASK_ID}.reviewed-continuation-baseline.json" <<'PYEOF'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
baseline = {
    "schema_version": value.get("schema_version"),
    "approval_id": value.get("approval_id"),
    "prior_task_id": value.get("prior_task_id"),
    "worktree_state_hash": value.get("worktree_state_hash"),
    "accepted_existing_paths": value.get("accepted_existing_paths", []),
    "allow_new_write_paths": value.get("allow_new_write_paths", []),
}
with open(sys.argv[2], "w", encoding="utf-8") as handle:
    json.dump(baseline, handle, indent=2, sort_keys=True)
    handle.write("\n")
PYEOF
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
            || name == "High-Token Work Routing Gate" \
            || name == "Delegation Continuity Gate"
    }
    function compact_keep_section(name) {
        return name == "ID" \
            || name == "Task Type" \
            || name == "Executor" \
            || name == "Task Mode" \
            || name == "Goal" \
            || name == "Scope" \
            || name == "Context" \
            || name == "Claude Context Packet" \
            || name == "Builder Contract" \
            || name == "Batch Builder Gate" \
            || name == "Claude Solution Planner Contract" \
            || name == "Solution Contract Inputs" \
            || name == "Required Draft Shape" \
            || name == "Exploratory Builder Contract" \
            || name == "Post-Implementation Contract" \
            || name == "Required Exploratory Report" \
            || name == "Checker Contract" \
            || name == "Revision Delta" \
            || name == "Dependency Summary" \
            || name == "Spec Gate" \
            || name == "Root Cause Gate" \
            || name == "Test-First / TDD Contract" \
            || name == "Direction / Boundary Acknowledgement" \
            || name == "Handoff Contract" \
            || name == "Required Revisions" \
            || name == "Required Changes" \
            || name == "Dependency Summary" \
            || name == "Acceptance Criteria" \
            || name == "Testing Responsibility" \
            || name == "Validation Contract" \
            || name == "Temporary File Policy" \
            || name == "Execution Progress" \
            || name == "Execution Phases" \
            || name == "Stop Conditions" \
            || name == "Files / Modules" \
            || name == "Execution Rules" \
            || name == "Required Report"
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
            || name == "Temporary File Policy" \
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
        } else if (view == "compact") {
            if (!compact_keep_section(name)) {
                skip = 1
                next
            }
            skip = 0
        }
        skip = 0
    }
    !skip { print }
    ' "$1"
}

# Compile small task-specific procedure cues before rendering the model-facing
# card. The compiler never changes the frozen task contract; it only emits a
# hash-bound packet that the capsule can embed. Missing on older installations
# is a compatibility fallback, not a safety downgrade.
_SKILL_CONTEXT_COMPILER="${SCRIPT_DIR}/compile-skill-context.py"
_SKILL_CONTEXT_AVAILABLE=0
_SKILL_CONTEXT_EMBEDDABLE=0
_SKILL_CONTEXT_EMBEDDED=0
_SKILL_CONTEXT_PHASE="bootstrap"
[ "$_EXECUTION_CAPSULE_MODE" != "delta" ] || _SKILL_CONTEXT_PHASE="delta"
if [ -n "$PYTHON_CMD" ] && [ -f "$_SKILL_CONTEXT_COMPILER" ]; then
    _skill_context_args=(
        --task-card "${WORKTREE_DIR}/TASK_CARD_FULL.md"
        --output "$SKILL_CONTEXT_PACKET_FILE"
        --receipt "$SKILL_CONTEXT_COMPILATION_FILE"
        --phase "$_SKILL_CONTEXT_PHASE"
        --continuation-kind "$_EXECUTION_CAPSULE_KIND"
        --strategy "$CLAUDE_CODE_CONTEXT_COMPILE_STRATEGY"
    )
    if ! "$PYTHON_CMD" "$_SKILL_CONTEXT_COMPILER" "${_skill_context_args[@]}" >/dev/null; then
        echo "Error: deterministic skill-context compilation failed." >&2
        exit 1
    fi
    _SKILL_CONTEXT_AVAILABLE=1
    [ ! -s "$SKILL_CONTEXT_PACKET_FILE" ] || _SKILL_CONTEXT_EMBEDDABLE=1
    # A complete, standard Builder card is already an execution-ready frozen
    # contract. Use the same bounded bootstrap projection without changing its
    # role/tool profile. Incomplete or exploratory cards retain the legacy
    # execution view rather than silently guessing missing constraints.
    if [ "$_EXECUTION_CAPSULE_MODE" = "legacy" ] && \
       [ "$CLAUDE_CODE_AUTO_BOOTSTRAP_CAPSULE" = "1" ] && \
       [ "${_PARSED_TASK_MODE:-unknown}" = "builder" ] && \
       [ "$CLAUDE_CODE_BUILDER_MODE" = "standard" ]; then
        _AUTO_BOOTSTRAP_CAPSULE="$("$PYTHON_CMD" - "$SKILL_CONTEXT_COMPILATION_FILE" <<'PYEOF'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        receipt = json.load(handle)
    print("1" if receipt.get("contract_anchors", {}).get("complete") is True else "0")
except (OSError, ValueError, TypeError):
    print("0")
PYEOF
)"
        if [ "$_AUTO_BOOTSTRAP_CAPSULE" = "1" ]; then
            _EXECUTION_CAPSULE_MODE="bootstrap"
            _REQUIRE_COMPLETE_EXECUTION_CONTRACT=1
        fi
    fi
fi

if [ -n "$RECOVERY_CLASSIFICATION_OPTION" ]; then
    _RECOVERY_DELTA_HELPER="${SCRIPT_DIR}/build-recovery-delta.py"
    if [ -z "$PYTHON_CMD" ] || [ ! -f "$_RECOVERY_DELTA_HELPER" ]; then
        echo "Error: recovery-delta helper is unavailable; refresh the bootstrapped workflow." >&2
        exit 1
    fi
    if ! "$PYTHON_CMD" "$_RECOVERY_DELTA_HELPER" \
        --task-card "${WORKTREE_DIR}/TASK_CARD_FULL.md" \
        --attempt-classification "$RECOVERY_CLASSIFICATION_OPTION" \
        --output "$RECOVERY_DELTA_FILE" \
        --receipt "$RECOVERY_DELTA_RECEIPT_FILE" >/dev/null; then
        echo "Error: deterministic recovery-delta generation failed." >&2
        exit 1
    fi
    _RECOVERY_DELTA_MODE="classification-bound"
    _REQUIRE_COMPLETE_EXECUTION_CONTRACT=1
fi

_EXECUTION_CAPSULE_HELPER="${SCRIPT_DIR}/build-execution-capsule.py"
if [ -n "$PYTHON_CMD" ] && [ -f "$_EXECUTION_CAPSULE_HELPER" ] && \
   [ "$_EXECUTION_CAPSULE_MODE" != "legacy" ]; then
    _execution_capsule_args=(
        --task-card "${WORKTREE_DIR}/TASK_CARD_FULL.md"
        --output "${WORKTREE_DIR}/CLAUDE_TASK_CARD.md"
        --mode "$_EXECUTION_CAPSULE_MODE"
        --continuation-kind "$_EXECUTION_CAPSULE_KIND"
        --receipt "$EXECUTION_CAPSULE_RECEIPT_FILE"
    )
    [ "$_REQUIRE_COMPLETE_EXECUTION_CONTRACT" -eq 0 ] || \
        _execution_capsule_args+=(--require-complete-contract)
    [ -z "$REHYDRATE_FROM_OPTION" ] || \
        _execution_capsule_args+=(--rehydrate-from "$REHYDRATE_FROM_OPTION")
    [ "$_CONTEXT_CHECKPOINT_RECEIPT_BOUND" -eq 0 ] || \
        _execution_capsule_args+=(--rehydrate-receipt "$CONTEXT_CHECKPOINT_RECEIPT_FILE")
    [ "$_SKILL_CONTEXT_EMBEDDABLE" -eq 0 ] || \
        _execution_capsule_args+=(
            --compiled-context "$SKILL_CONTEXT_PACKET_FILE"
            --compiled-context-receipt "$SKILL_CONTEXT_COMPILATION_FILE"
        )
    [ "$_RECOVERY_DELTA_MODE" = "none" ] || \
        _execution_capsule_args+=(
            --recovery-delta "$RECOVERY_DELTA_FILE"
            --recovery-delta-receipt "$RECOVERY_DELTA_RECEIPT_FILE"
        )
    [ -z "${_REVIEWED_CONTINUATION_APPROVAL:-}" ] || \
        _execution_capsule_args+=(
            --reviewed-continuation "$_REVIEWED_CONTINUATION_APPROVAL"
        )
    if ! "$PYTHON_CMD" "$_EXECUTION_CAPSULE_HELPER" "${_execution_capsule_args[@]}" >/dev/null; then
        echo "Error: failed to render the bounded Claude execution capsule." >&2
        exit 1
    fi
    [ "$_SKILL_CONTEXT_EMBEDDABLE" -eq 0 ] || _SKILL_CONTEXT_EMBEDDED=1
else
    render_claude_task_card "${WORKTREE_DIR}/TASK_CARD_FULL.md" > "${WORKTREE_DIR}/CLAUDE_TASK_CARD.md"
fi

# The runtime identity is created before the worktree-local card can be
# compiled. Refresh only the deterministic context-routing fields now that the
# actual capsule/recovery artifacts exist; without this refresh, a complete
# standard Builder would be recorded as legacy even when it used a bootstrap
# capsule.
if [ -n "$PYTHON_CMD" ] && [ -s "$RUNTIME_JSON" ]; then
    "$PYTHON_CMD" - "$RUNTIME_JSON" \
        "$_EXECUTION_CAPSULE_MODE" "$_AUTO_BOOTSTRAP_CAPSULE" \
        "$_RECOVERY_DELTA_MODE" "$RECOVERY_DELTA_FILE" "$RECOVERY_DELTA_RECEIPT_FILE" \
        "$SKILL_CONTEXT_PACKET_FILE" "$SKILL_CONTEXT_COMPILATION_FILE" \
        "$EXECUTION_CAPSULE_RECEIPT_FILE" "$CLAUDE_CODE_CONTEXT_COMPILE_STRATEGY" <<'PYEOF'
import json, os, sys, tempfile

path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("runtime identity must be an object")
    value.update({
        "execution_capsule_mode": sys.argv[2],
        "auto_bootstrap_capsule": sys.argv[3] == "1",
        "recovery_delta_mode": sys.argv[4],
        "recovery_delta": sys.argv[5],
        "recovery_delta_receipt": sys.argv[6],
        "skill_context_packet": sys.argv[7],
        "skill_context_compilation": sys.argv[8],
        "execution_capsule_receipt": sys.argv[9],
        "context_compile_strategy": sys.argv[10],
    })
    directory = os.path.dirname(path) or "."
    descriptor, temporary = tempfile.mkstemp(prefix=".runtime-context-", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
    print("Error: unable to refresh runtime context identity: {}".format(exc), file=sys.stderr)
    raise SystemExit(1)
PYEOF
fi

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
    # The advisor continuation replaces the rendered execution card below, so
    # append the immutable compiled guidance again after replacement.
    _SKILL_CONTEXT_EMBEDDED=0
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

if [ "$_SKILL_CONTEXT_EMBEDDABLE" -eq 1 ] && [ "$_SKILL_CONTEXT_EMBEDDED" -eq 0 ]; then
    {
        echo ""
        cat "$SKILL_CONTEXT_PACKET_FILE"
    } >> "${WORKTREE_DIR}/CLAUDE_TASK_CARD.md"
    _SKILL_CONTEXT_EMBEDDED=1
fi

# Revision receivers must get exact findings inline. An external review path is
# audit metadata, not executable context, and cannot replace finding contents.
_REVISION_VALIDATOR="${SCRIPT_DIR}/validate-revision-card.py"
if [ -n "$PYTHON_CMD" ] && [ -f "$_REVISION_VALIDATOR" ]; then
    if ! "$PYTHON_CMD" "$_REVISION_VALIDATOR" "${WORKTREE_DIR}/CLAUDE_TASK_CARD.md" \
        --output "$REVISION_CARD_VALIDATION_FILE"; then
        echo "Error: revision task card lacks concrete inline review findings. See ${REVISION_CARD_VALIDATION_FILE}" >&2
        exit 1
    fi
fi

if [ "$CLAUDE_CODE_VERBOSE" = "1" ]; then
    echo "Full task card copied to: ${WORKTREE_DIR}/TASK_CARD_FULL.md"
    echo "Compatibility task card copied to: ${WORKTREE_DIR}/TASK_CARD.md"
    echo "Claude execution card rendered to: ${WORKTREE_DIR}/CLAUDE_TASK_CARD.md"
fi

_INITIAL_EXECUTION_PHASE="context"
if [ "$CLAUDE_CODE_BUILDER_MODE" = "solution-planning" ]; then
    _INITIAL_EXECUTION_PHASE="planning"
fi
{
    echo "<!-- ${SEEDED_PROGRESS_MARKER} -->"
    echo "# Claude Progress"
    echo ""
    echo "- Goal: Execute ${TASK_CARD}"
    echo "- Current Phase: dispatch-started"
    echo "- Execution Phase: ${_INITIAL_EXECUTION_PHASE}"
    echo "- Implementation Complete: no"
    echo "- Assigned Tail Work: read Post-Implementation Contract"
    echo "- Tail Work Complete: no"
    echo "- Completion Ready: no"
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
    echo "The required completion headings are pre-created below. Fill them and remove the seeded marker."
    echo ""
    echo "## Requirements Summary"
    echo "## Files Changed"
    echo "## Acceptance Criteria Mapping"
    echo "## Out-of-Scope Confirmation"
    echo "## Plan Match"
    echo "## Checks Run"
} > "${WORKTREE_DIR}/CLAUDE_REPORT.md"

if [ -n "$CONTEXT_LEASE_OPTION" ] || [ -n "${_REVIEWED_CONTINUATION_TASK_ID:-}" ]; then
cat > "${WORKTREE_DIR}/CLAUDE_PROMPT.md" <<'EOF'
You are continuing a hash-bound execution lineage in a Codex/Claude Code workflow.

Execute only the current delta in `CLAUDE_TASK_CARD.md`. Continue from the resumed
conversation or the accepted context checkpoint embedded in that card. Do not
rebuild a repository model that the lineage already established.

Rules:
- `TASK_CARD_FULL.md` is a Codex-owned audit artifact; do not open or summarize it.
- Read only named targets needed for this delta. If an accepted fact is stale, stop
  and report the exact mismatch instead of broad discovery.
- Modify only receipt-authorized paths and preserve accepted prior work.
- Obey the current testing boundary; run only assigned narrow checks.
- Update `CLAUDE_PROGRESS.md` at material phase changes and remove seeded markers.
- Update `CLAUDE_REPORT.md` with changed paths, acceptance mapping, checks,
  deviations, blockers, and remaining risks.
- When assigned work and reporting are complete, set `Completion Ready: yes` and
  `Next Check: exit`, then exit normally without waiting for acknowledgement.

--- CLAUDE DELTA EXECUTION CARD ---
EOF
elif [ "$CLAUDE_CODE_BUILDER_MODE" = "solution-planning" ]; then
cat > "${WORKTREE_DIR}/CLAUDE_PROMPT.md" <<'EOF'
You are the solution planner in a Codex/Claude Code workflow.

Produce the structured solution contract assigned by `CLAUDE_TASK_CARD.md`. `TASK_CARD_FULL.md` is retained for audit only.

Rules:
- Read only inside the declared exploration boundary and update `CLAUDE_PROGRESS.md` at meaningful planning milestones.
- Use `Execution Phase: context`, `planning`, `contract-validation`, or `complete`; never label solution-contract work as `implementation`.
- Do not edit product source, tests, build files, or documentation. Your durable output is `solution-contract.draft.json`.
- Converge on one coherent end state: invariants, integration points, acceptance criteria, non-goals, genuine unknowns, and independently executable slices.
- Each slice must declare non-overlapping write scope, dependencies, and acceptance IDs. Do not turn recommendations into mandatory scope.
- Validate with `python ai/solution-contract.py validate solution-contract.draft.json`. If validation cannot run, record the blocker and still leave the best schema-shaped draft.
- Update `CLAUDE_REPORT.md` with the draft path, validation evidence, unresolved blocking decisions, and no source-change confirmation.
- Set `Implementation Complete: yes`, `Completion Ready: yes`, and `Next Check: exit` only after the structured draft is written; then exit normally.

--- CLAUDE SOLUTION-PLANNING CARD ---
EOF
elif [ "$CLAUDE_CODE_BUILDER_MODE" = "batch" ]; then
cat > "${WORKTREE_DIR}/CLAUDE_PROMPT.md" <<'EOF'
You are the batch executor in a Codex/Claude Code workflow.

Apply the single reviewed transformation in `CLAUDE_TASK_CARD.md` across only the listed independent write units.

Rules:
- Read the source-of-truth example and exact assigned paths; do not rediscover or redesign shared contracts.
- Update `CLAUDE_PROGRESS.md` before the first edit and after each completed unit group.
- Stop and report any unit that overlaps another owner, requires architecture judgment, or does not match the reviewed pattern.
- Never broaden the batch to nearby files. Report completed and blocked units separately; partial work is not silent success.
- Run only assigned narrow validation and update `CLAUDE_REPORT.md` with the changed-unit manifest, blocked units, validation evidence, and deviations.
- After assigned work is complete, set `Implementation Complete: yes`, `Completion Ready: yes`, and `Next Check: exit`; then exit normally.

--- CLAUDE BATCH CARD ---
EOF
elif [ "$CLAUDE_CODE_BUILDER_MODE" = "execution-only" ]; then
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
- Keep the stable execution fields in `CLAUDE_PROGRESS.md`: `Execution Phase`, `Implementation Complete`, `Assigned Tail Work`, `Tail Work Complete`, and `Completion Ready`.
- For `solution-planning`, use only `context`, `planning`, `contract-validation`, and `complete`; never label contract work as `implementation`.
- After implementation, perform only the task card's Post-Implementation Contract. Set `Completion Ready: yes` and `Next Check: exit`, write the report, and exit normally; do not wait for dispatcher acknowledgement.

--- CLAUDE EXECUTION CARD ---
EOF
elif [ "$CLAUDE_CODE_BUILDER_MODE" = "exploratory" ]; then
cat > "${WORKTREE_DIR}/CLAUDE_PROMPT.md" <<'EOF'
You are the exploratory executor in a Codex/Claude Code workflow.

The goal and exploration boundary in `CLAUDE_TASK_CARD.md` are fixed; the implementation path inside that boundary is yours to discover and execute.

Rules:
- Update `CLAUDE_PROGRESS.md` before exploration, when selecting an implementation path, before long commands, and when blocked.
- Keep exploration and implementation in this same run. Do not finish with only a repository summary, plan, bug list, or document analysis.
- Produce at least one durable assigned output: source diff, test, runnable prototype, or structured repository asset.
- Prefer a working vertical slice over broad unfinished scaffolding.
- Stay inside the declared read/write boundary and forbidden paths. Product, API, data-model, security, permission, migration, or destructive decisions remain Codex/human-owned unless explicitly resolved in the card.
- Use repository tools and existing patterns to resolve implementation details autonomously. Record only material assumptions and rejected alternatives.
- If no durable output is possible, stop with the concrete blocker and the smallest decision needed; prose-only completion is not success.
- Obey the testing boundary and update `CLAUDE_REPORT.md` with durable outputs, path chosen, assumptions checked, validation evidence, deviations, and remaining authority decisions.
- Keep the stable execution fields in `CLAUDE_PROGRESS.md`. After the durable output is complete, perform only assigned tail work, set `Completion Ready: yes` and `Next Check: exit`, write the report, and exit normally.

--- CLAUDE EXECUTION CARD ---
EOF
elif [ "$CLAUDE_CODE_PROMPT_PROFILE" = "brief" ]; then
cat > "${WORKTREE_DIR}/CLAUDE_PROMPT.md" <<'EOF'
You are the executor in a Codex/Claude Code workflow.

Execute `CLAUDE_TASK_CARD.md`. `TASK_CARD_FULL.md` is retained for audit and may be consulted when the execution card is insufficient, but do not broaden scope beyond the execution card.

Core rules:
- Codex owns routing, core semantics, and review; Claude edits only this positively routed mechanical/auxiliary scope.
- Update `CLAUDE_PROGRESS.md` before exploration or edits, at phase boundaries, before long commands, and when blocked.
- Remove dispatcher seeded markers when you first update `CLAUDE_PROGRESS.md` or `CLAUDE_REPORT.md`.
- If Direction / Boundary Acknowledgement is blocking, write it and stop for approval. If it is non-blocking and recommendation is `proceed`, continue implementation in the same run.
- Builder tasks implement and report direction. Do not add acceptance tests or broad validation unless explicitly assigned.
- Checker/Test tasks must use the exact Context Packet interface signature and runnable example before calling an API. After each test-file write, immediately run that file's syntax/import check and narrow single-file test before writing another test. Avoid broad implementation rewrites.
- Put generated validation helpers under `$TMPDIR`; repository-root scratch scripts are forbidden unless explicitly listed in Write paths.
- If one dispatch mixes implementation, test writing, broad validation, and phase stop gates without explicit `mixed-exception`, stop and recommend a split.
- If `Local validation allowed?` is `no`, do not run local validation; report exact commands only.
- If target, scope, testing responsibility, public API/data/security/migration impact, destructive actions, permissions, or production data are unclear, stop-and-report instead of guessing.
- Preserve failures, blockers, exact commands, exit codes, and key output. Do not include secrets, large logs, or full diffs in progress/report files.
- Keep `Execution Phase`, `Implementation Complete`, `Assigned Tail Work`, `Tail Work Complete`, and `Completion Ready` near the top of `CLAUDE_PROGRESS.md`.
- For `solution-planning`, use only `context`, `planning`, `contract-validation`, and `complete`; never label contract work as `implementation`.
- After implementation, do only explicitly assigned tail work. Then set `Completion Ready: yes` and `Next Check: exit`, write the final report, and exit normally without waiting for acknowledgement.

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
  - Execution Phase (`context`, `implementation`, `validation`, or `tail`; for solution-planning: `context`, `planning`, `contract-validation`, or `complete`)
  - Context Acquisition Complete (`yes` or `no`)
  - Planned First Write (exact path and intended change, or `none`)
  - Implementation Complete (`yes` or `no`)
  - Assigned Tail Work
  - Tail Work Complete (`yes` or `no`)
  - Completion Ready (`yes` or `no`)
- Before any command or investigation that may take more than a few minutes, write what you are about to do and what result you expect.
- Enter `Execution Phase: implementation` only after repository scanning, requirement understanding, and a local edit plan are complete. At that transition set `Context Acquisition Complete: yes` and a non-empty `Planned First Write`. This is edit readiness only; the dispatcher will not count it as durable output until product content changes.
- Do not include secrets, large logs, or full diffs.
- Preserve failed commands and observations instead of deleting or rewriting them; later recovery depends on that evidence.
- When assigned implementation is complete, set `Implementation Complete: yes` and enter `Execution Phase: tail`. Perform only work listed in the Post-Implementation Contract. Then set `Tail Work Complete: yes`, `Completion Ready: yes`, and `Next Check: exit`; update the final report and exit normally without waiting for dispatcher acknowledgement.
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
- Before the first API call in a Checker test, copy the exact signature, constructor fields, runnable call example, and async/sync rule from the Context Packet into your working plan. If any required item is missing or contradicts the repository, stop and report the evidence gap instead of guessing.
- After each assigned test file is written, immediately run the language syntax/import check and the exact single-file test. Fix or report that file before creating the next one; do not defer all validation until the end.
- Store ad-hoc validation helpers only under `$TMPDIR`. Any repository-local helper must be an explicitly allowed write path.
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

End the report with machine-readable mechanical claims (these do not replace
semantic review): one `claimed_file=<repo-relative-path>` line per implementation
file, `claimed_changed_file_count=<integer>`, optional
`claimed_symbol=<important-added-or-wired-symbol>` lines, and
`claimed_no_unexpected_files=yes|no`.
- Notes that help Codex compare the implementation against the original task.

Context Packet:
- If `CLAUDE_CONTEXT_PACKET.md` is present, read it before exploring the codebase. It contains pre-computed target files, symbols, snippets, and constraints relevant to this task.
- The context packet is dispatch evidence and should not be counted as an implementation change.

--- CLAUDE EXECUTION CARD ---
EOF
fi

# Capture only component identities for cache attribution.  This point is
# intentionally before the dynamic CodeGraph/worktree block and task card are
# appended, so the stable-prefix hash detects real template drift rather than
# expected per-task suffix changes.  Prompt bodies are never written to usage
# telemetry.
_CACHE_STABLE_PREFIX_SHA256=""
_CACHE_TASK_SUFFIX_SHA256=""
_CACHE_STABLE_PREFIX_BYTES=0
_CACHE_TASK_SUFFIX_BYTES=0
_CACHE_PROMPT_LAYOUT="static-core-v1"
if [ -n "$PYTHON_CMD" ]; then
    IFS=$'\t' read -r _CACHE_STABLE_PREFIX_SHA256 _CACHE_STABLE_PREFIX_BYTES _CACHE_TASK_SUFFIX_SHA256 _CACHE_TASK_SUFFIX_BYTES < <(
        "$PYTHON_CMD" - "${WORKTREE_DIR}/CLAUDE_PROMPT.md" "${WORKTREE_DIR}/CLAUDE_TASK_CARD.md" <<'PYEOF'
import hashlib, sys

def digest(path):
    with open(path, "rb") as handle:
        return "sha256:" + hashlib.sha256(handle.read()).hexdigest()

def byte_count(path):
    with open(path, "rb") as handle:
        return len(handle.read())

print("\t".join((
    digest(sys.argv[1]), str(byte_count(sys.argv[1])),
    digest(sys.argv[2]), str(byte_count(sys.argv[2])),
)))
PYEOF
    )
fi
case "$_CACHE_STABLE_PREFIX_BYTES" in ''|*[!0-9]*) _CACHE_STABLE_PREFIX_BYTES=0 ;; esac
case "$_CACHE_TASK_SUFFIX_BYTES" in ''|*[!0-9]*) _CACHE_TASK_SUFFIX_BYTES=0 ;; esac
cat >> "${WORKTREE_DIR}/CLAUDE_PROMPT.md" <<EOF

CodeGraph worktree identity:
- Guard status: ${CODEGRAPH_EXECUTION_STATUS}
- Guard action: ${CODEGRAPH_EXECUTION_ACTION}
- Guard reason: ${CODEGRAPH_EXECUTION_REASON}
- Receipt: ${CODEGRAPH_WORKTREE_RECEIPT_FILE}
- If guard status is not ready, do not use CodeGraph MCP/CLI results in this execution. They may belong to another worktree. Use the supplied Context Packet, LSP, ai/locate-code.py, targeted search, and targeted reads instead.
- If guard status is ready, CodeGraph queries must still run from ${WORKTREE_DIR}. Never reuse graph output carrying a different project/worktree identity.
EOF
cat "${WORKTREE_DIR}/CLAUDE_TASK_CARD.md" >> "${WORKTREE_DIR}/CLAUDE_PROMPT.md"

CLAUDE_CODE_TIMEOUT_SECONDS="${CLAUDE_CODE_TIMEOUT_SECONDS:-600}"
CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS="${CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS:-$CLAUDE_CODE_TIMEOUT_SECONDS}"
CLAUDE_CODE_HARD_TIMEOUT_SECONDS="${CLAUDE_CODE_HARD_TIMEOUT_SECONDS:-1500}"
CLAUDE_CODE_HEARTBEAT_SECONDS="${CLAUDE_CODE_HEARTBEAT_SECONDS:-30}"
CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS="${CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS:-0}"
CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS="${CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS:-300}"
CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS="${CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS:-300}"
CLAUDE_CODE_TIMEOUT_ADVISOR="${CLAUDE_CODE_TIMEOUT_ADVISOR:-auto}"
CLAUDE_CODE_TIMEOUT_ADVISOR_LEAD_SECONDS="${CLAUDE_CODE_TIMEOUT_ADVISOR_LEAD_SECONDS:-60}"
CLAUDE_CODE_TIMEOUT_ADVISOR_CALL_TIMEOUT_SECONDS="${CLAUDE_CODE_TIMEOUT_ADVISOR_CALL_TIMEOUT_SECONDS:-90}"
CLAUDE_CODE_TIMEOUT_ADVISOR_MAX_ATTEMPTS="${CLAUDE_CODE_TIMEOUT_ADVISOR_MAX_ATTEMPTS:-2}"
CLAUDE_CODE_TIMEOUT_ADVISOR_RETRY_SECONDS="${CLAUDE_CODE_TIMEOUT_ADVISOR_RETRY_SECONDS:-30}"

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

API_AVAILABILITY_HELPER="${SCRIPT_DIR}/claude-api-availability.py"
_CLAUDE_COMMAND_PATH="$(command -v claude 2>/dev/null || true)"
_STARTUP_PROBE_SOURCE="not-run"
_LAST_TOOL_INVENTORY=""
_LAST_TOOL_INVENTORY_VERIFIED="no"
_RUNTIME_TOOL_INVENTORY_STATUS="unverified"

record_api_availability() {
    local source="$1"
    [ -n "$PYTHON_CMD" ] && [ -f "$API_AVAILABILITY_HELPER" ] || return 0
    local inventory_args=()
    if [ "$_LAST_TOOL_INVENTORY_VERIFIED" = "yes" ]; then
        inventory_args+=(--tool-inventory-verified)
        while IFS= read -r _tool; do
            [ -n "$_tool" ] && inventory_args+=(--tool-inventory "$_tool")
        done < <(printf '%s\n' "$_LAST_TOOL_INVENTORY" | tr ',' '\n')
    fi
    "$PYTHON_CMD" "$API_AVAILABILITY_HELPER" record \
        --state "$API_AVAILABILITY_STATE_FILE" --repository "$REPO_ROOT" \
        --route "$CLAUDE_CODE_PROXY_MODE" --environment "$CLAUDE_CODE_PROBE_ENVIRONMENT" \
        --claude-command "$_CLAUDE_COMMAND_PATH" \
        --tool-profile "${CLAUDE_CODE_TOOL_PROFILE:-default}" \
        --source "$source" "${inventory_args[@]}" >/dev/null 2>&1 || true
}

invalidate_api_availability() {
    local reason="$1"
    [ -n "$PYTHON_CMD" ] && [ -f "$API_AVAILABILITY_HELPER" ] || return 0
    "$PYTHON_CMD" "$API_AVAILABILITY_HELPER" invalidate \
        --state "$API_AVAILABILITY_STATE_FILE" --reason "$reason" >/dev/null 2>&1 || true
}

load_cached_api_availability() {
    local artifact_file="$1"
    [ -n "$PYTHON_CMD" ] && [ -f "$API_AVAILABILITY_HELPER" ] || return 1
    "$PYTHON_CMD" "$API_AVAILABILITY_HELPER" check \
        --state "$API_AVAILABILITY_STATE_FILE" --repository "$REPO_ROOT" \
        --route "$CLAUDE_CODE_PROXY_MODE" --environment "$CLAUDE_CODE_PROBE_ENVIRONMENT" \
        --claude-command "$_CLAUDE_COMMAND_PATH" \
        --tool-profile "${CLAUDE_CODE_TOOL_PROFILE:-default}" \
        --ttl "$CLAUDE_CODE_API_AVAILABILITY_TTL_SECONDS" > "$artifact_file" 2>/dev/null
}

case "$CLAUDE_CODE_TIMEOUT_SECONDS" in
    ''|*[!0-9]*)
        echo "Error: CLAUDE_CODE_TIMEOUT_SECONDS must be a non-negative integer." >&2
        exit 1
        ;;
esac
case "$CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS" in
    ''|*[!0-9]*)
        echo "Error: CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS must be a non-negative integer." >&2
        exit 1
        ;;
esac
case "$CLAUDE_CODE_HARD_TIMEOUT_SECONDS" in
    ''|*[!0-9]*)
        echo "Error: CLAUDE_CODE_HARD_TIMEOUT_SECONDS must be a non-negative integer." >&2
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
case "$CLAUDE_CODE_TIMEOUT_ADVISOR" in
    auto|on|off) ;;
    *)
        echo "Error: CLAUDE_CODE_TIMEOUT_ADVISOR must be auto, on, or off." >&2
        exit 1
        ;;
esac
for _advisor_name in CLAUDE_CODE_TIMEOUT_ADVISOR_LEAD_SECONDS CLAUDE_CODE_TIMEOUT_ADVISOR_RETRY_SECONDS; do
    _advisor_value="${!_advisor_name}"
    case "$_advisor_value" in
        ''|*[!0-9]*)
            echo "Error: ${_advisor_name} must be a non-negative integer." >&2
            exit 1
            ;;
    esac
done
case "$CLAUDE_CODE_TIMEOUT_ADVISOR_CALL_TIMEOUT_SECONDS" in
    ''|*[!0-9]*|0)
        echo "Error: CLAUDE_CODE_TIMEOUT_ADVISOR_CALL_TIMEOUT_SECONDS must be a positive integer." >&2
        exit 1
        ;;
esac
case "$CLAUDE_CODE_TIMEOUT_ADVISOR_MAX_ATTEMPTS" in
    ''|*[!0-9]*|0)
        echo "Error: CLAUDE_CODE_TIMEOUT_ADVISOR_MAX_ATTEMPTS must be a positive integer." >&2
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

monitor_event() {
    # Compact, append-only boundary events are the only monitor surface an
    # agent needs while waiting. Values supplied here are machine-safe tokens.
    printf 'monitor_event source=dispatcher task_id=%s %s\n' "$TASK_ID" "$1" >> "$MONITOR_EVENT_LOG"
}

observe_runtime_activity() {
    # Observe only filesystem metadata. The dispatcher intentionally does not
    # read Claude session JSONL/transcript contents, and the resulting signal
    # is diagnostic only: it never refreshes product-progress deadlines.  The
    # observation is session-id filtered so another concurrent/old transcript
    # in the same Claude home cannot make this task appear active.
    local now_epoch="$1"
    local phase="$2"
    "$PYTHON_CMD" - "$ACTIVITY_OBSERVATION_FILE" "$TASK_ID" \
        "${_CLAUDE_PROJECTS_OBSERVATION_ROOT:-}" "${CLAUDE_SESSION_ID:-}" \
        "$now_epoch" "${LAST_CONTROL_ACTIVITY_EPOCH:-0}" \
        "${LAST_PRODUCT_CHANGE_EPOCH:-0}" "${ACTIVE_EXECUTION_DEADLINE:-0}" \
        "${HARD_TIMEOUT_DEADLINE:-0}" "$phase" <<'PYEOF'
import json, os, sys, tempfile

(output, task_id, session_root, session_id, now_raw, control_raw, product_raw,
 active_raw, hard_raw, phase) = sys.argv[1:]
now = int(now_raw)
control = int(control_raw or 0)
product = int(product_raw or 0)
active = int(active_raw or 0)
hard = int(hard_raw or 0)

session = 0
visited = 0
matching = 0
pending = [session_root] if session_root and os.path.isdir(session_root) else []
while pending and visited < 512:
    current = pending.pop()
    try:
        entries = list(os.scandir(current))
    except OSError:
        continue
    for entry in entries:
        if visited >= 512:
            break
        visited += 1
        try:
            if entry.is_dir(follow_symlinks=False):
                pending.append(entry.path)
            elif session_id and session_id in entry.name:
                session = max(session, int(entry.stat(follow_symlinks=False).st_mtime))
                matching += 1
        except OSError:
            continue

def optional(value):
    return value if value > 0 else None

def age(value):
    return max(0, now - value) if value > 0 else None

def remaining(value):
    return max(0, value - now) if value > 0 else None

value = {
    "schema_version": 1,
    "task_id": task_id,
    "sampled_at_epoch": now,
    "execution_state": phase,
    "last_session_activity_epoch": optional(session),
    "last_control_activity_epoch": optional(control),
    "last_product_change_epoch": optional(product),
    "seconds_since_session_activity": age(session),
    "seconds_since_control_activity": age(control),
    "seconds_since_product_change": age(product),
    "active_window_remaining_seconds": remaining(active),
    "hard_timeout_remaining_seconds": remaining(hard),
    "session_entries_sampled": visited,
    "matching_session_entries": matching,
    "session_activity_source": "session-id-filtered-transcript-mtime-without-content-read",
    "model_tool_split_available": False,
    "refreshes_product_window": False,
    "authority": "diagnostic-only",
}
directory = os.path.dirname(output) or "."
fd, temporary = tempfile.mkstemp(prefix=".activity-observation-", dir=directory)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, output)
except BaseException:
    try:
        os.unlink(temporary)
    except OSError:
        pass
    raise
print(session)
PYEOF
}

phase_event() {
    # Claude-authored progress is advisory. Normalize it into a compact JSONL
    # stream without treating the phase or command as completion evidence.
    local phase="$1"
    local current_command="${2:-}"
    "$PYTHON_CMD" - "$PHASE_EVENT_LOG" "$TASK_ID" "$phase" "$current_command" <<'PYEOF'
import datetime, json, sys
path, task_id, phase, command = sys.argv[1:]
value = {
    "schema_version": 1,
    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "task_id": task_id,
    "phase": phase,
    "current_validation_command": command[:1000] or None,
    "authority": "advisory-progress",
}
with open(path, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(value, sort_keys=True) + "\n")
PYEOF
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
    if [ -n "${_TOOL_PROFILE_AVAILABLE_TOOLS:-}" ]; then
        probe_env_args+=(--tools "$_TOOL_PROFILE_AVAILABLE_TOOLS")
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
    IFS=$'\t' read -r _LAST_TOOL_INVENTORY_VERIFIED _LAST_TOOL_INVENTORY < <(
        "$PYTHON_CMD" - "$artifact_file" <<'PYEOF' 2>/dev/null || printf 'no\t\n'
import json, sys
try:
    value = json.load(open(sys.argv[1], encoding="utf-8"))
    successful = [p for p in value.get("interaction_probes", []) if p.get("success")]
    probe = successful[-1] if successful else {}
    verified = probe.get("tool_inventory_verified") is True
    print(("yes" if verified else "no") + "\t" + ",".join(probe.get("tool_inventory", [])))
except (OSError, ValueError, TypeError):
    print("no\t")
PYEOF
    )
    if [ "$_LAST_PROBE_CONCLUSION" = "available" ]; then
        _LAST_PROBE_AUTHORITATIVE="yes"
        record_api_availability "${phase}-probe"
    else
        invalidate_api_availability "${phase}-probe-${_LAST_PROBE_CONCLUSION:-unknown}"
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

# Hash semantic progress while ignoring volatile clock-only rewrites. Rewriting
# the same milestone with a new Last Update/Timestamp must not refresh runtime.
progress_semantic_hash() {
    local file="$1"
    if [ ! -f "$file" ]; then
        echo ""
        return
    fi
    sed -E \
        -e '/^[[:space:]-]*(Last Update|Updated At|Timestamp|Observed At|Collected At):/Id' \
        -e 's/[[:space:]]+$//' \
        "$file" 2>/dev/null | sha1sum 2>/dev/null | awk '{print $1}' || true
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
    # 3. Non-seeded semantic progress changed. Clock-only rewrites are ignored.
    now_progress_bytes="$(file_size "${WORKTREE_DIR}/CLAUDE_PROGRESS.md")"
    if [ -s "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" ] && \
       ! file_contains "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" "$SEEDED_PROGRESS_MARKER"; then
        now_progress_hash="$(progress_semantic_hash "${WORKTREE_DIR}/CLAUDE_PROGRESS.md")"
        if [ "$now_progress_hash" != "${5:-}" ]; then
            return 0
        fi
    fi
    return 1
}

valid_claude_report_file() {
    local file="$1"
    [ -s "$file" ] || return 1
    local validator="${SCRIPT_DIR}/validate-claude-report.py"
    if [ -n "${PYTHON_CMD:-}" ] && [ -f "$validator" ]; then
        "$PYTHON_CMD" "$validator" "$file" >/dev/null 2>&1
        return $?
    fi
    # Compatibility fallback for an old partial installation. It remains
    # conservative and requires the standard report structure.
    file_contains "$file" "$SEEDED_REPORT_MARKER|$SEEDED_PROGRESS_MARKER|$FALLBACK_REPORT_MARKER" && return 1
    for heading in "Requirements Summary" "Files Changed" "Acceptance Criteria Mapping" \
                   "Out-of-Scope Confirmation" "Plan Match" "Checks Run"; do
        grep -Fqi "## ${heading}" "$file" 2>/dev/null || return 1
    done
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

worktree_digest() {
    # First progress and product-idle use the same full content hash. The prior
    # shortstat/status digest missed same-size rewrites and made an existing
    # continuation diff look like fresh model progress.
    if [ -n "${PYTHON_CMD:-}" ] && [ -f "${SCRIPT_DIR}/worktree_state_hash.py" ]; then
        "$PYTHON_CMD" "${SCRIPT_DIR}/worktree_state_hash.py" \
            --worktree "${WORKTREE_DIR:-.}" --ignore-empty-untracked \
            2>/dev/null
        return
    fi
    {
        git status --porcelain --untracked-files=no 2>/dev/null \
            | grep -v -E '^(.. )?(TASK_CARD|TASK_CARD_FULL|CLAUDE_TASK_CARD|CLAUDE_PROMPT|CLAUDE_REPORT|CLAUDE_PROGRESS|ADVISOR_REQUEST)(\.md|\.json)?$' || true
        while IFS= read -r -d '' _digest_untracked_path; do
            case "${_digest_untracked_path##*/}" in
                TASK_CARD.md|TASK_CARD_FULL.md|CLAUDE_TASK_CARD.md|CLAUDE_PROMPT.md|CLAUDE_REPORT.md|CLAUDE_PROGRESS.md|ADVISOR_REQUEST.json)
                    continue ;;
            esac
            if [ -s "$_digest_untracked_path" ] || [ -L "$_digest_untracked_path" ]; then
                printf '?? %s\n' "$_digest_untracked_path"
            fi
        done < <(git ls-files --others --exclude-standard -z 2>/dev/null || true)
        git diff --binary 2>/dev/null || true
        git diff --cached --binary 2>/dev/null || true
    } | sha256sum 2>/dev/null | awk '{print $1}' || true
}

write_runtime_approval_blocker() {
    grep -Eiq \
        'contains simple_expansion|requires ([^[:space:]]+ )?approval|approval (is )?required|permission denied|tool use[^[:cntrl:]]*(denied|rejected)|write-approved-file[^[:cntrl:]]*(blocked|denied|approval)|AI_WORKFLOW_WRITE_SCOPE_RECEIPT[^[:cntrl:]]*(blocked|denied|approval)' \
        "$STATUS_FILE" "$RAW_RESULT_FILE" "$RESULT_FILE" 2>/dev/null
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
    local broker_pid=""
    local descendant=""
    if [ -n "$descendants" ] && command -v ps >/dev/null 2>&1; then
        for descendant in $descendants; do
            if ps -p "$descendant" -o args= 2>/dev/null | grep -Fq "model-call-broker.py"; then
                broker_pid="$descendant"
                break
            fi
        done
    fi

    if [ -n "$broker_pid" ]; then
        progress_log "Stopping Claude (${reason}) after ${elapsed}s; requesting broker cancellation before wrapper pid=${CLAUDE_PID} broker_pid=${broker_pid}"
        # The broker converts TERM into a cancelled ledger transition and
        # terminates the model's dedicated process group.
        kill "$broker_pid" 2>/dev/null || true
        sleep 5
    else
        progress_log "Stopping Claude (${reason}) after ${elapsed}s; identity-confirming direct process tree wrapper pid=${CLAUDE_PID} descendants=${descendants:-none}"
    fi

    local termination_helper="${SCRIPT_DIR}/prepare-codex-takeover.py"
    if [ -n "${PYTHON_CMD:-}" ] && [ -f "$termination_helper" ] && \
       [ -f "$CLAUDE_IDENTITY_FILE" ]; then
        if "$PYTHON_CMD" "$termination_helper" terminate-process \
            --identity "$CLAUDE_IDENTITY_FILE" \
            --task-id "$TASK_ID" \
            --role claude \
            --terminate-timeout "$CLAUDE_CODE_TERMINATE_GRACE_SECONDS" \
            --reason "$reason" \
            --output "$PROCESS_TERMINATION_FILE" >/dev/null; then
            rm -f "$PID_FILE" "$CLAUDE_PID_FILE"
            CLAUDE_TERMINATION_CONFIRMED=1
            progress_log "Claude process tree confirmed inactive: receipt=${PROCESS_TERMINATION_FILE}"
            return 0
        fi
        CLAUDE_TERMINATION_FAILED=1
        progress_log "Claude process-tree termination failed closed: identity visibility or ownership could not be confirmed"
        return 1
    fi

    # Compatibility fallback for old standalone fixtures without the identity
    # helper. Refreshed workflow installs always use the identity-bound path.
    if kill -0 "$CLAUDE_PID" 2>/dev/null; then
        progress_log "Warning: identity helper unavailable; using compatibility tree freeze"
        kill -STOP "$CLAUDE_PID" $descendants 2>/dev/null || true
        kill -9 $descendants "$CLAUDE_PID" 2>/dev/null || true
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

refresh_runtime_session_identity() {
    # A resume failure deliberately starts a different Claude conversation.
    # Keep the runtime receipt authoritative so a later takeover cannot join
    # the prior session's counted result to this fresh-session result.
    local new_session_id="$1"
    local resume_failure_file="$2"
    [ -n "$PYTHON_CMD" ] || return 1
    "$PYTHON_CMD" - "$RUNTIME_JSON" "$new_session_id" "$resume_failure_file" <<'PYEOF'
import json, os, sys, tempfile

runtime_path, new_session_id, failure_path = sys.argv[1:]
with open(runtime_path, encoding="utf-8") as handle:
    value = json.load(handle)
if not isinstance(value, dict):
    raise ValueError("runtime receipt is not an object")
old_session_id = str(value.get("claude_session_id") or "")
if not old_session_id or not new_session_id or old_session_id == new_session_id:
    raise ValueError("session replacement identity is invalid")
try:
    generation = int(value.get("claude_session_generation", 0))
except (TypeError, ValueError) as exc:
    raise ValueError("runtime session generation is invalid") from exc
value["claude_session_id"] = new_session_id
value["claude_session_mode"] = "new"
value["claude_session_resume_status"] = "resume-failed-session-not-found-fresh-fallback"
value["claude_session_replaced_from"] = old_session_id
value["claude_session_resume_failure_receipt"] = (
    failure_path if os.path.isfile(failure_path) else None
)
value["claude_session_generation"] = generation + 1
directory = os.path.dirname(runtime_path) or "."
fd, temporary = tempfile.mkstemp(prefix="." + os.path.basename(runtime_path), dir=directory)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, runtime_path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PYEOF
}

run_claude() {
    # Common Claude CLI arguments shared by all invocation paths.
    # Tool profile arrays (_CLAUDE_TOOLS_ARGS, _CLAUDE_ALLOWED_ARGS) are
    # constructed before this function is called and applied identically
    # in direct/inherit and broker/bypass branches.
    # Length check avoids empty-array expansion error under set -u.
    local claude_base_args=(-p --permission-mode acceptEdits --output-format json)
    local direct_group_prefix=()
    if [ "${OS:-}" != "Windows_NT" ] && command -v setsid >/dev/null 2>&1; then
        # Brokered calls already create a new session. Keep the compatibility
        # direct path equally reclaimable as one task-owned process group.
        direct_group_prefix=(setsid)
    fi
    if [ "$CLAUDE_SESSION_MODE_EFFECTIVE" = "resume" ] && [ -n "$CLAUDE_SESSION_ID" ]; then
        claude_base_args+=(--resume "$CLAUDE_SESSION_ID")
    elif [ -n "$CLAUDE_SESSION_ID" ]; then
        claude_base_args+=(--session-id "$CLAUDE_SESSION_ID")
    fi
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
            "${direct_group_prefix[@]}" "${_CLAUDE_SANDBOX_PREFIX[@]}" \
                claude "${claude_base_args[@]}" \
                < CLAUDE_PROMPT.md > "$RESULT_FILE" 2>"${STATUS_FILE}"
        else
            (
                unset HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY
                unset http_proxy https_proxy all_proxy no_proxy
                "${direct_group_prefix[@]}" "${_CLAUDE_SANDBOX_PREFIX[@]}" \
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
                "${_CLAUDE_SANDBOX_PREFIX[@]}" claude "${claude_base_args[@]}"
        else
            (
                unset HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY
                unset http_proxy https_proxy all_proxy no_proxy
                python3 "${SCRIPT_DIR}/model-call-broker.py" "${broker_args[@]}" -- \
                    "${_CLAUDE_SANDBOX_PREFIX[@]}" claude "${claude_base_args[@]}"
            )
        fi
    fi
    local command_status=$?
    if [ "$command_status" -ne 0 ] && \
       [ "$CLAUDE_SESSION_MODE_EFFECTIVE" = "resume" ] && \
       [ "$_CLAUDE_RESUME_FALLBACK_USED" -eq 0 ] && \
       grep -Eiq 'No conversation found|conversation.*not found|session.*not found' "$STATUS_FILE" 2>/dev/null; then
        _CLAUDE_RESUME_FALLBACK_USED=1
        local failed_session_id="$CLAUDE_SESSION_ID"
        local resume_failure_file="${WORKTREE_ROOT}/${TASK_ID}.session-resume-failure.json"
        if [ -n "$PYTHON_CMD" ]; then
            "$PYTHON_CMD" - "$resume_failure_file" "$TASK_ID" "$failed_session_id" \
                "$TASK_CARD" "$BASE_COMMIT" "$WORKTREE_START_COMMIT" "$WRITE_SCOPE_RECEIPT_FILE" <<'PYEOF'
import hashlib, json, os, sys, tempfile
path, task_id, session_id, task_card, base_commit, worktree_start_commit, write_scope = sys.argv[1:]
with open(task_card, "rb") as handle:
    task_card_sha256 = "sha256:" + hashlib.sha256(handle.read()).hexdigest()
value = {
    "schema_version": 1,
    "status": "resume-failed",
    "task_id": task_id,
    "session_id": session_id,
    "task_card_sha256": task_card_sha256,
    "source_base_commit": base_commit,
    "worktree_start_commit": worktree_start_commit,
    "write_scope_receipt": write_scope if os.path.isfile(write_scope) else None,
    "failure_category": "session-not-found",
    "counts_as_model_failure": False,
    "fresh_same_owner_retry_authorized": True,
}
directory = os.path.dirname(path) or "."
fd, temporary = tempfile.mkstemp(prefix="." + os.path.basename(path), dir=directory)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(value, handle, indent=2, sort_keys=True)
    handle.write("\n")
os.replace(temporary, path)
PYEOF
            CLAUDE_SESSION_ID="$("$PYTHON_CMD" - <<'PYEOF'
import uuid
print(uuid.uuid4())
PYEOF
)"
        else
            CLAUDE_SESSION_ID=""
        fi
        CLAUDE_SESSION_MODE_EFFECTIVE="new"
        CLAUDE_SESSION_RESUME_STATUS="resume-failed-session-not-found-fresh-fallback"
        if ! refresh_runtime_session_identity "$CLAUDE_SESSION_ID" "$resume_failure_file"; then
            progress_log "Claude resume fallback blocked: runtime session identity could not be refreshed"
            return 1
        fi
        : > "$RESULT_FILE"
        : > "$STATUS_FILE"
        progress_log "Claude resume failed: session-not-found; recorded ${resume_failure_file}; retrying once with same owner and fresh session"
        run_claude
        return $?
    fi
    return "$command_status"
}

CLAUDE_LAUNCHED=0
DISPATCH_FINALIZED=0
DISPATCH_EXIT_HANDLER_ACTIVE=0
CLAUDE_TERMINATION_CONFIRMED=0
CLAUDE_TERMINATION_FAILED=0

dispatch_exit_handler() {
    local original_status="${1:-1}"
    local final_status="$original_status"
    trap - EXIT HUP INT TERM
    if [ "$DISPATCH_EXIT_HANDLER_ACTIVE" -eq 1 ]; then
        exit "$final_status"
    fi
    DISPATCH_EXIT_HANDLER_ACTIVE=1

    if [ "$CLAUDE_LAUNCHED" -eq 0 ] && [ "$DISPATCH_FINALIZED" -ne 1 ] && \
       [ ! -s "${OUTCOME_FILE:-}" ] && [ -n "${PYTHON_CMD:-}" ]; then
        if [ ! -s "${STATUS_FILE:-}" ]; then
            printf '%s\n' \
                "Claude dispatch exited before Builder execution." \
                "failure_category=preflight-error" \
                "builder_started=false" > "$STATUS_FILE" 2>/dev/null || true
        fi
        if [ -f "${SCRIPT_DIR}/classify-claude-attempt.py" ]; then
            "$PYTHON_CMD" "${SCRIPT_DIR}/classify-claude-attempt.py" \
                --exit-code "$original_status" --outcome preflight_error --progress none \
                --direction unknown --error-text-file "$STATUS_FILE" \
                --retry-ordinal "${_RETRY_ORDINAL:-0}" \
                > "$ATTEMPT_CLASSIFICATION_FILE" 2>/dev/null || true
        fi
        "$PYTHON_CMD" - "$RESULT_FILE" "$OUTCOME_FILE" "$TASK_ID" \
            "$original_status" "$ATTEMPT_CLASSIFICATION_FILE" \
            "$DISPATCH_EXECUTION_ENV" "$CLAUDE_CODE_HOST_AUTHORITY" <<'PYEOF' 2>/dev/null || true
import json, os, sys, tempfile
result, outcome, task_id, exit_status, classification, requested_env, host_authority = sys.argv[1:]
common = {
    "schema_version": 1,
    "task_id": task_id,
    "dispatch_outcome": "preflight-blocked",
    "failure_category": "preflight-error",
    "exit_status": int(exit_status),
    "builder_started": False,
    "claude_first_satisfied": False,
    "workflow_execution_status": "failed-to-dispatch",
    "completion_state": "failed-to-dispatch",
    "worktree_created": True,
    "attempt_classification": classification if os.path.isfile(classification) else None,
    "host_requested": requested_env == "host",
    "host_authorized": host_authority == "1",
    "host_effective": False,
    "merge_authorized": False,
}
def write(path, value):
    directory = os.path.dirname(path) or "."
    fd, temporary = tempfile.mkstemp(prefix="." + os.path.basename(path), dir=directory)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)
write(result, common)
write(outcome, {
    **common,
    "dispatch_success": False,
    "artifact_valid": False,
    "report_consistency": "not-applicable",
    "validation_success": "not-run",
    "semantic_acceptance": "not-reviewed",
})
PYEOF
        if [ -n "${MONITOR_EVENT_LOG:-}" ]; then
            printf '%s\n' "event=terminal running=no terminal=yes exit_status=${original_status} dispatch_outcome=preflight-blocked failure_category=preflight-error" \
                >> "$MONITOR_EVENT_LOG" 2>/dev/null || true
        fi
    fi

    if [ "$CLAUDE_LAUNCHED" -eq 1 ] && [ "$DISPATCH_FINALIZED" -ne 1 ]; then
        set +e
        if [ "${EXTENSION_ADVISOR_STATE:-idle}" = "running" ] && \
           declare -F cancel_extension_advisor >/dev/null 2>&1; then
            cancel_extension_advisor "dispatcher-abnormal-exit-${original_status}"
        fi
        stop_claude "dispatcher-abnormal-exit-${original_status}" "unknown"
        local cleanup_status=$?
        set -e
        [ "$cleanup_status" -eq 0 ] || CLAUDE_TERMINATION_FAILED=1
        if [ -n "${PYTHON_CMD:-}" ]; then
            "$PYTHON_CMD" - "$ABNORMAL_EXIT_FILE" "$OUTCOME_FILE" "$TASK_ID" \
                "$original_status" "$cleanup_status" "$PROCESS_TERMINATION_FILE" <<'PYEOF' 2>/dev/null || true
import json, os, sys, tempfile
receipt, outcome, task_id, exit_status, cleanup_status, termination = sys.argv[1:]
value = {
    "schema_version": 1,
    "status": "terminal",
    "task_id": task_id,
    "dispatch_outcome": "dispatcher-abnormal-exit",
    "exit_status": int(exit_status),
    "process_cleanup_confirmed": cleanup_status == "0",
    "process_termination_receipt": termination if os.path.isfile(termination) else None,
    "merge_authorized": False,
}

def write(path, payload):
    directory = os.path.dirname(path) or "."
    fd, temporary = tempfile.mkstemp(prefix="." + os.path.basename(path), dir=directory)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)
write(receipt, value)
if not os.path.exists(outcome):
    write(outcome, {
        **value,
        "dispatch_success": False,
        "artifact_valid": False,
        "validation_success": "unknown",
        "semantic_acceptance": "not-reviewed",
        "completion_state": "interrupted",
    })
PYEOF
        fi
        if [ -n "${MONITOR_EVENT_LOG:-}" ]; then
            printf '%s\n' "event=terminal running=no terminal=yes exit_status=${original_status} dispatch_outcome=dispatcher-abnormal-exit process_cleanup_confirmed=$([ "$cleanup_status" -eq 0 ] && echo yes || echo no)" \
                >> "$MONITOR_EVENT_LOG" 2>/dev/null || true
        fi
        [ "$final_status" -ne 0 ] || final_status=70
    fi

    if [ "$DISPATCH_FINALIZED" -eq 1 ] || [ "$CLAUDE_TERMINATION_CONFIRMED" -eq 1 ]; then
        rm -f "${PID_FILE:-}" "${CLAUDE_PID_FILE:-}" "${CHECKER_PID_FILE:-}"
    fi
    rm -f "${DISPATCHER_PID_FILE:-}"
    [ -z "${_REVIEWED_CONTINUATION_LEASE_DIR:-}" ] || rm -rf "$_REVIEWED_CONTINUATION_LEASE_DIR"
    [ -z "${_RETRY_RESERVATION_DIR:-}" ] || rm -rf "$_RETRY_RESERVATION_DIR"
    [ -z "${_ADVISOR_CONTINUE_RESERVATION_DIR:-}" ] || rm -rf "$_ADVISOR_CONTINUE_RESERVATION_DIR"
    [ -z "${_MANAGED_RUNTIME_SOURCE:-}" ] || rm -rf "$_MANAGED_RUNTIME_SOURCE"
    [ -z "${_MANAGED_RUNTIME_TARGET:-}" ] || rmdir "$_MANAGED_RUNTIME_TARGET" 2>/dev/null || true
    exit "$final_status"
}

# Subsequent setup has a task identity and must leave a terminal receipt even
# when it fails before the Builder process exists.
trap 'dispatch_exit_handler $?' EXIT

if [ "$CLAUDE_CODE_VERBOSE" = "1" ]; then
    echo "Invoking Claude Code..."
    echo "Progress log: $PROGRESS_FILE"
    echo "Agent Wait (once): bash \"$MONITOR_SCRIPT\" wait \"$TASK_ID\" --until terminal"
fi
cd "$WORKTREE_DIR"

: > "$PROGRESS_FILE"
: > "$MONITOR_EVENT_LOG"
write_network_header

progress_log "Starting Claude Code: execution_profile=${CLAUDE_CODE_EXECUTION_PROFILE}, prompt_profile=${CLAUDE_CODE_PROMPT_PROFILE}, evidence_mode=${CLAUDE_CODE_EVIDENCE_MODE}, proxy_mode=${CLAUDE_CODE_PROXY_MODE}, route_source=${_ROUTE_SOURCE}, context_acquisition_timeout_seconds=${CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS}, active_execution_window_seconds=${CLAUDE_CODE_TIMEOUT_SECONDS}, hard_timeout_seconds=${CLAUDE_CODE_HARD_TIMEOUT_SECONDS}, heartbeat_seconds=${CLAUDE_CODE_HEARTBEAT_SECONDS}, no_output_timeout_seconds=${CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS}, network_monitor=${CLAUDE_CODE_NETWORK_MONITOR}, worktree_strategy=${_RUNTIME_STRATEGY:-$CLAUDE_CODE_WORKTREE_STRATEGY}, large_repo_mode=${CLAUDE_CODE_LARGE_REPO_MODE}, task_mode=${_PARSED_TASK_MODE:-unknown}, declared_task_mode=${_DECLARED_TASK_MODE:-unknown}, task_mode_normalized=${_TASK_MODE_NORMALIZED}, verbose=${CLAUDE_CODE_VERBOSE}, approval_convergence=${CLAUDE_CODE_APPROVAL_BLOCKED_CONVERGENCE}, worktree_progress=${CLAUDE_CODE_WORKTREE_PROGRESS}, builder_mode=${CLAUDE_CODE_BUILDER_MODE}, tool_profile=${CLAUDE_CODE_TOOL_PROFILE}, tool_profile_derivation=${_TOOL_PROFILE_DERIVATION}, tool_profile_supported=$([ "$_TOOL_PROFILE_SUPPORTED" -eq 1 ] && echo yes || echo no), task_validation_allowlist=$([ "$CLAUDE_CODE_TASK_VALIDATION_ALLOWLIST" -eq 1 ] && echo yes || echo no), external_integrations_allowed=${_EXTERNAL_INTEGRATIONS_ALLOWED}, strict_mcp_isolation=${_STRICT_MCP_ISOLATION}, mcp_config_paths=${_MCP_CONFIG_PATHS_EVIDENCE}, plugin_paths=${_PLUGIN_PATHS_EVIDENCE}, external_integration_rejection=${_EXTERNAL_INTEGRATION_REJECTION:-none}, first_progress_timeout=${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS}, first_progress_timeout_source=${_FIRST_PROGRESS_TIMEOUT_SOURCE}, first_progress_action=${CLAUDE_CODE_FIRST_PROGRESS_ACTION}, progress_extension_seconds=${CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS}, growing_progress_extension_seconds=${CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS}, growth_extension_policy=renewable-product-growth-until-hard-timeout, api_probe_mode=${CLAUDE_CODE_API_PROBE_MODE}, probe_environment=${CLAUDE_CODE_PROBE_ENVIRONMENT}, startup_probe_conclusion=${_STARTUP_PROBE_CONCLUSION:-not-run}"

# Freeze the model-facing helpers from the same managed package as this
# dispatcher. Historical/reviewed worktrees may contain older ai/* helpers and
# must never select the runtime implementation for a newer launcher.
_MANAGED_RUNTIME_SOURCE="$(mktemp -d "${_SYSTEM_TMP_ROOT%/}/aiwf-runtime-${TASK_ID}.XXXXXX")" || {
    echo "Error: could not create the task managed-runtime bundle." >&2
    echo "failure_category=workflow-runtime-mismatch" >&2
    exit 1
}
_MANAGED_RUNTIME_TARGET="${WORKTREE_DIR}/.aiwf-runtime"
_MANAGED_RUNTIME_MOUNT_ENABLED=0
if { [ "$_PARSED_TASK_MODE" = "builder" ] || [ "$_PARSED_TASK_MODE" = "checker-test" ]; } && \
   [ "$CLAUDE_CODE_TOOL_PROFILE" != "diagnostic" ] && \
   [ "$CLAUDE_CODE_WRITE_SCOPE_ENFORCEMENT" != "off" ]; then
    _MANAGED_RUNTIME_MOUNT_ENABLED=1
fi
for _runtime_helper_name in write-approved-file.py run-approved-validation.py; do
    if [ ! -f "${SCRIPT_DIR}/${_runtime_helper_name}" ]; then
        printf '%s\n' \
            "Claude dispatch blocked before Builder execution." \
            "failure_category=workflow-runtime-mismatch" \
            "missing_runtime_helper=${_runtime_helper_name}" \
            "builder_started=false" > "$STATUS_FILE"
        echo "Error: managed runtime helper is missing beside the dispatcher: ${_runtime_helper_name}" >&2
        echo "failure_category=workflow-runtime-mismatch" >&2
        exit 1
    fi
    cp "${SCRIPT_DIR}/${_runtime_helper_name}" "${_MANAGED_RUNTIME_SOURCE}/${_runtime_helper_name}"
    chmod 0444 "${_MANAGED_RUNTIME_SOURCE}/${_runtime_helper_name}" 2>/dev/null || true
done
_OBSERVED_EXACT_WRITE_PROTOCOL="$("$PYTHON_CMD" \
    "${_MANAGED_RUNTIME_SOURCE}/write-approved-file.py" --runtime-protocol 2>/dev/null || true)"
_OBSERVED_VALIDATION_PROTOCOL="$("$PYTHON_CMD" \
    "${_MANAGED_RUNTIME_SOURCE}/run-approved-validation.py" --runtime-protocol 2>/dev/null || true)"
if [ "$_OBSERVED_EXACT_WRITE_PROTOCOL" != "$_EXACT_WRITE_PROTOCOL" ] || \
   [ "$_OBSERVED_VALIDATION_PROTOCOL" != "$_VALIDATION_RUNNER_PROTOCOL" ]; then
    printf '%s\n' \
        "Claude dispatch blocked before Builder execution." \
        "failure_category=workflow-runtime-mismatch" \
        "expected_runtime_protocol=${_EXACT_WRITE_PROTOCOL}" \
        "observed_runtime_protocol=${_OBSERVED_EXACT_WRITE_PROTOCOL:-missing}" \
        "expected_validation_protocol=${_VALIDATION_RUNNER_PROTOCOL}" \
        "observed_validation_protocol=${_OBSERVED_VALIDATION_PROTOCOL:-missing}" \
        "builder_started=false" > "$STATUS_FILE"
    echo "Error: dispatcher and exact-writer runtime protocols do not match." >&2
    echo "failure_category=workflow-runtime-mismatch" >&2
    exit 1
fi
mkdir -p "$_MANAGED_RUNTIME_TARGET"
"$PYTHON_CMD" - "$MANAGED_RUNTIME_BUNDLE_FILE" "$RUNTIME_JSON" \
    "$TASK_ID" "$_MANAGED_RUNTIME_PROTOCOL" "$_EXACT_WRITE_PROTOCOL" \
    "$_VALIDATION_RUNNER_PROTOCOL" \
    "$_MANAGED_RUNTIME_SOURCE" "$_MANAGED_RUNTIME_TARGET" <<'PYEOF'
import hashlib, json, os, sys, tempfile

(output, runtime_path, task_id, bundle_protocol, writer_protocol, validation_protocol,
 source, target) = sys.argv[1:]
helpers = {}
for name in ("write-approved-file.py", "run-approved-validation.py"):
    path = os.path.join(source, name)
    with open(path, "rb") as handle:
        digest = "sha256:" + hashlib.sha256(handle.read()).hexdigest()
    helpers[name] = {
        "sha256": digest,
        "model_path": ".aiwf-runtime/" + name,
    }
value = {
    "schema_version": 1,
    "status": "ready",
    "task_id": task_id,
    "bundle_protocol": bundle_protocol,
    "exact_write_protocol": writer_protocol,
    "validation_runner_protocol": validation_protocol,
    "helper_source": "dispatcher-sibling-snapshot",
    "historical_worktree_helpers_used": False,
    "mount_mode": "read-only",
    "helpers": helpers,
}
directory = os.path.dirname(output) or "."
fd, temporary = tempfile.mkstemp(prefix=".managed-runtime-", dir=directory)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(value, handle, indent=2, sort_keys=True)
    handle.write("\n")
os.replace(temporary, output)
if os.path.isfile(runtime_path):
    runtime = json.load(open(runtime_path, encoding="utf-8"))
    runtime["managed_runtime_bundle"] = output
    runtime["managed_runtime_protocol"] = bundle_protocol
    runtime["historical_worktree_helpers_used"] = False
    fd, temporary = tempfile.mkstemp(
        prefix=".runtime-bundle-", dir=os.path.dirname(runtime_path)
    )
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(runtime, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, runtime_path)
PYEOF

# --- Tool profile argument construction ---
# Build arrays for --tools and --allowedTools based on resolved profile.
# These arrays are applied identically in all four Claude invocation paths
# (direct/inherit and broker/bypass).
_CLAUDE_TOOLS_ARGS=()
_CLAUDE_ALLOWED_ARGS=()
_TOOL_PROFILE_AVAILABLE_TOOLS=""
_TOOL_PROFILE_ALLOWLIST_COUNT=0
_CACHE_TOOL_SCHEMA_SHA256=""
_CACHE_LANE=""

if [ "$_TOOL_PROFILE_SUPPORTED" -eq 1 ] && [ "$CLAUDE_CODE_TOOL_PROFILE" != "default" ]; then
    case "$CLAUDE_CODE_TOOL_PROFILE" in
        editor-only)
            _CLAUDE_TOOLS_ARGS=(--tools "Read,Edit,Write,Grep,Glob")
            ;;
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
        editor-only)       _TOOL_PROFILE_AVAILABLE_TOOLS="Read,Edit,Write,Grep,Glob" ;;
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
    _VALIDATION_LAUNCHER=""
    if [[ "${_TOOL_PROFILE_AVAILABLE_TOOLS}" == *Bash* ]] && [ "$CLAUDE_CODE_TASK_VALIDATION_ALLOWLIST" = "1" ]; then
        _TASK_CARD_FILE="${WORKTREE_DIR}/CLAUDE_TASK_CARD.md"
        _VALIDATION_HELPER="${SCRIPT_DIR}/run-approved-validation.py"
        if [ ! -f "$_VALIDATION_HELPER" ]; then
            echo "Error: task validation allowlist requires run-approved-validation.py; refresh workflow runtime files." >&2
            echo "failure_category=workflow-runtime-mismatch" >&2
            exit 1
        fi
        if [ -f "$_TASK_CARD_FILE" ] && [ -n "$PYTHON_CMD" ] && [ -f "$_VALIDATION_HELPER" ]; then
            _VALIDATION_SUMMARY="$("$PYTHON_CMD" "$_VALIDATION_HELPER" audit \
                --task-card "$_TASK_CARD_FILE" 2>/dev/null || echo "")"
            if [ -n "$_VALIDATION_SUMMARY" ]; then
                _TOOL_PROFILE_ALLOWLIST_COUNT="$("$PYTHON_CMD" -c "import json,sys; print(json.loads(sys.argv[1]).get('accepted',0))" "$_VALIDATION_SUMMARY" 2>/dev/null || echo 0)"
                _TOOL_PROFILE_ALLOWLIST_UNSAFE="$("$PYTHON_CMD" -c "import json,sys; print(json.loads(sys.argv[1]).get('unsafe',0))" "$_VALIDATION_SUMMARY" 2>/dev/null || echo 0)"
                _TOOL_PROFILE_ALLOWLIST_OVERSIZED="$("$PYTHON_CMD" -c "import json,sys; print(json.loads(sys.argv[1]).get('oversized',0))" "$_VALIDATION_SUMMARY" 2>/dev/null || echo 0)"
                _TOOL_PROFILE_ALLOWLIST_OVERFLOW="$("$PYTHON_CMD" -c "import json,sys; print(json.loads(sys.argv[1]).get('overflow',0))" "$_VALIDATION_SUMMARY" 2>/dev/null || echo 0)"
                _VALIDATION_LAUNCHER="$("$PYTHON_CMD" -c "import json,sys; print(json.loads(sys.argv[1]).get('first_launcher') or '')" "$_VALIDATION_SUMMARY" 2>/dev/null || echo '')"
            fi
            if [ "$_TOOL_PROFILE_ALLOWLIST_COUNT" -gt 0 ]; then
                if [ "$_MANAGED_RUNTIME_MOUNT_ENABLED" -eq 1 ]; then
                    _VALIDATION_HELPER_REL=".aiwf-runtime/run-approved-validation.py"
                else
                    _VALIDATION_HELPER_REL="${SCRIPT_DIR}/run-approved-validation.py"
                fi
                _allow_parts+=("Bash(${PYTHON_CMD} ${_VALIDATION_HELPER_REL} run)")
            fi
        fi
    fi

    if [ "${_TOOL_PROFILE_ALLOWLIST_UNSAFE:-0}" -gt 0 ] || \
       [ "${_TOOL_PROFILE_ALLOWLIST_OVERSIZED:-0}" -gt 0 ] || \
       [ "${_TOOL_PROFILE_ALLOWLIST_OVERFLOW:-0}" -gt 0 ]; then
        echo "Error: task validation commands cannot be fully pre-authorized; refusing to start Claude." >&2
        echo "failure_category=validation-allowlist-preflight" >&2
        echo "allowlist_unsafe=${_TOOL_PROFILE_ALLOWLIST_UNSAFE:-0}" >&2
        echo "allowlist_oversized=${_TOOL_PROFILE_ALLOWLIST_OVERSIZED:-0}" >&2
        echo "allowlist_overflow=${_TOOL_PROFILE_ALLOWLIST_OVERFLOW:-0}" >&2
        echo "Use one shell-free command per validation entry or a workflow validation helper." >&2
        exit 1
    fi

    if [ ${#_allow_parts[@]} -gt 0 ]; then
        _CLAUDE_ALLOWED_ARGS=(--allowedTools "$(IFS=,; echo "${_allow_parts[*]}")")
    fi
fi

progress_log "Tool profile resolved: profile=${CLAUDE_CODE_TOOL_PROFILE}, derivation=${_TOOL_PROFILE_DERIVATION}, cli_flags_supported=$([ "$_TOOL_PROFILE_SUPPORTED" -eq 1 ] && echo yes || echo no), requested_tools=${_TOOL_PROFILE_AVAILABLE_TOOLS:-none}, runtime_tool_inventory_verified=no, allowlist_accepted=${_TOOL_PROFILE_ALLOWLIST_COUNT}, allowlist_unsafe=${_TOOL_PROFILE_ALLOWLIST_UNSAFE:-0}, allowlist_oversized=${_TOOL_PROFILE_ALLOWLIST_OVERSIZED:-0}, allowlist_overflow=${_TOOL_PROFILE_ALLOWLIST_OVERFLOW:-0}, allowlist_enabled=$([ "$CLAUDE_CODE_TASK_VALIDATION_ALLOWLIST" -eq 1 ] && echo yes || echo no), external_integrations_allowed=${_EXTERNAL_INTEGRATIONS_ALLOWED}, strict_mcp_isolation=${_STRICT_MCP_ISOLATION}, mcp_config_paths=${_MCP_CONFIG_PATHS_EVIDENCE}, plugin_paths=${_PLUGIN_PATHS_EVIDENCE}, external_integration_rejection=${_EXTERNAL_INTEGRATION_REJECTION:-none}"

# Enforce exact Write paths before Claude starts. Bubblewrap makes the whole
# host filesystem read-only inside the model process and remounts only declared
# product/control paths plus the task temp directory writable. This also blocks
# repository writes attempted through Bash, not only Edit/Write tool calls.
_CLAUDE_SANDBOX_PREFIX=()
_WRITE_SCOPE_EFFECTIVE="off"
_WRITE_SCOPE_STAGING_ROOT="${TASK_TMPDIR}/write-sandbox"
_CLAUDE_WRITER_INPUT_SOURCE="${TASK_TMPDIR}/writer-input"
_CLAUDE_WRITER_INPUT_TARGET="${WORKTREE_DIR}/.aiwf-write-staging"
_CLAUDE_SESSION_ENV_SOURCE="${TASK_TMPDIR}/claude-session-env"
_CLAUDE_SESSION_ENV_TARGET="${HOME:-}/.claude/session-env"
_CLAUDE_PROJECTS_SOURCE="${WORKTREE_ROOT}/.session-store/${_LINEAGE_ROOT_TASK_ID:-$TASK_ID}/projects"
_CLAUDE_PROJECTS_TARGET="${HOME:-}/.claude/projects"
# In non-sandbox compatibility mode Claude writes its ordinary per-user
# transcript store.  Observe only the current session there; required exact
# write scope instead binds a lineage-local store and uses that as the source.
_CLAUDE_PROJECTS_OBSERVATION_ROOT="${HOME:-}/.claude/projects"
_WRITE_SCOPE_SYNC_FAILED=0
_WRITING_RUNTIME_ROLE=0
if { [ "$_PARSED_TASK_MODE" = "builder" ] || [ "$_PARSED_TASK_MODE" = "checker-test" ]; } && \
   [ "$CLAUDE_CODE_TOOL_PROFILE" != "diagnostic" ]; then
    _WRITING_RUNTIME_ROLE=1
fi
if [ -n "${_REVIEWED_CONTINUATION_TASK_ID:-}" ]; then
    CLAUDE_CODE_WRITE_SCOPE_ENFORCEMENT="required"
fi
if [ "$_WRITING_RUNTIME_ROLE" -eq 1 ]; then
    if [ "$CLAUDE_CODE_WRITE_SCOPE_ENFORCEMENT" = "auto" ]; then
        CLAUDE_CODE_WRITE_SCOPE_ENFORCEMENT="required"
    fi
    if [ "$CLAUDE_CODE_WRITE_SCOPE_ENFORCEMENT" = "required" ]; then
        if ! command -v bwrap >/dev/null 2>&1; then
            echo "Error: required write-scope enforcement needs bubblewrap; refusing post-run-only scope auditing." >&2
            exit 1
        fi
        if [ -z "$PYTHON_CMD" ] || [ ! -f "${SCRIPT_DIR}/prepare-write-sandbox.py" ]; then
            echo "Error: required write-scope enforcement helper is unavailable." >&2
            exit 1
        fi
        _WRITE_SCOPE_ARGS=(
            --task-card "${WORKTREE_DIR}/TASK_CARD_FULL.md" \
            --worktree "$WORKTREE_DIR" \
            --output "$WRITE_SCOPE_RECEIPT_FILE" \
            --staging-root "$_WRITE_SCOPE_STAGING_ROOT" \
            --print-bindings
        )
        if [ -n "${_REVIEWED_CONTINUATION_APPROVAL:-}" ]; then
            while IFS= read -r _approved_write_path; do
                [ -n "$_approved_write_path" ] || continue
                _WRITE_SCOPE_ARGS+=(--allow-path "$_approved_write_path")
            done < <("$PYTHON_CMD" - "$_REVIEWED_CONTINUATION_APPROVAL" <<'PYEOF'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
for path in value.get("allow_new_write_paths", []):
    print(path)
PYEOF
)
        fi
        _WRITE_BIND_OUTPUT="$("$PYTHON_CMD" "${SCRIPT_DIR}/prepare-write-sandbox.py" \
            "${_WRITE_SCOPE_ARGS[@]}" 2>&1)" || {
                echo "Error: required write-scope enforcement could not be prepared: ${_WRITE_BIND_OUTPUT}" >&2
                exit 1
            }
        if [ -z "${HOME:-}" ] || [ ! -f "${SCRIPT_DIR}/write-approved-file.py" ]; then
            echo "Error: required write-scope runtime needs HOME and write-approved-file.py." >&2
            exit 1
        fi
        _WRITE_APPROVED_HELPER_REL=".aiwf-runtime/write-approved-file.py"
        # Invoke the Python helper through the interpreter.  Installed workflow
        # copies intentionally keep data/helper files at 0644, and WSL-backed
        # worktrees can mask that mode difference during local probes.
        _WRITE_APPROVED_HELPER_CMD=("$PYTHON_CMD" "$_WRITE_APPROVED_HELPER_REL")
        export AI_WORKFLOW_WRITE_SCOPE_RECEIPT="$WRITE_SCOPE_RECEIPT_FILE"
        mkdir -p "$_CLAUDE_SESSION_ENV_SOURCE" "$_CLAUDE_SESSION_ENV_TARGET" \
            "$_CLAUDE_PROJECTS_SOURCE" "$_CLAUDE_PROJECTS_TARGET" \
            "$_CLAUDE_WRITER_INPUT_SOURCE" "$_CLAUDE_WRITER_INPUT_TARGET" || {
            echo "Error: could not prepare Claude session environment/transcript write mounts." >&2
            echo "failure_category=write-sandbox-session-storage-unavailable" >&2
            exit 1
        }
        _CLAUDE_PROJECTS_OBSERVATION_ROOT="$_CLAUDE_PROJECTS_SOURCE"
        for _writer_input_name in CONTENT OLD_FRAGMENT NEW_FRAGMENT; do
            printf 'AIWF_WRITER_INPUT_V1\n' > "${_CLAUDE_WRITER_INPUT_SOURCE}/${_writer_input_name}"
        done
        _CLAUDE_SANDBOX_PREFIX=(
            bwrap --die-with-parent --ro-bind / /
            --dev-bind /dev /dev --proc /proc
            --bind "$TASK_TMPDIR" "$TASK_TMPDIR"
            --ro-bind "$_MANAGED_RUNTIME_SOURCE" "$_MANAGED_RUNTIME_TARGET"
            --bind "$_CLAUDE_SESSION_ENV_SOURCE" "$_CLAUDE_SESSION_ENV_TARGET"
            --bind "$_CLAUDE_PROJECTS_SOURCE" "$_CLAUDE_PROJECTS_TARGET"
            --bind "$_CLAUDE_WRITER_INPUT_SOURCE" "$_CLAUDE_WRITER_INPUT_TARGET"
            --chdir "$WORKTREE_DIR"
        )
        while IFS=$'\t' read -r _write_bind_source _write_bind_target; do
            [ -n "$_write_bind_source" ] || continue
            [ -n "$_write_bind_target" ] || continue
            _CLAUDE_SANDBOX_PREFIX+=(--bind "$_write_bind_source" "$_write_bind_target")
        done <<< "$_WRITE_BIND_OUTPUT"
        _CLAUDE_SANDBOX_PREFIX+=(--)
        if ! "${_CLAUDE_SANDBOX_PREFIX[@]}" sh -c \
            ': > "$HOME/.claude/session-env/.aiwf-probe" && : > "$HOME/.claude/projects/.aiwf-probe"'; then
            echo "Error: Claude session environment/transcript storage is not writable in the sandbox." >&2
            echo "failure_category=write-sandbox-session-storage-read-only" >&2
            exit 1
        fi
        rm -f "$_CLAUDE_SESSION_ENV_SOURCE/.aiwf-probe" "$_CLAUDE_PROJECTS_SOURCE/.aiwf-probe"
        if ! "${_CLAUDE_SANDBOX_PREFIX[@]}" sh -c \
            'printf probe > .aiwf-write-staging/.atomic-probe.tmp && mv .aiwf-write-staging/.atomic-probe.tmp .aiwf-write-staging/.atomic-probe && rm .aiwf-write-staging/.atomic-probe'; then
            echo "Error: task-local writer input directory does not support atomic Edit-style writes." >&2
            echo "failure_category=write-sandbox-writer-input-read-only" >&2
            exit 1
        fi
        _WRITE_SCOPE_PROBE_PATHS=("CLAUDE_PROGRESS.md")
        _WRITE_SCOPE_PRODUCT_PROBE_PATH="$("$PYTHON_CMD" - "$WRITE_SCOPE_RECEIPT_FILE" <<'PYEOF'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
controls = set(value.get("control_write_paths", []))
for binding in value.get("bindings", []):
    if (isinstance(binding, dict) and binding.get("kind") == "file"
            and binding.get("relative_path") not in controls):
        print(binding["relative_path"])
        break
PYEOF
)"
        if [ -n "$_WRITE_SCOPE_PRODUCT_PROBE_PATH" ]; then
            _WRITE_SCOPE_PROBE_PATHS+=("$_WRITE_SCOPE_PRODUCT_PROBE_PATH")
        fi
        for _write_probe_path in "${_WRITE_SCOPE_PROBE_PATHS[@]}"; do
            if ! "${_CLAUDE_SANDBOX_PREFIX[@]}" "${_WRITE_APPROVED_HELPER_CMD[@]}" \
                    --path "$_write_probe_path" --probe >/dev/null; then
                echo "Error: exact approved writer could not write its receipt-bound staging file: ${_write_probe_path}" >&2
                echo "failure_category=write-sandbox-approved-writer-unavailable" >&2
                exit 1
            fi
        done
        cat >> "${WORKTREE_DIR}/CLAUDE_PROMPT.md" <<EOF

--- EXACT APPROVED FILE WRITER ---
The repository parent directories are intentionally read-only. Built-in Edit
may fail because it creates a neighboring temporary file, and Write may be
absent in some Claude Code versions. The task-local
\`.aiwf-write-staging/\` directory has a writable parent specifically for
Edit/Write input preparation; it is outside product evidence and cannot widen
the receipt's target scope. The approved writer reads its immutable receipt
internally, so do not expand environment variables. For a new file, or an
existing path listed under \`Full file replacement paths\`, use Edit or Write
to place the complete bytes in \`.aiwf-write-staging/CONTENT\`, then run exactly:

\`${PYTHON_CMD} ${_WRITE_APPROVED_HELPER_REL} --path REPOSITORY_RELATIVE_PATH --source .aiwf-write-staging/CONTENT\`

For a narrow edit, place the exact old and new byte fragments in
\`.aiwf-write-staging/OLD_FRAGMENT\` and
\`.aiwf-write-staging/NEW_FRAGMENT\`, then use the unique-match mode:

\`${PYTHON_CMD} ${_WRITE_APPROVED_HELPER_REL} --path REPOSITORY_RELATIVE_PATH --replace-old-source .aiwf-write-staging/OLD_FRAGMENT --replace-new-source .aiwf-write-staging/NEW_FRAGMENT\`

If the runtime has neither Edit nor Write, the shell-expansion-free
\`--content-base64\` and \`--replace-old-base64 ... --replace-new-base64 ...\`
forms remain available. Do not run a separate encoding command.

Unique replacement fails without writing unless the old fragment occurs
exactly once. Before touching the mounted product file, the writer builds and
validates the complete candidate. Python candidates must parse and compile,
must preserve valid dataclass field ordering, and may not introduce duplicate
top-level definitions/imports or remove an import that remains referenced.
JSON and TOML candidates must parse. A
failed candidate leaves the previous checkpoint unchanged. Replacing more
than 75% of an existing file of at least 4 KiB is rejected unless that exact
path is explicitly authorized for full-file replacement.

Existing files default to unique-fragment replacement. Complete replacement of
an existing file is rejected unless the task card explicitly authorizes that
exact path; do not use complete replacement merely to work around Edit.

The receipt rejects every undeclared path. Do not use Edit after an atomic-temp
failure and do not create repository-local helper files.
EOF
        if [[ "${_TOOL_PROFILE_AVAILABLE_TOOLS}" == *Bash* ]] && [ "$_TOOL_PROFILE_SUPPORTED" -eq 1 ]; then
            _approved_writer_allow="Bash(${PYTHON_CMD} ${_WRITE_APPROVED_HELPER_REL} --path * --source .aiwf-write-staging/CONTENT)"
            _approved_fragment_writer_allow="Bash(${PYTHON_CMD} ${_WRITE_APPROVED_HELPER_REL} --path * --replace-old-source .aiwf-write-staging/OLD_FRAGMENT --replace-new-source .aiwf-write-staging/NEW_FRAGMENT)"
            _approved_base64_writer_allow="Bash(${PYTHON_CMD} ${_WRITE_APPROVED_HELPER_REL} --path * --content-base64 *)"
            _approved_base64_fragment_writer_allow="Bash(${PYTHON_CMD} ${_WRITE_APPROVED_HELPER_REL} --path * --replace-old-base64 * --replace-new-base64 *)"
            if [ ${#_CLAUDE_ALLOWED_ARGS[@]} -gt 0 ]; then
                _CLAUDE_ALLOWED_ARGS[1]="${_CLAUDE_ALLOWED_ARGS[1]},${_approved_writer_allow},${_approved_fragment_writer_allow},${_approved_base64_writer_allow},${_approved_base64_fragment_writer_allow}"
            else
                _CLAUDE_ALLOWED_ARGS=(--allowedTools "${_approved_writer_allow},${_approved_fragment_writer_allow},${_approved_base64_writer_allow},${_approved_base64_fragment_writer_allow}")
            fi
        fi
        _WRITE_SCOPE_EFFECTIVE="required"
    fi
fi

# Hash the final tool contract only after validation and exact-writer entries
# have been added. Task-specific values are supplied through environment-bound
# receipts, keeping the allowed-tools schema stable without widening writes.
if [ -n "$PYTHON_CMD" ]; then
    IFS=$'\t' read -r _CACHE_TOOL_SCHEMA_SHA256 _CACHE_LANE < <(
        "$PYTHON_CMD" - \
            "$CLAUDE_CODE_PROXY_MODE" "$CLAUDE_CODE_PROMPT_PROFILE" \
            "$CLAUDE_CODE_TOOL_PROFILE" "$CLAUDE_CODE_BUILDER_MODE" \
            "${_PARSED_TASK_MODE:-unknown}" "${_TOOL_PROFILE_AVAILABLE_TOOLS:-}" \
            "${_CLAUDE_ALLOWED_ARGS[*]:-}" <<'PYEOF'
import hashlib, json, sys

route, prompt_profile, tool_profile, builder_mode, task_mode, tools, allowed = sys.argv[1:]
tool_contract = json.dumps(
    {"profile": tool_profile, "tools": tools, "allowed": allowed},
    ensure_ascii=True, sort_keys=True, separators=(",", ":"),
).encode("utf-8")
lane_contract = json.dumps(
    {
        "route": route, "prompt_profile": prompt_profile,
        "tool_profile": tool_profile, "builder_mode": builder_mode,
        "task_mode": task_mode,
    },
    ensure_ascii=True, sort_keys=True, separators=(",", ":"),
).encode("utf-8")
print(
    "sha256:" + hashlib.sha256(tool_contract).hexdigest()
    + "\tcache-lane:" + hashlib.sha256(lane_contract).hexdigest()
)
PYEOF
    )
fi
progress_log "Write scope enforcement resolved: requested=${_WRITE_SCOPE_REQUESTED}, effective=${_WRITE_SCOPE_EFFECTIVE}, receipt=${WRITE_SCOPE_RECEIPT_FILE}"

sync_write_scope_staging() {
    [ "$_WRITE_SCOPE_EFFECTIVE" = "required" ] || return 0
    "$PYTHON_CMD" "${SCRIPT_DIR}/prepare-write-sandbox.py" \
        --sync-receipt "$WRITE_SCOPE_RECEIPT_FILE" >/dev/null
}

# Verify only the launcher/capability, never run the assigned test suite twice.
# This gives Claude concrete evidence that Python/pytest is executable in the
# dispatch environment without introducing product-side effects.
if [ -n "$PYTHON_CMD" ]; then
    _CAPABILITY_COMMAND="${_VALIDATION_LAUNCHER:-}"
    "$PYTHON_CMD" - "$VALIDATION_CAPABILITY_FILE" "$_CAPABILITY_COMMAND" \
        "${_TOOL_PROFILE_ALLOWLIST_COUNT:-0}" <<'PYEOF'
import json, os, shlex, shutil, subprocess, sys, tempfile
output, command, allowlisted = sys.argv[1:]
tokens = shlex.split(command) if command else []
launcher = tokens[0] if tokens else ""
resolved = shutil.which(launcher) if launcher else None
probe = "not-assigned"
exit_code = None
if resolved:
    argv = [resolved, "--version"]
    if os.path.basename(resolved).lower().startswith("python"):
        argv = [resolved, "-c", "import sys; print(sys.executable)"]
    try:
        result = subprocess.run(argv, capture_output=True, timeout=10)
        exit_code = result.returncode
        probe = "available" if result.returncode == 0 else "launcher-failed"
    except (OSError, subprocess.TimeoutExpired):
        probe = "launcher-failed"
value = {
    "schema_version": 1, "exact_command": None,
    "assigned_command_body_stored": False,
    "launcher": launcher or None, "resolved_launcher": resolved,
    "allowlisted_command_count": int(allowlisted),
    "capability_status": probe, "probe_exit_code": exit_code,
    "scope": "launcher-only-no-assigned-tests-executed",
}
directory = os.path.dirname(output) or "."
fd, temporary = tempfile.mkstemp(prefix=".validation-capability-", dir=directory)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(value, handle, indent=2, sort_keys=True); handle.write("\n")
os.replace(temporary, output)
PYEOF
    _CAPABILITY_STATUS="$($PYTHON_CMD -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["capability_status"])' "$VALIDATION_CAPABILITY_FILE" 2>/dev/null || echo unknown)"
    {
        echo ""
        echo "## Validation Capability Receipt"
        echo ""
        echo "- Launcher capability: ${_CAPABILITY_STATUS}"
        echo "- Exact assigned commands allowlisted: ${_TOOL_PROFILE_ALLOWLIST_COUNT:-0}"
        echo "- Receipt: ${VALIDATION_CAPABILITY_FILE}"
        if [ "${_TOOL_PROFILE_ALLOWLIST_COUNT:-0}" -gt 0 ]; then
            echo "- Permission decision: the task-card validation runner is pre-authorized through the Checker Bash allowlist."
            echo "- Execute \`${PYTHON_CMD} ${_VALIDATION_HELPER_REL} run\` without requesting another approval. It reads and runs only the shell-free commands frozen in this card."
            echo "- Report a sandbox/permission blocker only after that invocation is denied, including the original denial."
        fi
        echo "- Launcher probing does not execute the assigned tests; run them and report the real exit code."
    } >> "${WORKTREE_DIR}/CLAUDE_TASK_CARD.md"
    progress_log "Validation capability recorded: status=${_CAPABILITY_STATUS}, artifact=${VALIDATION_CAPABILITY_FILE}"
fi

write_preflight_failure_receipts() {
    local category="$1"
    local attempt_outcome="$2"
    local exit_status="$3"
    local missing_tools="${4:-}"
    if [ -f "${SCRIPT_DIR}/classify-claude-attempt.py" ]; then
        "$PYTHON_CMD" "${SCRIPT_DIR}/classify-claude-attempt.py" \
            --exit-code "$exit_status" --outcome "$attempt_outcome" --progress none \
            --direction unknown --error-text-file "$STATUS_FILE" \
            --retry-ordinal "${_RETRY_ORDINAL:-0}" \
            > "$ATTEMPT_CLASSIFICATION_FILE" 2>/dev/null || true
    fi
    "$PYTHON_CMD" - "$RESULT_FILE" "$OUTCOME_FILE" "$TASK_ID" "$category" \
        "$ATTEMPT_CLASSIFICATION_FILE" "$missing_tools" "$DISPATCH_EXECUTION_ENV" \
        "$CLAUDE_CODE_HOST_AUTHORITY" <<'PYEOF'
import json, os, sys, tempfile
result, outcome, task_id, category, classification, missing, requested_env, host_authority = sys.argv[1:]
host_requested = requested_env == "host"
host_authorized = host_authority == "1"
host_effective = host_requested and host_authorized
common = {
    "schema_version": 1,
    "task_id": task_id,
    "dispatch_outcome": "preflight-blocked",
    "failure_category": category,
    "builder_started": False,
    "claude_first_satisfied": False,
    "workflow_execution_status": "failed-to-dispatch",
    "completion_state": "failed-to-dispatch",
    "worktree_created": True,
    "attempt_classification": classification if os.path.isfile(classification) else None,
    "missing_runtime_tools": sorted(filter(None, missing.split(","))),
    "host_requested": host_requested,
    "host_authorized": host_authorized,
    "host_effective": host_effective,
    "merge_authorized": False,
}
def write(path, value):
    directory = os.path.dirname(path) or "."
    fd, temporary = tempfile.mkstemp(prefix="." + os.path.basename(path), dir=directory)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)
write(result, common)
write(outcome, {
    **common,
    "dispatch_success": False,
    "artifact_valid": False,
    "report_consistency": "not-applicable",
    "validation_success": "not-run",
    "semantic_acceptance": "not-reviewed",
})
PYEOF
}

# --- Unified interaction probe: startup phase ---
# Adaptive mode accepts a recent success bound to this repository, route,
# environment, and Claude executable. A missing/stale cache triggers one live
# probe. Later suspicious zero-output still triggers a live probe regardless.
_STARTUP_PROBE_CONCLUSION="not-run"
if [ "${_EARLY_STARTUP_PROBE_CONCLUSION:-not-run}" = "available" ]; then
    _STARTUP_PROBE_CONCLUSION="available"
    _STARTUP_PROBE_SOURCE="${_EARLY_STARTUP_PROBE_SOURCE:-early}"
    _LAST_PROBE_AUTHORITATIVE="yes"
    IFS=$'\t' read -r _LAST_TOOL_INVENTORY_VERIFIED _LAST_TOOL_INVENTORY < <(
        "$PYTHON_CMD" - "$STARTUP_INTERACTION_HEALTH_FILE" <<'PYEOF' 2>/dev/null || printf 'no\t\n'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
if "tool_inventory" in value:
    inventory = value.get("tool_inventory", [])
    verified = value.get("tool_inventory_verified") is True
else:
    successful = [p for p in value.get("interaction_probes", []) if p.get("success")]
    probe = successful[-1] if successful else {}
    inventory = probe.get("tool_inventory", [])
    verified = probe.get("tool_inventory_verified") is True
print(("yes" if verified else "no") + "\t" + ",".join(inventory))
PYEOF
    )
    if [ "$_STARTUP_PROBE_SOURCE" = "early-live" ]; then
        progress_log "Startup interaction probe: conclusion=available, phase=early-pre-worktree"
    fi
    progress_log "Startup API availability reused: conclusion=available, source=${_STARTUP_PROBE_SOURCE}, artifact=${STARTUP_INTERACTION_HEALTH_FILE}"
elif [ "$CLAUDE_CODE_API_PROBE_MODE" != "always" ] && \
   [ "$CLAUDE_CODE_STARTUP_PREFLIGHT_REQUIRED" = "1" ] && \
   load_cached_api_availability "$STARTUP_INTERACTION_HEALTH_FILE"; then
    _STARTUP_PROBE_CONCLUSION="available"
    _STARTUP_PROBE_SOURCE="cache"
    _LAST_PROBE_AUTHORITATIVE="yes"
    IFS=$'\t' read -r _LAST_TOOL_INVENTORY_VERIFIED _LAST_TOOL_INVENTORY < <(
        "$PYTHON_CMD" - "$STARTUP_INTERACTION_HEALTH_FILE" <<'PYEOF' 2>/dev/null || printf 'no\t\n'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
verified = value.get("tool_inventory_verified") is True
print(("yes" if verified else "no") + "\t" + ",".join(value.get("tool_inventory", [])))
PYEOF
    )
    progress_log "Startup API availability reused: conclusion=available, source=cache, ttl_seconds=${CLAUDE_CODE_API_AVAILABILITY_TTL_SECONDS}, artifact=${STARTUP_INTERACTION_HEALTH_FILE}"
elif [ "$CLAUDE_CODE_API_PROBE_MODE" = "always" ] || \
     [ "$CLAUDE_CODE_STARTUP_PREFLIGHT_REQUIRED" = "1" ]; then
    run_interaction_probe "startup" "$STARTUP_INTERACTION_HEALTH_FILE"
    _STARTUP_PROBE_CONCLUSION="$_LAST_PROBE_CONCLUSION"
    _STARTUP_PROBE_SOURCE="live"
    progress_log "Startup interaction probe: conclusion=${_STARTUP_PROBE_CONCLUSION}, required=${CLAUDE_CODE_STARTUP_PREFLIGHT_REQUIRED}"
fi

# Fail before the Builder call when the actual stream init inventory cannot
# satisfy the requested profile. Exact-write mode may replace missing Edit or
# Write only when Bash itself is present and receipt enforcement is active.
if [ "${_STARTUP_PROBE_CONCLUSION}" = "available" ] && \
   [ -n "${_TOOL_PROFILE_AVAILABLE_TOOLS:-}" ]; then
    _TOOL_INVENTORY_MISSING="$({
        "$PYTHON_CMD" - "$_TOOL_PROFILE_AVAILABLE_TOOLS" \
            "$_LAST_TOOL_INVENTORY_VERIFIED" "$_LAST_TOOL_INVENTORY" \
            "$_WRITE_SCOPE_EFFECTIVE" <<'PYEOF'
import sys
requested, verified, observed, write_scope = sys.argv[1:]
if verified != "yes":
    print("inventory-unverified")
    raise SystemExit
available = set(filter(None, observed.split(",")))
missing = set(filter(None, requested.split(","))) - available
if write_scope == "required" and "Bash" in available:
    missing.difference_update({"Edit", "Write"})
print(",".join(sorted(missing)))
PYEOF
    } 2>/dev/null)"
    if [ -n "$_TOOL_INVENTORY_MISSING" ]; then
        _RUNTIME_TOOL_INVENTORY_STATUS="mismatch:${_TOOL_INVENTORY_MISSING}"
        {
            echo "Claude dispatch preflight blocked before Builder execution."
            echo "failure_category=tool-capability-mismatch"
            echo "missing_runtime_tools=${_TOOL_INVENTORY_MISSING}"
            echo "builder_started=false"
        } > "$STATUS_FILE"
        write_preflight_failure_receipts \
            "tool-capability-mismatch" "preflight_error" 1 "$_TOOL_INVENTORY_MISSING"
        progress_log "Dispatch preflight blocked: category=tool-capability-mismatch, missing_runtime_tools=${_TOOL_INVENTORY_MISSING}, builder_started=no"
        monitor_event "event=terminal running=no terminal=yes exit_status=1 dispatch_outcome=preflight-blocked failure_category=tool-capability-mismatch"
        sync_write_scope_staging >/dev/null 2>&1 || true
        echo "Error: Claude runtime tool inventory does not satisfy the task profile." >&2
        echo "failure_category=tool-capability-mismatch" >&2
        echo "missing_runtime_tools=${_TOOL_INVENTORY_MISSING}" >&2
        exit 1
    fi
    _RUNTIME_TOOL_INVENTORY_STATUS="verified"
    if [ "$_WRITE_SCOPE_EFFECTIVE" = "required" ] && \
       { ! printf ',%s,' "$_LAST_TOOL_INVENTORY" | grep -Fq ',Edit,' || \
         ! printf ',%s,' "$_LAST_TOOL_INVENTORY" | grep -Fq ',Write,'; }; then
        _RUNTIME_TOOL_INVENTORY_STATUS="verified-exact-writer-fallback"
    fi
fi

if [ -n "$PYTHON_CMD" ] && [ -s "$RUNTIME_JSON" ]; then
    "$PYTHON_CMD" - "$RUNTIME_JSON" "$_LAST_TOOL_INVENTORY_VERIFIED" \
        "$_LAST_TOOL_INVENTORY" "$_RUNTIME_TOOL_INVENTORY_STATUS" \
        "$DISPATCH_EXECUTION_ENV" "$CLAUDE_CODE_HOST_AUTHORITY" \
        "$_STARTUP_PROBE_CONCLUSION" <<'PYEOF' 2>/dev/null || true
import json, os, sys, tempfile
path, verified, inventory, status, requested_env, authority, conclusion = sys.argv[1:]
value = json.load(open(path, encoding="utf-8"))
value["runtime_tool_inventory_verified"] = verified == "yes"
value["runtime_tool_inventory"] = sorted(set(filter(None, inventory.split(","))))
value["runtime_tool_inventory_status"] = status
value["host_requested"] = requested_env == "host"
value["host_authorized"] = authority == "1"
value["host_effective"] = requested_env == "host" and conclusion == "available"
fd, temporary = tempfile.mkstemp(prefix=".runtime-tools-", dir=os.path.dirname(path))
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(value, handle, indent=2, sort_keys=True)
    handle.write("\n")
os.replace(temporary, path)
PYEOF
fi

if [ "$CLAUDE_CODE_STARTUP_PREFLIGHT_REQUIRED" = "1" ] && \
   [ "${_STARTUP_PROBE_CONCLUSION}" != "available" ]; then
    _STARTUP_NEEDS_HOST_EXECUTION=0
    if [ "${_STARTUP_PROBE_CONCLUSION}" = "inconclusive-restricted-environment" ] && \
       [ "$CLAUDE_CODE_HOST_AUTHORITY" != "1" ]; then
        _STARTUP_NEEDS_HOST_EXECUTION=1
    fi
    _STARTUP_FAILURE_CATEGORY="$({
        "$PYTHON_CMD" - "$STARTUP_INTERACTION_HEALTH_FILE" <<'PYEOF'
import json, sys
try:
    value = json.load(open(sys.argv[1], encoding="utf-8"))
    probes = value.get("interaction_probes", [])
    print(probes[-1].get("failure_category", "interaction-unavailable") if probes else "interaction-unavailable")
except (OSError, ValueError, TypeError, IndexError):
    print("interaction-unavailable")
PYEOF
    } 2>/dev/null)"
    if [ "$_STARTUP_NEEDS_HOST_EXECUTION" -eq 1 ]; then
        _STARTUP_FAILURE_CATEGORY="sandbox-network-host-handoff"
        _STARTUP_OUTCOME="network_error"
    else
        case "$_STARTUP_FAILURE_CATEGORY" in
            workspace-not-trusted) _STARTUP_OUTCOME="approval_blocked" ;;
            transport|timeout) _STARTUP_OUTCOME="network_error" ;;
            *) _STARTUP_OUTCOME="preflight_error" ;;
        esac
    fi
    {
        echo "Claude dispatch preflight blocked before Builder execution."
        echo "failure_category=${_STARTUP_FAILURE_CATEGORY}"
        echo "interaction_conclusion=${_STARTUP_PROBE_CONCLUSION}"
        echo "worktree=${WORKTREE_DIR}"
        if [ "$_STARTUP_NEEDS_HOST_EXECUTION" -eq 1 ]; then
            echo "needs_host_execution=true"
            echo "host_handoff_required=true"
            echo "host_handoff_action=rerun-identical-dispatch-on-authorized-host-once"
            echo "host_retry_command_form=stable-cli"
            echo "host_retry_dirty_source_mode=${CLAUDE_CODE_DIRTY_SOURCE_MODE}"
            if [ -n "$CONTEXT_LEASE_OPTION" ]; then
                echo "host_retry_context_lease=$CONTEXT_LEASE_OPTION"
                echo "host_retry_continuation_kind=$CONTINUATION_KIND_OPTION"
            elif [ -n "${_REVIEWED_CONTINUATION_APPROVAL:-}" ]; then
                echo "host_retry_reviewed_continuation=${_REVIEWED_CONTINUATION_APPROVAL}"
            else
                echo "host_retry_task_id=${TASK_ID}"
            fi
        else
            echo "Resolve workspace trust, transport, or execution-environment access, then rerun the exact dispatch."
        fi
    } > "$STATUS_FILE"
    if [ -f "${SCRIPT_DIR}/classify-claude-attempt.py" ]; then
        "$PYTHON_CMD" "${SCRIPT_DIR}/classify-claude-attempt.py" \
            --exit-code 75 --outcome "$_STARTUP_OUTCOME" --progress none \
            --direction unknown --error-text-file "$STATUS_FILE" \
            --retry-ordinal "${_RETRY_ORDINAL:-0}" \
            > "$ATTEMPT_CLASSIFICATION_FILE" 2>/dev/null || true
    fi
    "$PYTHON_CMD" - "$RESULT_FILE" "$OUTCOME_FILE" "$TASK_ID" "$_STARTUP_FAILURE_CATEGORY" \
        "$STARTUP_INTERACTION_HEALTH_FILE" "$ATTEMPT_CLASSIFICATION_FILE" \
        "$_STARTUP_NEEDS_HOST_EXECUTION" \
        "${_REVIEWED_CONTINUATION_APPROVAL:-}" "$TASK_CARD" \
        "$CLAUDE_CODE_DIRTY_SOURCE_MODE" "$DISPATCH_EXECUTION_ENV" \
        "$CLAUDE_CODE_HOST_AUTHORITY" "${TOOL_PROFILE_OPTION:-}" \
        "$CONTEXT_LEASE_OPTION" "$CONTINUATION_KIND_OPTION" \
        "$FORCE_FRESH_SESSION_OPTION" "$REHYDRATE_FROM_OPTION" <<'PYEOF'
import json, os, sys
output, outcome, task_id, category, health, classification, needs_host, reviewed_approval, task_card, dirty_source_mode, requested_env, host_authority, tool_profile, context_lease, continuation_kind, force_fresh, rehydrate_from = sys.argv[1:]
needs_host_execution = needs_host == "1"
host_requested = requested_env == "host"
host_authorized = host_authority == "1"
host_effective = host_requested and host_authorized
host_retry_environment = None
host_retry_args = None
if needs_host_execution:
    # Keep the legacy environment receipt for backward compatibility, but
    # make the preferred retry a stable CLI shape that can match a narrow
    # persistent host-execution approval rule.
    host_retry_environment = {"CLAUDE_CODE_HOST_AUTHORITY": "1"}
    host_retry_args = [task_card, "--execution-env", "host"]
    if dirty_source_mode == "snapshot":
        host_retry_environment["CLAUDE_CODE_DIRTY_SOURCE_MODE"] = "snapshot"
        host_retry_args.extend(["--dirty-source-mode", "snapshot"])
    if tool_profile:
        host_retry_environment["CLAUDE_CODE_TOOL_PROFILE"] = tool_profile
        host_retry_args.extend(["--tool-profile", tool_profile])
    if context_lease:
        host_retry_args.extend([
            "--context-lease", context_lease,
            "--continuation-kind", continuation_kind,
        ])
        if force_fresh == "1":
            host_retry_args.append("--force-fresh-session")
        if rehydrate_from:
            host_retry_args.extend(["--rehydrate-from", rehydrate_from])
    elif reviewed_approval:
        host_retry_environment["CLAUDE_CODE_REVIEWED_CONTINUATION"] = reviewed_approval
        host_retry_args.extend(["--reviewed-continuation", reviewed_approval])
    else:
        host_retry_environment["CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID"] = task_id
        host_retry_args.extend(["--retry-in-place-task-id", task_id])
value = {
    "schema_version": 1,
    "task_id": task_id,
    "dispatch_outcome": "preflight-blocked",
    "failure_category": category,
    "builder_started": False,
    "claude_first_satisfied": False,
    "workflow_execution_status": "failed-to-dispatch",
    "completion_state": "failed-to-dispatch",
    "interaction_health": health,
    "attempt_classification": classification if os.path.exists(classification) else None,
    "needs_host_execution": needs_host_execution,
    "host_handoff_required": needs_host_execution,
    "host_handoff_action": (
        "rerun-identical-dispatch-on-authorized-host-once"
        if needs_host_execution else None
    ),
    "host_retry_environment": host_retry_environment,
    "host_retry_environment_legacy": needs_host_execution,
    "host_retry_args": host_retry_args,
    "host_retry_args_authoritative": needs_host_execution,
    "host_retry_command_form": "stable-cli" if needs_host_execution else None,
    "host_requested": host_requested,
    "host_authorized": host_authorized,
    "host_effective": host_effective,
    "merge_authorized": False,
}
with open(output, "w", encoding="utf-8") as handle:
    json.dump(value, handle, indent=2, sort_keys=True)
    handle.write("\n")
with open(outcome, "w", encoding="utf-8") as handle:
    json.dump({
        **value,
        "dispatch_success": False,
        "artifact_valid": False,
        "report_consistency": "not-applicable",
        "validation_success": "not-run",
        "semantic_acceptance": "not-reviewed",
    }, handle, indent=2, sort_keys=True)
    handle.write("\n")
PYEOF
    progress_log "Dispatch preflight blocked: category=${_STARTUP_FAILURE_CATEGORY}, conclusion=${_STARTUP_PROBE_CONCLUSION}, builder_started=no"
    monitor_event "event=terminal running=no terminal=yes exit_status=75 dispatch_outcome=preflight-blocked failure_category=${_STARTUP_FAILURE_CATEGORY}"
    # Remove mount-only placeholders for never-created product files before a
    # host retry inspects the worktree. Seeded control artifacts remain intact.
    sync_write_scope_staging >/dev/null 2>&1 || true
    echo "Error: Claude dispatch preflight failed (${_STARTUP_FAILURE_CATEGORY})." >&2
    echo "Evidence: ${STARTUP_INTERACTION_HEALTH_FILE}" >&2
    if [ "$_STARTUP_NEEDS_HOST_EXECUTION" -eq 1 ]; then
        echo "needs_host_execution=true" >&2
        echo "host_handoff_required=true" >&2
        printf 'host_retry_command=bash %q %q --execution-env host' "$0" "$TASK_CARD" >&2
        if [ "$CLAUDE_CODE_DIRTY_SOURCE_MODE" = "snapshot" ]; then
            printf ' --dirty-source-mode snapshot' >&2
        fi
        [ -z "${TOOL_PROFILE_OPTION:-}" ] || printf ' --tool-profile %q' "$TOOL_PROFILE_OPTION" >&2
        if [ -n "$CONTEXT_LEASE_OPTION" ]; then
            printf ' --context-lease %q --continuation-kind %q' \
                "$CONTEXT_LEASE_OPTION" "$CONTINUATION_KIND_OPTION" >&2
            [ "$FORCE_FRESH_SESSION_OPTION" -eq 0 ] || printf ' --force-fresh-session' >&2
            [ -z "$REHYDRATE_FROM_OPTION" ] || printf ' --rehydrate-from %q' "$REHYDRATE_FROM_OPTION" >&2
            printf '\n' >&2
        elif [ -n "${_REVIEWED_CONTINUATION_APPROVAL:-}" ]; then
            printf ' --reviewed-continuation %q\n' "$_REVIEWED_CONTINUATION_APPROVAL" >&2
        else
            printf ' --retry-in-place-task-id %q\n' "$TASK_ID" >&2
        fi
        echo "host_retry_limit=1" >&2
    fi
    exit 75
fi

# Freeze the approved product baseline before the child can write. Reviewed
# continuations therefore start from their accepted dirty state, while a very
# fast first child write still differs from this pre-launch digest.
if [ -z "$PYTHON_CMD" ] || [ ! -f "${SCRIPT_DIR}/worktree_state_hash.py" ] || \
   ! "$PYTHON_CMD" "${SCRIPT_DIR}/worktree_state_hash.py" \
       --worktree "$WORKTREE_DIR" --ignore-empty-untracked --json \
       --output "$PRODUCT_BASELINE_FILE"; then
    echo "Error: could not compute pre-launch product baseline digest." >&2
    exit 1
fi
DISPATCH_PRODUCT_BASELINE_DIGEST="$("$PYTHON_CMD" - "$PRODUCT_BASELINE_FILE" <<'PYEOF'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8")).get("product_hash", ""))
PYEOF
)"
if [ -z "$DISPATCH_PRODUCT_BASELINE_DIGEST" ]; then
    echo "Error: pre-launch canonical product baseline did not contain a hash." >&2
    exit 1
fi
"$PYTHON_CMD" - "$PRODUCT_BASELINE_FILE" "$TASK_ID" "$WORKTREE_DIR" \
    "${_REVIEWED_CONTINUATION_APPROVAL_ID:-}" <<'PYEOF'
import json, os, sys, tempfile
output, task_id, worktree, approval_id = sys.argv[1:]
value = json.load(open(output, encoding="utf-8"))
value.update({
    "task_id": task_id,
    "worktree": os.path.abspath(worktree),
    "content_digest": value["product_hash"],
    "reviewed_continuation_approval_id": approval_id or None,
    "first_progress_requires_relative_content_change": True,
})
directory = os.path.dirname(output)
fd, temporary = tempfile.mkstemp(prefix=".product-baseline-", dir=directory)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(value, handle, indent=2, sort_keys=True)
    handle.write("\n")
os.replace(temporary, output)
PYEOF

# A reviewed approval remains merely reserved through startup preflight.  Only
# consume it once the approved baseline is frozen and Builder launch is
# imminent.  An exit-75 host handoff therefore releases the lease via the EXIT
# trap and can replay the same hash/session/path-bound approval exactly once.
if [ -n "${_REVIEWED_CONTINUATION_LEASE_DIR:-}" ]; then
    if ! mkdir "$_REVIEWED_CONTINUATION_CONSUMED_DIR" 2>/dev/null; then
        echo "Error: reviewed-continuation approval became consumed before dispatch start." >&2
        exit 1
    fi
    printf '%s\n' "$$" > "${_REVIEWED_CONTINUATION_CONSUMED_DIR}/dispatcher.pid"
    printf '%s\n' "$_REVIEWED_CONTINUATION_APPROVAL" > "${_REVIEWED_CONTINUATION_CONSUMED_DIR}/approval.path"
    printf '%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date '+%Y-%m-%d %H:%M:%S')" > "${_REVIEWED_CONTINUATION_CONSUMED_DIR}/consumed-at"
    rm -rf "$_REVIEWED_CONTINUATION_LEASE_DIR"
    _REVIEWED_CONTINUATION_LEASE_DIR=""
fi

# One lifecycle owner handles normal cleanup and every catchable abnormal exit.
# SIGKILL cannot run user-space cleanup; its surviving identity receipt remains
# fail-closed evidence for the takeover path.
trap 'dispatch_exit_handler $?' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

set +e
run_claude &
CLAUDE_PID=$!
CLAUDE_LAUNCHED=1
echo "$CLAUDE_PID" > "$PID_FILE"
echo "$CLAUDE_PID" > "$CLAUDE_PID_FILE"
if [ -n "$PYTHON_CMD" ] && [ -f "${SCRIPT_DIR}/process-identity.py" ]; then
    "$PYTHON_CMD" "${SCRIPT_DIR}/process-identity.py" capture \
        --pid "$CLAUDE_PID" --task-id "$TASK_ID" --role claude \
        --output "$CLAUDE_IDENTITY_FILE" >/dev/null 2>&1 || true
fi
progress_log "Claude process started: pid=${CLAUDE_PID}"
monitor_event "event=started running=yes terminal=no"

START_EPOCH="$(date +%s)"
CLAUDE_TIMED_OUT=0
CLAUDE_NO_OUTPUT_TIMED_OUT=0
CLAUDE_APPROVAL_CONVERGED=0
CLAUDE_WRITE_BLOCKED_CONVERGED=0
CLAUDE_COMPLETION_CONVERGED=0
CLAUDE_FIRST_PROGRESS_TIMED_OUT=0
_APPROVAL_CONVERGENCE_COUNT=0
_WRITE_BLOCKER_CONVERGENCE_COUNT=0
_LAST_APPROVAL_FP=""
LAST_ACTIVITY_EPOCH="$START_EPOCH"
LAST_CONTROL_ACTIVITY_EPOCH="$START_EPOCH"
LAST_SESSION_ACTIVITY_EPOCH=0
SESSION_ACTIVITY_SECONDS_AGO=-1
PRODUCT_ACTIVITY_SECONDS_AGO=-1
ACTIVE_WINDOW_REMAINING_SECONDS=-1
HARD_TIMEOUT_REMAINING_SECONDS=-1
LAST_TOTAL_BYTES=0
LAST_WORKTREE_DIGEST="$DISPATCH_PRODUCT_BASELINE_DIGEST"
LAST_RESULT_STATUS_BYTES=0
LAST_REPORT_HASH="$(sha1sum "${WORKTREE_DIR}/CLAUDE_REPORT.md" 2>/dev/null | awk '{print $1}' || true)"
LAST_PROGRESS_SEMANTIC_HASH="$(progress_semantic_hash "${WORKTREE_DIR}/CLAUDE_PROGRESS.md")"
FIRST_PROGRESS_DETECTED=0
FIRST_PROGRESS_SIGNAL=""
FIRST_PROGRESS_ELAPSED_SECONDS=""
FIRST_WORKTREE_CHANGE_SECONDS=""
FIRST_PROGRESS_OBSERVATION_RECORDED=0
BLOCKER_RECORDED=0
BLOCKER_ACTIVITY_STATE=""
EDIT_READY_DETECTED=0
EDIT_READY_ELAPSED_SECONDS=""
EDIT_READY_GRACE_EXPIRED=0
LAST_PRODUCT_CHANGE_EPOCH=0
PRODUCT_IDLE_SECONDS=0
PRODUCT_IDLE_CONFIRMATION_COUNT=0
PRODUCT_IDLE_STOPPED=0
TAIL_TIMEOUT_STOPPED=0
EXECUTION_ACTIVITY_STATE="context-acquisition"
IMPLEMENTATION_COMPLETE_DETECTED=0
IMPLEMENTATION_COMPLETE_ELAPSED_SECONDS=""
COMPLETION_READY_DETECTED=0
COMPLETION_READY_ELAPSED_SECONDS=""
COMPLETION_EVIDENCE_ELAPSED_SECONDS=""
VALIDATION_STARTED_ELAPSED_SECONDS=""
VALIDATION_EVIDENCE_ACTIVE=0
_LAST_MONITOR_MATERIAL_DIGEST=""
_LAST_MONITOR_PRODUCT_DIGEST="$DISPATCH_PRODUCT_BASELINE_DIGEST"
_LAST_EMITTED_PHASE=""
_LAST_EMITTED_VALIDATION_COMMAND=""
_CONTINUATION_THRESHOLD_SECONDS=120
INITIAL_PROGRESS_HASH="$(sha1sum "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" 2>/dev/null | awk '{print $1}' || true)"
# --- Two-stage execution clock ---
# Context acquisition starts at process launch. The first role-specific
# substantive signal refreshes exactly one complete active-execution window.
# Product growth may renew the active window after that; the hard deadline
# always wins. Control/report activity never qualifies for renewal.
CONTEXT_ACQUISITION_DEADLINE=0
ACTIVE_EXECUTION_DEADLINE=0
ACTIVE_WINDOW_REFRESHED=0
HARD_TIMEOUT_DEADLINE=0
if [ "$CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS" -gt 0 ]; then
    CONTEXT_ACQUISITION_DEADLINE=$((START_EPOCH + CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS))
fi
if [ "$CLAUDE_CODE_HARD_TIMEOUT_SECONDS" -gt 0 ]; then
    HARD_TIMEOUT_DEADLINE=$((START_EPOCH + CLAUDE_CODE_HARD_TIMEOUT_SECONDS))
fi
# Compatibility names describe the post-active-window extension state; the
# same state is renewed only by further product-content growth.
TIMEOUT_EXTENSION_ACTIVE=0
TIMEOUT_EXTENSION_COUNT=0
TIMEOUT_EXTENSION_STARTED_EPOCH=0
TIMEOUT_EXTENSION_DEADLINE=0
TIMEOUT_EXTENSION_REASON=""
# Snapshot of progress indicators at extension start, for detecting further growth.
EXTENSION_START_WORKTREE_DIGEST=""
EXTENSION_START_REPORT_BYTES=0
EXTENSION_START_PROGRESS_BYTES=0
EXTENSION_START_REPORT_HASH=""
EXTENSION_START_PROGRESS_HASH=""
# Kept in evidence schemas for compatibility; this marks the first renewable
# product-growth window after the initial extension.
SECOND_EXTENSION_ACTIVE=0
SECOND_EXTENSION_STARTED_EPOCH=0
SECOND_EXTENSION_DEADLINE=0
SECOND_EXTENSION_REASON=""
SECOND_EXTENSION_START_WORKTREE_DIGEST=""
SECOND_EXTENSION_START_REPORT_BYTES=0
SECOND_EXTENSION_START_PROGRESS_BYTES=0
_LOOP_SLEEP_SECONDS="$CLAUDE_CODE_HEARTBEAT_SECONDS"
PRODUCT_STATE_SAMPLING_FAILED=0
EXTENSION_ADVISOR_STATE="idle"
EXTENSION_ADVISOR_PID=""
EXTENSION_ADVISOR_PROCESS_GROUP=0
EXTENSION_ADVISOR_ATTEMPTS=0
EXTENSION_ADVISOR_EVALUATION_ID=""
EXTENSION_ADVISOR_WINDOW_KIND=""
EXTENSION_ADVISOR_BASE_DIGEST=""
EXTENSION_ADVISOR_DECISION=""
EXTENSION_ADVISOR_CONFIDENCE=""
EXTENSION_ADVISOR_REASON=""
EXTENSION_ADVISOR_SUMMARY=""
EXTENSION_ADVISOR_ACTIVITY_EVIDENCE="false"
EXTENSION_ADVISOR_ACTIVITY_SIGNAL="unavailable"
EXTENSION_ADVISOR_ACTIVITY_ASSESSMENT="insufficient"
EXTENSION_ADVISOR_NEXT_EPOCH=0
EXTENSION_ADVISOR_LAST_STATUS="not-run"
EXTENSION_ADVISOR_WAITING_FOR_IDLE_RECORDED=0
EXTENSION_PENDING_ACTIVE=0
EXTENSION_PENDING_RECORDED=0

write_extension_advisor_receipt() {
    local status="$1"
    local reason="${2:-none}"
    local decision="${3:-}"
    local confidence="${4:-}"
    EXTENSION_ADVISOR_LAST_STATUS="$status"
    [ -n "$PYTHON_CMD" ] || return 0
    "$PYTHON_CMD" - "$EXTENSION_ADVISOR_RECEIPT_FILE" "$TASK_ID" \
        "$EXTENSION_ADVISOR_EVALUATION_ID" "$EXTENSION_ADVISOR_WINDOW_KIND" \
        "$status" "$reason" "$decision" "$confidence" \
        "$EXTENSION_ADVISOR_BASE_DIGEST" "${CURRENT_WORKTREE_DIGEST:-}" \
        "$EXTENSION_ADVISOR_ATTEMPTS" "$EXTENSION_CAPSULE_FILE" \
        "$EXTENSION_ADVISOR_OUTPUT_FILE" "${NOW_EPOCH:-0}" \
        "$EXTENSION_ADVISOR_ACTIVITY_EVIDENCE" "$EXTENSION_ADVISOR_ACTIVITY_SIGNAL" \
        "$EXTENSION_ADVISOR_ACTIVITY_ASSESSMENT" <<'PYEOF'
import hashlib, json, os, sys, tempfile

(path, task_id, evaluation_id, window_kind, status, reason, decision, confidence,
 baseline_digest, current_digest, attempts, capsule, output, sampled_at,
 activity_evidence, activity_signal, activity_assessment) = sys.argv[1:]

def digest(candidate):
    try:
        with open(candidate, "rb") as handle:
            return "sha256:" + hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return None

value = {
    "schema_version": 1,
    "task_id": task_id,
    "evaluation_id": evaluation_id or None,
    "window_kind": window_kind or None,
    "status": status,
    "reason": reason or None,
    "decision": decision or None,
    "confidence": confidence or None,
    "baseline_product_digest": baseline_digest or None,
    "current_product_digest": current_digest or None,
    "attempt": int(attempts or 0),
    "sampled_at_epoch": int(sampled_at or 0),
    "capsule": capsule,
    "capsule_sha256": digest(capsule),
    "spark_output_sha256": digest(output),
    "model_activity_evidence_available": activity_evidence == "true",
    "model_activity_signal": activity_signal or "unavailable",
    "spark_activity_assessment": activity_assessment or "insufficient",
    "spark_is_advisory": True,
    "interrupt_authorized_by_spark": False,
    "hard_timeout_still_authoritative": True,
}
history = []
try:
    with open(path, "r", encoding="utf-8") as handle:
        previous = json.load(handle)
    if isinstance(previous, dict) and isinstance(previous.get("events"), list):
        history = previous["events"][-31:]
except (OSError, ValueError, TypeError):
    pass
history.append({
    "evaluation_id": evaluation_id or None,
    "window_kind": window_kind or None,
    "status": status,
    "reason": reason or None,
    "decision": decision or None,
    "confidence": confidence or None,
    "model_activity_evidence_available": activity_evidence == "true",
    "model_activity_signal": activity_signal or "unavailable",
    "spark_activity_assessment": activity_assessment or "insufficient",
    "baseline_product_digest": baseline_digest or None,
    "current_product_digest": current_digest or None,
    "sampled_at_epoch": int(sampled_at or 0),
})
value["events"] = history[-32:]
directory = os.path.dirname(path) or "."
fd, temporary = tempfile.mkstemp(prefix=".extension-advisor-", dir=directory)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)
except BaseException:
    try:
        os.unlink(temporary)
    except OSError:
        pass
    raise
PYEOF
}

extension_advisor_available() {
    [ "$CLAUDE_CODE_TIMEOUT_ADVISOR" != "off" ] && \
    [ -n "$PYTHON_CMD" ] && \
    command -v timeout >/dev/null 2>&1 && \
    [ -f "${SCRIPT_DIR}/claude-extension-capsule.py" ] && \
    [ -f "${SCRIPT_DIR}/run-codex-spark.sh" ]
}

start_extension_advisor() {
    local now_epoch="$1"
    local window_kind="${2:-active-execution}"
    local window_deadline="${3:-$ACTIVE_EXECUTION_DEADLINE}"
    local advisor_use_setsid=0
    if ! extension_advisor_available; then
        EXTENSION_ADVISOR_STATE="unavailable"
        EXTENSION_ADVISOR_REASON="runtime-helper-unavailable"
        write_extension_advisor_receipt "unavailable" "$EXTENSION_ADVISOR_REASON"
        return 1
    fi
    if [ "$EXTENSION_ADVISOR_ATTEMPTS" -ge "$CLAUDE_CODE_TIMEOUT_ADVISOR_MAX_ATTEMPTS" ]; then
        EXTENSION_ADVISOR_STATE="exhausted"
        EXTENSION_ADVISOR_REASON="attempt-budget-exhausted"
        write_extension_advisor_receipt "exhausted" "$EXTENSION_ADVISOR_REASON"
        return 1
    fi

    EXTENSION_ADVISOR_ATTEMPTS=$((EXTENSION_ADVISOR_ATTEMPTS + 1))
    EXTENSION_ADVISOR_WINDOW_KIND="$window_kind"
    EXTENSION_ADVISOR_EVALUATION_ID="${TASK_ID}-${window_kind}-${EXTENSION_ADVISOR_ATTEMPTS}-${now_epoch}"
    EXTENSION_ADVISOR_BASE_DIGEST="$CURRENT_WORKTREE_DIGEST"
    EXTENSION_ADVISOR_DECISION=""
    EXTENSION_ADVISOR_CONFIDENCE=""
    EXTENSION_ADVISOR_REASON=""
    EXTENSION_ADVISOR_SUMMARY=""
    EXTENSION_ADVISOR_ACTIVITY_EVIDENCE="false"
    EXTENSION_ADVISOR_ACTIVITY_SIGNAL="unavailable"
    EXTENSION_ADVISOR_ACTIVITY_ASSESSMENT="insufficient"
    EXTENSION_ADVISOR_WAITING_FOR_IDLE_RECORDED=0
    : > "$EXTENSION_ADVISOR_OUTPUT_FILE"
    : > "$EXTENSION_ADVISOR_STDERR_FILE"
    if ! "$PYTHON_CMD" "${SCRIPT_DIR}/claude-extension-capsule.py" \
        --session-root "${_CLAUDE_PROJECTS_OBSERVATION_ROOT:-}" \
        --session-id "$CLAUDE_SESSION_ID" \
        --task-id "$TASK_ID" \
        --task-card "${WORKTREE_DIR}/TASK_CARD_FULL.md" \
        --product-state "$PRODUCT_LIVE_STATE_FILE" \
        --status-file "$STATUS_FILE" \
        --output "$EXTENSION_CAPSULE_FILE" \
        --evaluation-id "$EXTENSION_ADVISOR_EVALUATION_ID" \
        --window-kind "$window_kind" \
        --window-deadline "$window_deadline" \
        --hard-deadline "$HARD_TIMEOUT_DEADLINE" \
        --sampled-at "$now_epoch" \
        --last-product-change "$LAST_PRODUCT_CHANGE_EPOCH" \
        --last-session-activity "$LAST_SESSION_ACTIVITY_EPOCH" \
        --recent-activity-window "$CLAUDE_CODE_RECENT_ACTIVITY_WINDOW_SECONDS"; then
        EXTENSION_ADVISOR_STATE="failed"
        EXTENSION_ADVISOR_REASON="capsule-build-failed"
        EXTENSION_ADVISOR_NEXT_EPOCH=$((now_epoch + CLAUDE_CODE_TIMEOUT_ADVISOR_RETRY_SECONDS))
        write_extension_advisor_receipt "failed" "$EXTENSION_ADVISOR_REASON"
        return 1
    fi

    command -v setsid >/dev/null 2>&1 && advisor_use_setsid=1
    (
        cd "$WORKTREE_DIR"
        if [ "$advisor_use_setsid" -eq 1 ]; then
            exec setsid timeout "${CLAUDE_CODE_TIMEOUT_ADVISOR_CALL_TIMEOUT_SECONDS}s" \
                bash "${SCRIPT_DIR}/run-codex-spark.sh" \
                --brief-file "$EXTENSION_CAPSULE_FILE" \
                --mode monitor-triage --result-mode direct --diagnostics off --sandbox read-only \
                --execution-env "$DISPATCH_EXECUTION_ENV"
        fi
        exec timeout "${CLAUDE_CODE_TIMEOUT_ADVISOR_CALL_TIMEOUT_SECONDS}s" \
            bash "${SCRIPT_DIR}/run-codex-spark.sh" \
            --brief-file "$EXTENSION_CAPSULE_FILE" \
            --mode monitor-triage --result-mode direct --diagnostics off --sandbox read-only \
            --execution-env "$DISPATCH_EXECUTION_ENV"
    ) > "$EXTENSION_ADVISOR_OUTPUT_FILE" 2> "$EXTENSION_ADVISOR_STDERR_FILE" &
    EXTENSION_ADVISOR_PID=$!
    EXTENSION_ADVISOR_PROCESS_GROUP="$advisor_use_setsid"
    EXTENSION_ADVISOR_STATE="running"
    write_extension_advisor_receipt "running" "awaiting-spark-judgment"
    progress_log "Spark timeout evaluation started: window_kind=${window_kind}, evaluation_id=${EXTENSION_ADVISOR_EVALUATION_ID}, attempt=${EXTENSION_ADVISOR_ATTEMPTS}/${CLAUDE_CODE_TIMEOUT_ADVISOR_MAX_ATTEMPTS}, baseline_product_digest=${EXTENSION_ADVISOR_BASE_DIGEST}, claude_continues=yes"
    monitor_event "event=extension-evaluation-started running=yes terminal=no window_kind=${window_kind} evaluation_id=${EXTENSION_ADVISOR_EVALUATION_ID} attempt=${EXTENSION_ADVISOR_ATTEMPTS} window_deadline_epoch=${window_deadline} hard_deadline_epoch=${HARD_TIMEOUT_DEADLINE} claude_continues=yes"
}

cancel_extension_advisor() {
    local reason="$1"
    [ "$EXTENSION_ADVISOR_STATE" = "running" ] || return 0
    if kill -0 "$EXTENSION_ADVISOR_PID" 2>/dev/null; then
        if [ "$EXTENSION_ADVISOR_PROCESS_GROUP" -eq 1 ]; then
            kill -TERM -- "-${EXTENSION_ADVISOR_PID}" 2>/dev/null || true
        else
            kill -TERM "$EXTENSION_ADVISOR_PID" 2>/dev/null || true
        fi
        sleep 1
        if kill -0 "$EXTENSION_ADVISOR_PID" 2>/dev/null; then
            if [ "$EXTENSION_ADVISOR_PROCESS_GROUP" -eq 1 ]; then
                kill -KILL -- "-${EXTENSION_ADVISOR_PID}" 2>/dev/null || true
            else
                kill -KILL "$EXTENSION_ADVISOR_PID" 2>/dev/null || true
            fi
        fi
    fi
    wait "$EXTENSION_ADVISOR_PID" 2>/dev/null || true
    EXTENSION_ADVISOR_STATE="superseded"
    EXTENSION_ADVISOR_REASON="$reason"
    write_extension_advisor_receipt "superseded" "$reason"
}

collect_extension_advisor() {
    [ "$EXTENSION_ADVISOR_STATE" = "running" ] || return 0
    if kill -0 "$EXTENSION_ADVISOR_PID" 2>/dev/null; then
        return 0
    fi
    local advisor_status=0
    if wait "$EXTENSION_ADVISOR_PID" 2>/dev/null; then
        advisor_status=0
    else
        advisor_status=$?
    fi
    EXTENSION_ADVISOR_PID=""
    EXTENSION_ADVISOR_PROCESS_GROUP=0
    EXTENSION_ADVISOR_DECISION="$(awk -F= '$1=="decision" {v=$2} END {print v}' "$EXTENSION_ADVISOR_OUTPUT_FILE" 2>/dev/null || true)"
    EXTENSION_ADVISOR_CONFIDENCE="$(awk -F= '$1=="confidence" {v=$2} END {print v}' "$EXTENSION_ADVISOR_OUTPUT_FILE" 2>/dev/null || true)"
    EXTENSION_ADVISOR_REASON="$(awk -F= '$1=="reason_code" {v=$2} END {print substr(v,1,160)}' "$EXTENSION_ADVISOR_OUTPUT_FILE" 2>/dev/null || true)"
    EXTENSION_ADVISOR_SUMMARY="$(awk -F= '$1=="summary" {sub(/^[^=]*=/, ""); v=$0} END {print substr(v,1,240)}' "$EXTENSION_ADVISOR_OUTPUT_FILE" 2>/dev/null | tr '\r\n' '  ' || true)"
    local spark_status="$(awk -F= '$1=="spark_status" {v=$2} END {print v}' "$EXTENSION_ADVISOR_OUTPUT_FILE" 2>/dev/null || true)"
    local response_received="$(awk -F= '$1=="spark_model_response_received" {v=$2} END {print v}' "$EXTENSION_ADVISOR_OUTPUT_FILE" 2>/dev/null || true)"
    local activity_evidence="false"
    local activity_signal="unavailable"
    if [ -s "$EXTENSION_CAPSULE_FILE" ]; then
        IFS=$'\t' read -r activity_evidence activity_signal < <(
            "$PYTHON_CMD" - "$EXTENSION_CAPSULE_FILE" <<'PYEOF' 2>/dev/null || printf 'false\tunavailable\n'
import json, sys
try:
    value = json.load(open(sys.argv[1], encoding="utf-8"))
    summary = value.get("activity_summary") or {}
    evidence = value.get("activity_evidence_available") is True
    signal = str(summary.get("activity_signal", "unavailable"))
    print(("true" if evidence else "false") + "\t" + signal)
except (OSError, TypeError, ValueError):
    print("false\tunavailable")
PYEOF
        )
    fi
    EXTENSION_ADVISOR_ACTIVITY_EVIDENCE="$activity_evidence"
    EXTENSION_ADVISOR_ACTIVITY_SIGNAL="$activity_signal"
    EXTENSION_ADVISOR_ACTIVITY_ASSESSMENT="$(awk -F= '$1=="activity_assessment" {v=$2} END {print v}' "$EXTENSION_ADVISOR_OUTPUT_FILE" 2>/dev/null || true)"
    case "$EXTENSION_ADVISOR_DECISION" in continue|inspect|interrupt-candidate|uncertain) ;; *) EXTENSION_ADVISOR_DECISION="" ;; esac
    case "$EXTENSION_ADVISOR_CONFIDENCE" in high|medium|low) ;; *) EXTENSION_ADVISOR_CONFIDENCE="low" ;; esac
    case "$EXTENSION_ADVISOR_ACTIVITY_ASSESSMENT" in task-directed|unproductive|insufficient) ;; *) EXTENSION_ADVISOR_ACTIVITY_ASSESSMENT="insufficient" ;; esac
    if [ "$EXTENSION_ADVISOR_DECISION" = "continue" ] && \
       { [ "$activity_evidence" != "true" ] || [ "$EXTENSION_ADVISOR_ACTIVITY_ASSESSMENT" != "task-directed" ]; }; then
        EXTENSION_ADVISOR_DECISION="uncertain"
        EXTENSION_ADVISOR_CONFIDENCE="low"
        if [ "$activity_evidence" != "true" ]; then
            EXTENSION_ADVISOR_REASON="no-recent-model-or-tool-activity"
        else
            EXTENSION_ADVISOR_REASON="spark-missing-task-directed-activity-assessment"
        fi
    elif [ "$EXTENSION_ADVISOR_DECISION" = "interrupt-candidate" ] && \
         [ "$EXTENSION_ADVISOR_ACTIVITY_ASSESSMENT" != "unproductive" ]; then
        EXTENSION_ADVISOR_DECISION="uncertain"
        EXTENSION_ADVISOR_CONFIDENCE="low"
        EXTENSION_ADVISOR_REASON="spark-interrupt-without-unproductive-assessment"
    fi
    if [ "$advisor_status" -eq 0 ] && [ "$spark_status" = "success" ] && \
       [ "$response_received" = "yes" ] && [ -n "$EXTENSION_ADVISOR_DECISION" ]; then
        EXTENSION_ADVISOR_STATE="ready"
        write_extension_advisor_receipt "ready" "${EXTENSION_ADVISOR_REASON:-spark-judgment}" \
            "$EXTENSION_ADVISOR_DECISION" "$EXTENSION_ADVISOR_CONFIDENCE"
        progress_log "Spark timeout judgment ready: window_kind=${EXTENSION_ADVISOR_WINDOW_KIND}, evaluation_id=${EXTENSION_ADVISOR_EVALUATION_ID}, decision=${EXTENSION_ADVISOR_DECISION}, confidence=${EXTENSION_ADVISOR_CONFIDENCE}, reason=${EXTENSION_ADVISOR_REASON:-spark-judgment}, activity_evidence=${EXTENSION_ADVISOR_ACTIVITY_EVIDENCE}, activity_signal=${EXTENSION_ADVISOR_ACTIVITY_SIGNAL}, activity_assessment=${EXTENSION_ADVISOR_ACTIVITY_ASSESSMENT}, product_digest_bound=${EXTENSION_ADVISOR_BASE_DIGEST}"
        monitor_event "event=extension-evaluation-result running=yes terminal=no window_kind=${EXTENSION_ADVISOR_WINDOW_KIND} evaluation_id=${EXTENSION_ADVISOR_EVALUATION_ID} decision=${EXTENSION_ADVISOR_DECISION} confidence=${EXTENSION_ADVISOR_CONFIDENCE} reason=${EXTENSION_ADVISOR_REASON:-spark-judgment} activity_evidence=${EXTENSION_ADVISOR_ACTIVITY_EVIDENCE} activity_signal=${EXTENSION_ADVISOR_ACTIVITY_SIGNAL} activity_assessment=${EXTENSION_ADVISOR_ACTIVITY_ASSESSMENT} product_digest=${EXTENSION_ADVISOR_BASE_DIGEST}"
    else
        EXTENSION_ADVISOR_STATE="failed"
        EXTENSION_ADVISOR_REASON="spark-unavailable-or-invalid"
        EXTENSION_ADVISOR_NEXT_EPOCH=$((${NOW_EPOCH:-0} + CLAUDE_CODE_TIMEOUT_ADVISOR_RETRY_SECONDS))
        write_extension_advisor_receipt "failed" "$EXTENSION_ADVISOR_REASON"
        progress_log "Spark active-window evaluation produced no valid judgment: evaluation_id=${EXTENSION_ADVISOR_EVALUATION_ID}, exit_status=${advisor_status}, retry_after_epoch=${EXTENSION_ADVISOR_NEXT_EPOCH}, claude_continues=yes"
    fi
}

refresh_active_window_for_product_growth() {
    local now_epoch="$1"
    local previous_evaluation="$EXTENSION_ADVISOR_EVALUATION_ID"
    local refresh_reason="canonical_product_growth"
    local refresh_signal="canonical_product_growth"
    if [ "$EXTENSION_ADVISOR_STATE" = "running" ]; then
        cancel_extension_advisor "product-growth-during-evaluation"
        refresh_reason="product_growth_during_extension_evaluation"
        refresh_signal="product_growth_during_extension_evaluation"
    elif [ "$EXTENSION_PENDING_ACTIVE" -eq 1 ] || \
         [ "$EXTENSION_ADVISOR_STATE" = "ready" ] || \
         [ "$EXTENSION_ADVISOR_STATE" = "failed" ] || \
         [ "$EXTENSION_ADVISOR_STATE" = "unavailable" ] || \
         [ "$EXTENSION_ADVISOR_STATE" = "exhausted" ]; then
        EXTENSION_ADVISOR_STATE="superseded"
        EXTENSION_ADVISOR_REASON="product-growth-after-evaluation-snapshot"
        write_extension_advisor_receipt "superseded" "$EXTENSION_ADVISOR_REASON"
        refresh_reason="product_growth_during_extension_evaluation"
        refresh_signal="product_growth_during_extension_evaluation"
    fi
    ACTIVE_EXECUTION_DEADLINE=$((now_epoch + CLAUDE_CODE_TIMEOUT_SECONDS))
    if [ "$HARD_TIMEOUT_DEADLINE" -gt 0 ] && [ "$ACTIVE_EXECUTION_DEADLINE" -gt "$HARD_TIMEOUT_DEADLINE" ]; then
        ACTIVE_EXECUTION_DEADLINE="$HARD_TIMEOUT_DEADLINE"
    fi
    TIMEOUT_EXTENSION_DEADLINE="$ACTIVE_EXECUTION_DEADLINE"
    TIMEOUT_EXTENSION_REASON="$refresh_reason"
    EXTENSION_PENDING_ACTIVE=0
    EXTENSION_PENDING_RECORDED=0
    EXTENSION_ADVISOR_STATE="idle"
    EXTENSION_ADVISOR_ATTEMPTS=0
    EXTENSION_ADVISOR_NEXT_EPOCH=0
    EXTENSION_ADVISOR_WAITING_FOR_IDLE_RECORDED=0
    if [ "$refresh_reason" = "product_growth_during_extension_evaluation" ]; then
        progress_log "Spark evaluation superseded by real product growth: evaluation_id=${previous_evaluation:-none}, active_window_refreshed=yes, active_deadline_epoch=${ACTIVE_EXECUTION_DEADLINE}, hard_deadline_epoch=${HARD_TIMEOUT_DEADLINE}"
    else
        progress_log "Canonical product growth refreshed the active window: active_deadline_epoch=${ACTIVE_EXECUTION_DEADLINE}, hard_deadline_epoch=${HARD_TIMEOUT_DEADLINE}"
    fi
    monitor_event "event=active-window-refreshed running=yes terminal=no elapsed_seconds=${ELAPSED} signal=${refresh_signal} reason=${refresh_reason} product_delta_from_baseline=${PRODUCT_DELTA_FROM_BASELINE} worktree_changes=${WORKTREE_CHANGES} product_changes=${WORKTREE_CHANGES} last_product_change_epoch=${LAST_PRODUCT_CHANGE_EPOCH} active_window_seconds=${CLAUDE_CODE_TIMEOUT_SECONDS} active_window_remaining_seconds=$((ACTIVE_EXECUTION_DEADLINE > 0 ? ACTIVE_EXECUTION_DEADLINE - now_epoch : -1)) active_deadline_epoch=${ACTIVE_EXECUTION_DEADLINE} hard_deadline_epoch=${HARD_TIMEOUT_DEADLINE}"
}

apply_spark_confirmed_extension() {
    local now_epoch="$1"
    local window_kind="$2"
    local extension_seconds="$CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS"
    local extension_event="started"
    local extension_deadline=0
    if [ "$TIMEOUT_EXTENSION_ACTIVE" -eq 1 ]; then
        extension_seconds="$CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS"
        extension_event="renewed"
    fi
    [ "$extension_seconds" -gt 0 ] || return 1

    TIMEOUT_EXTENSION_ACTIVE=1
    TIMEOUT_EXTENSION_COUNT=$((TIMEOUT_EXTENSION_COUNT + 1))
    TIMEOUT_EXTENSION_STARTED_EPOCH="$now_epoch"
    extension_deadline=$((now_epoch + extension_seconds))
    if [ "$HARD_TIMEOUT_DEADLINE" -gt 0 ] && [ "$extension_deadline" -gt "$HARD_TIMEOUT_DEADLINE" ]; then
        extension_deadline="$HARD_TIMEOUT_DEADLINE"
    fi
    TIMEOUT_EXTENSION_DEADLINE="$extension_deadline"
    TIMEOUT_EXTENSION_REASON="spark_confirmed_active_on_plan"
    if [ "$window_kind" = "context-acquisition" ]; then
        CONTEXT_ACQUISITION_DEADLINE="$extension_deadline"
    else
        ACTIVE_EXECUTION_DEADLINE="$extension_deadline"
    fi
    EXTENSION_START_WORKTREE_DIGEST="$LAST_WORKTREE_DIGEST"
    EXTENSION_START_REPORT_BYTES="$REPORT_BYTES"
    EXTENSION_START_PROGRESS_BYTES="$CLAUDE_PROGRESS_BYTES"
    if [ "$TIMEOUT_EXTENSION_COUNT" -ge 2 ]; then
        SECOND_EXTENSION_ACTIVE=1
        SECOND_EXTENSION_STARTED_EPOCH="$now_epoch"
        SECOND_EXTENSION_DEADLINE="$extension_deadline"
        SECOND_EXTENSION_REASON="spark_confirmed_continued_activity"
    fi
    write_extension_advisor_receipt "applied-extend" "$TIMEOUT_EXTENSION_REASON" \
        "$EXTENSION_ADVISOR_DECISION" "$EXTENSION_ADVISOR_CONFIDENCE"
    if [ "$window_kind" = "active-execution" ]; then
        progress_log "Spark-confirmed active window extension: evaluation_id=${EXTENSION_ADVISOR_EVALUATION_ID}, ordinal=${TIMEOUT_EXTENSION_COUNT}, extension_seconds=${extension_seconds}, deadline_epoch=${extension_deadline}, hard_deadline_epoch=${HARD_TIMEOUT_DEADLINE}"
    else
        progress_log "Spark-confirmed context-acquisition extension: evaluation_id=${EXTENSION_ADVISOR_EVALUATION_ID}, ordinal=${TIMEOUT_EXTENSION_COUNT}, extension_seconds=${extension_seconds}, deadline_epoch=${extension_deadline}, hard_deadline_epoch=${HARD_TIMEOUT_DEADLINE}"
    fi
    monitor_event "event=active-window-extended running=yes terminal=no window_kind=${window_kind} elapsed_seconds=${ELAPSED} reason=${TIMEOUT_EXTENSION_REASON} extension_event=${extension_event} extension_ordinal=${TIMEOUT_EXTENSION_COUNT} extension_seconds=${extension_seconds} advisor_decision=${EXTENSION_ADVISOR_DECISION} advisor_confidence=${EXTENSION_ADVISOR_CONFIDENCE} active_window_remaining_seconds=$((extension_deadline - now_epoch)) active_deadline_epoch=${ACTIVE_EXECUTION_DEADLINE} context_deadline_epoch=${CONTEXT_ACQUISITION_DEADLINE} hard_deadline_epoch=${HARD_TIMEOUT_DEADLINE}"
    EXTENSION_PENDING_ACTIVE=0
    EXTENSION_PENDING_RECORDED=0
    EXTENSION_ADVISOR_STATE="idle"
    EXTENSION_ADVISOR_ATTEMPTS=0
    EXTENSION_ADVISOR_NEXT_EPOCH=0
    EXTENSION_ADVISOR_WAITING_FOR_IDLE_RECORDED=0
    return 0
}

if [ -n "$PYTHON_CMD" ]; then
    phase_event "exploring" ""
    _LAST_EMITTED_PHASE="exploring"
fi
while claude_is_running; do
    sleep "$_LOOP_SLEEP_SECONDS"
    NOW_EPOCH="$(date +%s)"
    ELAPSED=$((NOW_EPOCH - START_EPOCH))

    if ! claude_is_running; then
        break
    fi

    if ! sync_write_scope_staging; then
        _WRITE_SCOPE_SYNC_FAILED=1
        progress_log "Write scope staging synchronization failed; stopping task fail-closed"
        stop_claude "write scope staging synchronization failed" "$ELAPSED"
        break
    fi

    RESULT_BYTES="$(file_size "$RESULT_FILE")"
    STATUS_BYTES="$(file_size "$STATUS_FILE")"
    REPORT_BYTES="$(file_size "${WORKTREE_DIR}/CLAUDE_REPORT.md")"
    CLAUDE_PROGRESS_BYTES="$(file_size "${WORKTREE_DIR}/CLAUDE_PROGRESS.md")"
    CLAUDE_TASK_BYTES="$(file_size "${WORKTREE_DIR}/CLAUDE_TASK_CARD.md")"
    if [ -z "$PYTHON_CMD" ] || [ ! -f "${SCRIPT_DIR}/worktree_state_hash.py" ] || \
       ! "$PYTHON_CMD" "${SCRIPT_DIR}/worktree_state_hash.py" \
           --worktree "$WORKTREE_DIR" --ignore-empty-untracked --json \
           --baseline-state "$PRODUCT_BASELINE_FILE" \
           --output "$PRODUCT_LIVE_STATE_FILE"; then
        PRODUCT_STATE_SAMPLING_FAILED=1
        EXECUTION_ACTIVITY_STATE="runtime-evidence-error"
        progress_log "Canonical product-state sampling failed; stopping task fail-closed"
        stop_claude "canonical product-state sampling failed" "$ELAPSED"
        break
    fi
    IFS=$'\t' read -r WORKTREE_CHANGES CURRENT_WORKTREE_DIGEST < <(
        "$PYTHON_CMD" - "$PRODUCT_LIVE_STATE_FILE" <<'PYEOF'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
print("\t".join((
    str(value.get("incremental_product_change_count", "")),
    str(value.get("product_hash", "")),
)))
PYEOF
    )
    case "$WORKTREE_CHANGES" in
        ''|*[!0-9]*)
            PRODUCT_STATE_SAMPLING_FAILED=1
            EXECUTION_ACTIVITY_STATE="runtime-evidence-error"
            progress_log "Canonical product-state count was invalid; stopping task fail-closed"
            stop_claude "canonical product-state count invalid" "$ELAPSED"
            break
            ;;
    esac
    if [ -z "$CURRENT_WORKTREE_DIGEST" ]; then
        PRODUCT_STATE_SAMPLING_FAILED=1
        EXECUTION_ACTIVITY_STATE="runtime-evidence-error"
        progress_log "Canonical product-state digest was missing; stopping task fail-closed"
        stop_claude "canonical product-state digest missing" "$ELAPSED"
        break
    fi
    PRODUCT_DELTA_FROM_BASELINE=0
    if [ "$CURRENT_WORKTREE_DIGEST" != "$DISPATCH_PRODUCT_BASELINE_DIGEST" ]; then
        PRODUCT_DELTA_FROM_BASELINE=1
    fi
    TOTAL_BYTES=$((RESULT_BYTES + STATUS_BYTES + REPORT_BYTES + CLAUDE_PROGRESS_BYTES + CLAUDE_TASK_BYTES))
    RESULT_STATUS_BYTES=$((RESULT_BYTES + STATUS_BYTES))
    CURRENT_REPORT_HASH="$(sha1sum "${WORKTREE_DIR}/CLAUDE_REPORT.md" 2>/dev/null | awk '{print $1}' || true)"
    CURRENT_PROGRESS_SEMANTIC_HASH="$(progress_semantic_hash "${WORKTREE_DIR}/CLAUDE_PROGRESS.md")"
    RESULT_STATUS_CHANGED=0
    REPORT_CHANGED=0
    PROGRESS_SEMANTIC_CHANGED=0
    [ "$RESULT_STATUS_BYTES" -ne "$LAST_RESULT_STATUS_BYTES" ] && RESULT_STATUS_CHANGED=1
    [ "$CURRENT_REPORT_HASH" != "$LAST_REPORT_HASH" ] && REPORT_CHANGED=1
    [ "$CURRENT_PROGRESS_SEMANTIC_HASH" != "$LAST_PROGRESS_SEMANTIC_HASH" ] && PROGRESS_SEMANTIC_CHANGED=1
    if [ "$RESULT_STATUS_CHANGED" -eq 1 ] || [ "$REPORT_CHANGED" -eq 1 ] || \
       [ "$PROGRESS_SEMANTIC_CHANGED" -eq 1 ]; then
        LAST_CONTROL_ACTIVITY_EPOCH="$NOW_EPOCH"
    fi
    WORKTREE_CHANGED=0
    if [ "$CURRENT_WORKTREE_DIGEST" != "$LAST_WORKTREE_DIGEST" ]; then
        WORKTREE_CHANGED=1
        LAST_WORKTREE_DIGEST="$CURRENT_WORKTREE_DIGEST"
        if [ -z "$FIRST_WORKTREE_CHANGE_SECONDS" ]; then
            FIRST_WORKTREE_CHANGE_SECONDS="$ELAPSED"
        fi
        # The canonical digest excludes runtime controls, so every digest
        # transition is real product activity.  A transition back to the
        # approved baseline is still an edit and must reset the idle clock.
        LAST_PRODUCT_CHANGE_EPOCH="$NOW_EPOCH"
        PRODUCT_IDLE_CONFIRMATION_COUNT=0
    fi
    collect_extension_advisor
    # After the first durable delta, every later canonical product-content
    # change renews the complete active window. This closes the race between a
    # Spark snapshot and a concurrent Claude write; the hard deadline remains
    # authoritative. Control/report/session activity never enters this path.
    if [ "$WORKTREE_CHANGED" -eq 1 ] && [ "$FIRST_PROGRESS_DETECTED" -eq 1 ]; then
        refresh_active_window_for_product_growth "$NOW_EPOCH"
    fi
    if [ "$RESULT_STATUS_BYTES" -ne "$LAST_RESULT_STATUS_BYTES" ] || \
       [ "$CURRENT_REPORT_HASH" != "$LAST_REPORT_HASH" ] || \
       [ "$CURRENT_PROGRESS_SEMANTIC_HASH" != "$LAST_PROGRESS_SEMANTIC_HASH" ] || \
       [ "$WORKTREE_CHANGED" -eq 1 ]; then
        LAST_TOTAL_BYTES="$TOTAL_BYTES"
        LAST_RESULT_STATUS_BYTES="$RESULT_STATUS_BYTES"
        LAST_REPORT_HASH="$CURRENT_REPORT_HASH"
        LAST_PROGRESS_SEMANTIC_HASH="$CURRENT_PROGRESS_SEMANTIC_HASH"
        LAST_ACTIVITY_EPOCH="$NOW_EPOCH"
    fi
    QUIET_SECONDS=$((NOW_EPOCH - LAST_ACTIVITY_EPOCH))
    LAST_SESSION_ACTIVITY_EPOCH="$(observe_runtime_activity "$NOW_EPOCH" "$EXECUTION_ACTIVITY_STATE" 2>/dev/null || echo 0)"
    case "$LAST_SESSION_ACTIVITY_EPOCH" in ''|*[!0-9]*) LAST_SESSION_ACTIVITY_EPOCH=0 ;; esac
    SESSION_ACTIVITY_SECONDS_AGO=-1
    [ "$LAST_SESSION_ACTIVITY_EPOCH" -le 0 ] || SESSION_ACTIVITY_SECONDS_AGO=$((NOW_EPOCH - LAST_SESSION_ACTIVITY_EPOCH))
    PRODUCT_ACTIVITY_SECONDS_AGO=-1
    [ "$LAST_PRODUCT_CHANGE_EPOCH" -le 0 ] || PRODUCT_ACTIVITY_SECONDS_AGO=$((NOW_EPOCH - LAST_PRODUCT_CHANGE_EPOCH))
    ACTIVE_WINDOW_REMAINING_SECONDS=-1
    [ "$ACTIVE_EXECUTION_DEADLINE" -le 0 ] || ACTIVE_WINDOW_REMAINING_SECONDS=$((ACTIVE_EXECUTION_DEADLINE - NOW_EPOCH))
    [ "$ACTIVE_WINDOW_REMAINING_SECONDS" -ge 0 ] || [ "$ACTIVE_EXECUTION_DEADLINE" -le 0 ] || ACTIVE_WINDOW_REMAINING_SECONDS=0
    HARD_TIMEOUT_REMAINING_SECONDS=-1
    [ "$HARD_TIMEOUT_DEADLINE" -le 0 ] || HARD_TIMEOUT_REMAINING_SECONDS=$((HARD_TIMEOUT_DEADLINE - NOW_EPOCH))
    [ "$HARD_TIMEOUT_REMAINING_SECONDS" -ge 0 ] || [ "$HARD_TIMEOUT_DEADLINE" -le 0 ] || HARD_TIMEOUT_REMAINING_SECONDS=0
    NETWORK_SUMMARY="$(capture_network_snapshot "$CLAUDE_PID" "$ELAPSED" "$QUIET_SECONDS")"
    _EMIT_HEARTBEAT=0
    if [ "$CLAUDE_CODE_WORKTREE_PROGRESS" = "verbose" ] || \
       [ "$RESULT_STATUS_CHANGED" -eq 1 ] || [ "$REPORT_CHANGED" -eq 1 ] || \
       [ "$PROGRESS_SEMANTIC_CHANGED" -eq 1 ] || [ "$WORKTREE_CHANGED" -eq 1 ]; then
        _EMIT_HEARTBEAT=1
    elif { [ "$HARD_TIMEOUT_DEADLINE" -gt 0 ] && \
           [ $((HARD_TIMEOUT_DEADLINE - NOW_EPOCH)) -le "$CLAUDE_CODE_HEARTBEAT_SECONDS" ]; } || \
         { [ "$CONTEXT_ACQUISITION_DEADLINE" -gt 0 ] && [ "$FIRST_PROGRESS_DETECTED" -eq 0 ] && \
           [ $((CONTEXT_ACQUISITION_DEADLINE - NOW_EPOCH)) -le "$CLAUDE_CODE_HEARTBEAT_SECONDS" ]; }; then
        _EMIT_HEARTBEAT=1
    fi
    if [ "$_EMIT_HEARTBEAT" -eq 1 ]; then
        progress_log "Claude still running: pid=${CLAUDE_PID}, state_change_or_threshold=yes, elapsed_seconds=${ELAPSED}, quiet_seconds=${QUIET_SECONDS}, session_activity_seconds_ago=${SESSION_ACTIVITY_SECONDS_AGO}, product_activity_seconds_ago=${PRODUCT_ACTIVITY_SECONDS_AGO}, active_window_remaining_seconds=${ACTIVE_WINDOW_REMAINING_SECONDS}, hard_timeout_remaining_seconds=${HARD_TIMEOUT_REMAINING_SECONDS}, result_bytes=${RESULT_BYTES}, status_bytes=${STATUS_BYTES}, report_bytes=${REPORT_BYTES}, claude_progress_bytes=${CLAUDE_PROGRESS_BYTES}, claude_task_bytes=${CLAUDE_TASK_BYTES}, worktree_changes=${WORKTREE_CHANGES}, worktree_changed=${WORKTREE_CHANGED}, product_delta_from_baseline=${PRODUCT_DELTA_FROM_BASELINE}, first_progress_detected=${FIRST_PROGRESS_DETECTED}, edit_ready=${EDIT_READY_DETECTED}, execution_state=${EXECUTION_ACTIVITY_STATE}, product_idle_seconds=${PRODUCT_IDLE_SECONDS}, idle_confirmations=${PRODUCT_IDLE_CONFIRMATION_COUNT}, ${NETWORK_SUMMARY}"
    fi

    VALIDATION_EVIDENCE_ACTIVE=0
    # Stage markers are Claude-authored advisory evidence. They never stop the
    # process; Completion Ready means the child should flush its report/result
    # and exit voluntarily under the prompt contract.
    if [ -s "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" ] && \
       ! file_contains "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" "$SEEDED_PROGRESS_MARKER"; then
        if [ -z "$VALIDATION_STARTED_ELAPSED_SECONDS" ] && \
           grep -Eiq '^(<!--[[:space:]]*)?-?[[:space:]]*(Execution|Current) Phase:[[:space:]]*(validation|testing|checking|contract-validation)' "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" 2>/dev/null; then
            VALIDATION_STARTED_ELAPSED_SECONDS="$ELAPSED"
            progress_log "Claude execution phase observed: phase=validation, elapsed_seconds=${ELAPSED}"
        fi
        if [ -n "$VALIDATION_STARTED_ELAPSED_SECONDS" ] && \
           grep -Eiq '(validation|test|checker|command)[ _:-]*(started|running|executing)|Validation Command:[[:space:]]*[^[:space:]]' "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" 2>/dev/null; then
            VALIDATION_EVIDENCE_ACTIVE=1
        fi
        if [ "$EDIT_READY_DETECTED" -eq 0 ] && \
           [ "$_PARSED_TASK_MODE" != "checker-test" ] && \
           [ "$CLAUDE_CODE_BUILDER_MODE" != "solution-planning" ] && \
           grep -Eiq '(Execution|Current) Phase:[[:space:]]*(implementation|implementing|editing|writing|patching)' "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" 2>/dev/null && \
           grep -Eiq '^(-[[:space:]]*)?Context Acquisition Complete:[[:space:]]*yes([[:space:]]|$)' "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" 2>/dev/null && \
           grep -Eiq '^(-[[:space:]]*)?Planned First Write:[[:space:]]*[^[:space:]]' "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" 2>/dev/null; then
            EDIT_READY_DETECTED=1
            EDIT_READY_ELAPSED_SECONDS="$ELAPSED"
            EXECUTION_ACTIVITY_STATE="implementation-ready"
            progress_log "Claude editing readiness declared: elapsed_seconds=${ELAPSED}, durable_product_write=no, grace_seconds=${CLAUDE_CODE_EDIT_READY_GRACE_SECONDS}"
        fi
        if [ "$IMPLEMENTATION_COMPLETE_DETECTED" -eq 0 ] && \
           grep -Eiq '^-?[[:space:]]*Implementation Complete:[[:space:]]*yes([[:space:]]|$)' "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" 2>/dev/null; then
            IMPLEMENTATION_COMPLETE_DETECTED=1
            IMPLEMENTATION_COMPLETE_ELAPSED_SECONDS="$ELAPSED"
            progress_log "Claude implementation completion observed: elapsed_seconds=${ELAPSED}, action=await-assigned-tail-and-voluntary-exit"
        fi
        if [ "$COMPLETION_READY_DETECTED" -eq 0 ] && \
           grep -Eiq '^-?[[:space:]]*Completion Ready:[[:space:]]*yes([[:space:]]|$)' "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" 2>/dev/null; then
            COMPLETION_READY_DETECTED=1
            COMPLETION_READY_ELAPSED_SECONDS="$ELAPSED"
            progress_log "Claude completion-ready observed: elapsed_seconds=${ELAPSED}, interrupt_authorized=no, action=await-voluntary-exit"
        fi
    fi

    # Recompute current blocker evidence. Generic words such as `Blocker: none`
    # or a historical mention of "blocked" must not turn normal editing into a
    # blocked execution state.
    BLOCKER_RECORDED=0
    BLOCKER_ACTIVITY_STATE=""
    for _blocker_file in "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" "${WORKTREE_DIR}/CLAUDE_REPORT.md"; do
        [ -f "$_blocker_file" ] || continue
        file_contains "$_blocker_file" "$SEEDED_PROGRESS_MARKER|$SEEDED_REPORT_MARKER" && continue
        _BLOCKER_TEXT="$(
            grep -Ei '^-?[[:space:]]*Blocker:[[:space:]]*.+$|blocked by (approval|permission|sandbox|network|dependency|authentication)|workspace (is )?not trusted|unable to connect|waiting for (tool|command|process|approval|permission)' \
                "$_blocker_file" 2>/dev/null \
            | grep -Eiv 'Blocker:[[:space:]]*(none|no|n/?a|not blocked|none reported yet)([[:space:]]|$)' \
            || true
        )"
        [ -n "$_BLOCKER_TEXT" ] || continue
        BLOCKER_RECORDED=1
        if printf '%s\n' "$_BLOCKER_TEXT" | grep -Eiq 'approval|permission|sandbox|network|dependency|authentication|not trusted|unable to connect'; then
            BLOCKER_ACTIVITY_STATE="external-blocked"
        elif printf '%s\n' "$_BLOCKER_TEXT" | grep -Eiq 'waiting for (tool|command|process)'; then
            BLOCKER_ACTIVITY_STATE="waiting-tool"
        else
            BLOCKER_ACTIVITY_STATE="semantic-blocked"
        fi
        break
    done

    # Once Completion Ready is paired with role-specific durable evidence,
    # shorten sampling and allow one small flush window before deterministic
    # convergence. Builders require both their implementation-complete marker
    # and a product delta; a report alone can never terminate an editing run.
    _COMPLETION_READY_EVIDENCE=0
    _COMPLETION_READY_ROLE=""
    if [ "$COMPLETION_READY_DETECTED" -eq 1 ] && [ "$BLOCKER_RECORDED" -eq 0 ] && \
       valid_claude_report_file "${WORKTREE_DIR}/CLAUDE_REPORT.md"; then
        if [ "$CLAUDE_CODE_BUILDER_MODE" = "solution-planning" ] && \
           [ -s "${WORKTREE_DIR}/solution-contract.draft.json" ]; then
            _COMPLETION_READY_EVIDENCE=1
            _COMPLETION_READY_ROLE="planner"
        elif [ "$_PARSED_TASK_MODE" = "checker-test" ] && \
             { [ "$PRODUCT_DELTA_FROM_BASELINE" -eq 1 ] || [ -n "$VALIDATION_STARTED_ELAPSED_SECONDS" ]; }; then
            _COMPLETION_READY_EVIDENCE=1
            _COMPLETION_READY_ROLE="checker"
        elif [ "$_PARSED_TASK_MODE" = "builder" ] && \
             [ "$IMPLEMENTATION_COMPLETE_DETECTED" -eq 1 ] && \
             [ "$PRODUCT_DELTA_FROM_BASELINE" -eq 1 ]; then
            _COMPLETION_READY_EVIDENCE=1
            _COMPLETION_READY_ROLE="builder"
        fi
    fi
    if [ "$_COMPLETION_READY_EVIDENCE" -eq 1 ]; then
        if [ -z "$COMPLETION_EVIDENCE_ELAPSED_SECONDS" ]; then
            COMPLETION_EVIDENCE_ELAPSED_SECONDS="$ELAPSED"
            progress_log "Completion-ready durable evidence observed: role=${_COMPLETION_READY_ROLE}, flush_window_seconds=${CLAUDE_CODE_COMPLETION_READY_TIMEOUT_SECONDS}, sampling_seconds=$([ "$_LOOP_SLEEP_SECONDS" -gt 5 ] && echo 5 || echo "$_LOOP_SLEEP_SECONDS")"
        fi
        if [ "$_LOOP_SLEEP_SECONDS" -gt 5 ]; then
            _LOOP_SLEEP_SECONDS=5
        fi
        if [ "$CLAUDE_CODE_COMPLETION_READY_TIMEOUT_SECONDS" -eq 0 ] || \
           [ $((ELAPSED - COMPLETION_EVIDENCE_ELAPSED_SECONDS)) -ge "$CLAUDE_CODE_COMPLETION_READY_TIMEOUT_SECONDS" ]; then
            CLAUDE_COMPLETION_CONVERGED=1
            EXECUTION_ACTIVITY_STATE="completion-ready-converged"
            stop_claude "completion-ready durable evidence flush window complete" "$ELAPSED"
            break
        fi
    fi

    # --- First-substantive-progress detection ---
    # Builder reading/planning, acknowledgement, generic progress text, seeded
    # artifacts, and blockers are useful evidence but do not refresh the clock.
    # Builder execution requires an implementation diff. An explicitly named
    # editing phase is only readiness evidence. Checker execution may also start from an explicit validation
    # command/process marker. A valid Claude-owned report is substantive evidence.
    if [ "$FIRST_PROGRESS_DETECTED" -eq 0 ]; then
        _FP_SIGNAL=""
        if [ "$PRODUCT_DELTA_FROM_BASELINE" -eq 1 ]; then
            if [ "$_PARSED_TASK_MODE" = "checker-test" ]; then
                _FP_SIGNAL="checker_worktree_change"
            else
                _FP_SIGNAL="builder_worktree_change"
            fi
        fi
        if [ -z "$_FP_SIGNAL" ]; then
            if [ "$_PARSED_TASK_MODE" = "checker-test" ] && \
               [ "$_CHECKER_WRITES_TESTS" -eq 0 ] && \
               [ -s "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" ] && \
               ! file_contains "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" "$SEEDED_PROGRESS_MARKER" && \
               grep -Eiq '(validation|test|checker|command)[ _:-]*(started|running|executing)|Current Phase:[[:space:]]*(validation|testing|checking)' "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" 2>/dev/null; then
                _FP_SIGNAL="checker_validation_started"
            elif [ "$CLAUDE_CODE_BUILDER_MODE" = "solution-planning" ] && \
                 [ -s "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" ] && \
                 ! file_contains "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" "$SEEDED_PROGRESS_MARKER" && \
                 grep -Eiq '(Execution|Current) Phase:[[:space:]]*(contract-validation|complete)|Substantive progress:[[:space:]]*yes' "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" 2>/dev/null; then
                _FP_SIGNAL="solution_contract_substantive"
            fi
        fi
        if [ -z "$_FP_SIGNAL" ] && [ "$_PARSED_TASK_MODE" != "builder" ] && \
           valid_claude_report_file "${WORKTREE_DIR}/CLAUDE_REPORT.md"; then
            _FP_SIGNAL="valid_report"
        fi
        if [ -n "$_FP_SIGNAL" ]; then
            FIRST_PROGRESS_DETECTED=1
            FIRST_PROGRESS_SIGNAL="$_FP_SIGNAL"
            FIRST_PROGRESS_ELAPSED_SECONDS="$ELAPSED"
            if [ "$PRODUCT_DELTA_FROM_BASELINE" -eq 1 ] && \
               [ "$LAST_PRODUCT_CHANGE_EPOCH" -eq 0 ]; then
                # A very fast child may create its first product file before the
                # first sampling loop, so the initial digest already contains it.
                LAST_PRODUCT_CHANGE_EPOCH="$NOW_EPOCH"
            fi
            if [ "$CLAUDE_CODE_TIMEOUT_SECONDS" -gt 0 ]; then
                ACTIVE_EXECUTION_DEADLINE=$((NOW_EPOCH + CLAUDE_CODE_TIMEOUT_SECONDS))
                if [ "$HARD_TIMEOUT_DEADLINE" -gt 0 ] && [ "$ACTIVE_EXECUTION_DEADLINE" -gt "$HARD_TIMEOUT_DEADLINE" ]; then
                    ACTIVE_EXECUTION_DEADLINE="$HARD_TIMEOUT_DEADLINE"
                fi
            fi
            ACTIVE_WINDOW_REFRESHED=1
            progress_log "First substantive progress detected: signal=${_FP_SIGNAL}, first_progress_detected=1, elapsed_seconds=${ELAPSED}, active_window_refreshed=yes, active_deadline_epoch=${ACTIVE_EXECUTION_DEADLINE}, hard_deadline_epoch=${HARD_TIMEOUT_DEADLINE}"
            monitor_event "event=active-window-refreshed running=yes terminal=no elapsed_seconds=${ELAPSED} signal=${_FP_SIGNAL} product_delta_from_baseline=${PRODUCT_DELTA_FROM_BASELINE} worktree_changes=${WORKTREE_CHANGES} product_changes=${WORKTREE_CHANGES} last_product_change_epoch=${LAST_PRODUCT_CHANGE_EPOCH} active_window_seconds=${CLAUDE_CODE_TIMEOUT_SECONDS} active_window_remaining_seconds=$((ACTIVE_EXECUTION_DEADLINE > 0 ? ACTIVE_EXECUTION_DEADLINE - NOW_EPOCH : -1)) active_deadline_epoch=${ACTIVE_EXECUTION_DEADLINE} hard_deadline_epoch=${HARD_TIMEOUT_DEADLINE} activity_receipt=${ACTIVITY_OBSERVATION_FILE}"
            if [ "$EXTENSION_ADVISOR_WINDOW_KIND" = "context-acquisition" ] && \
               [ "$EXTENSION_ADVISOR_STATE" != "idle" ]; then
                if [ "$EXTENSION_ADVISOR_STATE" = "running" ]; then
                    cancel_extension_advisor "first-product-growth-during-context-evaluation"
                else
                    EXTENSION_ADVISOR_STATE="superseded"
                    EXTENSION_ADVISOR_REASON="first-product-growth-after-context-snapshot"
                    write_extension_advisor_receipt "superseded" "$EXTENSION_ADVISOR_REASON"
                fi
                EXTENSION_PENDING_ACTIVE=0
                EXTENSION_PENDING_RECORDED=0
                EXTENSION_ADVISOR_STATE="idle"
                EXTENSION_ADVISOR_ATTEMPTS=0
                EXTENSION_ADVISOR_NEXT_EPOCH=0
                progress_log "Context timeout evaluation superseded by first product progress: evaluation_id=${EXTENSION_ADVISOR_EVALUATION_ID:-none}, active_deadline_epoch=${ACTIVE_EXECUTION_DEADLINE}"
            fi
        elif [ "$CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS" -gt 0 ] && \
             [ "$ELAPSED" -ge "$CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS" ]; then
            if [ "$CLAUDE_CODE_FIRST_PROGRESS_ACTION" = "observe" ]; then
                # Observation mode: run probe at most once for attribution, record event, continue.
                # Only run the observation probe when probe_mode=always; failure-only
                # defers probing to confirmed zero-output at finalization.
                if [ "$_OBSERVATION_PROBE_RAN" -eq 0 ] && \
                   [ "$CLAUDE_CODE_API_PROBE_MODE" = "always" ] && \
                   [ "${_STARTUP_PROBE_CONCLUSION:-not-run}" != "available" ]; then
                    _OBSERVATION_PROBE_RAN=1
                    run_interaction_probe "observation" "$INTERACTION_HEALTH_FILE"
                    _OBSERVATION_PROBE_CONCLUSION="$_LAST_PROBE_CONCLUSION"
                    _OBSERVATION_PROBE_AUTHORITATIVE="$_LAST_PROBE_AUTHORITATIVE"
                    progress_log "First-progress observation probe: conclusion=${_OBSERVATION_PROBE_CONCLUSION}, artifact=${INTERACTION_HEALTH_FILE}"
                elif [ "$_OBSERVATION_PROBE_RAN" -eq 0 ] && \
                     [ "${_STARTUP_PROBE_CONCLUSION:-not-run}" = "available" ]; then
                    progress_log "First-progress observation probe skipped: startup probe already confirmed availability"
                fi
                if [ "$FIRST_PROGRESS_OBSERVATION_RECORDED" -eq 0 ]; then
                    FIRST_PROGRESS_OBSERVATION_RECORDED=1
                    progress_log "First-progress observation: no substantive progress within ${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS}s; continuing within context-acquisition window (action=${CLAUDE_CODE_FIRST_PROGRESS_ACTION}, probe_mode=${CLAUDE_CODE_API_PROBE_MODE})"
                fi
            elif [ "$CLAUDE_CODE_TIMEOUT_ADVISOR" = "off" ]; then
                # Compatibility stop mode. With the advisor enabled, this
                # boundary is handled below without interrupting Claude first.
                CLAUDE_FIRST_PROGRESS_TIMED_OUT=1
                stop_claude "first_progress_timeout after ${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS}s" "$ELAPSED"
                break
            elif [ "$FIRST_PROGRESS_OBSERVATION_RECORDED" -eq 0 ]; then
                FIRST_PROGRESS_OBSERVATION_RECORDED=1
                progress_log "First-progress boundary reached; Claude continues pending Spark timeout judgment: timeout_seconds=${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS}, advisor=${CLAUDE_CODE_TIMEOUT_ADVISOR}"
            fi
        fi
    fi

    # Editing readiness prevents immediate interruption but is not durable
    # execution evidence. It receives one short bridge window to produce a
    # real product delta; it never refreshes the full active window by itself.
    if [ "$EDIT_READY_DETECTED" -eq 1 ] && [ "$FIRST_PROGRESS_DETECTED" -eq 0 ] && \
       [ "$CLAUDE_CODE_EDIT_READY_GRACE_SECONDS" -gt 0 ] && \
       [ $((ELAPSED - EDIT_READY_ELAPSED_SECONDS)) -ge "$CLAUDE_CODE_EDIT_READY_GRACE_SECONDS" ] && \
       [ "$BLOCKER_RECORDED" -eq 0 ]; then
        EDIT_READY_GRACE_EXPIRED=1
        CLAUDE_TIMED_OUT=1
        TIMEOUT_EXTENSION_REASON="declared_editing_without_output"
        EXECUTION_ACTIVITY_STATE="declared-editing-without-output"
        stop_claude "editing readiness produced no durable product write within ${CLAUDE_CODE_EDIT_READY_GRACE_SECONDS}s" "$ELAPSED"
        break
    fi

    # After the first product delta, watch actual product-content digest rather
    # than file count. Validation, assigned tail work, and explicit blockers are
    # distinct phases where a quiet product diff is expected.
    if [ "$LAST_PRODUCT_CHANGE_EPOCH" -gt 0 ]; then
        PRODUCT_IDLE_SECONDS=$((NOW_EPOCH - LAST_PRODUCT_CHANGE_EPOCH))
        if [ "$VALIDATION_EVIDENCE_ACTIVE" -eq 1 ]; then
            EXECUTION_ACTIVITY_STATE="validation"
            PRODUCT_IDLE_CONFIRMATION_COUNT=0
        elif [ "$IMPLEMENTATION_COMPLETE_DETECTED" -eq 1 ] || [ "$COMPLETION_READY_DETECTED" -eq 1 ]; then
            EXECUTION_ACTIVITY_STATE="tail-work"
            PRODUCT_IDLE_CONFIRMATION_COUNT=0
        elif [ "$BLOCKER_RECORDED" -eq 1 ]; then
            EXECUTION_ACTIVITY_STATE="${BLOCKER_ACTIVITY_STATE:-semantic-blocked}"
            PRODUCT_IDLE_CONFIRMATION_COUNT=0
        elif [ "$CLAUDE_CODE_PRODUCT_IDLE_TIMEOUT_SECONDS" -gt 0 ] && \
             [ "$PRODUCT_IDLE_SECONDS" -ge "$CLAUDE_CODE_PRODUCT_IDLE_TIMEOUT_SECONDS" ]; then
            EXECUTION_ACTIVITY_STATE="implementation-idle"
            if [ "$PRODUCT_IDLE_CONFIRMATION_COUNT" -lt "$CLAUDE_CODE_PRODUCT_IDLE_CONFIRMATIONS" ]; then
                PRODUCT_IDLE_CONFIRMATION_COUNT=$((PRODUCT_IDLE_CONFIRMATION_COUNT + 1))
                progress_log "Product edit idle candidate: idle_seconds=${PRODUCT_IDLE_SECONDS}, confirmation=${PRODUCT_IDLE_CONFIRMATION_COUNT}/${CLAUDE_CODE_PRODUCT_IDLE_CONFIRMATIONS}, validation=no, tail=no, blocker=no, timeout_advisor=${CLAUDE_CODE_TIMEOUT_ADVISOR}"
            fi
            if [ "$PRODUCT_IDLE_CONFIRMATION_COUNT" -ge "$CLAUDE_CODE_PRODUCT_IDLE_CONFIRMATIONS" ]; then
                if [ "$CLAUDE_CODE_TIMEOUT_ADVISOR" = "off" ]; then
                    PRODUCT_IDLE_STOPPED=1
                    CLAUDE_TIMED_OUT=1
                    TIMEOUT_EXTENSION_REASON="product_idle_confirmed"
                    stop_claude "product content unchanged for ${PRODUCT_IDLE_SECONDS}s across ${PRODUCT_IDLE_CONFIRMATION_COUNT} confirmations" "$ELAPSED"
                    break
                fi
                EXECUTION_ACTIVITY_STATE="implementation-idle-candidate"
            fi
        else
            EXECUTION_ACTIVITY_STATE="implementation-active"
            PRODUCT_IDLE_CONFIRMATION_COUNT=0
        fi
    elif [ "$EDIT_READY_DETECTED" -eq 1 ]; then
        EXECUTION_ACTIVITY_STATE="implementation-ready"
    fi

    _NORMALIZED_PHASE="exploring"
    _CURRENT_VALIDATION_COMMAND=""
    if [ "$VALIDATION_EVIDENCE_ACTIVE" -eq 1 ]; then
        _NORMALIZED_PHASE="validating"
        _CURRENT_VALIDATION_COMMAND="$(
            sed -n -E 's/^[[:space:]-]*(Current )?Validation Command:[[:space:]]*//Ip' \
                "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" 2>/dev/null | head -1
        )"
    elif [ "$IMPLEMENTATION_COMPLETE_DETECTED" -eq 1 ] || \
         [ "$COMPLETION_READY_DETECTED" -eq 1 ]; then
        _NORMALIZED_PHASE="reporting"
    elif [ "$PRODUCT_DELTA_FROM_BASELINE" -eq 1 ] || [ "$EDIT_READY_DETECTED" -eq 1 ]; then
        _NORMALIZED_PHASE="editing"
    fi
    if [ -n "$PYTHON_CMD" ] && \
       { [ "$_NORMALIZED_PHASE" != "$_LAST_EMITTED_PHASE" ] || \
         [ "$_CURRENT_VALIDATION_COMMAND" != "$_LAST_EMITTED_VALIDATION_COMMAND" ]; }; then
        phase_event "$_NORMALIZED_PHASE" "$_CURRENT_VALIDATION_COMMAND"
        _LAST_EMITTED_PHASE="$_NORMALIZED_PHASE"
        _LAST_EMITTED_VALIDATION_COMMAND="$_CURRENT_VALIDATION_COMMAND"
        progress_log "Claude normalized progress: phase=${_NORMALIZED_PHASE}, current_validation_command=${_CURRENT_VALIDATION_COMMAND:-none}"
    fi

    # Bound report formatting independently from productive implementation.
    if [ "$IMPLEMENTATION_COMPLETE_DETECTED" -eq 1 ] && \
       [ "$CLAUDE_CODE_TAIL_TIMEOUT_SECONDS" -gt 0 ] && \
       [ $((ELAPSED - IMPLEMENTATION_COMPLETE_ELAPSED_SECONDS)) -ge "$CLAUDE_CODE_TAIL_TIMEOUT_SECONDS" ]; then
        TAIL_TIMEOUT_STOPPED=1
        CLAUDE_TIMED_OUT=1
        TIMEOUT_EXTENSION_REASON="tail_report_timeout"
        EXECUTION_ACTIVITY_STATE="tail-timeout"
        stop_claude "tail/report timeout after ${CLAUDE_CODE_TAIL_TIMEOUT_SECONDS}s" "$ELAPSED"
        break
    fi

    # The dispatcher already owns liveness, timeout, artifact, and worktree
    # sampling. Publish only material changes so an observing Codex can block on
    # this file instead of running ps/tail or starting a second polling watcher.
    _MONITOR_MATERIAL_DIGEST="${RESULT_BYTES}|${STATUS_BYTES}|${CURRENT_REPORT_HASH}|${CURRENT_PROGRESS_SEMANTIC_HASH}|${WORKTREE_CHANGES}|${CURRENT_WORKTREE_DIGEST}|${FIRST_PROGRESS_DETECTED}|${FIRST_PROGRESS_SIGNAL}|${EDIT_READY_DETECTED}|${EXECUTION_ACTIVITY_STATE}|${PRODUCT_IDLE_CONFIRMATION_COUNT}|${BLOCKER_RECORDED}|${IMPLEMENTATION_COMPLETE_DETECTED}|${COMPLETION_READY_DETECTED}|${VALIDATION_STARTED_ELAPSED_SECONDS}"
    if [ "$_MONITOR_MATERIAL_DIGEST" != "$_LAST_MONITOR_MATERIAL_DIGEST" ]; then
        if [ "$PRODUCT_DELTA_FROM_BASELINE" -eq 1 ] && \
           [ "$CURRENT_WORKTREE_DIGEST" != "$_LAST_MONITOR_PRODUCT_DIGEST" ]; then
            monitor_event "event=material-change running=yes terminal=no elapsed_seconds=${ELAPSED} quiet_seconds=${QUIET_SECONDS} session_activity_seconds_ago=${SESSION_ACTIVITY_SECONDS_AGO} product_activity_seconds_ago=${PRODUCT_ACTIVITY_SECONDS_AGO} last_product_change_epoch=${LAST_PRODUCT_CHANGE_EPOCH} active_window_remaining_seconds=${ACTIVE_WINDOW_REMAINING_SECONDS} hard_timeout_remaining_seconds=${HARD_TIMEOUT_REMAINING_SECONDS} activity_receipt=${ACTIVITY_OBSERVATION_FILE} result_bytes=${RESULT_BYTES} status_bytes=${STATUS_BYTES} report_bytes=${REPORT_BYTES} progress_bytes=${CLAUDE_PROGRESS_BYTES} worktree_changes=${WORKTREE_CHANGES} product_changes=${WORKTREE_CHANGES} product_delta_from_baseline=${PRODUCT_DELTA_FROM_BASELINE} first_progress=${FIRST_PROGRESS_DETECTED} first_progress_signal=${FIRST_PROGRESS_SIGNAL:-none} active_window_refreshed=${ACTIVE_WINDOW_REFRESHED} active_deadline_epoch=${ACTIVE_EXECUTION_DEADLINE} hard_deadline_epoch=${HARD_TIMEOUT_DEADLINE} edit_ready=${EDIT_READY_DETECTED} execution_state=${EXECUTION_ACTIVITY_STATE} product_idle_seconds=${PRODUCT_IDLE_SECONDS} idle_confirmations=${PRODUCT_IDLE_CONFIRMATION_COUNT} blocker=${BLOCKER_RECORDED} implementation_complete=${IMPLEMENTATION_COMPLETE_DETECTED} completion_ready=${COMPLETION_READY_DETECTED}"
        fi
        _LAST_MONITOR_MATERIAL_DIGEST="$_MONITOR_MATERIAL_DIGEST"
        _LAST_MONITOR_PRODUCT_DIGEST="$CURRENT_WORKTREE_DIGEST"
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
                if [ "$_APPROVAL_CONVERGENCE_COUNT" -ge "$CLAUDE_CODE_APPROVAL_CONVERGENCE_HEARTBEATS" ]; then
                    progress_log "Approval-blocked early convergence: stable for ${_APPROVAL_CONVERGENCE_COUNT} heartbeats after ${ELAPSED}s"
                    stop_claude "approval-blocked early convergence" "$ELAPSED"
                    CLAUDE_APPROVAL_CONVERGED=1
                    break
                fi
            else
                _APPROVAL_CONVERGENCE_COUNT=1
                _LAST_APPROVAL_FP="$_ABC_FP"
                if [ "$_APPROVAL_CONVERGENCE_COUNT" -ge "$CLAUDE_CODE_APPROVAL_CONVERGENCE_HEARTBEATS" ]; then
                    progress_log "Approval-blocked early convergence: stable for ${_APPROVAL_CONVERGENCE_COUNT} heartbeats after ${ELAPSED}s"
                    stop_claude "approval-blocked early convergence" "$ELAPSED"
                    CLAUDE_APPROVAL_CONVERGED=1
                    break
                fi
            fi
        else
            _APPROVAL_CONVERGENCE_COUNT=0
            _LAST_APPROVAL_FP=""
        fi
    fi

    # A writing task whose exact-writer command is rejected by the runtime
    # permission layer cannot create durable progress. Stop after two observed
    # confirmations instead of misclassifying a ten-minute wait as model
    # no-progress. A real product delta always wins and clears this candidate.
    if [ "${CLAUDE_CODE_APPROVAL_BLOCKED_CONVERGENCE:-1}" = "1" ] && \
       [ "$_WRITING_RUNTIME_ROLE" -eq 1 ] && \
       [ "$PRODUCT_DELTA_FROM_BASELINE" -eq 0 ] && \
       write_runtime_approval_blocker; then
        _WRITE_BLOCKER_CONVERGENCE_COUNT=$((_WRITE_BLOCKER_CONVERGENCE_COUNT + 1))
        if [ "$_WRITE_BLOCKER_CONVERGENCE_COUNT" -ge "$CLAUDE_CODE_APPROVAL_CONVERGENCE_HEARTBEATS" ]; then
            CLAUDE_WRITE_BLOCKED_CONVERGED=1
            EXECUTION_ACTIVITY_STATE="external-write-blocked"
            progress_log "Exact-writer approval blocker converged: confirmations=${_WRITE_BLOCKER_CONVERGENCE_COUNT}, elapsed_seconds=${ELAPSED}"
            stop_claude "exact-writer approval blocker" "$ELAPSED"
            break
        fi
    else
        _WRITE_BLOCKER_CONVERGENCE_COUNT=0
    fi

    # Start the bounded Spark evaluation shortly before either the initial
    # context-acquisition deadline or a later active-execution deadline.
    # Claude remains the writing owner. A failed/absent judgment never stops it.
    _ADVISOR_WINDOW_KIND=""
    _ADVISOR_WINDOW_DEADLINE=0
    if [ "$FIRST_PROGRESS_DETECTED" -eq 0 ] && [ "$EDIT_READY_DETECTED" -eq 0 ]; then
        _ADVISOR_WINDOW_KIND="context-acquisition"
        _ADVISOR_WINDOW_DEADLINE="$CONTEXT_ACQUISITION_DEADLINE"
    elif [ "$FIRST_PROGRESS_DETECTED" -eq 1 ]; then
        _ADVISOR_WINDOW_KIND="active-execution"
        _ADVISOR_WINDOW_DEADLINE="$ACTIVE_EXECUTION_DEADLINE"
    fi
    if [ "$CLAUDE_CODE_TIMEOUT_ADVISOR" != "off" ] && \
       [ "$_ADVISOR_WINDOW_DEADLINE" -gt 0 ] && \
       [ "$NOW_EPOCH" -ge $((_ADVISOR_WINDOW_DEADLINE - CLAUDE_CODE_TIMEOUT_ADVISOR_LEAD_SECONDS)) ] && \
       [ "$NOW_EPOCH" -lt "$_ADVISOR_WINDOW_DEADLINE" ] && \
       [ "$NOW_EPOCH" -ge "$EXTENSION_ADVISOR_NEXT_EPOCH" ]; then
        if [ "$EXTENSION_ADVISOR_STATE" = "idle" ] || \
           { [ "$EXTENSION_ADVISOR_STATE" = "failed" ] && \
             [ "$EXTENSION_ADVISOR_ATTEMPTS" -lt "$CLAUDE_CODE_TIMEOUT_ADVISOR_MAX_ATTEMPTS" ]; }; then
            start_extension_advisor "$NOW_EPOCH" "$_ADVISOR_WINDOW_KIND" "$_ADVISOR_WINDOW_DEADLINE" || true
        fi
    fi

    if [ "$HARD_TIMEOUT_DEADLINE" -gt 0 ] && [ "$NOW_EPOCH" -ge "$HARD_TIMEOUT_DEADLINE" ]; then
        CLAUDE_TIMED_OUT=1
        if [ "$EXTENSION_ADVISOR_STATE" = "running" ]; then
            cancel_extension_advisor "hard-timeout"
        fi
        stop_claude "hard runtime timeout" "$ELAPSED"
        break
    fi

    if [ "$FIRST_PROGRESS_DETECTED" -eq 0 ] && [ "$EDIT_READY_DETECTED" -eq 0 ]; then
        if [ "$CONTEXT_ACQUISITION_DEADLINE" -gt 0 ] && [ "$NOW_EPOCH" -ge "$CONTEXT_ACQUISITION_DEADLINE" ]; then
            if [ "$CLAUDE_CODE_TIMEOUT_ADVISOR" = "off" ]; then
                CLAUDE_TIMED_OUT=1
                TIMEOUT_EXTENSION_REASON="context_acquisition_expired"
                stop_claude "context acquisition timeout without substantive execution progress" "$ELAPSED"
                break
            fi
            EXTENSION_PENDING_ACTIVE=1
            EXECUTION_ACTIVITY_STATE="context-extension-pending"
            if [ "$EXTENSION_PENDING_RECORDED" -eq 0 ]; then
                EXTENSION_PENDING_RECORDED=1
                progress_log "Context-acquisition window elapsed; Claude continues while Spark judgment is pending: context_deadline_epoch=${CONTEXT_ACQUISITION_DEADLINE}, hard_deadline_epoch=${HARD_TIMEOUT_DEADLINE}, advisor_state=${EXTENSION_ADVISOR_STATE}"
                monitor_event "event=extension-evaluation-pending running=yes terminal=no window_kind=context-acquisition elapsed_seconds=${ELAPSED} advisor_state=${EXTENSION_ADVISOR_STATE} attempt=${EXTENSION_ADVISOR_ATTEMPTS} window_deadline_epoch=${CONTEXT_ACQUISITION_DEADLINE} hard_deadline_epoch=${HARD_TIMEOUT_DEADLINE} claude_continues=yes"
            fi
            if [ "$EXTENSION_ADVISOR_STATE" = "idle" ] || \
               { [ "$EXTENSION_ADVISOR_STATE" = "failed" ] && \
                 [ "$EXTENSION_ADVISOR_ATTEMPTS" -lt "$CLAUDE_CODE_TIMEOUT_ADVISOR_MAX_ATTEMPTS" ] && \
                 [ "$NOW_EPOCH" -ge "$EXTENSION_ADVISOR_NEXT_EPOCH" ]; }; then
                start_extension_advisor "$NOW_EPOCH" "context-acquisition" "$CONTEXT_ACQUISITION_DEADLINE" || true
            fi
            if [ "$EXTENSION_ADVISOR_STATE" = "ready" ]; then
                if [ "$EXTENSION_ADVISOR_DECISION" = "continue" ] && \
                   { [ "$EXTENSION_ADVISOR_CONFIDENCE" = "high" ] || \
                     [ "$EXTENSION_ADVISOR_CONFIDENCE" = "medium" ]; }; then
                    apply_spark_confirmed_extension "$NOW_EPOCH" "context-acquisition" || true
                elif [ "$EXTENSION_ADVISOR_DECISION" = "interrupt-candidate" ] && \
                     [ "$EXTENSION_ADVISOR_CONFIDENCE" = "high" ] && \
                     [ "$CURRENT_WORKTREE_DIGEST" = "$EXTENSION_ADVISOR_BASE_DIGEST" ]; then
                    CLAUDE_TIMED_OUT=1
                    TIMEOUT_EXTENSION_REASON="spark_stop_without_initial_product_progress"
                    write_extension_advisor_receipt "applied-stop" "$TIMEOUT_EXTENSION_REASON" \
                        "$EXTENSION_ADVISOR_DECISION" "$EXTENSION_ADVISOR_CONFIDENCE"
                    stop_claude "context acquisition timeout after Spark stop advice and no product progress" "$ELAPSED"
                    break
                else
                    EXTENSION_ADVISOR_REASON="spark-no-actionable-judgment"
                    EXTENSION_ADVISOR_NEXT_EPOCH=$((NOW_EPOCH + CLAUDE_CODE_TIMEOUT_ADVISOR_RETRY_SECONDS))
                    if [ "$EXTENSION_ADVISOR_ATTEMPTS" -ge "$CLAUDE_CODE_TIMEOUT_ADVISOR_MAX_ATTEMPTS" ]; then
                        EXTENSION_ADVISOR_STATE="exhausted"
                        write_extension_advisor_receipt "exhausted" "$EXTENSION_ADVISOR_REASON" \
                            "$EXTENSION_ADVISOR_DECISION" "$EXTENSION_ADVISOR_CONFIDENCE"
                    else
                        EXTENSION_ADVISOR_STATE="failed"
                        write_extension_advisor_receipt "no-judgment" "$EXTENSION_ADVISOR_REASON" \
                            "$EXTENSION_ADVISOR_DECISION" "$EXTENSION_ADVISOR_CONFIDENCE"
                    fi
                    progress_log "Spark context-acquisition judgment was not actionable; Claude continues: decision=${EXTENSION_ADVISOR_DECISION:-none}, confidence=${EXTENSION_ADVISOR_CONFIDENCE:-low}, retry_after_epoch=${EXTENSION_ADVISOR_NEXT_EPOCH}, hard_deadline_epoch=${HARD_TIMEOUT_DEADLINE}"
                fi
            fi
        fi
    elif [ "$ACTIVE_EXECUTION_DEADLINE" -gt 0 ] && [ "$NOW_EPOCH" -ge "$ACTIVE_EXECUTION_DEADLINE" ] && \
         [ "$CLAUDE_CODE_TIMEOUT_ADVISOR" != "off" ]; then
        EXTENSION_PENDING_ACTIVE=1
        EXECUTION_ACTIVITY_STATE="extension-pending"
        if [ "$EXTENSION_PENDING_RECORDED" -eq 0 ]; then
            EXTENSION_PENDING_RECORDED=1
            progress_log "Active window elapsed; Claude continues while Spark judgment is pending: active_deadline_epoch=${ACTIVE_EXECUTION_DEADLINE}, hard_deadline_epoch=${HARD_TIMEOUT_DEADLINE}, advisor_state=${EXTENSION_ADVISOR_STATE}"
            monitor_event "event=extension-evaluation-pending running=yes terminal=no elapsed_seconds=${ELAPSED} advisor_state=${EXTENSION_ADVISOR_STATE} attempt=${EXTENSION_ADVISOR_ATTEMPTS} active_deadline_epoch=${ACTIVE_EXECUTION_DEADLINE} hard_deadline_epoch=${HARD_TIMEOUT_DEADLINE} claude_continues=yes"
        fi

        if [ "$EXTENSION_ADVISOR_STATE" = "idle" ] || \
           { [ "$EXTENSION_ADVISOR_STATE" = "failed" ] && \
             [ "$EXTENSION_ADVISOR_ATTEMPTS" -lt "$CLAUDE_CODE_TIMEOUT_ADVISOR_MAX_ATTEMPTS" ] && \
             [ "$NOW_EPOCH" -ge "$EXTENSION_ADVISOR_NEXT_EPOCH" ]; }; then
            start_extension_advisor "$NOW_EPOCH" || true
        fi

        if [ "$EXTENSION_ADVISOR_STATE" = "ready" ]; then
            if [ "$CURRENT_WORKTREE_DIGEST" != "$EXTENSION_ADVISOR_BASE_DIGEST" ]; then
                refresh_active_window_for_product_growth "$NOW_EPOCH"
            elif [ "$EXTENSION_ADVISOR_DECISION" = "continue" ] && \
                 { [ "$EXTENSION_ADVISOR_CONFIDENCE" = "high" ] || \
                   [ "$EXTENSION_ADVISOR_CONFIDENCE" = "medium" ]; }; then
                apply_spark_confirmed_extension "$NOW_EPOCH" "active-execution" || true
            elif [ "$EXTENSION_ADVISOR_DECISION" = "interrupt-candidate" ] && \
                 [ "$EXTENSION_ADVISOR_CONFIDENCE" = "high" ]; then
                if [ "$PRODUCT_IDLE_CONFIRMATION_COUNT" -ge "$CLAUDE_CODE_PRODUCT_IDLE_CONFIRMATIONS" ]; then
                    CLAUDE_TIMED_OUT=1
                    PRODUCT_IDLE_STOPPED=1
                    TIMEOUT_EXTENSION_REASON="spark_stop_with_confirmed_product_idle"
                    write_extension_advisor_receipt "applied-stop" "$TIMEOUT_EXTENSION_REASON" \
                        "$EXTENSION_ADVISOR_DECISION" "$EXTENSION_ADVISOR_CONFIDENCE"
                    stop_claude "active execution timeout after Spark stop advice and confirmed product idle" "$ELAPSED"
                    break
                fi
                # Preserve the hash-current Spark candidate until deterministic
                # idle corroboration catches up. Do not spend another model call
                # or treat the pending local condition as an invalid judgment.
                if [ "$EXTENSION_ADVISOR_WAITING_FOR_IDLE_RECORDED" -eq 0 ]; then
                    EXTENSION_ADVISOR_WAITING_FOR_IDLE_RECORDED=1
                    write_extension_advisor_receipt "pending-local-corroboration" \
                        "awaiting-product-idle-confirmations" \
                        "$EXTENSION_ADVISOR_DECISION" "$EXTENSION_ADVISOR_CONFIDENCE"
                    progress_log "Spark stop candidate is waiting for deterministic product-idle corroboration: confirmation=${PRODUCT_IDLE_CONFIRMATION_COUNT}/${CLAUDE_CODE_PRODUCT_IDLE_CONFIRMATIONS}, claude_continues=yes"
                    monitor_event "event=extension-evaluation-pending running=yes terminal=no evaluation_id=${EXTENSION_ADVISOR_EVALUATION_ID} reason=awaiting-product-idle-corroboration idle_confirmations=${PRODUCT_IDLE_CONFIRMATION_COUNT}/${CLAUDE_CODE_PRODUCT_IDLE_CONFIRMATIONS} claude_continues=yes"
                fi
            else
                EXTENSION_ADVISOR_REASON="spark-no-actionable-judgment"
                EXTENSION_ADVISOR_NEXT_EPOCH=$((NOW_EPOCH + CLAUDE_CODE_TIMEOUT_ADVISOR_RETRY_SECONDS))
                if [ "$EXTENSION_ADVISOR_ATTEMPTS" -ge "$CLAUDE_CODE_TIMEOUT_ADVISOR_MAX_ATTEMPTS" ]; then
                    EXTENSION_ADVISOR_STATE="exhausted"
                    write_extension_advisor_receipt "exhausted" "$EXTENSION_ADVISOR_REASON" \
                        "$EXTENSION_ADVISOR_DECISION" "$EXTENSION_ADVISOR_CONFIDENCE"
                else
                    EXTENSION_ADVISOR_STATE="failed"
                    write_extension_advisor_receipt "no-judgment" "$EXTENSION_ADVISOR_REASON" \
                        "$EXTENSION_ADVISOR_DECISION" "$EXTENSION_ADVISOR_CONFIDENCE"
                fi
                progress_log "Spark judgment was not actionable; Claude continues: decision=${EXTENSION_ADVISOR_DECISION:-none}, confidence=${EXTENSION_ADVISOR_CONFIDENCE:-low}, retry_after_epoch=${EXTENSION_ADVISOR_NEXT_EPOCH}, hard_deadline_epoch=${HARD_TIMEOUT_DEADLINE}"
            fi
        fi
    elif [ "$ACTIVE_EXECUTION_DEADLINE" -gt 0 ] && [ "$NOW_EPOCH" -ge "$ACTIVE_EXECUTION_DEADLINE" ]; then
        # Compatibility path when the timeout advisor is explicitly disabled.
        _RECENT_PRODUCT_ACTIVITY_SECONDS=$((NOW_EPOCH - LAST_PRODUCT_CHANGE_EPOCH))
        _EXTENSION_SECONDS="$CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS"
        _EXTENSION_EVENT="started"
        _PRODUCT_GROWTH_ELIGIBLE=0
        if [ "$LAST_PRODUCT_CHANGE_EPOCH" -gt 0 ] && \
           [ "$_RECENT_PRODUCT_ACTIVITY_SECONDS" -le "$CLAUDE_CODE_RECENT_ACTIVITY_WINDOW_SECONDS" ]; then
            if [ "$TIMEOUT_EXTENSION_ACTIVE" -eq 0 ] || \
               [ "$LAST_WORKTREE_DIGEST" != "$EXTENSION_START_WORKTREE_DIGEST" ]; then
                _PRODUCT_GROWTH_ELIGIBLE=1
            fi
        fi
        if [ "$TIMEOUT_EXTENSION_ACTIVE" -eq 1 ]; then
            _EXTENSION_SECONDS="$CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS"
            _EXTENSION_EVENT="renewed"
        fi
        if [ "$_EXTENSION_SECONDS" -gt 0 ] && [ "$_PRODUCT_GROWTH_ELIGIBLE" -eq 1 ]; then
            TIMEOUT_EXTENSION_ACTIVE=1
            TIMEOUT_EXTENSION_COUNT=$((TIMEOUT_EXTENSION_COUNT + 1))
            TIMEOUT_EXTENSION_STARTED_EPOCH="$NOW_EPOCH"
            TIMEOUT_EXTENSION_DEADLINE=$((NOW_EPOCH + _EXTENSION_SECONDS))
            if [ "$HARD_TIMEOUT_DEADLINE" -gt 0 ] && [ "$TIMEOUT_EXTENSION_DEADLINE" -gt "$HARD_TIMEOUT_DEADLINE" ]; then
                TIMEOUT_EXTENSION_DEADLINE="$HARD_TIMEOUT_DEADLINE"
            fi
            ACTIVE_EXECUTION_DEADLINE="$TIMEOUT_EXTENSION_DEADLINE"
            TIMEOUT_EXTENSION_REASON="recent_product_growth_at_active_deadline"
            EXTENSION_START_WORKTREE_DIGEST="$LAST_WORKTREE_DIGEST"
            EXTENSION_START_REPORT_BYTES="$REPORT_BYTES"
            EXTENSION_START_PROGRESS_BYTES="$CLAUDE_PROGRESS_BYTES"
            EXTENSION_START_REPORT_HASH="$(sha1sum "${WORKTREE_DIR}/CLAUDE_REPORT.md" 2>/dev/null | awk '{print $1}' || true)"
            EXTENSION_START_PROGRESS_HASH="$(progress_semantic_hash "${WORKTREE_DIR}/CLAUDE_PROGRESS.md")"
            if [ "$TIMEOUT_EXTENSION_COUNT" -ge 2 ]; then
                SECOND_EXTENSION_ACTIVE=1
                SECOND_EXTENSION_STARTED_EPOCH="$NOW_EPOCH"
                SECOND_EXTENSION_DEADLINE="$TIMEOUT_EXTENSION_DEADLINE"
                SECOND_EXTENSION_REASON="continued_product_growth"
            fi
            if [ "$_EXTENSION_EVENT" = "started" ]; then
                progress_log "Single growth extension started: policy=renewable-product-growth, ordinal=${TIMEOUT_EXTENSION_COUNT}, active_window=${CLAUDE_CODE_TIMEOUT_SECONDS}s, extension=${_EXTENSION_SECONDS}s, deadline_epoch=${TIMEOUT_EXTENSION_DEADLINE}, hard_deadline_epoch=${HARD_TIMEOUT_DEADLINE}, recent_product_activity_seconds=${_RECENT_PRODUCT_ACTIVITY_SECONDS}"
            else
                progress_log "Product-growth extension renewed: ordinal=${TIMEOUT_EXTENSION_COUNT}, extension=${_EXTENSION_SECONDS}s, deadline_epoch=${TIMEOUT_EXTENSION_DEADLINE}, hard_deadline_epoch=${HARD_TIMEOUT_DEADLINE}, recent_product_activity_seconds=${_RECENT_PRODUCT_ACTIVITY_SECONDS}"
            fi
            monitor_event "event=active-window-extended running=yes terminal=no elapsed_seconds=${ELAPSED} reason=${TIMEOUT_EXTENSION_REASON} extension_event=${_EXTENSION_EVENT} extension_ordinal=${TIMEOUT_EXTENSION_COUNT} extension_seconds=${_EXTENSION_SECONDS} recent_product_activity_seconds=${_RECENT_PRODUCT_ACTIVITY_SECONDS} last_product_change_epoch=${LAST_PRODUCT_CHANGE_EPOCH} active_window_remaining_seconds=$((ACTIVE_EXECUTION_DEADLINE > 0 ? ACTIVE_EXECUTION_DEADLINE - NOW_EPOCH : -1)) active_deadline_epoch=${ACTIVE_EXECUTION_DEADLINE} hard_deadline_epoch=${HARD_TIMEOUT_DEADLINE} activity_receipt=${ACTIVITY_OBSERVATION_FILE}"
        else
            CLAUDE_TIMED_OUT=1
            if [ "$TIMEOUT_EXTENSION_ACTIVE" -eq 1 ]; then
                TIMEOUT_EXTENSION_REASON="growth_extension_expired_without_recent_product_change"
                stop_claude "growth extension expired without recent product change (last product change ${_RECENT_PRODUCT_ACTIVITY_SECONDS}s ago)" "$ELAPSED"
            else
                TIMEOUT_EXTENSION_REASON="stale_product_progress_at_active_deadline"
                stop_claude "active execution timeout (last product change ${_RECENT_PRODUCT_ACTIVITY_SECONDS}s ago)" "$ELAPSED"
            fi
            break
        fi
    fi

    if [ "$CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS" -gt 0 ] && \
       [ "$QUIET_SECONDS" -ge "$CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS" ] && \
       [ "$EXTENSION_PENDING_ACTIVE" -eq 0 ] && \
       [ "$EXTENSION_ADVISOR_STATE" != "running" ]; then
        CLAUDE_NO_OUTPUT_TIMED_OUT=1
        stop_claude "no output for ${QUIET_SECONDS}s" "$ELAPSED"
        break
    fi
done

if [ "$EXTENSION_ADVISOR_STATE" = "running" ]; then
    cancel_extension_advisor "claude-terminal"
fi
wait "$CLAUDE_PID"
CLAUDE_STATUS=$?
if ! sync_write_scope_staging; then
    _WRITE_SCOPE_SYNC_FAILED=1
    CLAUDE_STATUS=1
    progress_log "Final write scope staging synchronization failed"
fi
set -e
if [ "$CLAUDE_COMPLETION_CONVERGED" -eq 1 ]; then
    CLAUDE_CONVERGENCE_PROCESS_STATUS="$CLAUDE_STATUS"
    CLAUDE_STATUS=0
fi

# --- Spec item 4: distinct child exit detection and finalization transition ---
# Log the moment the Claude child is detected as no longer running, then allow
# one bounded filesystem drain so exit-adjacent writes are included.
progress_log "Claude child exited: pid=${CLAUDE_PID}, exit_status=${CLAUDE_STATUS}; entering bounded terminal drain"
monitor_event "event=child-exited running=no terminal=no exit_status=${CLAUDE_STATUS}"

_DRAIN_INITIAL_DIGEST="$(worktree_digest)"
_DRAIN_FINAL_DIGEST="$_DRAIN_INITIAL_DIGEST"
_DRAIN_CHANGED=0
if [ "$CLAUDE_CODE_TERMINAL_DRAIN_SECONDS" -gt 0 ]; then
    sleep "$CLAUDE_CODE_TERMINAL_DRAIN_SECONDS"
    _DRAIN_FINAL_DIGEST="$(worktree_digest)"
    if [ "$_DRAIN_FINAL_DIGEST" != "$_DRAIN_INITIAL_DIGEST" ]; then
        _DRAIN_CHANGED=1
        sleep "$CLAUDE_CODE_TERMINAL_DRAIN_SECONDS"
        _DRAIN_FINAL_DIGEST="$(worktree_digest)"
    fi
fi
progress_log "Terminal drain complete: seconds=${CLAUDE_CODE_TERMINAL_DRAIN_SECONDS}, late_change_detected=${_DRAIN_CHANGED}, final_digest=${_DRAIN_FINAL_DIGEST:-none}"
monitor_event "event=terminal-drain running=no terminal=no late_change_detected=${_DRAIN_CHANGED}"

END_EPOCH="$(date +%s)"
ELAPSED=$((END_EPOCH - START_EPOCH))
_PHASE_CONTEXT_SECONDS="${FIRST_PROGRESS_ELAPSED_SECONDS:-$ELAPSED}"
if [ -n "$IMPLEMENTATION_COMPLETE_ELAPSED_SECONDS" ]; then
    _PHASE_IMPLEMENTATION_END="$IMPLEMENTATION_COMPLETE_ELAPSED_SECONDS"
else
    _PHASE_IMPLEMENTATION_END="$ELAPSED"
fi
if [ -n "$FIRST_PROGRESS_ELAPSED_SECONDS" ]; then
    _PHASE_IMPLEMENTATION_SECONDS=$((_PHASE_IMPLEMENTATION_END - FIRST_PROGRESS_ELAPSED_SECONDS))
    if [ "$_PHASE_IMPLEMENTATION_SECONDS" -lt 0 ]; then _PHASE_IMPLEMENTATION_SECONDS=0; fi
else
    _PHASE_IMPLEMENTATION_SECONDS=0
fi
if [ -n "$IMPLEMENTATION_COMPLETE_ELAPSED_SECONDS" ]; then
    _PHASE_TAIL_SECONDS=$((ELAPSED - IMPLEMENTATION_COMPLETE_ELAPSED_SECONDS))
    if [ "$_PHASE_TAIL_SECONDS" -lt 0 ]; then _PHASE_TAIL_SECONDS=0; fi
else
    _PHASE_TAIL_SECONDS=0
fi
if [ -n "$VALIDATION_STARTED_ELAPSED_SECONDS" ]; then
    _PHASE_VALIDATION_SECONDS=$((ELAPSED - VALIDATION_STARTED_ELAPSED_SECONDS))
    if [ "$_PHASE_VALIDATION_SECONDS" -lt 0 ]; then _PHASE_VALIDATION_SECONDS=0; fi
    _PHASE_VALIDATION_OBSERVED=true
else
    _PHASE_VALIDATION_SECONDS=0
    _PHASE_VALIDATION_OBSERVED=false
fi
_CONTEXT_CHECKPOINT_BYTES=0
if [ -f "$CONTEXT_CHECKPOINT_FILE" ]; then
    _CONTEXT_CHECKPOINT_BYTES="$(wc -c < "$CONTEXT_CHECKPOINT_FILE" | tr -d '[:space:]')"
fi
_EXECUTION_CAPSULE_BYTES=0
if [ -f "${WORKTREE_DIR}/CLAUDE_TASK_CARD.md" ]; then
    _EXECUTION_CAPSULE_BYTES="$(wc -c < "${WORKTREE_DIR}/CLAUDE_TASK_CARD.md" | tr -d '[:space:]')"
fi
_SKILL_CONTEXT_PACKET_BYTES=0
if [ -f "$SKILL_CONTEXT_PACKET_FILE" ]; then
    _SKILL_CONTEXT_PACKET_BYTES="$(wc -c < "$SKILL_CONTEXT_PACKET_FILE" | tr -d '[:space:]')"
fi
_CONTEXT_COMPILATION_STRATEGY="$CLAUDE_CODE_CONTEXT_COMPILE_STRATEGY"
_CONTEXT_COVERAGE_REQUIRED_COUNT=0
_CONTEXT_COVERAGE_UNCOVERED_COUNT=0
_CONTEXT_CANDIDATE_TOPDOWN_COUNT=0
_CONTEXT_CANDIDATE_BOTTOMUP_COUNT=0
_CONTEXT_ZERO_MARGINAL_OMITTED_COUNT=0
_CONTEXT_RESCUE_MARGINAL_COVERAGE_COUNT=0
_CONTEXT_MINIMUM_SUFFICIENT=false
if [ -n "$PYTHON_CMD" ] && [ -s "$SKILL_CONTEXT_COMPILATION_FILE" ]; then
    readarray -t _CONTEXT_COMPILATION_FIELDS < <("$PYTHON_CMD" - "$SKILL_CONTEXT_COMPILATION_FILE" <<'PYEOF' 2>/dev/null || true
import json, sys
try:
    value = json.load(open(sys.argv[1], encoding="utf-8"))
    coverage = value.get("coverage", {})
    routes = value.get("candidate_routes", {})
    candidates = value.get("candidates", [])
    rescued = value.get("rescued", [])
    print(value.get("strategy", "coverage"))
    print(len(coverage.get("required", [])))
    print(len(coverage.get("uncovered", [])))
    print(len(routes.get("top_down", [])))
    print(len(routes.get("bottom_up", [])))
    print(sum(1 for item in candidates if item.get("reason") == "zero-marginal-coverage"))
    print(sum(len(item.get("marginal_coverage", [])) for item in rescued))
    print("true" if coverage.get("minimum_sufficient") is True else "false")
except (OSError, ValueError, TypeError):
    pass
PYEOF
)
    _CONTEXT_COMPILATION_STRATEGY="${_CONTEXT_COMPILATION_FIELDS[0]:-$_CONTEXT_COMPILATION_STRATEGY}"
    _CONTEXT_COVERAGE_REQUIRED_COUNT="${_CONTEXT_COMPILATION_FIELDS[1]:-0}"
    _CONTEXT_COVERAGE_UNCOVERED_COUNT="${_CONTEXT_COMPILATION_FIELDS[2]:-0}"
    _CONTEXT_CANDIDATE_TOPDOWN_COUNT="${_CONTEXT_COMPILATION_FIELDS[3]:-0}"
    _CONTEXT_CANDIDATE_BOTTOMUP_COUNT="${_CONTEXT_COMPILATION_FIELDS[4]:-0}"
    _CONTEXT_ZERO_MARGINAL_OMITTED_COUNT="${_CONTEXT_COMPILATION_FIELDS[5]:-0}"
    _CONTEXT_RESCUE_MARGINAL_COVERAGE_COUNT="${_CONTEXT_COMPILATION_FIELDS[6]:-0}"
    _CONTEXT_MINIMUM_SUFFICIENT="${_CONTEXT_COMPILATION_FIELDS[7]:-false}"
fi
case "$_CONTEXT_CHECKPOINT_BYTES" in ''|*[!0-9]*) _CONTEXT_CHECKPOINT_BYTES=0 ;; esac
case "$_EXECUTION_CAPSULE_BYTES" in ''|*[!0-9]*) _EXECUTION_CAPSULE_BYTES=0 ;; esac
case "$_SKILL_CONTEXT_PACKET_BYTES" in ''|*[!0-9]*) _SKILL_CONTEXT_PACKET_BYTES=0 ;; esac
case "$_CONTEXT_COVERAGE_REQUIRED_COUNT" in ''|*[!0-9]*) _CONTEXT_COVERAGE_REQUIRED_COUNT=0 ;; esac
case "$_CONTEXT_COVERAGE_UNCOVERED_COUNT" in ''|*[!0-9]*) _CONTEXT_COVERAGE_UNCOVERED_COUNT=0 ;; esac
case "$_CONTEXT_CANDIDATE_TOPDOWN_COUNT" in ''|*[!0-9]*) _CONTEXT_CANDIDATE_TOPDOWN_COUNT=0 ;; esac
case "$_CONTEXT_CANDIDATE_BOTTOMUP_COUNT" in ''|*[!0-9]*) _CONTEXT_CANDIDATE_BOTTOMUP_COUNT=0 ;; esac
case "$_CONTEXT_ZERO_MARGINAL_OMITTED_COUNT" in ''|*[!0-9]*) _CONTEXT_ZERO_MARGINAL_OMITTED_COUNT=0 ;; esac
case "$_CONTEXT_RESCUE_MARGINAL_COVERAGE_COUNT" in ''|*[!0-9]*) _CONTEXT_RESCUE_MARGINAL_COVERAGE_COUNT=0 ;; esac
case "$_CONTEXT_COMPILATION_STRATEGY" in coverage|anchors-only) ;; *) _CONTEXT_COMPILATION_STRATEGY=coverage ;; esac
case "$_CONTEXT_MINIMUM_SUFFICIENT" in true|false) ;; *) _CONTEXT_MINIMUM_SUFFICIENT=false ;; esac
{
    echo '{'
    echo '  "schema_version": 1,'
    printf '  "task_id": "%s",\n' "$TASK_ID"
    printf '  "active_elapsed_seconds": %s,\n' "$ELAPSED"
    printf '  "context_acquisition_seconds": %s,\n' "$_PHASE_CONTEXT_SECONDS"
    printf '  "implementation_seconds": %s,\n' "$_PHASE_IMPLEMENTATION_SECONDS"
    printf '  "tail_seconds": %s,\n' "$_PHASE_TAIL_SECONDS"
    printf '  "validation_seconds_observed": %s,\n' "$_PHASE_VALIDATION_SECONDS"
    printf '  "validation_phase_observed": %s,\n' "$_PHASE_VALIDATION_OBSERVED"
    printf '  "first_progress_signal": "%s",\n' "${FIRST_PROGRESS_SIGNAL:-none}"
    printf '  "edit_ready_observed": %s,\n' "$([ "$EDIT_READY_DETECTED" -eq 1 ] && echo true || echo false)"
    printf '  "edit_ready_elapsed_seconds": %s,\n' "${EDIT_READY_ELAPSED_SECONDS:-null}"
    printf '  "edit_ready_grace_expired": %s,\n' "$([ "$EDIT_READY_GRACE_EXPIRED" -eq 1 ] && echo true || echo false)"
    printf '  "final_execution_activity_state": "%s",\n' "$EXECUTION_ACTIVITY_STATE"
    printf '  "final_product_idle_seconds": %s,\n' "$PRODUCT_IDLE_SECONDS"
    printf '  "product_idle_stopped": %s,\n' "$([ "$PRODUCT_IDLE_STOPPED" -eq 1 ] && echo true || echo false)"
    printf '  "tail_timeout_seconds": %s,\n' "$CLAUDE_CODE_TAIL_TIMEOUT_SECONDS"
    printf '  "tail_timeout_stopped": %s,\n' "$([ "$TAIL_TIMEOUT_STOPPED" -eq 1 ] && echo true || echo false)"
    printf '  "implementation_complete_observed": %s,\n' "$([ "$IMPLEMENTATION_COMPLETE_DETECTED" -eq 1 ] && echo true || echo false)"
    printf '  "completion_ready_observed": %s,\n' "$([ "$COMPLETION_READY_DETECTED" -eq 1 ] && echo true || echo false)"
    printf '  "completion_ready_elapsed_seconds": %s,\n' "${COMPLETION_READY_ELAPSED_SECONDS:-null}"
    printf '  "completion_evidence_elapsed_seconds": %s,\n' "${COMPLETION_EVIDENCE_ELAPSED_SECONDS:-null}"
    printf '  "completion_ready_timeout_seconds": %s,\n' "$CLAUDE_CODE_COMPLETION_READY_TIMEOUT_SECONDS"
    printf '  "completion_ready_converged": %s,\n' "$([ "$CLAUDE_COMPLETION_CONVERGED" -eq 1 ] && echo true || echo false)"
    printf '  "write_blocker_converged": %s,\n' "$([ "$CLAUDE_WRITE_BLOCKED_CONVERGED" -eq 1 ] && echo true || echo false)"
    printf '  "context_lease_route": "%s",\n' "${_CONTEXT_LEASE_ROUTE:-none}"
    if [ -n "$CONTEXT_LEASE_OPTION" ]; then
        printf '  "context_lease_calls_used": %s,\n' "$_CONTEXT_LEASE_CALLS_USED"
        printf '  "context_lease_max_warm_calls": %s,\n' "$_CONTEXT_LEASE_MAX_WARM_CALLS"
    else
        echo '  "context_lease_calls_used": null,'
        echo '  "context_lease_max_warm_calls": null,'
    fi
    printf '  "context_checkpoint_mode": "%s",\n' "$_CONTEXT_CHECKPOINT_MODE"
    printf '  "context_checkpoint_bytes": %s,\n' "$_CONTEXT_CHECKPOINT_BYTES"
    printf '  "execution_capsule_mode": "%s",\n' "$_EXECUTION_CAPSULE_MODE"
    printf '  "execution_capsule_bytes": %s,\n' "$_EXECUTION_CAPSULE_BYTES"
    printf '  "skill_context_packet_bytes": %s,\n' "$_SKILL_CONTEXT_PACKET_BYTES"
    printf '  "context_compilation_strategy": "%s",\n' "$_CONTEXT_COMPILATION_STRATEGY"
    printf '  "context_coverage_required_count": %s,\n' "$_CONTEXT_COVERAGE_REQUIRED_COUNT"
    printf '  "context_coverage_uncovered_count": %s,\n' "$_CONTEXT_COVERAGE_UNCOVERED_COUNT"
    printf '  "context_candidate_topdown_count": %s,\n' "$_CONTEXT_CANDIDATE_TOPDOWN_COUNT"
    printf '  "context_candidate_bottomup_count": %s,\n' "$_CONTEXT_CANDIDATE_BOTTOMUP_COUNT"
    printf '  "context_zero_marginal_omitted_count": %s,\n' "$_CONTEXT_ZERO_MARGINAL_OMITTED_COUNT"
    printf '  "context_rescue_marginal_coverage_count": %s,\n' "$_CONTEXT_RESCUE_MARGINAL_COVERAGE_COUNT"
    printf '  "context_minimum_sufficient": %s,\n' "$_CONTEXT_MINIMUM_SUFFICIENT"
    printf '  "cache_prompt_layout": "%s",\n' "$_CACHE_PROMPT_LAYOUT"
    printf '  "cache_stable_prefix_bytes": %s,\n' "$_CACHE_STABLE_PREFIX_BYTES"
    printf '  "cache_task_suffix_bytes": %s,\n' "$_CACHE_TASK_SUFFIX_BYTES"
    printf '  "recovery_delta_mode": "%s",\n' "$_RECOVERY_DELTA_MODE"
    echo '  "measurement": "dispatcher heartbeat observation; phase boundaries are approximate"'
    echo '}'
} > "$PHASE_METRICS_FILE"
if [ -n "${AI_WORKFLOW_CLAUDE_PHASE_METRICS_FILE:-}" ]; then
    _PHASE_METRICS_EXPORT="${AI_WORKFLOW_CLAUDE_PHASE_METRICS_FILE}"
    if mkdir -p "$(dirname "$_PHASE_METRICS_EXPORT")" 2>/dev/null && \
       cp "$PHASE_METRICS_FILE" "$_PHASE_METRICS_EXPORT" 2>/dev/null; then
        progress_log "Claude phase metrics exported: artifact=${_PHASE_METRICS_EXPORT}"
    else
        progress_log "Claude phase metrics export failed: artifact=${_PHASE_METRICS_EXPORT}; canonical artifact preserved at ${PHASE_METRICS_FILE}"
    fi
fi
progress_log "Claude phase metrics saved: context_seconds=${_PHASE_CONTEXT_SECONDS}, implementation_seconds=${_PHASE_IMPLEMENTATION_SECONDS}, tail_seconds=${_PHASE_TAIL_SECONDS}, validation_seconds_observed=${_PHASE_VALIDATION_SECONDS}, completion_ready=${COMPLETION_READY_DETECTED}, artifact=${PHASE_METRICS_FILE}"
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
if [ "${CLAUDE_COMPLETION_CONVERGED:-0}" -eq 1 ]; then
    {
        echo ""
        echo "[dispatch] Claude stopped after durable completion-ready evidence converged."
        echo "[dispatch] Convergence type: completion_ready_evidence"
        echo "[dispatch] Original process status: ${CLAUDE_CONVERGENCE_PROCESS_STATUS:-unknown}"
        echo "[dispatch] Progress log: ${PROGRESS_FILE}"
    } >> "$STATUS_FILE"
    progress_log "Claude finished by completion-ready evidence convergence: elapsed_seconds=${ELAPSED}, original_wait_status=${CLAUDE_CONVERGENCE_PROCESS_STATUS:-unknown}"
elif [ "${CLAUDE_WRITE_BLOCKED_CONVERGED:-0}" -eq 1 ]; then
    {
        echo ""
        echo "[dispatch] Claude stopped because its receipt-bound writer was rejected by the runtime permission layer."
        echo "[dispatch] Convergence type: external_write_blocker"
        echo "[dispatch] Counts toward model failure: no"
        echo "[dispatch] Progress log: ${PROGRESS_FILE}"
    } >> "$STATUS_FILE"
    progress_log "Claude finished by external write-blocker convergence: elapsed_seconds=${ELAPSED}, wait_status=${CLAUDE_STATUS}"
elif [ "${CLAUDE_APPROVAL_CONVERGED:-0}" -eq 1 ]; then
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
        echo "[dispatch] Context-acquisition timeout seconds: ${CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS}"
        echo "[dispatch] Active-execution window seconds: ${CLAUDE_CODE_TIMEOUT_SECONDS}"
        echo "[dispatch] Active window refreshed: $([ "$ACTIVE_WINDOW_REFRESHED" -eq 1 ] && echo yes || echo no)"
        echo "[dispatch] Active deadline epoch: ${ACTIVE_EXECUTION_DEADLINE}"
        echo "[dispatch] Hard timeout seconds: ${CLAUDE_CODE_HARD_TIMEOUT_SECONDS}"
        echo "[dispatch] Hard deadline epoch: ${HARD_TIMEOUT_DEADLINE}"
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
        echo "[dispatch] Growth extension limit: 1"
        echo "[dispatch] Second extension used: no"
        echo "[dispatch] Final hard cap seconds: ${CLAUDE_CODE_HARD_TIMEOUT_SECONDS}"
        echo "[dispatch] Progress log: ${PROGRESS_FILE}"
    } >> "$STATUS_FILE"
    progress_log "Claude finished by timeout: elapsed_seconds=${ELAPSED}, wait_status=${CLAUDE_STATUS}, active_window_refreshed=${ACTIVE_WINDOW_REFRESHED}, active_deadline_epoch=${ACTIVE_EXECUTION_DEADLINE}, hard_deadline_epoch=${HARD_TIMEOUT_DEADLINE}, extension_active=${TIMEOUT_EXTENSION_ACTIVE}, extension_count=${TIMEOUT_EXTENSION_COUNT:-0}, extension_reason=${TIMEOUT_EXTENSION_REASON:-none}, growth_extension_policy=renewable-product-growth-until-hard-timeout"
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
            "${CLAUDE_CODE_API_PROBE_MODE:-adaptive}" "${CLAUDE_CODE_PROBE_ENVIRONMENT:-auto}" \
            "${CLAUDE_CODE_FIRST_PROGRESS_ACTION:-observe}" "${_OBSERVATION_PROBE_RAN:-0}" \
            "${SECOND_EXTENSION_ACTIVE:-0}" "${CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS:-0}" \
            "${SECOND_EXTENSION_REASON:-}" \
            "${CLAUDE_CODE_RECENT_ACTIVITY_WINDOW_SECONDS:-120}" \
            "${CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS:-0}" \
            "${CLAUDE_CODE_HARD_TIMEOUT_SECONDS:-0}" \
            "${ACTIVE_WINDOW_REFRESHED:-0}" "${ACTIVE_EXECUTION_DEADLINE:-0}" \
            "${HARD_TIMEOUT_DEADLINE:-0}" "${TIMEOUT_EXTENSION_COUNT:-0}" \
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
    context_acquisition_timeout,
    hard_timeout,
    active_window_refreshed,
    active_execution_deadline,
    hard_timeout_deadline,
    extension_count,
) = sys.argv[1:33]

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
    "context_acquisition_timeout_seconds": int(context_acquisition_timeout),
    "active_execution_window_refreshed": active_window_refreshed == "1",
    "active_execution_deadline_epoch": int(active_execution_deadline),
    "hard_timeout_seconds": int(hard_timeout),
    "hard_timeout_deadline_epoch": int(hard_timeout_deadline),
    "growth_extension_limit": 0,
    "growth_extension_policy": "renewable-product-growth-until-hard-timeout",
    "growth_extension_count": int(extension_count),
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
            echo "  \"probe_mode\": \"${CLAUDE_CODE_API_PROBE_MODE:-adaptive}\","
            echo "  \"probe_environment\": \"${CLAUDE_CODE_PROBE_ENVIRONMENT:-auto}\","
            echo "  \"observation_probe_ran\": $([ "${_OBSERVATION_PROBE_RAN:-0}" -eq 1 ] && echo true || echo false),"
            echo "  \"timeout_extension_used\": $([ "${TIMEOUT_EXTENSION_ACTIVE:-0}" -eq 1 ] && echo true || echo false),"
            echo "  \"timeout_extension_seconds\": $([ "${TIMEOUT_EXTENSION_ACTIVE:-0}" -eq 1 ] && echo "${CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS:-0}" || echo 0),"
            _ext_reason=""
            if [ "${TIMEOUT_EXTENSION_ACTIVE:-0}" -eq 1 ]; then _ext_reason="${TIMEOUT_EXTENSION_REASON:-}"; fi
            echo "  \"timeout_extension_reason\": \"${_ext_reason}\","
            echo "  \"base_timeout_reason\": \"${TIMEOUT_EXTENSION_REASON:-none}\","
            echo "  \"recent_activity_window_seconds\": ${CLAUDE_CODE_RECENT_ACTIVITY_WINDOW_SECONDS:-120},"
            echo "  \"context_acquisition_timeout_seconds\": ${CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS:-0},"
            echo "  \"active_execution_window_refreshed\": $([ "${ACTIVE_WINDOW_REFRESHED:-0}" -eq 1 ] && echo true || echo false),"
            echo "  \"active_execution_deadline_epoch\": ${ACTIVE_EXECUTION_DEADLINE:-0},"
            echo "  \"hard_timeout_seconds\": ${CLAUDE_CODE_HARD_TIMEOUT_SECONDS:-0},"
            echo "  \"hard_timeout_deadline_epoch\": ${HARD_TIMEOUT_DEADLINE:-0},"
            echo '  "growth_extension_limit": 0,'
            echo '  "growth_extension_policy": "renewable-product-growth-until-hard-timeout",'
            echo "  \"growth_extension_count\": ${TIMEOUT_EXTENSION_COUNT:-0},"
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
    CHECK_ARGS=(
        --report "$CHECKER_REPORT_FILE"
        --receipt "$CHECKER_VALIDATION_RECEIPT_FILE"
        --logs-dir "$CHECKER_LOGS_DIR"
        --jobs "$CLAUDE_CODE_CHECKER_JOBS"
        --task-card "${WORKTREE_DIR}/CLAUDE_TASK_CARD.md"
    )
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
    (
        if [ -n "$PYTHON_CMD" ] && [ -f "${SCRIPT_DIR}/process-identity.py" ]; then
            "$PYTHON_CMD" "${SCRIPT_DIR}/process-identity.py" capture \
                --pid "$BASHPID" --task-id "$TASK_ID" --role checker \
                --output "$CHECKER_IDENTITY_FILE" >/dev/null 2>&1 || true
        fi
        exec bash "$CHECK_SCRIPT" "${CHECK_ARGS[@]}"
    ) >> "$STATUS_FILE" 2>&1 &
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

if [ -n "$PYTHON_CMD" ] && [ -f "${SCRIPT_DIR}/change-size-advisory.py" ]; then
    if "$PYTHON_CMD" "${SCRIPT_DIR}/change-size-advisory.py" \
        --worktree "$WORKTREE_DIR" --output "$CHANGE_SIZE_ADVISORY_FILE" >/dev/null; then
        _CHANGE_SIZE_STATUS="$("$PYTHON_CMD" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("status","unknown"))' "$CHANGE_SIZE_ADVISORY_FILE" 2>/dev/null || echo unknown)"
        progress_log "Change-size advisory: status=${_CHANGE_SIZE_STATUS}, receipt=${CHANGE_SIZE_ADVISORY_FILE}"
    fi
fi

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

IMPLEMENTATION_CHANGES=0
TOTAL_PRODUCT_CHANGES=0
CONTROL_CHANGES=0
FINAL_PRODUCT_DIGEST=""
FINAL_PRODUCT_DELTA=0
PRODUCT_STATE_FINALIZATION_FAILED=0
if [ -n "$PYTHON_CMD" ] && [ -f "${SCRIPT_DIR}/worktree_state_hash.py" ] && \
   "$PYTHON_CMD" "${SCRIPT_DIR}/worktree_state_hash.py" \
       --worktree "$WORKTREE_DIR" --ignore-empty-untracked --json \
       --baseline-state "$PRODUCT_BASELINE_FILE" \
       --output "$PRODUCT_STATE_FILE"; then
    IFS=$'\t' read -r IMPLEMENTATION_CHANGES TOTAL_PRODUCT_CHANGES CONTROL_CHANGES FINAL_PRODUCT_DIGEST FINAL_PRODUCT_DELTA < <(
        "$PYTHON_CMD" - "$PRODUCT_STATE_FILE" <<'PYEOF'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
print("\t".join((
    str(value.get("incremental_product_change_count", 0)),
    str(value.get("product_change_count", 0)),
    str(value.get("control_change_count", 0)),
    str(value.get("product_hash", "")),
    "1" if value.get("product_delta_from_baseline") else "0",
)))
PYEOF
    )
else
    PRODUCT_STATE_FINALIZATION_FAILED=1
    progress_log "Canonical final product-state snapshot failed; terminal evidence will fail closed"
fi
# A child can write immediately before it exits while a bounded Spark judgment
# is in flight, leaving no subsequent live sampling iteration. Reconcile the
# canonical final snapshot before applying a first-progress timeout so a real
# product delta is never misclassified as no progress.
if [ "$FIRST_PROGRESS_DETECTED" -eq 0 ] && [ "$FINAL_PRODUCT_DELTA" = "1" ]; then
    FIRST_PROGRESS_DETECTED=1
    if [ "$_PARSED_TASK_MODE" = "checker-test" ]; then
        FIRST_PROGRESS_SIGNAL="checker_worktree_change_terminal_reconcile"
    else
        FIRST_PROGRESS_SIGNAL="builder_worktree_change_terminal_reconcile"
    fi
    FIRST_PROGRESS_ELAPSED_SECONDS="$ELAPSED"
    [ "$LAST_PRODUCT_CHANGE_EPOCH" -gt 0 ] || LAST_PRODUCT_CHANGE_EPOCH="$END_EPOCH"
    CLAUDE_FIRST_PROGRESS_TIMED_OUT=0
    progress_log "First substantive progress detected: signal=${FIRST_PROGRESS_SIGNAL}, first_progress_detected=1, elapsed_seconds=${ELAPSED}, source=terminal_product_state_reconciliation"
    monitor_event "event=first-progress-reconciled running=no terminal=no elapsed_seconds=${ELAPSED} signal=${FIRST_PROGRESS_SIGNAL} product_delta_from_baseline=1 source=terminal_product_state_reconciliation"
elif [ "$CLAUDE_FIRST_PROGRESS_TIMED_OUT" -eq 0 ] && \
     [ "$FIRST_PROGRESS_DETECTED" -eq 0 ] && \
     [ "$CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS" -gt 0 ] && \
     [ "$ELAPSED" -ge "$CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS" ] && \
     [ "$CLAUDE_CODE_FIRST_PROGRESS_ACTION" != "observe" ]; then
    # Git Bash can lose visibility of a background wrapper PID while its script
    # descendant is still running. Preserve the first-progress contract only
    # after the final canonical product snapshot confirms no real delta.
    CLAUDE_FIRST_PROGRESS_TIMED_OUT=1
    progress_log "First-progress timeout reconciled after child exit: elapsed_seconds=${ELAPSED}, timeout_seconds=${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS}"
fi
VALID_CLAUDE_REPORT=0
REPORT_ARTIFACT_REASON="validation-not-run"
REPORT_ARTIFACT_NORMALIZED="no"
_REPORT_ARTIFACT_VALIDATOR="${SCRIPT_DIR}/validate-claude-report.py"
if [ -n "$PYTHON_CMD" ] && [ -f "$_REPORT_ARTIFACT_VALIDATOR" ]; then
    if "$PYTHON_CMD" "$_REPORT_ARTIFACT_VALIDATOR" \
        "${WORKTREE_DIR}/CLAUDE_REPORT.md" \
        --output "$REPORT_ARTIFACT_VALIDATION_FILE" >/dev/null 2>&1; then
        VALID_CLAUDE_REPORT=1
    fi
    if [ -s "$REPORT_ARTIFACT_VALIDATION_FILE" ]; then
        IFS=$'\t' read -r REPORT_ARTIFACT_REASON REPORT_ARTIFACT_NORMALIZED < <(
            "$PYTHON_CMD" - "$REPORT_ARTIFACT_VALIDATION_FILE" <<'PYEOF' 2>/dev/null || printf 'validation-receipt-unreadable\tno\n'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
reasons = value.get("reasons") or []
print("\t".join((
    ",".join(str(reason) for reason in reasons) or "valid",
    "yes" if value.get("normalization_applied") else "no",
)))
PYEOF
        )
    fi
elif valid_claude_report_file "${WORKTREE_DIR}/CLAUDE_REPORT.md"; then
    VALID_CLAUDE_REPORT=1
    REPORT_ARTIFACT_REASON="compatibility-validator-pass"
else
    REPORT_ARTIFACT_REASON="validator-unavailable-or-invalid"
fi
progress_log "Claude report artifact validation: valid=$([ "$VALID_CLAUDE_REPORT" -eq 1 ] && echo yes || echo no), reason=${REPORT_ARTIFACT_REASON}, normalized=${REPORT_ARTIFACT_NORMALIZED}, artifact=${REPORT_ARTIFACT_VALIDATION_FILE}"

# Verify cheap mechanical report claims before semantic Codex review.  This is
# evidence: conflicts never become acceptance and never rewrite the
# implementation. Reports without the required machine-readable claims remain
# reviewable, but their completion state is downgraded to needs-review.
REPORT_CONSISTENCY_STATUS="not-run"
ARTIFACT_VALID="no"
VALIDATION_STATUS="unknown"
COMPLETION_STATE="needs-review"
_REPORT_VERIFIER="${SCRIPT_DIR}/verify-claude-report.py"
if [ "$VALID_CLAUDE_REPORT" -eq 1 ] && [ -n "$PYTHON_CMD" ] && [ -f "$_REPORT_VERIFIER" ]; then
    if "$PYTHON_CMD" "$_REPORT_VERIFIER" \
        --report "${WORKTREE_DIR}/CLAUDE_REPORT.md" \
        --worktree "$WORKTREE_DIR" \
        --base "$WORKTREE_START_COMMIT" \
        --task-card "${WORKTREE_DIR}/TASK_CARD_FULL.md" \
        --output "$REPORT_CONSISTENCY_FILE"; then
        IFS=$'\t' read -r REPORT_CONSISTENCY_STATUS ARTIFACT_VALID VALIDATION_STATUS COMPLETION_STATE < <(
            "$PYTHON_CMD" - "$REPORT_CONSISTENCY_FILE" <<'PYEOF' 2>/dev/null || printf 'error\tno\tunknown\tneeds-review\n'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
print("\t".join((
    str(value.get("status", "error")),
    "yes" if value.get("artifact_valid") else "no",
    str(value.get("validation_status", "unknown")),
    str(value.get("completion_state", "needs-review")),
)))
PYEOF
        )
        progress_log "Claude report/diff consistency: status=${REPORT_CONSISTENCY_STATUS}, artifact=${REPORT_CONSISTENCY_FILE}"
    else
        REPORT_CONSISTENCY_STATUS="error"
        progress_log "Claude report/diff consistency helper failed; semantic review remains required"
    fi
else
    progress_log "Claude report/diff consistency skipped: valid_report=$([ "$VALID_CLAUDE_REPORT" -eq 1 ] && echo yes || echo no), helper_available=$([ -f "$_REPORT_VERIFIER" ] && echo yes || echo no)"
fi

# --- Post-execution scope enforcement for advisor continuation ---
# After Claude exits, recompute changed paths/state and enforce the validated
# allowed/forbidden boundaries.  A violation produces non-zero semantic failure,
# remains isolated and never reports acceptance/merge.
ADVISOR_POST_RUN_SCOPE_VIOLATION=0
PLANNER_OUTPUT_SCOPE_VIOLATION=0
PLANNER_CONTRACT_MISSING=0
if [ "$CLAUDE_CODE_BUILDER_MODE" = "solution-planning" ]; then
    _PLANNER_CHANGED_PATHS="$(
        printf '%s\n%s\n%s\n' \
            "$(git -C "$WORKTREE_DIR" diff --name-only 2>/dev/null || true)" \
            "$(git -C "$WORKTREE_DIR" diff --cached --name-only 2>/dev/null || true)" \
            "$(git -C "$WORKTREE_DIR" ls-files --others --exclude-standard 2>/dev/null || true)" \
        | sort -u | sed '/^$/d'
    )"
    _PLANNER_FORBIDDEN_CHANGES=""
    while IFS= read -r _planner_path; do
        [ -z "$_planner_path" ] && continue
        case "$_planner_path" in
            solution-contract.draft.json|CLAUDE_*.md|CLAUDE_*.json|TASK_CARD*.md|ADVISOR_REQUEST.json|advisor-*.json|advisor-*.md|truncation-manifest.json)
                ;;
            *)
                _PLANNER_FORBIDDEN_CHANGES="${_PLANNER_FORBIDDEN_CHANGES}  ${_planner_path}\n"
                ;;
        esac
    done <<< "$_PLANNER_CHANGED_PATHS"
    if [ -n "$_PLANNER_FORBIDDEN_CHANGES" ]; then
        PLANNER_OUTPUT_SCOPE_VIOLATION=1
        ADVISOR_POST_RUN_SCOPE_VIOLATION=1
        progress_log "Solution-planner output scope FAILED: product/source changes detected"
        printf 'Error: solution-planner changed paths outside its structured artifact contract:\n%b' \
            "$_PLANNER_FORBIDDEN_CHANGES" >&2
    elif [ ! -s "${WORKTREE_DIR}/solution-contract.draft.json" ]; then
        PLANNER_CONTRACT_MISSING=1
        progress_log "Solution-planner output missing: solution-contract.draft.json"
    else
        progress_log "Solution-planner output scope PASSED: structured draft only"
    fi
fi
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

if [ -n "${_REVIEWED_CONTINUATION_TASK_ID:-}" ]; then
    if ! "$PYTHON_CMD" "${SCRIPT_DIR}/prepare-worktree-continuation.py" post-run \
        --approval "$_REVIEWED_CONTINUATION_APPROVAL" \
        > "${WORKTREE_ROOT}/${TASK_ID}.reviewed-continuation-post-run.json"; then
        ADVISOR_POST_RUN_SCOPE_VIOLATION=1
        progress_log "Post-run scope enforcement FAILED: reviewed continuation approval ${_REVIEWED_CONTINUATION_APPROVAL_ID}"
    else
        progress_log "Post-run scope enforcement PASSED: reviewed continuation approval ${_REVIEWED_CONTINUATION_APPROVAL_ID}"
    fi
fi

# Test-writing Checker output is mechanically enforced after the model exits.
# This turns write scope, non-empty files, syntax, and per-file validation into
# evidence rather than relying only on prompt compliance. Transport/no-progress
# failures retain their original classification and do not become scope errors.
CHECKER_CONTRACT_VIOLATION=0
if [ "$_CHECKER_WRITES_TESTS" -eq 1 ] && \
   [ "$CLAUDE_CODE_CHECKER_RUNTIME_ENFORCEMENT" -eq 1 ] && \
   { [ "$IMPLEMENTATION_CHANGES" -gt 0 ] || [ "$VALID_CLAUDE_REPORT" -eq 1 ]; }; then
    _CHECKER_ENFORCER="${SCRIPT_DIR}/enforce-checker-contract.py"
    if [ -z "$PYTHON_CMD" ] || [ ! -f "$_CHECKER_ENFORCER" ]; then
        CHECKER_CONTRACT_VIOLATION=1
        progress_log "Checker runtime enforcement unavailable: Python/helper missing"
    elif ! "$PYTHON_CMD" "$_CHECKER_ENFORCER" \
        --worktree "$WORKTREE_DIR" \
        --task-card "${WORKTREE_DIR}/TASK_CARD_FULL.md" \
        --output "$CHECKER_CONTRACT_RECEIPT_FILE" \
        --timeout "$CLAUDE_CODE_CHECKER_FILE_TIMEOUT_SECONDS" >/dev/null; then
        CHECKER_CONTRACT_VIOLATION=1
        progress_log "Checker runtime enforcement FAILED: ${CHECKER_CONTRACT_RECEIPT_FILE}"
    else
        progress_log "Checker runtime enforcement PASSED: ${CHECKER_CONTRACT_RECEIPT_FILE}"
        VALIDATION_STATUS="verified"
    fi
fi

DISPATCH_EVIDENCE_STATE="$(classify_dispatch_evidence "$IMPLEMENTATION_CHANGES" "$VALID_CLAUDE_REPORT" "${WORKTREE_DIR}/CLAUDE_PROGRESS.md" "${WORKTREE_DIR}/CLAUDE_REPORT.md")"
case "$DISPATCH_EVIDENCE_STATE" in
    "diff + valid report") DISPATCH_EVIDENCE_CODE="diff-plus-valid-report" ;;
    "diff without report") DISPATCH_EVIDENCE_CODE="diff-without-report" ;;
    "acknowledgement only") DISPATCH_EVIDENCE_CODE="acknowledgement-only" ;;
    "seeded report only") DISPATCH_EVIDENCE_CODE="seeded-report-only" ;;
    "valid report without diff") DISPATCH_EVIDENCE_CODE="valid-report-without-diff" ;;
    *) DISPATCH_EVIDENCE_CODE="no-valid-report" ;;
esac
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
WRITE_RUNTIME_BLOCKED=0
if [ "$IMPLEMENTATION_CHANGES" -eq 0 ] && \
   { [ "${CLAUDE_WRITE_BLOCKED_CONVERGED:-0}" -eq 1 ] || write_runtime_approval_blocker; }; then
    WRITE_RUNTIME_BLOCKED=1
fi
DISPATCH_OUTCOME="success"
if [ "${_WRITE_SCOPE_SYNC_FAILED:-0}" -eq 1 ]; then
    DISPATCH_OUTCOME="write_staging_failed"
elif [ "${PRODUCT_STATE_SAMPLING_FAILED:-0}" -eq 1 ] || \
   [ "${PRODUCT_STATE_FINALIZATION_FAILED:-0}" -eq 1 ]; then
    DISPATCH_OUTCOME="runtime_evidence_error"
elif [ "${PLANNER_OUTPUT_SCOPE_VIOLATION:-0}" -eq 1 ]; then
    DISPATCH_OUTCOME="scope_violation"
elif [ "${PLANNER_CONTRACT_MISSING:-0}" -eq 1 ]; then
    DISPATCH_OUTCOME="missing_required_artifact"
elif [ "${CHECKER_CONTRACT_VIOLATION:-0}" -eq 1 ]; then
    DISPATCH_OUTCOME="checker_contract_violation"
elif [ "$_PARSED_TASK_MODE" = "builder" ] && \
     [ "$CLAUDE_CODE_BUILDER_MODE" != "solution-planning" ] && \
     [ "${IMPLEMENTATION_COMPLETE_DETECTED:-0}" -eq 1 ] && \
     [ "$IMPLEMENTATION_CHANGES" -eq 0 ]; then
    DISPATCH_OUTCOME="missing_required_artifact"
elif [ "${ADVISOR_POST_RUN_SCOPE_VIOLATION:-0}" -eq 1 ]; then
    # Post-run scope violation is a semantic failure; never report acceptance/merge.
    DISPATCH_OUTCOME="scope_violation"
elif [ "$CLAUDE_SEMANTIC_ERROR" -eq 1 ]; then
    if [ "$IMPLEMENTATION_CHANGES" -gt 0 ]; then
        DISPATCH_OUTCOME="api_error_with_diff"
    else
        DISPATCH_OUTCOME="api_error_without_diff"
    fi
elif [ "${CLAUDE_APPROVAL_CONVERGED:-0}" -eq 1 ] || [ "$WRITE_RUNTIME_BLOCKED" -eq 1 ]; then
    DISPATCH_OUTCOME="approval_blocked"
elif [ "$ZERO_OUTPUT_PROBE_CONCLUSION" = "unavailable-in-current-environment" ] || \
     [ "$ZERO_OUTPUT_PROBE_CONCLUSION" = "inconclusive-restricted-environment" ]; then
    # A failed minimal interaction means this round cannot be attributed to
    # model execution. In a restricted sandbox the evidence is inconclusive,
    # but it still must not count toward takeover.
    DISPATCH_OUTCOME="network_error"
elif [ "$CLAUDE_TIMED_OUT" -eq 1 ] || [ "$CLAUDE_NO_OUTPUT_TIMED_OUT" -eq 1 ] || [ "${CLAUDE_FIRST_PROGRESS_TIMED_OUT:-0}" -eq 1 ]; then
    if [ "${TAIL_TIMEOUT_STOPPED:-0}" -eq 1 ] && \
       [ "$IMPLEMENTATION_CHANGES" -gt 0 ]; then
        # Productive implementation and evidence finalization have separate
        # outcomes. Missing tail prose is recovered from the stable diff and
        # receipts and must not request another implementation round by itself.
        DISPATCH_OUTCOME="evidence_tail_incomplete"
    elif [ "${_STARTUP_PROBE_CONCLUSION:-not-run}" = "available" ]; then
        DISPATCH_OUTCOME="execution_timeout"
    else
        DISPATCH_OUTCOME="timeout"
    fi
elif [ "${CLAUDE_COMPLETION_CONVERGED:-0}" -eq 1 ]; then
    DISPATCH_OUTCOME="success"
elif [ "$RESULT_FALLBACK_GENERATED" -eq 1 ]; then
    DISPATCH_OUTCOME="fallback"
elif [ "$IMPLEMENTATION_CHANGES" -eq 0 ] && [ "$VALID_CLAUDE_REPORT" -eq 0 ]; then
    DISPATCH_OUTCOME="no_useful_progress"
fi

# Keep transport/process completion separate from artifact validity, validation,
# and semantic acceptance. A normal child exit with an inconsistent report is a
# successful dispatch process, but never a completed task.
DISPATCH_SUCCESS="no"
SEMANTIC_ACCEPTANCE="pending-codex-review"
if [ "$DISPATCH_OUTCOME" = "success" ]; then
    DISPATCH_SUCCESS="yes"
    if [ "$ARTIFACT_VALID" != "yes" ] || \
       [ "$VALIDATION_STATUS" = "missing-evidence" ] || \
       [ "$VALIDATION_STATUS" = "claimed-unverified" ] || \
       [ "$VALIDATION_STATUS" = "failed" ] || \
       [ "$VALIDATION_STATUS" = "unknown" ]; then
        COMPLETION_STATE="needs-review"
    else
        COMPLETION_STATE="semantic-review-required"
    fi
elif [ "$DISPATCH_OUTCOME" = "evidence_tail_incomplete" ]; then
    DISPATCH_SUCCESS="yes"
    COMPLETION_STATE="needs-review"
else
    case "$DISPATCH_OUTCOME" in
        approval_blocked|network_error|preflight_error|runtime_evidence_error) COMPLETION_STATE="external-blocked" ;;
        scope_violation|checker_contract_violation|write_staging_failed) COMPLETION_STATE="needs-revision" ;;
        *) COMPLETION_STATE="incomplete" ;;
    esac
fi

OPERATOR_STATE="execution-incomplete"
if [ "$DISPATCH_SUCCESS" = "yes" ] && \
   [ "$DISPATCH_EVIDENCE_CODE" = "diff-without-report" ] && \
   [ "$IMPLEMENTATION_CHANGES" -gt 0 ]; then
    OPERATOR_STATE="implementation-stable-awaiting-review"
elif [ "$COMPLETION_STATE" = "needs-review" ] || \
     [ "$COMPLETION_STATE" = "semantic-review-required" ]; then
    OPERATOR_STATE="terminal-awaiting-review"
elif [ "$COMPLETION_STATE" = "external-blocked" ]; then
    OPERATOR_STATE="terminal-external-blocked"
elif [ "$COMPLETION_STATE" = "needs-revision" ]; then
    OPERATOR_STATE="terminal-needs-revision"
fi

if [ -n "$PYTHON_CMD" ]; then
    CLAUDE_FIRST_SATISFIED="no"
    if [ "$CLAUDE_LAUNCHED" -eq 1 ] && \
       { [ "$IMPLEMENTATION_CHANGES" -gt 0 ] || [ "$VALID_CLAUDE_REPORT" -eq 1 ]; }; then
        CLAUDE_FIRST_SATISFIED="yes"
    fi
    WORKFLOW_EXECUTION_STATUS="claude-first-degraded"
    [ "$CLAUDE_FIRST_SATISFIED" = "yes" ] && WORKFLOW_EXECUTION_STATUS="claude-first-executed"
    "$PYTHON_CMD" - "$OUTCOME_FILE" "$TASK_ID" "$DISPATCH_OUTCOME" "$DISPATCH_SUCCESS" \
        "$REPORT_CONSISTENCY_STATUS" "$ARTIFACT_VALID" "$VALIDATION_STATUS" \
        "$REPORT_ARTIFACT_REASON" "$REPORT_ARTIFACT_NORMALIZED" "$REPORT_ARTIFACT_VALIDATION_FILE" \
        "$SEMANTIC_ACCEPTANCE" "$COMPLETION_STATE" "$OPERATOR_STATE" "$CLAUDE_LAUNCHED" \
        "$CLAUDE_FIRST_SATISFIED" "$WORKFLOW_EXECUTION_STATUS" \
        "$DISPATCH_EXECUTION_ENV" "$CLAUDE_CODE_HOST_AUTHORITY" \
        "${_STARTUP_PROBE_CONCLUSION:-not-run}" "$WRITE_RUNTIME_BLOCKED" \
        "$DISPATCH_EVIDENCE_CODE" "$IMPLEMENTATION_CHANGES" "$TOTAL_PRODUCT_CHANGES" "$CONTROL_CHANGES" \
        "$FINAL_PRODUCT_DIGEST" "$FINAL_PRODUCT_DELTA" "$PRODUCT_STATE_FILE" \
        "${EXTENSION_ADVISOR_STATE:-not-run}" "${EXTENSION_ADVISOR_LAST_STATUS:-not-run}" \
        "${EXTENSION_ADVISOR_ATTEMPTS:-0}" \
        "$EXTENSION_ADVISOR_RECEIPT_FILE" <<'PYEOF'
import json, os, sys, tempfile
(
    output, task_id, dispatch_outcome, dispatch_success, report_consistency,
    artifact_valid, validation_success, report_artifact_reason,
    report_artifact_normalized, report_artifact_validation,
    semantic_acceptance, completion_state,
    operator_state, builder_launched, claude_first_satisfied, workflow_status,
    requested_env, host_authority, startup_probe, write_runtime_blocked,
    evidence_state, product_changes, total_product_changes, control_changes, product_hash,
    product_delta, product_state_receipt, extension_advisor_state,
    extension_advisor_last_status, extension_advisor_attempts,
    extension_advisor_receipt,
) = sys.argv[1:]
value = {
    "schema_version": 1,
    "task_id": task_id,
    "dispatch_outcome": dispatch_outcome,
    "dispatch_success": dispatch_success == "yes",
    "artifact_valid": artifact_valid == "yes",
    "report_artifact_reason": report_artifact_reason,
    "report_artifact_normalized": report_artifact_normalized == "yes",
    "report_artifact_validation": (
        report_artifact_validation if os.path.isfile(report_artifact_validation) else None
    ),
    "report_consistency": report_consistency,
    "validation_success": validation_success,
    "semantic_acceptance": semantic_acceptance,
    "completion_state": completion_state,
    "operator_state": operator_state,
    "builder_started": builder_launched == "1",
    "claude_first_satisfied": claude_first_satisfied == "yes",
    "workflow_execution_status": workflow_status,
    "host_requested": requested_env == "host",
    "host_authorized": host_authority == "1",
    "host_effective": requested_env == "host" and startup_probe == "available",
    "write_runtime_blocked": write_runtime_blocked == "1",
    "evidence_state": evidence_state,
    "product_changes": int(product_changes),
    "total_product_changes": int(total_product_changes),
    "control_changes": int(control_changes),
    "product_hash": product_hash or None,
    "product_delta_from_baseline": product_delta == "1",
    "product_state_receipt": product_state_receipt,
    "extension_advisor_state": extension_advisor_state,
    "extension_advisor_last_status": extension_advisor_last_status,
    "extension_advisor_attempts": int(extension_advisor_attempts),
    "extension_advisor_receipt": (
        extension_advisor_receipt if os.path.isfile(extension_advisor_receipt) else None
    ),
}
directory = os.path.dirname(output) or "."
fd, temporary = tempfile.mkstemp(prefix=".outcome-", dir=directory)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, output)
except BaseException:
    try:
        os.unlink(temporary)
    except OSError:
        pass
    raise
PYEOF
fi

# Missing prose is recoverable when a useful diff exists. Emit a deterministic
# receipt so Codex can review the bounded tail without reconstructing it.
if [ -n "$PYTHON_CMD" ] && [ "$IMPLEMENTATION_CHANGES" -gt 0 ] && [ "$VALID_CLAUDE_REPORT" -eq 0 ]; then
    "$PYTHON_CMD" - "$RECOVERED_COMPLETION_FILE" "$TASK_ID" "$WORKTREE_DIR" \
        "$DIFF_FILE" "$VALIDATION_STATUS" "$TAIL_TIMEOUT_STOPPED" "$COMPLETION_STATE" \
        "$OPERATOR_STATE" \
        "$CHECKER_CONTRACT_RECEIPT_FILE" "$CHECKER_VALIDATION_RECEIPT_FILE" \
        "$WRITE_SCOPE_RECEIPT_FILE" \
        "${WORKTREE_ROOT}/${TASK_ID}.reviewed-continuation-post-run.json" \
        "$DISPATCH_OUTCOME" "${TIMEOUT_EXTENSION_REASON:-}" \
        "$CLAUDE_CODE_TAIL_TIMEOUT_SECONDS" <<'PYEOF'
import hashlib, json, os, subprocess, sys, tempfile
(
    output, task_id, worktree, diff_path, validation, tail_timeout, completion,
    operator_state, checker_receipt, validation_receipt, write_scope_receipt, continuation_receipt,
    dispatch_outcome, timeout_reason, report_tail_window,
) = sys.argv[1:]
status = subprocess.run(
    ["git", "-C", worktree, "status", "--porcelain"], capture_output=True,
    text=True, encoding="utf-8", errors="replace",
).stdout.splitlines()
paths = sorted({line[3:].strip() for line in status if len(line) > 3 and line[3:].strip()})
def file_evidence(path):
    if not path or not os.path.isfile(path):
        return {"path": path or None, "available": False}
    raw = open(path, "rb").read()
    value = {"path": os.path.abspath(path), "available": True,
             "sha256": "sha256:" + hashlib.sha256(raw).hexdigest()}
    try:
        parsed = json.loads(raw.decode("utf-8"))
        value["status"] = parsed.get("status") or parsed.get("validation_status")
    except (UnicodeDecodeError, ValueError, TypeError):
        value["status"] = "unparsed"
    return value
def path_state(path):
    full = os.path.join(worktree, path)
    try:
        info = os.lstat(full)
    except OSError:
        return {"path": path, "kind": "missing"}
    if os.path.islink(full):
        return {"path": path, "kind": "symlink"}
    if os.path.isfile(full):
        raw = open(full, "rb").read()
        return {"path": path, "kind": "file", "size": len(raw),
                "sha256": "sha256:" + hashlib.sha256(raw).hexdigest()}
    return {"path": path, "kind": "other"}
try:
    diff_bytes = open(diff_path, "rb").read()
except OSError:
    diff_bytes = b""
value = {
    "schema_version": 1, "task_id": task_id,
    "recovery_reason": "missing-claude-report", "changed_files": paths,
    "report_recovery_source": "deterministic-diff-and-receipts",
    "evidence_usability": "recoverable",
    "direct_acceptance_eligible": False,
    "diff_sha256": "sha256:" + hashlib.sha256(diff_bytes).hexdigest(),
    "validation_status": validation, "claude_report_complete": False,
    "tail_timeout_stopped": tail_timeout == "1", "completion_state": completion,
    "operator_state": operator_state,
    "dispatch_outcome": dispatch_outcome,
    "timeout_reason": timeout_reason or None,
    "implementation_window_complete": True,
    "report_tail_window_seconds": int(report_tail_window),
    "report_recovery_attempts": 1,
    "report_recovery_policy": "single-bounded-deterministic-recovery",
    "changed_path_state": [path_state(path) for path in paths],
    "validation_receipts": {
        "checker_contract": file_evidence(checker_receipt),
        "read_only_fanout": file_evidence(validation_receipt),
        "write_scope": file_evidence(write_scope_receipt),
        "reviewed_continuation": file_evidence(continuation_receipt),
    },
    "codex_bounded_tail_takeover_eligible": completion == "needs-review",
    "authority": "codex-review-required",
}
directory = os.path.dirname(output) or "."
fd, temporary = tempfile.mkstemp(prefix=".recovered-completion-", dir=directory)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True); handle.write("\n")
    os.replace(temporary, output)
except BaseException:
    try: os.unlink(temporary)
    except OSError: pass
    raise
PYEOF
    progress_log "Recovered completion receipt saved: ${RECOVERED_COMPLETION_FILE}"
fi

# Produce a reviewable product-only patch from the execution baseline. In
# dirty-snapshot mode this excludes source changes that predated delegation and
# makes whole-worktree merge explicitly ineligible.
if [ -n "$PYTHON_CMD" ] && [ "$IMPLEMENTATION_CHANGES" -gt 0 ] && \
   [ -f "${SCRIPT_DIR}/build-scoped-handoff.py" ] && \
   [ -s "$WRITE_SCOPE_RECEIPT_FILE" ]; then
    _SCOPED_HANDOFF_ARGS=(
        --task-id "$TASK_ID"
        --worktree "$WORKTREE_DIR"
        --source-base "$BASE_COMMIT"
        --execution-base "$WORKTREE_START_COMMIT"
        --write-scope "$WRITE_SCOPE_RECEIPT_FILE"
        --validation-receipt "$CHECKER_VALIDATION_RECEIPT_FILE"
        --output-dir "$WORKTREE_ROOT"
    )
    if [ "$CLAUDE_CODE_DIRTY_SOURCE_MODE" = "snapshot" ] || \
       [ -n "$DIRTY_SNAPSHOT_COMMIT" ]; then
        _SCOPED_HANDOFF_ARGS+=(--dirty-snapshot)
    fi
    if "$PYTHON_CMD" "${SCRIPT_DIR}/build-scoped-handoff.py" \
        "${_SCOPED_HANDOFF_ARGS[@]}" >/dev/null; then
        progress_log "Scoped handoff ready: manifest=${SCOPED_HANDOFF_MANIFEST_FILE}, patch=${SCOPED_HANDOFF_PATCH_FILE}"
    else
        progress_log "Scoped handoff blocked; inspect ${SCOPED_HANDOFF_MANIFEST_FILE:-unavailable} before applying any worktree changes"
    fi
fi

# Summarize the final evidence chain without granting semantic acceptance.
if [ -n "$PYTHON_CMD" ] && [ -f "${SCRIPT_DIR}/build-acceptance-bundle.py" ]; then
    _ACCEPTANCE_BUNDLE_ARGS=(
        --worktree "$WORKTREE_DIR"
        --outcome "$OUTCOME_FILE"
        --report-artifact-validation "$REPORT_ARTIFACT_VALIDATION_FILE"
        --report-consistency "$REPORT_CONSISTENCY_FILE"
        --write-scope "$WRITE_SCOPE_RECEIPT_FILE"
        --checker-contract "$CHECKER_CONTRACT_RECEIPT_FILE"
        --validation-receipt "$CHECKER_VALIDATION_RECEIPT_FILE"
        --scoped-handoff "$SCOPED_HANDOFF_MANIFEST_FILE"
        --recovered-completion "$RECOVERED_COMPLETION_FILE"
        --task-card "${WORKTREE_DIR}/TASK_CARD_FULL.md"
        --output "$ACCEPTANCE_BUNDLE_FILE"
        --capsule-output "$ACCEPTANCE_CAPSULE_FILE"
        --stdout-mode off
    )
    if [ -s "${AI_WORKFLOW_ACCEPTANCE_GRAPH_FILE:-}" ]; then
        _ACCEPTANCE_BUNDLE_ARGS+=(--acceptance-graph "$AI_WORKFLOW_ACCEPTANCE_GRAPH_FILE")
    fi
    if [ -s "${AI_WORKFLOW_DELTA_REVIEW_PACKET_FILE:-}" ]; then
        _ACCEPTANCE_BUNDLE_ARGS+=(--delta-review-packet "$AI_WORKFLOW_DELTA_REVIEW_PACKET_FILE")
    fi
    if [ -s "${AI_WORKFLOW_INVARIANT_MATRIX_FILE:-}" ]; then
        _ACCEPTANCE_BUNDLE_ARGS+=(--invariant-matrix "$AI_WORKFLOW_INVARIANT_MATRIX_FILE")
    fi
    if [ -s "${AI_WORKFLOW_SYMBOL_SUMMARY_FILE:-}" ]; then
        _ACCEPTANCE_BUNDLE_ARGS+=(--symbol-summary "$AI_WORKFLOW_SYMBOL_SUMMARY_FILE")
    fi
    if "$PYTHON_CMD" "${SCRIPT_DIR}/build-acceptance-bundle.py" \
        "${_ACCEPTANCE_BUNDLE_ARGS[@]}" >/dev/null; then
        progress_log "Acceptance evidence saved: bundle=${ACCEPTANCE_BUNDLE_FILE}, capsule=${ACCEPTANCE_CAPSULE_FILE}"
    else
        progress_log "Acceptance bundle advisory failed; authoritative outcome remains ${OUTCOME_FILE}"
    fi
fi

# Record exactly one cross-model handoff after terminal evidence is available.
# Integrated aiwf runs point this at their run-events.jsonl; standalone
# dispatches use the repository-local handoff ledger.  Recording is advisory
# and must never change the dispatch result.
if [ -n "$PYTHON_CMD" ] && [ -f "$HANDOFF_RECORDER" ]; then
    HANDOFF_EVENTS_PATH="${AI_WORKFLOW_HANDOFF_EVENTS_PATH:-${REPO_ROOT}/.ai-workflow/handoff-events.jsonl}"
    HANDOFF_RUN_ID="${AI_WORKFLOW_HANDOFF_RUN_ID:-${AI_WORKFLOW_RUN_ID:-$TASK_ID}}"
    HANDOFF_TASK_ID="${AI_WORKFLOW_TASK_ID:-$TASK_ID}"
    HANDOFF_TASK_TYPE="${AI_WORKFLOW_HANDOFF_TASK_TYPE:-${CLAUDE_CODE_BUILDER_MODE:-${_PARSED_TASK_MODE:-unknown}}}"
    _HANDOFF_PAYLOAD_BYTES="unknown"
    _HANDOFF_TASK_CARD_BYTES="unknown"
    _HANDOFF_FIRST_ACTION_SECONDS="unknown"
    if [ -f "${WORKTREE_DIR}/CLAUDE_TASK_CARD.md" ]; then
        _HANDOFF_PAYLOAD_BYTES="$(wc -c < "${WORKTREE_DIR}/CLAUDE_TASK_CARD.md" | tr -d '[:space:]')"
    fi
    if [ -f "${WORKTREE_DIR}/TASK_CARD_FULL.md" ]; then
        _HANDOFF_TASK_CARD_BYTES="$(wc -c < "${WORKTREE_DIR}/TASK_CARD_FULL.md" | tr -d '[:space:]')"
    fi
    case "${FIRST_PROGRESS_ELAPSED_SECONDS:-}" in
        ''|*[!0-9]*)
            case "${FIRST_WORKTREE_CHANGE_SECONDS:-}" in
                ''|*[!0-9]*) ;;
                *) _HANDOFF_FIRST_ACTION_SECONDS="$FIRST_WORKTREE_CHANGE_SECONDS" ;;
            esac
            ;;
        *) _HANDOFF_FIRST_ACTION_SECONDS="$FIRST_PROGRESS_ELAPSED_SECONDS" ;;
    esac
    if _HANDOFF_RECORD_OUTPUT="$($PYTHON_CMD "$HANDOFF_RECORDER" \
        --events-path "$HANDOFF_EVENTS_PATH" \
        --run-id "$HANDOFF_RUN_ID" \
        --task-id "$HANDOFF_TASK_ID" \
        --sender "codex" \
        --receiver "claude" \
        --task-type "$HANDOFF_TASK_TYPE" \
        --dispatch-outcome "$DISPATCH_OUTCOME" \
        --payload-bytes "$_HANDOFF_PAYLOAD_BYTES" \
        --novel-payload-bytes "${AI_WORKFLOW_HANDOFF_NOVEL_PAYLOAD_BYTES:-unknown}" \
        --repeated-payload-bytes "${AI_WORKFLOW_HANDOFF_REPEATED_PAYLOAD_BYTES:-unknown}" \
        --task-card-bytes "$_HANDOFF_TASK_CARD_BYTES" \
        --review-packet-bytes "${AI_WORKFLOW_REVIEW_PACKET_BYTES:-unknown}" \
        --receiver-reads-before-first-action "${AI_WORKFLOW_HANDOFF_RECEIVER_READS:-unknown}" \
        --receiver-searches-before-first-action "${AI_WORKFLOW_HANDOFF_RECEIVER_SEARCHES:-unknown}" \
        --seconds-to-first-meaningful-action "$_HANDOFF_FIRST_ACTION_SECONDS" \
        --known-facts-rediscovered "${AI_WORKFLOW_HANDOFF_KNOWN_FACTS_REDISCOVERED:-unknown}" \
        --rejected-hypotheses-revisited "${AI_WORKFLOW_HANDOFF_REJECTED_HYPOTHESES_REVISITED:-unknown}" \
        --handoff-revision-count "${AI_WORKFLOW_HANDOFF_REVISION_COUNT:-unknown}" \
        --context-objects-requested "${AI_WORKFLOW_HANDOFF_CONTEXT_OBJECTS_REQUESTED:-unknown}" \
        --context-cache-hits "${AI_WORKFLOW_HANDOFF_CONTEXT_CACHE_HITS:-unknown}" 2>&1)"; then
        progress_log "Handoff event recorded: events=${HANDOFF_EVENTS_PATH}, result=${_HANDOFF_RECORD_OUTPUT}"
    else
        progress_log "Handoff event advisory: ${_HANDOFF_RECORD_OUTPUT:-recording failed}"
    fi
fi

# Canonical per-call usage is append-only and intentionally records incomplete
# terminal calls too. Legacy Markdown usage remains for compatibility.
MODEL_USAGE_HELPER="${SCRIPT_DIR}/model-usage.py"
MODEL_USAGE_LEDGER="${AI_WORKFLOW_MODEL_USAGE_LEDGER:-${REPO_ROOT}/.ai-workflow/model-usage.jsonl}"
if [ -n "$PYTHON_CMD" ] && [ -f "$MODEL_USAGE_HELPER" ] && [ -f "$RESULT_FILE" ]; then
    _CACHE_PROVIDER_ROUTE_SHA256="$("$PYTHON_CMD" - \
        "${ANTHROPIC_BASE_URL:-}" "$STARTUP_INTERACTION_HEALTH_FILE" "$INTERACTION_HEALTH_FILE" <<'PYEOF' 2>/dev/null || true
import hashlib, json, sys
from urllib.parse import urlsplit

route = sys.argv[1]
for path in sys.argv[2:]:
    try:
        value = json.load(open(path, encoding="utf-8"))
        route = value.get("base_url_origin") or route
    except (OSError, ValueError, TypeError):
        pass
if route:
    parsed = urlsplit(route)
    origin = "{}://{}".format(parsed.scheme.lower(), parsed.netloc.lower()) if parsed.netloc else route
    print("sha256:" + hashlib.sha256(origin.encode("utf-8")).hexdigest())
PYEOF
)"
    _CACHE_MODEL_HINT="$("$PYTHON_CMD" - \
        "$STARTUP_INTERACTION_HEALTH_FILE" "$INTERACTION_HEALTH_FILE" <<'PYEOF' 2>/dev/null || true
import json, sys

model = ""
for path in sys.argv[1:]:
    try:
        value = json.load(open(path, encoding="utf-8"))
        model = value.get("model") or model
    except (OSError, ValueError, TypeError):
        pass
print(model)
PYEOF
)"
    MODEL_USAGE_ARGS=(
        --source claude --input "$RESULT_FILE" --ledger "$MODEL_USAGE_LEDGER"
        --task-id "${AI_WORKFLOW_TASK_ID:-$TASK_ID}" --call-id "$TASK_ID" --role claude
        --stage "${_PARSED_TASK_MODE:-builder}" --result "$DISPATCH_OUTCOME"
        --cache-lane "$_CACHE_LANE"
        --stable-prefix-sha256 "$_CACHE_STABLE_PREFIX_SHA256"
        --tool-schema-sha256 "$_CACHE_TOOL_SCHEMA_SHA256"
        --task-suffix-sha256 "$_CACHE_TASK_SUFFIX_SHA256"
        --session-mode "$CLAUDE_SESSION_MODE_EFFECTIVE"
        --session-resume-status "$CLAUDE_SESSION_RESUME_STATUS"
    )
    if [ -n "$_CACHE_PROVIDER_ROUTE_SHA256" ]; then
        MODEL_USAGE_ARGS+=(--provider-route-sha256 "$_CACHE_PROVIDER_ROUTE_SHA256")
    fi
    if [ -n "$_CACHE_MODEL_HINT" ]; then
        MODEL_USAGE_ARGS+=(--model "$_CACHE_MODEL_HINT")
    fi
    if [ -n "${FIRST_PROGRESS_ELAPSED_SECONDS:-}" ]; then
        MODEL_USAGE_ARGS+=(--first-progress-ms "$((FIRST_PROGRESS_ELAPSED_SECONDS * 1000))")
    fi
    if [ -n "${AI_WORKFLOW_RUN_ID:-}" ]; then
        MODEL_USAGE_ARGS+=(--run-id "$AI_WORKFLOW_RUN_ID")
    fi
    if [ -n "${AI_WORKFLOW_EXPERIMENT_ARM:-}" ]; then
        MODEL_USAGE_ARGS+=(--experiment-arm "$AI_WORKFLOW_EXPERIMENT_ARM")
    fi
    "$PYTHON_CMD" "$MODEL_USAGE_HELPER" capture "${MODEL_USAGE_ARGS[@]}" \
        >/dev/null 2>>"$PROGRESS_FILE" || \
        progress_log "Warning: canonical Claude usage capture failed; legacy usage evidence was preserved"
fi

ATTEMPT_FAILURE_CLASS="unavailable"
ATTEMPT_COUNTS_TOWARD_TAKEOVER="unknown"
ATTEMPT_RECOMMENDED_ACTION="inspect-evidence-before-counting"
ATTEMPT_SAME_WORKTREE_RETRY="false"
if [ -n "$PYTHON_CMD" ] && [ -f "${SCRIPT_DIR}/classify-claude-attempt.py" ]; then
    # Claude runs in a task-owned background process. A resume fallback can
    # replace its session UUID there, so terminal accounting must read the
    # authoritative runtime receipt instead of the dispatcher's stale shell
    # variable.
    _ATTEMPT_SESSION_ID="$CLAUDE_SESSION_ID"
    if [ -s "$RUNTIME_JSON" ]; then
        _ATTEMPT_SESSION_ID="$("$PYTHON_CMD" - "$RUNTIME_JSON" <<'PYEOF' 2>/dev/null || true
import json, sys
try:
    value = json.load(open(sys.argv[1], encoding="utf-8"))
    print(value.get("claude_session_id", ""))
except (OSError, ValueError, TypeError):
    pass
PYEOF
)"
    fi
    _ATTEMPT_PROGRESS="none"
    if [ "$DISPATCH_EVIDENCE_STATE" = "acknowledgement only" ]; then
        _ATTEMPT_PROGRESS="acknowledgement"
    elif [ "${BLOCKER_RECORDED:-0}" -eq 1 ]; then
        _ATTEMPT_PROGRESS="blocker"
    elif [ "$IMPLEMENTATION_CHANGES" -gt 0 ] || \
         { [ "$VALID_CLAUDE_REPORT" -eq 1 ] && [ "$_PARSED_TASK_MODE" != "builder" ]; }; then
        _ATTEMPT_PROGRESS="useful"
    fi
    _ATTEMPT_ARGS=(
        --exit-code "$CLAUDE_STATUS" --outcome "$DISPATCH_OUTCOME"
        --diff-changes "$IMPLEMENTATION_CHANGES" --progress "$_ATTEMPT_PROGRESS"
        --direction "$ADVISOR_DIRECTION" --error-text-file "$STATUS_FILE"
        --blocker-kind "$ADVISOR_BLOCKER_KIND"
        --delegation-mode "${AI_WORKFLOW_DELEGATION_MODE:-unknown}"
        --retry-ordinal "${_RETRY_ORDINAL:-0}"
        --task-mode "${_PARSED_TASK_MODE:-unknown}"
        --report-consistency "${REPORT_CONSISTENCY_STATUS:-not-run}"
    )
    # A classification can be retained without an identity for diagnostics,
    # but only a complete session-bound identity may later participate in a
    # two-round takeover candidate.
    if [ -n "$_ATTEMPT_SESSION_ID" ] && [ -f "${WORKTREE_DIR}/TASK_CARD_FULL.md" ]; then
        _ATTEMPT_ARGS+=(
            --task-id "$TASK_ID"
            --lineage-root-task-id "${_LINEAGE_ROOT_TASK_ID:-$TASK_ID}"
            --task-card "${WORKTREE_DIR}/TASK_CARD_FULL.md"
            --source-base-commit "$BASE_COMMIT"
            --execution-base-commit "$WORKTREE_START_COMMIT"
            --source-repository "$REPO_ROOT"
            --worktree "$WORKTREE_DIR"
            --claude-session-id "$_ATTEMPT_SESSION_ID"
        )
        if [ -n "${CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID:-}" ]; then
            _ATTEMPT_ARGS+=(--retry-of "$CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID")
        fi
    fi
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

# Failure accounting cannot cross a reviewed/advisor continuation or a fresh
# route. Only the explicit retry-in-place edge joins two attempts, and the
# receipt builder additionally binds both attempts to one Claude session.
_TAKEOVER_PRIOR_TASK_ID="${CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID:-}"
if [ -n "$_TAKEOVER_PRIOR_TASK_ID" ] && [ -s "$ATTEMPT_CLASSIFICATION_FILE" ] && \
   [ -s "${WORKTREE_ROOT}/${_TAKEOVER_PRIOR_TASK_ID}.attempt-classification.json" ] && \
   [ -s "${WORKTREE_ROOT}/${_TAKEOVER_PRIOR_TASK_ID}.runtime.json" ] && \
   [ -f "${SCRIPT_DIR}/build-takeover-receipt.py" ]; then
    if "$PYTHON_CMD" "${SCRIPT_DIR}/build-takeover-receipt.py" \
        --current "$ATTEMPT_CLASSIFICATION_FILE" \
        --prior "${WORKTREE_ROOT}/${_TAKEOVER_PRIOR_TASK_ID}.attempt-classification.json" \
        --task-card "${WORKTREE_DIR}/TASK_CARD_FULL.md" \
        --runtime "$RUNTIME_JSON" \
        --prior-runtime "${WORKTREE_ROOT}/${_TAKEOVER_PRIOR_TASK_ID}.runtime.json" \
        --current-task-id "$TASK_ID" --prior-task-id "$_TAKEOVER_PRIOR_TASK_ID" \
        --lineage-root-task-id "${_LINEAGE_ROOT_TASK_ID:-$_TAKEOVER_PRIOR_TASK_ID}" \
        --output "$TAKEOVER_RECEIPT_FILE" >/dev/null 2>&1; then
        ATTEMPT_RECOMMENDED_ACTION="prepare-codex-takeover"
        progress_log "Takeover candidate issued after two counted rounds; process-stop preparation is still required: ${TAKEOVER_RECEIPT_FILE}"
    fi
fi

HANDOFF_STATUS="missing"
HANDOFF_PATCH_BYTES="0"
HANDOFF_OUT_OF_SCOPE_COUNT="0"
HANDOFF_DELIVERABLE="no"
if [ -n "$PYTHON_CMD" ] && [ -s "$SCOPED_HANDOFF_MANIFEST_FILE" ]; then
    IFS=$'\t' read -r HANDOFF_STATUS HANDOFF_PATCH_BYTES HANDOFF_OUT_OF_SCOPE_COUNT HANDOFF_DELIVERABLE < <(
        "$PYTHON_CMD" - "$SCOPED_HANDOFF_MANIFEST_FILE" <<'PYEOF' 2>/dev/null || printf 'invalid\t0\t0\tno\n'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
patch = value.get("patch") if isinstance(value.get("patch"), dict) else {}
out_of_scope = value.get("out_of_scope_product_paths")
if not isinstance(out_of_scope, list):
    out_of_scope = value.get("unexpected_changed_paths") or []
print("\t".join((
    str(value.get("status", "unknown")),
    str(patch.get("bytes", 0)),
    str(len(out_of_scope)),
    "yes" if value.get("deliverable") else "no",
)))
PYEOF
    )
fi
progress_log "Dispatch evidence classification: state=${DISPATCH_EVIDENCE_STATE}, implementation_changes=${IMPLEMENTATION_CHANGES}, valid_claude_report=$([ "$VALID_CLAUDE_REPORT" -eq 1 ] && echo yes || echo no), report_artifact_reason=${REPORT_ARTIFACT_REASON}, dispatch_outcome=${DISPATCH_OUTCOME}, semantic_error=$([ "$CLAUDE_SEMANTIC_ERROR" -eq 1 ] && echo yes || echo no), probe_mode=${CLAUDE_CODE_API_PROBE_MODE}, probe_environment=${CLAUDE_CODE_PROBE_ENVIRONMENT}, first_progress_action=${CLAUDE_CODE_FIRST_PROGRESS_ACTION}, observation_probe_ran=$([ "${_OBSERVATION_PROBE_RAN:-0}" -eq 1 ] && echo yes || echo no)"
progress_log "Delivery summary: product_changes=${IMPLEMENTATION_CHANGES}, report_reason=${REPORT_ARTIFACT_REASON}, report_normalized=${REPORT_ARTIFACT_NORMALIZED}, handoff_status=${HANDOFF_STATUS}, patch_bytes=${HANDOFF_PATCH_BYTES}, out_of_scope_product_paths=${HANDOFF_OUT_OF_SCOPE_COUNT}, deliverable=${HANDOFF_DELIVERABLE}"
progress_log "Outcome gates: dispatch_success=${DISPATCH_SUCCESS}, artifact_valid=${ARTIFACT_VALID}, report_consistency=${REPORT_CONSISTENCY_STATUS}, validation_success=${VALIDATION_STATUS}, semantic_acceptance=${SEMANTIC_ACCEPTANCE}, completion_state=${COMPLETION_STATE}, operator_state=${OPERATOR_STATE}, artifact=${OUTCOME_FILE}"
progress_log "API attribution: startup_conclusion=${_STARTUP_PROBE_CONCLUSION:-not-run}, startup_source=${_STARTUP_PROBE_SOURCE:-not-run}, zero_output_conclusion=${ZERO_OUTPUT_PROBE_CONCLUSION}, authoritative=${ZERO_OUTPUT_PROBE_AUTHORITATIVE}"
# Authoritative final outcome — emitted exactly once, after semantic validation.
progress_log "Final dispatch outcome: ${DISPATCH_OUTCOME}, elapsed_seconds=${ELAPSED}, semantic_error=$([ "$CLAUDE_SEMANTIC_ERROR" -eq 1 ] && echo yes || echo no)"
{
    echo ""
    echo "[dispatch] Evidence classification: ${DISPATCH_EVIDENCE_STATE}"
    echo "[dispatch] Implementation changes: ${IMPLEMENTATION_CHANGES}"
    echo "[dispatch] Valid Claude-owned report: $([ "$VALID_CLAUDE_REPORT" -eq 1 ] && echo yes || echo no)"
    echo "[dispatch] Claude report artifact reason: ${REPORT_ARTIFACT_REASON}"
    echo "[dispatch] Claude report normalized: ${REPORT_ARTIFACT_NORMALIZED}"
    echo "[dispatch] Scoped handoff status: ${HANDOFF_STATUS}"
    echo "[dispatch] Scoped patch bytes: ${HANDOFF_PATCH_BYTES}"
    echo "[dispatch] Out-of-scope product paths: ${HANDOFF_OUT_OF_SCOPE_COUNT}"
    echo "[dispatch] Deliverable: ${HANDOFF_DELIVERABLE}"
    echo "[dispatch] Dispatch outcome: ${DISPATCH_OUTCOME}"
    echo "[dispatch] Dispatch success: ${DISPATCH_SUCCESS}"
    echo "[dispatch] Artifact valid: ${ARTIFACT_VALID}"
    echo "[dispatch] Report consistency: ${REPORT_CONSISTENCY_STATUS}"
    echo "[dispatch] Validation success: ${VALIDATION_STATUS}"
    echo "[dispatch] Semantic acceptance: ${SEMANTIC_ACCEPTANCE}"
    echo "[dispatch] Completion state: ${COMPLETION_STATE}"
    echo "[dispatch] Operator state: ${OPERATOR_STATE}"
    echo "[dispatch] Outcome artifact: ${OUTCOME_FILE}"
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
    echo "[dispatch] Startup probe source: ${_STARTUP_PROBE_SOURCE:-not-run}"
    echo "[dispatch] Startup interaction health artifact: ${STARTUP_INTERACTION_HEALTH_FILE}"
    echo "[dispatch] Zero-output API probe: ${ZERO_OUTPUT_PROBE_CONCLUSION}"
    echo "[dispatch] Zero-output API probe authoritative: ${ZERO_OUTPUT_PROBE_AUTHORITATIVE}"
    echo "[dispatch] Interaction health artifact: ${INTERACTION_HEALTH_FILE}"
    echo "[dispatch] Same-worktree retry eligible: ${ATTEMPT_SAME_WORKTREE_RETRY}"
    if [ -s "$TAKEOVER_RECEIPT_FILE" ]; then
        echo "[dispatch] Bounded takeover receipt: ${TAKEOVER_RECEIPT_FILE}"
    fi
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

# Any useful model-owned evidence is a stronger availability signal than the
# fixed probe and refreshes the same bounded cache.
if [ "$IMPLEMENTATION_CHANGES" -gt 0 ] || \
   [ "$VALID_CLAUDE_REPORT" -eq 1 ] || \
   [ "${_ATTEMPT_PROGRESS:-none}" = "useful" ] || \
   [ "${_ATTEMPT_PROGRESS:-none}" = "acknowledgement" ] || \
   [ "${_ATTEMPT_PROGRESS:-none}" = "blocker" ]; then
    record_api_availability "dispatch-evidence"
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
        echo "- Context-acquisition timeout seconds: ${CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS:-0}"
        echo "- Active execution window seconds: ${CLAUDE_CODE_TIMEOUT_SECONDS:-0}"
        echo "- Active window refreshed: $([ "${ACTIVE_WINDOW_REFRESHED:-0}" -eq 1 ] && echo yes || echo no)"
        echo "- Hard timeout seconds: ${CLAUDE_CODE_HARD_TIMEOUT_SECONDS:-0}"
        echo "- Progress extension used: $([ "${TIMEOUT_EXTENSION_ACTIVE:-0}" -eq 1 ] && echo yes || echo no)"
        if [ "${TIMEOUT_EXTENSION_ACTIVE:-0}" -eq 1 ]; then
            echo "- Extension seconds: ${CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS:-0}"
            echo "- Extension reason: ${TIMEOUT_EXTENSION_REASON:-}"
        elif [ -n "${TIMEOUT_EXTENSION_REASON:-}" ]; then
            echo "- Base timeout reason: ${TIMEOUT_EXTENSION_REASON}"
        fi
        echo "- Growth extension policy: renewable product growth until hard timeout"
        echo "- Growth extension count: ${TIMEOUT_EXTENSION_COUNT:-0}"
        echo "- Second extension used: $([ "${SECOND_EXTENSION_ACTIVE:-0}" -eq 1 ] && echo yes || echo no)"
        echo "- No-output timed out: $([ "$CLAUDE_NO_OUTPUT_TIMED_OUT" -eq 1 ] && echo yes || echo no)"
        echo "- First-progress timed out: $([ "${CLAUDE_FIRST_PROGRESS_TIMED_OUT:-0}" -eq 1 ] && echo yes || echo no)"
        echo "- First-progress signal: ${FIRST_PROGRESS_SIGNAL:-none}"
        echo "- Builder mode: ${CLAUDE_CODE_BUILDER_MODE:-standard}"
        echo "- API probe mode: ${CLAUDE_CODE_API_PROBE_MODE:-adaptive}"
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
        echo "- Canonical product state: $PRODUCT_STATE_FILE"
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

_FINAL_OBSERVATION_EPOCH="$(date +%s)"
LAST_SESSION_ACTIVITY_EPOCH="$(observe_runtime_activity "$_FINAL_OBSERVATION_EPOCH" "$EXECUTION_ACTIVITY_STATE" 2>/dev/null || echo 0)"
case "$LAST_SESSION_ACTIVITY_EPOCH" in ''|*[!0-9]*) LAST_SESSION_ACTIVITY_EPOCH=0 ;; esac
SESSION_ACTIVITY_SECONDS_AGO=-1
[ "$LAST_SESSION_ACTIVITY_EPOCH" -le 0 ] || SESSION_ACTIVITY_SECONDS_AGO=$((_FINAL_OBSERVATION_EPOCH - LAST_SESSION_ACTIVITY_EPOCH))
PRODUCT_ACTIVITY_SECONDS_AGO=-1
[ "$LAST_PRODUCT_CHANGE_EPOCH" -le 0 ] || PRODUCT_ACTIVITY_SECONDS_AGO=$((_FINAL_OBSERVATION_EPOCH - LAST_PRODUCT_CHANGE_EPOCH))
ALL_WORKTREE_CHANGES=$((TOTAL_PRODUCT_CHANGES + CONTROL_CHANGES))
monitor_event "event=terminal running=no terminal=yes exit_status=${CLAUDE_STATUS} dispatch_outcome=${DISPATCH_OUTCOME} dispatch_success=${DISPATCH_SUCCESS} artifact_valid=${ARTIFACT_VALID} validation_success=${VALIDATION_STATUS} semantic_acceptance=${SEMANTIC_ACCEPTANCE} completion_state=${COMPLETION_STATE} operator_state=${OPERATOR_STATE} evidence_state=${DISPATCH_EVIDENCE_CODE} worktree_changes=${ALL_WORKTREE_CHANGES} product_changes=${IMPLEMENTATION_CHANGES} total_product_changes=${TOTAL_PRODUCT_CHANGES} control_changes=${CONTROL_CHANGES} product_delta_from_baseline=${FINAL_PRODUCT_DELTA} product_hash=${FINAL_PRODUCT_DIGEST:-unavailable} edit_ready=${EDIT_READY_DETECTED} execution_state=${EXECUTION_ACTIVITY_STATE} session_activity_seconds_ago=${SESSION_ACTIVITY_SECONDS_AGO} product_activity_seconds_ago=${PRODUCT_ACTIVITY_SECONDS_AGO} last_product_change_epoch=${LAST_PRODUCT_CHANGE_EPOCH} activity_receipt=${ACTIVITY_OBSERVATION_FILE} product_idle_seconds=${PRODUCT_IDLE_SECONDS} idle_confirmations=${PRODUCT_IDLE_CONFIRMATION_COUNT} product_idle_stopped=${PRODUCT_IDLE_STOPPED} extension_advisor_state=${EXTENSION_ADVISOR_STATE:-not-run} extension_advisor_last_status=${EXTENSION_ADVISOR_LAST_STATUS:-not-run} extension_advisor_attempts=${EXTENSION_ADVISOR_ATTEMPTS:-0} extension_advisor_receipt=${EXTENSION_ADVISOR_RECEIPT_FILE}"
DISPATCH_FINALIZED=1

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
if [ -n "${_REVIEWED_CONTINUATION_TASK_ID:-}" ]; then
    echo "Worktree Strategy: reviewed-continuation (prior: ${_REVIEWED_CONTINUATION_TASK_ID}, approval: ${_REVIEWED_CONTINUATION_APPROVAL_ID})"
elif [ -n "${_RETRY_TASK_ID:-}" ]; then
    echo "Worktree Strategy: retry-in-place (prior: ${CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID})"
else
    echo "Worktree Strategy: $CLAUDE_CODE_WORKTREE_STRATEGY"
fi
echo "Runtime ID:       $TASK_ID"
echo "Runtime Identity: $RUNTIME_JSON"
echo "Phase Metrics:    $PHASE_METRICS_FILE"
if [ "$_CONTEXT_CHECKPOINT_MODE" != "none" ]; then
    echo "Context Checkpoint: ${REHYDRATE_FROM_OPTION} (${_CONTEXT_CHECKPOINT_MODE})"
fi
echo "Large Repo Mode: $CLAUDE_CODE_LARGE_REPO_MODE"
echo "Prompt Profile:  $CLAUDE_CODE_PROMPT_PROFILE"
echo "Evidence Mode:   $CLAUDE_CODE_EVIDENCE_MODE"
if [ "$_TASK_MODE_NORMALIZED" -eq 1 ]; then
    echo "Task Mode:       ${_PARSED_TASK_MODE} (normalized from ${_DECLARED_TASK_MODE}; ${_TASK_MODE_NORMALIZATION_REASON})"
else
    echo "Task Mode:       ${_PARSED_TASK_MODE:-unknown}"
fi
echo "Builder Mode:    $CLAUDE_CODE_BUILDER_MODE"
echo "Tool Profile:    $CLAUDE_CODE_TOOL_PROFILE (${_TOOL_PROFILE_DERIVATION})"
echo "First Progress:  ${CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS}s ${CLAUDE_CODE_FIRST_PROGRESS_ACTION}"
echo "Context Window:  ${CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS}s"
echo "Active Window:   ${CLAUDE_CODE_TIMEOUT_SECONDS}s (renewed by product growth; hard-cap bounded)"
echo "Growth Ext:      ${CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS}s initial, ${CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS}s renewable (hard-cap bounded)"
echo "Hard Cap:        ${CLAUDE_CODE_HARD_TIMEOUT_SECONDS}s"
echo "Dispatch Outcome:${DISPATCH_OUTCOME}"
echo "Completion State:${COMPLETION_STATE}"
echo "Operator State:  ${OPERATOR_STATE}"
echo "Product Changes: ${IMPLEMENTATION_CHANGES}"
echo "Report Reason:   ${REPORT_ARTIFACT_REASON} (normalized=${REPORT_ARTIFACT_NORMALIZED})"
echo "Scoped Handoff:  ${HANDOFF_STATUS} (patch_bytes=${HANDOFF_PATCH_BYTES}, out_of_scope=${HANDOFF_OUT_OF_SCOPE_COUNT}, deliverable=${HANDOFF_DELIVERABLE})"
echo "Outcome Gates:   $OUTCOME_FILE"
echo "Product State:   $PRODUCT_STATE_FILE"
if [ -s "$ACCEPTANCE_BUNDLE_FILE" ]; then
    echo "Acceptance Bundle: $ACCEPTANCE_BUNDLE_FILE"
fi
if [ -s "$ACCEPTANCE_CAPSULE_FILE" ]; then
    echo "Acceptance Capsule: $ACCEPTANCE_CAPSULE_FILE"
fi
echo "Task Card Full:  ${WORKTREE_DIR}/TASK_CARD_FULL.md"
echo "Claude Task:     ${WORKTREE_DIR}/CLAUDE_TASK_CARD.md"
echo "Result:          $RESULT_FILE"
echo "Raw Result:      $RAW_RESULT_FILE"
echo "Status:          $STATUS_FILE"
echo "Network Log:     $NETWORK_FILE"
echo "Attempt Class:   $ATTEMPT_CLASSIFICATION_FILE"
echo "Runtime Bundle:  $MANAGED_RUNTIME_BUNDLE_FILE"
if [ -s "$SKILL_CONTEXT_COMPILATION_FILE" ]; then
    echo "Skill Context:   $SKILL_CONTEXT_COMPILATION_FILE"
fi
if [ -s "$DIRTY_SNAPSHOT_RECEIPT_FILE" ]; then
    echo "Dirty Snapshot:  $DIRTY_SNAPSHOT_RECEIPT_FILE"
fi
if [ -s "$CHECKER_CONTRACT_RECEIPT_FILE" ]; then
    echo "Checker Contract:$CHECKER_CONTRACT_RECEIPT_FILE"
fi
if [ -s "$TAKEOVER_RECEIPT_FILE" ]; then
    echo "Takeover Receipt: $TAKEOVER_RECEIPT_FILE"
fi
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
echo "Phase Events:    $PHASE_EVENT_LOG"
echo "Report:          $REPORT_FILE"
echo "Claude PID:      $PID_FILE"
echo "Dispatcher PID:  $DISPATCHER_PID_FILE"
echo "Claude Role PID: $CLAUDE_PID_FILE"
echo "Checker PID:     $CHECKER_PID_FILE"
echo "Progress Log:    $PROGRESS_FILE"
echo "Agent Wait (once): bash \"$MONITOR_SCRIPT\" wait \"$TASK_ID\" --until terminal"
echo "Manual diagnostics only: bash \"${SCRIPT_DIR}/status-claude.sh\" \"$TASK_ID\" --details"
echo ""
echo "Changes have NOT been merged. Review the diff and merge manually."
if [ "$CLAUDE_CODE_WORKTREE_STRATEGY" = "reuse-managed" ]; then
    echo "Reusable managed worktree kept for future dispatches: $WORKTREE_DIR"
    echo "To discard it: git worktree remove $WORKTREE_DIR"
else
    echo "To remove the worktree: git worktree remove $WORKTREE_DIR"
fi
