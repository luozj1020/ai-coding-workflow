#!/usr/bin/env bash
# run-codex-spark.sh  -  Optional Codex Spark auxiliary execution for the workflow.
#
# Usage:
#   bash ai/run-codex-spark.sh <task-card> [options]
#   bash ai/run-codex-spark.sh --brief "<short task summary>" [options]
#       [--model gpt-5.3-codex-spark] [--sandbox read-only|workspace-write]
#       [--budget-mode aggressive|balanced|conservative]
#       [--fast-path-max-diff-lines N]
#       [--artifact .worktrees/claude-....report.md] [--output .worktrees/codex-spark-...]
#
# Defaults are intentionally conservative: auto-selected auxiliary role,
# read-only, balanced budget mode, optional Spark, and no strong-model fallback.

set -euo pipefail

PATH="/usr/bin:/bin:/mingw64/bin:${PATH}"
export PATH

usage() {
    cat >&2 <<'EOF'
Usage: run-codex-spark.sh <task-card> [options]
       run-codex-spark.sh (--brief TEXT|--brief-file PATH|--stdin-brief) [options]

Options:
  --brief TEXT      Use a short pre-task-card brief for early size/cost routing.
  --brief-file PATH Read the pre-task-card brief from PATH.
  --stdin-brief     Read the pre-task-card brief from stdin.
  --mode MODE       auto, task-size-classifier, execution-cost-estimator,
                    review-only, task-card-audit, plan-splitter,
                    validation-planner, failure-triage, evidence-checker,
                    micro-builder, controlled-builder, parallel-planner,
                    observe-synthesizer, task-card-drafter,
                    context-packet-builder, preflight-bundle, direction-precheck,
                    acceptance-matrix, postflight-bundle, revision-drafter,
                    lesson-extractor, or monitor-triage
  --fast-path-max-diff-lines N
                    Explicit ordinary diff threshold override (1-200; default
                    is selected from repository scale)
  --concentrated-fast-path-max-diff-lines N
                    Explicit concentrated context-reuse threshold override
                    (100-500; default is selected from repository scale)
  --repository-scale SCALE
                    auto, small, medium, large, or giant (default auto)
  --routing-event EVENT
                    Pre-card event: initial, revision, narrow, retry, or next-phase
  --model MODEL     Codex model slug (default: gpt-5.3-codex-spark)
  --sandbox MODE    read-only or workspace-write (default: read-only)
  --budget-mode     aggressive, balanced, or conservative (default: balanced)
  --result-mode MODE
                    direct, minimal, or full (default: auto-resolved per mode)
  --allow-write PATH
                    Allow controlled-builder to write REPO_RELATIVE_PATH.
                    May be passed 1-3 times for controlled-builder mode.
  --max-diff-lines N
                    Maximum added+deleted lines for controlled-builder (1-200)
  --artifact PATH   Add a bounded artifact excerpt to the Spark prompt.
                    May be passed more than once.
  --output DIR      Artifact directory (default: .worktrees/codex-spark-<timestamp>)
  --diagnostics MODE
                    off, failure, or full (default: failure).
                    off: strict zero-persistence even on failure.
                    failure: preserve compact diagnostic on unusable result.
                    full: preserve existing full evidence set for reproduction.
  --allow-dirty-source
                    Allow micro-builder dispatch from a dirty source repo
  --require-spark   Treat Spark unavailability as a hard helper failure
  --execution-env ENV
                    Execution environment: auto, host, or sandbox (default: auto).
                    auto: detect restricted sandbox from CODEX_SANDBOX_NETWORK_DISABLED.
                    host: caller asserts outside-sandbox authority; unsets inherited marker.
                    sandbox: preserve inherited marker and existing invocation behavior.
  -h, --help        Show this help

Environment:
  CODEX_SPARK_CODEX_BIN
  CODEX_SPARK_MODEL
  CODEX_SPARK_MODE
  CODEX_SPARK_SANDBOX
  CODEX_SPARK_OUTPUT_DIR
  CODEX_SPARK_RESULT_MODE=direct|minimal|full
  CODEX_SPARK_DIAGNOSTICS=off|failure|full
  CODEX_SPARK_ARTIFACT_LINES=160
  CODEX_SPARK_STDOUT_MAX_BYTES=32768
  CODEX_SPARK_CALL_TIMEOUT_SECONDS=75
  CODEX_SPARK_ALLOW_DIRTY_SOURCE=1
  CODEX_SPARK_REQUIRED=1
  AI_SPARK_BUDGET_MODE=aggressive|balanced|conservative
  CODEX_FAST_PATH_MAX_DIFF_LINES=100
  CODEX_CONCENTRATED_FAST_PATH_MAX_DIFF_LINES=500
  CODEX_FULL_REREVIEW_FAST_PATH_MAX_DIFF_LINES=800
  CODEX_FULL_REREVIEW_FAST_PATH_MAX_FILES=8
  CODEX_REPOSITORY_SCALE=auto
  CODEX_SPARK_ROUTING_EVENT=initial|revision|narrow|retry|next-phase
  CODEX_SPARK_EXECUTION_ENV=auto|host|sandbox
EOF
}

TASK_CARD=""
BRIEF_TEXT=""
BRIEF_FILE=""
STDIN_BRIEF="no"
INPUT_KIND="task-card"
CODEX_BIN="${CODEX_SPARK_CODEX_BIN:-codex}"
MODE="${CODEX_SPARK_MODE:-auto}"
REQUESTED_MODE="$MODE"
MODEL="${CODEX_SPARK_MODEL:-gpt-5.3-codex-spark}"
SANDBOX="${CODEX_SPARK_SANDBOX:-read-only}"
OUTPUT_DIR="${CODEX_SPARK_OUTPUT_DIR:-}"
ARTIFACT_LINES="${CODEX_SPARK_ARTIFACT_LINES:-160}"
STDOUT_MAX_BYTES="${CODEX_SPARK_STDOUT_MAX_BYTES:-32768}"
CALL_TIMEOUT_SECONDS="${CODEX_SPARK_CALL_TIMEOUT_SECONDS:-75}"
ALLOW_DIRTY_SOURCE="${CODEX_SPARK_ALLOW_DIRTY_SOURCE:-0}"
REQUIRE_SPARK="${CODEX_SPARK_REQUIRED:-0}"
BUDGET_MODE="${AI_SPARK_BUDGET_MODE:-balanced}"
REQUESTED_BUDGET_MODE="$BUDGET_MODE"
SPARK_INVOKED="yes"
SPARK_MODEL_RESPONSE_RECEIVED="no"
SPARK_AUTO_DISABLED="no"
SPARK_DISABLE_REASON="not applicable"
SPARK_HOST_HANDOFF_REQUIRED="no"
SPARK_CHECKS_RUN="codex exec"
HELPER_EXIT_STATUS=0
SPARK_PIPELINE_STAGE=""
SPARK_ROLES_EXECUTED=""
SPARK_CALLS_USED=0
DIRECT_ENVELOPE_STARTED="no"
SPARK_PROVISIONAL_ACCEPTANCE="not applicable"
ARTIFACTS=()
RESULT_MODE="${CODEX_SPARK_RESULT_MODE:-}"
EXPLICIT_RESULT_MODE="no"
DIAGNOSTICS_MODE="${CODEX_SPARK_DIAGNOSTICS:-failure}"
ALLOWED_WRITES=()
MAX_DIFF_LINES=""
FAST_PATH_MAX_DIFF_LINES="${CODEX_FAST_PATH_MAX_DIFF_LINES:-100}"
CONCENTRATED_FAST_PATH_MAX_DIFF_LINES="${CODEX_CONCENTRATED_FAST_PATH_MAX_DIFF_LINES:-500}"
FULL_REREVIEW_FAST_PATH_MAX_DIFF_LINES="${CODEX_FULL_REREVIEW_FAST_PATH_MAX_DIFF_LINES:-800}"
FULL_REREVIEW_FAST_PATH_MAX_FILES="${CODEX_FULL_REREVIEW_FAST_PATH_MAX_FILES:-8}"
FAST_PATH_THRESHOLD_EXPLICIT="$([ -n "${CODEX_FAST_PATH_MAX_DIFF_LINES+x}" ] && echo yes || echo no)"
CONCENTRATED_THRESHOLD_EXPLICIT="$([ -n "${CODEX_CONCENTRATED_FAST_PATH_MAX_DIFF_LINES+x}" ] && echo yes || echo no)"
REPOSITORY_SCALE_REQUESTED="${CODEX_REPOSITORY_SCALE:-auto}"
ROUTING_EVENT="${CODEX_SPARK_ROUTING_EVENT:-initial}"
EXPLICIT_OUTPUT="no"
EXECUTION_ENV="${CODEX_SPARK_EXECUTION_ENV:-auto}"

while [ $# -gt 0 ]; do
    case "$1" in
        --brief)
            [ $# -ge 2 ] || { echo "Error: --brief requires a value." >&2; exit 1; }
            BRIEF_TEXT="$2"
            shift 2
            ;;
        --brief-file)
            [ $# -ge 2 ] || { echo "Error: --brief-file requires a value." >&2; exit 1; }
            BRIEF_FILE="$2"
            shift 2
            ;;
        --stdin-brief)
            STDIN_BRIEF="yes"
            shift
            ;;
        --mode)
            [ $# -ge 2 ] || { echo "Error: --mode requires a value." >&2; exit 1; }
            MODE="$2"
            REQUESTED_MODE="$MODE"
            shift 2
            ;;
        --model)
            [ $# -ge 2 ] || { echo "Error: --model requires a value." >&2; exit 1; }
            MODEL="$2"
            shift 2
            ;;
        --sandbox)
            [ $# -ge 2 ] || { echo "Error: --sandbox requires a value." >&2; exit 1; }
            SANDBOX="$2"
            shift 2
            ;;
        --budget-mode)
            [ $# -ge 2 ] || { echo "Error: --budget-mode requires a value." >&2; exit 1; }
            BUDGET_MODE="$2"
            REQUESTED_BUDGET_MODE="$BUDGET_MODE"
            shift 2
            ;;
        --artifact)
            [ $# -ge 2 ] || { echo "Error: --artifact requires a value." >&2; exit 1; }
            ARTIFACTS+=("$2")
            shift 2
            ;;
        --result-mode)
            [ $# -ge 2 ] || { echo "Error: --result-mode requires a value." >&2; exit 1; }
            RESULT_MODE="$2"
            EXPLICIT_RESULT_MODE="yes"
            shift 2
            ;;
        --allow-write)
            [ $# -ge 2 ] || { echo "Error: --allow-write requires a value." >&2; exit 1; }
            ALLOWED_WRITES+=("$2")
            shift 2
            ;;
        --max-diff-lines)
            [ $# -ge 2 ] || { echo "Error: --max-diff-lines requires a value." >&2; exit 1; }
            MAX_DIFF_LINES="$2"
            shift 2
            ;;
        --fast-path-max-diff-lines)
            [ $# -ge 2 ] || { echo "Error: --fast-path-max-diff-lines requires a value." >&2; exit 1; }
            FAST_PATH_MAX_DIFF_LINES="$2"
            FAST_PATH_THRESHOLD_EXPLICIT="yes"
            shift 2
            ;;
        --concentrated-fast-path-max-diff-lines)
            [ $# -ge 2 ] || { echo "Error: --concentrated-fast-path-max-diff-lines requires a value." >&2; exit 1; }
            CONCENTRATED_FAST_PATH_MAX_DIFF_LINES="$2"
            CONCENTRATED_THRESHOLD_EXPLICIT="yes"
            shift 2
            ;;
        --repository-scale)
            [ $# -ge 2 ] || { echo "Error: --repository-scale requires a value." >&2; exit 1; }
            REPOSITORY_SCALE_REQUESTED="$2"
            shift 2
            ;;
        --routing-event)
            [ $# -ge 2 ] || { echo "Error: --routing-event requires a value." >&2; exit 1; }
            ROUTING_EVENT="$2"
            shift 2
            ;;
        --output)
            [ $# -ge 2 ] || { echo "Error: --output requires a value." >&2; exit 1; }
            OUTPUT_DIR="$2"
            EXPLICIT_OUTPUT="yes"
            shift 2
            ;;
        --diagnostics)
            [ $# -ge 2 ] || { echo "Error: --diagnostics requires a value." >&2; exit 1; }
            DIAGNOSTICS_MODE="$2"
            shift 2
            ;;
        --allow-dirty-source)
            ALLOW_DIRTY_SOURCE="1"
            shift
            ;;
        --require-spark)
            REQUIRE_SPARK="1"
            shift
            ;;
        --execution-env)
            [ $# -ge 2 ] || { echo "Error: --execution-env requires a value." >&2; exit 1; }
            EXECUTION_ENV="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --*)
            echo "Error: unknown option: $1" >&2
            usage
            exit 1
            ;;
        *)
            if [ -n "$TASK_CARD" ]; then
                echo "Error: multiple task cards provided: $TASK_CARD and $1" >&2
                usage
                exit 1
            fi
            TASK_CARD="$1"
            shift
            ;;
    esac
done

_INPUT_COUNT=0
[ -n "$TASK_CARD" ] && _INPUT_COUNT=$((_INPUT_COUNT + 1))
[ -n "$BRIEF_TEXT" ] && _INPUT_COUNT=$((_INPUT_COUNT + 1))
[ -n "$BRIEF_FILE" ] && _INPUT_COUNT=$((_INPUT_COUNT + 1))
[ "$STDIN_BRIEF" = "yes" ] && _INPUT_COUNT=$((_INPUT_COUNT + 1))
if [ "$_INPUT_COUNT" -ne 1 ]; then
    echo "Error: provide exactly one task card, --brief, --brief-file, or --stdin-brief." >&2
    usage
    exit 1
fi
if [ -n "$BRIEF_TEXT" ] || [ -n "$BRIEF_FILE" ] || [ "$STDIN_BRIEF" = "yes" ]; then
    INPUT_KIND="brief"
fi

case "$MODE" in
    auto|task-size-classifier|execution-cost-estimator|review-only|task-card-audit|plan-splitter|validation-planner|failure-triage|evidence-checker|micro-builder|controlled-builder|parallel-planner|observe-synthesizer|task-card-drafter|context-packet-builder|preflight-bundle|direction-precheck|acceptance-matrix|postflight-bundle|revision-drafter|lesson-extractor|monitor-triage) ;;
    *)
        echo "Error: invalid --mode: $MODE" >&2
        exit 1
        ;;
esac

case "$BUDGET_MODE" in
    aggressive|balanced|conservative) ;;
    *)
        echo "Error: invalid --budget-mode: $BUDGET_MODE (expected aggressive, balanced, or conservative)" >&2
        exit 1
        ;;
esac

case "$ARTIFACT_LINES" in
    ''|*[!0-9]*)
        echo "Error: CODEX_SPARK_ARTIFACT_LINES must be a non-negative integer." >&2
        exit 1
        ;;
esac

case "$SANDBOX" in
    read-only|workspace-write) ;;
    *)
        echo "Error: invalid --sandbox: $SANDBOX" >&2
        exit 1
        ;;
esac

if [ -n "$RESULT_MODE" ]; then
    case "$RESULT_MODE" in
        direct|minimal|full) ;;
        *)
            echo "Error: invalid --result-mode: $RESULT_MODE (expected direct, minimal, or full)" >&2
            exit 1
            ;;
    esac
fi

case "$DIAGNOSTICS_MODE" in
    off|failure|full) ;;
    *)
        echo "Error: invalid --diagnostics: $DIAGNOSTICS_MODE (expected off, failure, or full)" >&2
        exit 1
        ;;
esac

if [ -n "$MAX_DIFF_LINES" ]; then
    case "$MAX_DIFF_LINES" in
        ''|*[!0-9]*)
            echo "Error: --max-diff-lines must be a positive integer." >&2
            exit 1
            ;;
        0)
            echo "Error: --max-diff-lines must be at least 1." >&2
            exit 1
            ;;
    esac
    if [ "$MAX_DIFF_LINES" -gt 200 ]; then
        echo "Error: --max-diff-lines must be at most 200." >&2
        exit 1
    fi
fi

# Validate FAST_PATH_MAX_DIFF_LINES (1..200, integer)
case "$FAST_PATH_MAX_DIFF_LINES" in
    ''|*[!0-9]*)
        echo "Error: --fast-path-max-diff-lines must be a positive integer." >&2
        exit 1
        ;;
    0)
        echo "Error: --fast-path-max-diff-lines must be at least 1." >&2
        exit 1
        ;;
esac
if [ "$FAST_PATH_MAX_DIFF_LINES" -gt 200 ]; then
    echo "Error: --fast-path-max-diff-lines must be at most 200." >&2
    exit 1
fi
case "$CONCENTRATED_FAST_PATH_MAX_DIFF_LINES" in
    ''|*[!0-9]*)
        echo "Error: --concentrated-fast-path-max-diff-lines must be an integer." >&2
        exit 1
        ;;
esac
if [ "$CONCENTRATED_FAST_PATH_MAX_DIFF_LINES" -lt 100 ] || [ "$CONCENTRATED_FAST_PATH_MAX_DIFF_LINES" -gt 500 ]; then
    echo "Error: --concentrated-fast-path-max-diff-lines must be between 100 and 500." >&2
    exit 1
fi
if [ "$CONCENTRATED_FAST_PATH_MAX_DIFF_LINES" -lt "$FAST_PATH_MAX_DIFF_LINES" ]; then
    echo "Error: concentrated fast-path threshold cannot be lower than the ordinary fast-path threshold." >&2
    exit 1
fi
case "$FULL_REREVIEW_FAST_PATH_MAX_DIFF_LINES" in
    ''|*[!0-9]*)
        echo "Error: CODEX_FULL_REREVIEW_FAST_PATH_MAX_DIFF_LINES must be an integer." >&2
        exit 1
        ;;
esac
if [ "$FULL_REREVIEW_FAST_PATH_MAX_DIFF_LINES" -lt 100 ] || [ "$FULL_REREVIEW_FAST_PATH_MAX_DIFF_LINES" -gt 2000 ]; then
    echo "Error: CODEX_FULL_REREVIEW_FAST_PATH_MAX_DIFF_LINES must be between 100 and 2000." >&2
    exit 1
fi
case "$FULL_REREVIEW_FAST_PATH_MAX_FILES" in
    ''|*[!0-9]*|0)
        echo "Error: CODEX_FULL_REREVIEW_FAST_PATH_MAX_FILES must be between 1 and 10." >&2
        exit 1
        ;;
esac
if [ "$FULL_REREVIEW_FAST_PATH_MAX_FILES" -gt 10 ]; then
    echo "Error: CODEX_FULL_REREVIEW_FAST_PATH_MAX_FILES must be between 1 and 10." >&2
    exit 1
fi
case "$ROUTING_EVENT" in
    initial|revision|narrow|retry|next-phase) ;;
    *) echo "Error: --routing-event must be initial, revision, narrow, retry, or next-phase." >&2; exit 1 ;;
esac

case "$EXECUTION_ENV" in
    auto|host|sandbox) ;;
    *) echo "Error: invalid --execution-env: $EXECUTION_ENV (expected auto, host, or sandbox)" >&2; exit 1 ;;
esac
case "$STDOUT_MAX_BYTES" in
    ''|*[!0-9]*)
        echo "Error: CODEX_SPARK_STDOUT_MAX_BYTES must be an integer." >&2
        exit 1
        ;;
esac
if [ "$STDOUT_MAX_BYTES" -lt 4096 ] || [ "$STDOUT_MAX_BYTES" -gt 131072 ]; then
    echo "Error: CODEX_SPARK_STDOUT_MAX_BYTES must be between 4096 and 131072." >&2
    exit 1
fi
case "$CALL_TIMEOUT_SECONDS" in
    ''|*[!0-9]*) echo "Error: CODEX_SPARK_CALL_TIMEOUT_SECONDS must be a positive integer." >&2; exit 1 ;;
    0) echo "Error: CODEX_SPARK_CALL_TIMEOUT_SECONDS must be a positive integer." >&2; exit 1 ;;
esac
case "$REPOSITORY_SCALE_REQUESTED" in
    auto|small|medium|large|giant) ;;
    *) echo "Error: --repository-scale must be auto, small, medium, large, or giant." >&2; exit 1 ;;
esac

# Resolve execution environment after CLI parsing.
# For explicit host/sandbox, use the requested value. For auto, detect whether
# the inherited sandbox restricts network access.
RESOLVED_EXECUTION_ENV="$EXECUTION_ENV"
if [ "$EXECUTION_ENV" = "auto" ]; then
    case "${CODEX_SANDBOX_NETWORK_DISABLED:-}" in
        1|true|TRUE|True|yes|YES|Yes)
            RESOLVED_EXECUTION_ENV="sandbox-restricted" ;;
        *)
            RESOLVED_EXECUTION_ENV="auto-unrestricted" ;;
    esac
fi

if [ "$INPUT_KIND" = "task-card" ] && [ ! -f "$TASK_CARD" ]; then
    echo "Error: task card not found: $TASK_CARD" >&2
    exit 1
fi
if [ -n "$BRIEF_FILE" ] && [ ! -f "$BRIEF_FILE" ]; then
    echo "Error: brief file not found: $BRIEF_FILE" >&2
    exit 1
fi

if [ "$INPUT_KIND" = "brief" ]; then
    case "$MODE" in
        auto|task-size-classifier|execution-cost-estimator|preflight-bundle|observe-synthesizer|task-card-drafter|monitor-triage) ;;
        *)
            echo "Error: pre-task-card brief input is only supported by auto, task-size-classifier, execution-cost-estimator, preflight-bundle, observe-synthesizer, task-card-drafter, or monitor-triage." >&2
            exit 1
            ;;
    esac
fi

for artifact in "${ARTIFACTS[@]}"; do
    if [ ! -e "$artifact" ]; then
        echo "Error: artifact not found: $artifact" >&2
        exit 1
    fi
done

if ! command -v git >/dev/null 2>&1; then
    echo "Error: git is not installed or not in PATH." >&2
    exit 1
fi

if [ "$INPUT_KIND" = "task-card" ]; then
    REPO_ROOT="$(git -C "$(dirname "$TASK_CARD")" rev-parse --show-toplevel 2>/dev/null || git rev-parse --show-toplevel 2>/dev/null || pwd)"
else
    REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
INITIAL_SOURCE_STATUS=""

# Deterministic repository-scale profile.  Failure is conservative: keep the
# ordinary 100/2 gate and disable the concentrated expansion at the same cap.
REPOSITORY_SCALE_DETECTED="unknown"
REPOSITORY_ROUTING_SCALE="small"
REPOSITORY_TRACKED_FILES="unknown"
REPOSITORY_SOURCE_FILES="unknown"
REPOSITORY_GIT_SIZE_KIB="unknown"
REPOSITORY_WORKTREE_SAMPLES="0"
REPOSITORY_WORKTREE_MEDIAN_SECONDS="unknown"
REPOSITORY_WORKTREE_COST="unknown"
REPOSITORY_IO_PROMOTED="no"
ORDINARY_FAST_PATH_MAX_FILES=2
CONCENTRATED_FAST_PATH_MAX_FILES=2
_SCALE_ORDINARY_LINES=100
_SCALE_CONCENTRATED_LINES=100
_SCALE_HELPER="${SCRIPT_DIR}/repository-scale.py"
_SCALE_PYTHON=""
if command -v python3 >/dev/null 2>&1; then
    _SCALE_PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
    _SCALE_PYTHON="python"
fi
if [ -n "$_SCALE_PYTHON" ] && [ -f "$_SCALE_HELPER" ]; then
    _SCALE_OUTPUT="$($_SCALE_PYTHON "$_SCALE_HELPER" --repo "$REPO_ROOT" --scale "$REPOSITORY_SCALE_REQUESTED" --format shell 2>/dev/null || true)"
    while IFS='=' read -r _scale_key _scale_value; do
        case "$_scale_key" in
            repository_scale_detected) REPOSITORY_SCALE_DETECTED="$_scale_value" ;;
            routing_scale) REPOSITORY_ROUTING_SCALE="$_scale_value" ;;
            tracked_files) REPOSITORY_TRACKED_FILES="$_scale_value" ;;
            source_files) REPOSITORY_SOURCE_FILES="$_scale_value" ;;
            git_size_kib) REPOSITORY_GIT_SIZE_KIB="$_scale_value" ;;
            worktree_history_samples) REPOSITORY_WORKTREE_SAMPLES="$_scale_value" ;;
            worktree_setup_median_seconds) REPOSITORY_WORKTREE_MEDIAN_SECONDS="$_scale_value" ;;
            worktree_cost) REPOSITORY_WORKTREE_COST="$_scale_value" ;;
            io_promoted) REPOSITORY_IO_PROMOTED="$_scale_value" ;;
            ordinary_lines) _SCALE_ORDINARY_LINES="$_scale_value" ;;
            ordinary_files) ORDINARY_FAST_PATH_MAX_FILES="$_scale_value" ;;
            concentrated_lines) _SCALE_CONCENTRATED_LINES="$_scale_value" ;;
            concentrated_files) CONCENTRATED_FAST_PATH_MAX_FILES="$_scale_value" ;;
        esac
    done <<< "$_SCALE_OUTPUT"
fi
if [ "$FAST_PATH_THRESHOLD_EXPLICIT" != "yes" ]; then
    FAST_PATH_MAX_DIFF_LINES="$_SCALE_ORDINARY_LINES"
fi
if [ "$CONCENTRATED_THRESHOLD_EXPLICIT" != "yes" ]; then
    CONCENTRATED_FAST_PATH_MAX_DIFF_LINES="$_SCALE_CONCENTRATED_LINES"
fi
if [ "$CONCENTRATED_FAST_PATH_MAX_DIFF_LINES" -lt "$FAST_PATH_MAX_DIFF_LINES" ]; then
    CONCENTRATED_FAST_PATH_MAX_DIFF_LINES="$FAST_PATH_MAX_DIFF_LINES"
fi

# ---------------------------------------------------------------------------
# Helper functions that read $TASK_CARD (not $TASK_CARD_COPY) so they can be
# called before the output directory exists.
# ---------------------------------------------------------------------------

artifact_failure_signals() {
    [ "${#ARTIFACTS[@]}" -gt 0 ] || return 1
    grep -Eiq \
        'FAILED|ERROR|timeout|timed out|no valid report|seeded report|fallback report|acknowledgement only|quota|auth|permission|stale HEAD|dirty source|no useful progress' \
        "${ARTIFACTS[@]}" 2>/dev/null
}

artifact_name_matches() {
    local pattern="$1"
    local artifact=""
    for artifact in "${ARTIFACTS[@]}"; do
        case "$(basename "$artifact")" in
            $pattern) return 0 ;;
        esac
    done
    return 1
}

is_read_only_synthesis_mode() {
    case "$MODE" in
        observe-synthesizer|task-card-drafter|context-packet-builder|preflight-bundle|direction-precheck|acceptance-matrix|postflight-bundle|revision-drafter|lesson-extractor|execution-cost-estimator|monitor-triage)
            return 0 ;;
        *)
            return 1 ;;
    esac
}

is_checker_task() {
    [ "$INPUT_KIND" = "task-card" ] || return 1
    grep -Eiq 'checker-test|Validation Contract|Local validation allowed|Test-First / TDD|TDD mode' "$TASK_CARD"
}

is_advisory_mode() {
    ! is_source_writing_mode
}

is_source_writing_mode() {
    case "$MODE" in
        micro-builder|controlled-builder) return 0 ;;
        *) return 1 ;;
    esac
}

resolve_auto_mode() {
    if [ "$MODE" != "auto" ]; then
        return
    fi
    case "$BUDGET_MODE" in
        conservative)
            if [ "${#ARTIFACTS[@]}" -gt 0 ]; then
                if artifact_failure_signals; then
                    MODE="failure-triage"
                elif artifact_name_matches "*.diff" || artifact_name_matches "*.diffstat.txt"; then
                    MODE="review-only"
                else
                    MODE="evidence-checker"
                fi
            elif is_checker_task; then
                MODE="validation-planner"
            else
                MODE="task-size-classifier"
            fi
            ;;
        balanced)
            if [ "${#ARTIFACTS[@]}" -gt 0 ]; then
                if artifact_failure_signals; then
                    MODE="failure-triage"
                else
                    MODE="postflight-bundle"
                fi
            elif is_checker_task; then
                MODE="validation-planner"
            else
                MODE="preflight-bundle"
            fi
            ;;
        aggressive)
            if [ "${#ARTIFACTS[@]}" -gt 0 ]; then
                if artifact_failure_signals; then
                    MODE="failure-triage"
                else
                    MODE="postflight-bundle"
                fi
            else
                MODE="preflight-bundle"
            fi
            ;;
    esac
}

resolve_pipeline_stage() {
    case "$MODE" in
        preflight-bundle|observe-synthesizer|task-card-drafter|context-packet-builder)
            SPARK_PIPELINE_STAGE="preflight" ;;
        postflight-bundle|direction-precheck|acceptance-matrix)
            SPARK_PIPELINE_STAGE="postflight" ;;
        failure-triage|revision-drafter)
            SPARK_PIPELINE_STAGE="failure" ;;
        task-size-classifier|execution-cost-estimator|plan-splitter)
            SPARK_PIPELINE_STAGE="planning" ;;
        validation-planner)
            SPARK_PIPELINE_STAGE="validation" ;;
        micro-builder|controlled-builder)
            SPARK_PIPELINE_STAGE="builder" ;;
        lesson-extractor)
            SPARK_PIPELINE_STAGE="learning" ;;
        monitor-triage)
            SPARK_PIPELINE_STAGE="monitoring" ;;
        review-only|task-card-audit|evidence-checker|parallel-planner)
            SPARK_PIPELINE_STAGE="standalone" ;;
    esac
}

resolve_roles_executed() {
    case "$MODE" in
        preflight-bundle)
            SPARK_ROLES_EXECUTED="risk-classifier,evidence-synthesizer,task-card-drafter,context-packet-builder,unknown-extractor,split-advisor,execution-cost-estimator" ;;
        postflight-bundle)
            SPARK_ROLES_EXECUTED="direction-checker,boundary-checker,acceptance-mapper,evidence-conflict-detector,validation-advisor,acceptance-advisor" ;;
        failure-triage)
            if [ "$BUDGET_MODE" = "aggressive" ]; then
                SPARK_ROLES_EXECUTED="failure-triage,revision-drafter"
            else
                SPARK_ROLES_EXECUTED="$MODE"
            fi ;;
        observe-synthesizer|task-card-drafter|context-packet-builder|direction-precheck|acceptance-matrix|revision-drafter|lesson-extractor|monitor-triage)
            SPARK_ROLES_EXECUTED="$MODE" ;;
        *)
            SPARK_ROLES_EXECUTED="$MODE" ;;
    esac
}

# ---------------------------------------------------------------------------
# Resolve mode, pipeline, and result mode BEFORE any directory creation.
# ---------------------------------------------------------------------------

resolve_auto_mode
resolve_pipeline_stage
resolve_roles_executed
SPARK_CALLS_USED=0

# Provisional acceptance: pending output only for acceptance/postflight roles
case "$MODE" in
    postflight-bundle|acceptance-matrix)
        SPARK_PROVISIONAL_ACCEPTANCE="pending output" ;;
    *)
        SPARK_PROVISIONAL_ACCEPTANCE="not applicable" ;;
esac

# Resolve result mode
# - source-writing modes always force full
# - advisory/read-only modes default to direct
# - explicit --output upgrades implicit direct to minimal
if is_source_writing_mode; then
    if [ "$INPUT_KIND" != "task-card" ]; then
        echo "Error: source-writing mode '$MODE' requires a full task card." >&2
        exit 1
    fi
    if [ "$EXPLICIT_RESULT_MODE" = "yes" ] && [ "$RESULT_MODE" != "full" ]; then
        echo "Error: source-writing mode '$MODE' requires --result-mode full." >&2
        exit 1
    fi
    RESULT_MODE="full"
else
    if [ -z "$RESULT_MODE" ]; then
        RESULT_MODE="direct"
    fi
    if [ "$EXPLICIT_OUTPUT" = "yes" ] && [ "$RESULT_MODE" = "direct" ] && [ "$EXPLICIT_RESULT_MODE" = "no" ]; then
        RESULT_MODE="minimal"
    fi
fi

if [ "$EXPLICIT_OUTPUT" = "yes" ] && [ "$RESULT_MODE" = "direct" ]; then
    echo "Error: --output is incompatible with --result-mode direct. Use --result-mode minimal or full when specifying --output." >&2
    exit 1
fi

if is_source_writing_mode; then
    INITIAL_SOURCE_STATUS="$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all)"
fi

# ---------------------------------------------------------------------------
# Create working directory based on resolved result mode.
#   direct  → mktemp only; no permanent OUTPUT_DIR created
#   minimal → OUTPUT_DIR for report only; all working files in mktemp
#   full    → everything in OUTPUT_DIR
# ---------------------------------------------------------------------------

TEMP_WORK_DIR=""

if [ "$RESULT_MODE" = "direct" ]; then
    # Direct mode: only a transient temp dir; cleaned up on exit
    TEMP_WORK_DIR="$(mktemp -d)"
    cleanup_temp() {
        if [ -n "$TEMP_WORK_DIR" ] && [ -d "$TEMP_WORK_DIR" ]; then
            rm -rf "$TEMP_WORK_DIR"
        fi
    }
    trap cleanup_temp EXIT

    PROMPT_FILE="${TEMP_WORK_DIR}/codex-spark.prompt.md"
    REPORT_FILE="${TEMP_WORK_DIR}/codex-spark.report.md"
    RESULT_FILE="${TEMP_WORK_DIR}/codex-spark.result.txt"
    STDERR_FILE="${TEMP_WORK_DIR}/codex-spark.stderr.log"
    DIFF_FILE="${TEMP_WORK_DIR}/codex-spark.diff"
    DIFFSTAT_FILE="${TEMP_WORK_DIR}/codex-spark.diffstat.txt"
    STATUS_FILE="${TEMP_WORK_DIR}/codex-spark.worktree-status.txt"
    TASK_CARD_COPY="${TEMP_WORK_DIR}/TASK_CARD.md"
    ARTIFACT_MANIFEST="${TEMP_WORK_DIR}/codex-spark.artifacts.txt"
elif [ "$RESULT_MODE" = "minimal" ]; then
    # Minimal mode: OUTPUT_DIR holds only the report; working files are transient
    if [ -z "$OUTPUT_DIR" ]; then
        OUTPUT_DIR="${REPO_ROOT}/.worktrees/codex-spark-${TIMESTAMP}"
    fi
    mkdir -p "$OUTPUT_DIR"
    OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"

    TEMP_WORK_DIR="$(mktemp -d)"
    cleanup_temp() {
        if [ -n "$TEMP_WORK_DIR" ] && [ -d "$TEMP_WORK_DIR" ]; then
            rm -rf "$TEMP_WORK_DIR"
        fi
    }
    trap cleanup_temp EXIT

    PROMPT_FILE="${TEMP_WORK_DIR}/codex-spark.prompt.md"
    RESULT_FILE="${TEMP_WORK_DIR}/codex-spark.result.txt"
    STDERR_FILE="${TEMP_WORK_DIR}/codex-spark.stderr.log"
    DIFF_FILE="${TEMP_WORK_DIR}/codex-spark.diff"
    DIFFSTAT_FILE="${TEMP_WORK_DIR}/codex-spark.diffstat.txt"
    STATUS_FILE="${TEMP_WORK_DIR}/codex-spark.worktree-status.txt"
    TASK_CARD_COPY="${TEMP_WORK_DIR}/TASK_CARD.md"
    ARTIFACT_MANIFEST="${TEMP_WORK_DIR}/codex-spark.artifacts.txt"
    REPORT_FILE="${OUTPUT_DIR}/codex-spark.report.md"
else
    # Full mode: everything in OUTPUT_DIR
    if [ -z "$OUTPUT_DIR" ]; then
        OUTPUT_DIR="${REPO_ROOT}/.worktrees/codex-spark-${TIMESTAMP}"
    fi
    mkdir -p "$OUTPUT_DIR"
    OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"

    PROMPT_FILE="${OUTPUT_DIR}/codex-spark.prompt.md"
    REPORT_FILE="${OUTPUT_DIR}/codex-spark.report.md"
    RESULT_FILE="${OUTPUT_DIR}/codex-spark.result.txt"
    STDERR_FILE="${OUTPUT_DIR}/codex-spark.stderr.log"
    DIFF_FILE="${OUTPUT_DIR}/codex-spark.diff"
    DIFFSTAT_FILE="${OUTPUT_DIR}/codex-spark.diffstat.txt"
    STATUS_FILE="${OUTPUT_DIR}/codex-spark.worktree-status.txt"
    TASK_CARD_COPY="${OUTPUT_DIR}/TASK_CARD.md"
    ARTIFACT_MANIFEST="${OUTPUT_DIR}/codex-spark.artifacts.txt"
fi

# Codex JSONL is retained separately from the final advisor message so token
# usage can be normalized without exposing event noise to downstream callers.
SPARK_EVENTS_FILE="${RESULT_FILE%.txt}.events.jsonl"

WORKTREE_DIR=""
RUN_DIR="$REPO_ROOT"
CODEX_STATUS=0
CODEX_RUNTIME_HOME=""
CODEX_RUNTIME_PARENT_CREATED="no"

if [ "$INPUT_KIND" = "task-card" ]; then
    cp "$TASK_CARD" "$TASK_CARD_COPY"
elif [ -n "$BRIEF_FILE" ]; then
    cp "$BRIEF_FILE" "$TASK_CARD_COPY"
elif [ "$STDIN_BRIEF" = "yes" ]; then
    cat > "$TASK_CARD_COPY"
else
    printf '%s\n' "$BRIEF_TEXT" > "$TASK_CARD_COPY"
fi
: > "$ARTIFACT_MANIFEST"
for artifact in "${ARTIFACTS[@]}"; do
    printf '%s\n' "$artifact" >> "$ARTIFACT_MANIFEST"
done

# All advisory modes run from a writable artifact/temp directory. This keeps
# Codex app-server/helper initialization away from the source repository and
# applies consistently to legacy review/planning modes too.
if is_advisory_mode && [ "$SANDBOX" = "read-only" ]; then
    SANDBOX="workspace-write"
    if [ "$RESULT_MODE" = "direct" ] || [ "$RESULT_MODE" = "minimal" ]; then
        RUN_DIR="$TEMP_WORK_DIR"
    else
        RUN_DIR="$OUTPUT_DIR"
    fi
    SPARK_CHECKS_RUN="codex exec (${MODE} in artifact dir)"
fi

# The CLI initializes local app-server state before contacting Spark. In
# sandboxed sessions the user's normal CODEX_HOME may be read-only even when
# the working directory is writable. Give advisory calls a transient writable
# home while linking only the existing read-only identity/config inputs.
if is_advisory_mode; then
    if [ ! -d "${REPO_ROOT}/.worktrees" ]; then
        mkdir -p "${REPO_ROOT}/.worktrees"
        CODEX_RUNTIME_PARENT_CREATED="yes"
    fi
    CODEX_RUNTIME_HOME="$(mktemp -d "${REPO_ROOT}/.worktrees/.codex-spark-runtime.XXXXXX")"
    cleanup_codex_runtime_home() {
        [ -z "$CODEX_RUNTIME_HOME" ] || rm -rf "$CODEX_RUNTIME_HOME"
        if [ "$CODEX_RUNTIME_PARENT_CREATED" = "yes" ]; then
            rmdir "${REPO_ROOT}/.worktrees" 2>/dev/null || true
        fi
    }
    if [ -n "$TEMP_WORK_DIR" ]; then
        trap 'cleanup_temp; cleanup_codex_runtime_home' EXIT
    else
        trap cleanup_codex_runtime_home EXIT
    fi
    _ORIGINAL_CODEX_HOME="${CODEX_HOME:-${HOME}/.codex}"
    for _codex_input in auth.json config.toml installation_id models_cache.json version.json; do
        if [ -f "${_ORIGINAL_CODEX_HOME}/${_codex_input}" ]; then
            cp "${_ORIGINAL_CODEX_HOME}/${_codex_input}" "${CODEX_RUNTIME_HOME}/${_codex_input}"
        fi
    done
    # workspace-write grants the invocation cwd. Keep cwd and CODEX_HOME in the
    # same transient workspace so app-server state is actually writable.
    RUN_DIR="$CODEX_RUNTIME_HOME"
fi

if [ "$MODE" = "micro-builder" ] && [ "$SANDBOX" != "workspace-write" ]; then
    echo "Error: micro-builder mode requires --sandbox workspace-write." >&2
    exit 1
fi

if [ "$MODE" = "controlled-builder" ] && [ "$SANDBOX" != "workspace-write" ]; then
    echo "Error: controlled-builder mode requires --sandbox workspace-write." >&2
    exit 1
fi

# Validate controlled-builder allow-write path (hardened)
validate_allow_write_path() {
    local path="$1"
    # Reject empty paths
    if [ -z "$path" ]; then
        echo "Error: --allow-write path must not be empty." >&2
        return 1
    fi
    # Reject control characters (C0 + DEL)
    if [[ "$path" =~ [[:cntrl:]] ]]; then
        echo "Error: --allow-write path contains control characters: $path" >&2
        return 1
    fi
    # Reject absolute paths
    if [[ "$path" = /* ]]; then
        echo "Error: --allow-write path must be repo-relative, not absolute: $path" >&2
        return 1
    fi
    # Reject traversal components
    if [[ "$path" = *".."* ]]; then
        echo "Error: --allow-write path must not contain '..': $path" >&2
        return 1
    fi
    # Reject directory-wide wildcards, globs, and trailing slash
    if [[ "$path" = */ || "$path" = *"*"* ]]; then
        echo "Error: --allow-write path must be a specific file, not a directory or glob: $path" >&2
        return 1
    fi
    # Verify path is within the repository
    local resolved="${REPO_ROOT}/${path}"
    if [[ "$resolved" != "${REPO_ROOT}"/* ]]; then
        echo "Error: --allow-write path is outside the repository: $path" >&2
        return 1
    fi
    # Reject symlinks in path or any existing parent component
    local check_path="$REPO_ROOT"
    IFS='/' read -ra _path_parts <<< "$path"
    for _part in "${_path_parts[@]}"; do
        [ -n "$_part" ] || continue
        check_path="${check_path}/${_part}"
        if [ -L "$check_path" ]; then
            echo "Error: --allow-write path crosses a symlink: $check_path" >&2
            return 1
        fi
    done
    return 0
}

# Controlled-builder requires specific arguments
if [ "$MODE" = "controlled-builder" ]; then
    if [ "${#ALLOWED_WRITES[@]}" -lt 1 ] || [ "${#ALLOWED_WRITES[@]}" -gt 3 ]; then
        echo "Error: controlled-builder requires 1-3 --allow-write arguments." >&2
        exit 1
    fi
    if [ -z "$MAX_DIFF_LINES" ]; then
        echo "Error: controlled-builder requires --max-diff-lines (1-200)." >&2
        exit 1
    fi
    # Validate each allow-write path
    for aw_path in "${ALLOWED_WRITES[@]}"; do
        validate_allow_write_path "$aw_path" || exit 1
    done
    # Check for duplicate paths
    unique_writes=$(printf '%s\n' "${ALLOWED_WRITES[@]}" | sort -u | wc -l)
    if [ "$unique_writes" -ne "${#ALLOWED_WRITES[@]}" ]; then
        echo "Error: --allow-write paths must be unique." >&2
        exit 1
    fi
fi

micro_builder_contract_missing() {
    if ! grep -Eiq 'micro-builder' "$TASK_CARD_COPY"; then
        echo "micro-builder mode is not explicitly authorized in the task card"
        return 0
    fi
    if ! grep -Eiq 'Source edits allowed\?[[:space:]]*\|[[:space:]]*yes|Source edits allowed[[:space:]]*\|[[:space:]]*yes' "$TASK_CARD_COPY"; then
        echo "task card does not explicitly allow Spark source edits"
        return 0
    fi
    if ! grep -Eiq '1-2 files|1 or 2 files|one or two files|no more than two files|max files[[:space:]]*\|[[:space:]]*1-2' "$TASK_CARD_COPY"; then
        echo "task card does not limit Spark micro-builder scope to one or two files"
        return 0
    fi
    if ! grep -Eiq 'public API[[:space:]/_-]*(risk)?[[:space:]]*\|[[:space:]]*no|no public API|no API contract' "$TASK_CARD_COPY"; then
        echo "task card does not rule out public API or contract risk"
        return 0
    fi
    if ! grep -Eiq 'narrow validation|focused validation|exact validation|Validation command' "$TASK_CARD_COPY"; then
        echo "task card does not provide narrow validation for Spark micro-builder"
        return 0
    fi
    return 1
}

markdown_table_value() {
    local field_name="$1"
    awk -F '|' -v wanted="$field_name" '
        function trim(value) {
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
            return value
        }
        {
            field = trim($2)
            if (tolower(field) == tolower(wanted)) {
                print trim($3)
                exit
            }
        }
    ' "$TASK_CARD_COPY"
}

controlled_builder_contract_missing() {
    if ! grep -Eiq 'controlled-builder' "$TASK_CARD_COPY"; then
        echo "controlled-builder mode is not explicitly authorized in the task card"
        return 0
    fi
    if [ "$(markdown_table_value "Controlled-builder authorized?")" != "yes" ]; then
        echo "task card does not explicitly set Controlled-builder authorized? to yes"
        return 0
    fi
    if ! grep -Eiq 'Source edits allowed\?[[:space:]]*\|[[:space:]]*yes|Source edits allowed[[:space:]]*\|[[:space:]]*yes' "$TASK_CARD_COPY"; then
        echo "task card does not explicitly allow Spark source edits"
        return 0
    fi
    if ! grep -Eiq 'maximum 3 files|max files[[:space:]]*\|[[:space:]]*3|max files[[:space:]]*\|[[:space:]]*1-3' "$TASK_CARD_COPY"; then
        echo "task card does not limit Spark controlled-builder scope to at most three files"
        return 0
    fi
    local _task_diff_cap
    _task_diff_cap="$(markdown_table_value "Max diff lines")"
    if ! [[ "$_task_diff_cap" =~ ^[0-9]+$ ]] || [ "$_task_diff_cap" -lt 1 ] || [ "$_task_diff_cap" -gt 200 ]; then
        echo "task card Max diff lines must be a numeric authorization from 1 to 200"
        return 0
    fi
    if [ "$MAX_DIFF_LINES" -gt "$_task_diff_cap" ]; then
        echo "CLI --max-diff-lines exceeds the task card Max diff lines authorization"
        return 0
    fi
    if ! grep -Eiq 'public API[[:space:]/_-]*(risk)?[[:space:]]*\|[[:space:]]*no|no public API|no API contract' "$TASK_CARD_COPY"; then
        echo "task card does not rule out public API or contract risk"
        return 0
    fi
    if ! grep -Eiq 'data model[[:space:]]*(risk)?[[:space:]]*\|[[:space:]]*no|no data model' "$TASK_CARD_COPY"; then
        echo "task card does not rule out data model risk"
        return 0
    fi
    if ! grep -Eiq 'security[[:space:]]*(risk)?[[:space:]]*\|[[:space:]]*no|no security' "$TASK_CARD_COPY"; then
        echo "task card does not rule out security risk"
        return 0
    fi
    if ! grep -Eiq 'migration[[:space:]]*(risk)?[[:space:]]*\|[[:space:]]*no|no migration' "$TASK_CARD_COPY"; then
        echo "task card does not rule out migration risk"
        return 0
    fi
    if ! grep -Eiq 'permission[[:space:]]*(risk)?[[:space:]]*\|[[:space:]]*no|no permission' "$TASK_CARD_COPY"; then
        echo "task card does not rule out permission risk"
        return 0
    fi
    if ! grep -Eiq 'concurrency[[:space:]]*(risk)?[[:space:]]*\|[[:space:]]*no|no concurrency' "$TASK_CARD_COPY"; then
        echo "task card does not rule out concurrency risk"
        return 0
    fi
    if ! grep -Eiq 'cross-module[[:space:]]*(risk)?[[:space:]]*\|[[:space:]]*no|no cross-module' "$TASK_CARD_COPY"; then
        echo "task card does not rule out cross-module contract risk"
        return 0
    fi
    if ! grep -Eiq 'narrow validation|focused validation|exact validation|Validation command' "$TASK_CARD_COPY"; then
        echo "task card does not provide narrow validation for Spark controlled-builder"
        return 0
    fi
    local _ref_value
    _ref_value="$(markdown_table_value "Existing pattern")"
    if [ -z "$_ref_value" ]; then
        _ref_value="$(markdown_table_value "Source-of-truth reference")"
    fi
    case "$_ref_value" in
        ''|none|None|NONE|n/a|N/A|'-')
            echo "task card existing-pattern or source-of-truth reference field is empty or none"
            return 0
            ;;
    esac
    # Require Controlled-builder allowed paths row
    if [ -z "$(markdown_table_value "Controlled-builder allowed paths")" ]; then
        echo "task card does not provide a Controlled-builder allowed paths row"
        return 0
    fi
    return 1
}

write_report_header() {
    local exit_value="${1:-pending}"
    local diff_value="${2:-pending}"
    # For minimal mode, transient paths are cleaned up — do not reference them
    local invocation_artifact="$PROMPT_FILE"
    local task_card_ref="$TASK_CARD_COPY"
    local artifact_dir_ref="$OUTPUT_DIR"
    local manifest_ref="${ARTIFACT_MANIFEST}"
    if [ "$RESULT_MODE" = "minimal" ]; then
        invocation_artifact="(transient, cleaned up)"
        task_card_ref="(transient, cleaned up)"
        manifest_ref="$([ "${#ARTIFACTS[@]}" -gt 0 ] && echo "(transient, cleaned up)" || echo none)"
    fi
    if [ -z "$artifact_dir_ref" ]; then
        artifact_dir_ref="(none, direct mode)"
    fi
    {
        echo "# Codex Spark Report"
        echo ""
        echo "## Codex Spark Follow-up"
        echo ""
        echo "| Field | Value |"
        echo "|-------|-------|"
        echo "| Spark enabled in task card? | yes |"
        echo "| Spark invoked? | ${SPARK_INVOKED} |"
        echo "| Spark model response received? | ${SPARK_MODEL_RESPONSE_RECEIVED} |"
        echo "| Spark purpose used | ${MODE} |"
        echo "| Spark requested mode | ${REQUESTED_MODE} |"
        echo "| Execution environment requested | ${EXECUTION_ENV} |"
        echo "| Execution environment resolved | ${RESOLVED_EXECUTION_ENV} |"
        echo "| Result mode | ${RESULT_MODE} |"
        echo "| Spark model used | ${MODEL} |"
        echo "| Spark budget mode requested | ${REQUESTED_BUDGET_MODE} |"
        echo "| Spark budget mode effective | ${BUDGET_MODE} |"
        echo "| Spark pipeline stage | ${SPARK_PIPELINE_STAGE} |"
        echo "| Spark roles executed | ${SPARK_ROLES_EXECUTED} |"
        echo "| Spark calls used | ${SPARK_CALLS_USED} |"
        echo "| Spark provisional acceptance | ${SPARK_PROVISIONAL_ACCEPTANCE} |"
        echo "| Strong review required | yes |"
        echo "| Merge authorized | no |"
        echo "| Task size classification | $([ "$MODE" = "task-size-classifier" ] && echo "see Spark output" || echo "not used") |"
        echo "| Spark routing recommendation | $([ "$MODE" = "task-size-classifier" ] && echo "see Spark output" || echo "not used") |"
        echo "| Spark classification confidence | $([ "$MODE" = "task-size-classifier" ] && echo "see Spark output" || echo "not used") |"
        echo "| Invocation command or artifact | ${invocation_artifact} |"
        echo "| Sandbox used | ${SANDBOX} |"
        echo "| Isolated worktree used? | $([ -n "$WORKTREE_DIR" ] && echo yes || echo no) |"
        echo "| Source diff produced? | ${diff_value} |"
        echo "| Spark checks run | ${SPARK_CHECKS_RUN} |"
        echo "| Spark exit code | ${exit_value} |"
        echo "| Spark auto-disabled? | ${SPARK_AUTO_DISABLED} |"
        echo "| Auto-disable reason | ${SPARK_DISABLE_REASON} |"
        echo "| Host handoff required? | ${SPARK_HOST_HANDOFF_REQUIRED} |"
        echo "| Helper exit behavior | $([ "$REQUIRE_SPARK" = "1" ] && echo require-spark || echo optional-spark) |"
        echo "| Strong-model fallback used | no |"
        echo "| Spark output can satisfy acceptance? | no, advisory only unless Codex separately verifies and records acceptance |"
        echo "| Spark result accepted by Codex? | pending review |"
        echo "| accepted_suggestions | pending Codex review |"
        echo "| ignored_suggestions | pending Codex review |"
        echo "| conflicts_with_claude | pending review |"
        echo "| conflicts_with_local_evidence | pending review |"
        echo "| acceptance_satisfied_by_spark | no |"
        echo "| Remaining Spark-related risk | pending review |"
        echo "| Artifact directory | ${artifact_dir_ref} |"
        echo "| Task card copy | ${task_card_ref} |"
        echo "| Artifact inputs | ${manifest_ref} |"
        if [ -n "$WORKTREE_DIR" ]; then
            echo "| Worktree | ${WORKTREE_DIR} |"
        fi
        echo ""
    } > "$REPORT_FILE"
}

write_report_header

if [ "$MODE" = "micro-builder" ]; then
    MICRO_BUILDER_MISSING="$(micro_builder_contract_missing || true)"
    if [ -n "$MICRO_BUILDER_MISSING" ]; then
        if [ "$RESULT_MODE" = "direct" ]; then
            echo "Error: micro-builder contract missing: ${MICRO_BUILDER_MISSING}" >&2
            exit 2
        fi
        {
            echo "## Result"
            echo ""
            echo "Blocked: Spark micro-builder requires explicit tiny-scope authorization."
            echo ""
            echo "Missing contract: ${MICRO_BUILDER_MISSING}."
            echo ""
            echo "Required task-card evidence: micro-builder authorization, source edits allowed, at most one or two files, no public API/contract risk, and narrow validation."
        } >> "$REPORT_FILE"
        echo "Error: micro-builder contract missing: ${MICRO_BUILDER_MISSING}" >&2
        exit 2
    fi
fi

auto_disable_spark() {
    local reason="$1"
    local codex_exit="${2:-not-run}"
    SPARK_AUTO_DISABLED="yes"
    SPARK_DISABLE_REASON="$reason"
    SPARK_CHECKS_RUN="not run"
    HELPER_EXIT_STATUS=0
    if [ "$RESULT_MODE" = "direct" ]; then
        emit_direct_envelope_start
        echo "spark_status=unavailable"
        echo "spark_auto_disabled=yes"
        echo "spark_disable_reason=${reason}"
        echo "spark_model_response_received=${SPARK_MODEL_RESPONSE_RECEIVED}"
        echo "spark_protocol_end=aiwf-spark-stdout-v1"
        if [ "$SPARK_HOST_HANDOFF_REQUIRED" = "yes" ]; then
            echo "needs_host_execution=true" >&2
            echo "host_handoff_required=true" >&2
            echo "execution_env_requested=${EXECUTION_ENV}" >&2
            echo "execution_env_resolved=${RESOLVED_EXECUTION_ENV}" >&2
        fi
        echo "Codex Spark auto-disabled: ${reason}" >&2
        exit "$HELPER_EXIT_STATUS"
    fi
    write_report_header "$codex_exit" "no"
    {
        echo "## Result"
        echo ""
        echo "Codex Spark was auto-disabled for this run: ${reason}."
        echo ""
        echo "Spark is auxiliary in the workflow, so the helper exits 0 by default and the main Claude/Codex flow may continue."
        echo ""
        echo "Strong-model fallback is disabled by this helper; re-run explicitly with another model only after human approval."
    } >> "$REPORT_FILE"
    echo "Codex Spark auto-disabled: ${reason}" >&2
    echo "Codex Spark report: $REPORT_FILE" >&2
    exit "$HELPER_EXIT_STATUS"
}

spark_unavailable_failure() {
    local text=""
    if [ -s "$STDERR_FILE" ]; then
        text="$(tr '[:upper:]' '[:lower:]' < "$STDERR_FILE")"
    fi
    printf '%s\n' "$text" | grep -Eiq \
        'quota|rate limit|rate-limit|insufficient|exceeded|billing|credit|model.*(not|unavailable|unsupported|unknown)|not.*model|access|permission|unauthori[sz]ed|forbidden|login|auth|network|connection|timeout|timed out|proxy|dns|read-only file system|os error 30|app-server|failed to initialize'
}

spark_failure_auto_disable_reason() {
    local text=""
    if [ -s "$STDERR_FILE" ]; then
        text="$(tr '[:upper:]' '[:lower:]' < "$STDERR_FILE")"
    fi
    if printf '%s\n' "$text" | grep -Eiq 'read-only file system|os error 30'; then
        echo "codex exec failed because a required path was read-only"
    elif printf '%s\n' "$text" | grep -Eiq 'app-server'; then
        echo "codex exec failed during local app-server initialization before a confirmed Spark response"
    elif printf '%s\n' "$text" | grep -Eiq 'failed to initialize'; then
        echo "codex exec failed during local helper initialization before a confirmed Spark response"
    else
        echo "codex exec reported model, quota, auth, network, or access unavailability"
    fi
}

# ---------------------------------------------------------------------------
# Diagnostic support: compact failure records for direct mode.
# ---------------------------------------------------------------------------

DIAGNOSTIC_DIR=""
DIAGNOSTIC_FAILURE_CLASS="none"

emit_direct_envelope_start() {
    [ "$RESULT_MODE" = "direct" ] || return 0
    [ "$DIRECT_ENVELOPE_STARTED" = "no" ] || return 0
    echo "spark_protocol=aiwf-spark-stdout-v1"
    echo "spark_status=started"
    echo "spark_mode=${MODE}"
    echo "spark_routing_event=${ROUTING_EVENT}"
    DIRECT_ENVELOPE_STARTED="yes"
}

emit_bounded_direct_result() {
    local result_bytes
    local head_bytes
    local tail_bytes
    result_bytes="$(wc -c < "$RESULT_FILE")"
    if [ "$result_bytes" -le "$STDOUT_MAX_BYTES" ]; then
        cat "$RESULT_FILE"
        echo "spark_output_truncated=no"
        echo "spark_output_bytes=${result_bytes}"
        return
    fi

    # Estimator-family consumers need the schema fields, not tens of thousands
    # of advisory prose tokens. Preserve every recognized machine line.
    if [ "$MODE" = "execution-cost-estimator" ] || [ "$MODE" = "task-size-classifier" ] || [ "$MODE" = "preflight-bundle" ]; then
        grep -E '^(predicted_diff_lines_low|predicted_diff_lines_high|predicted_files|context_scope|validation_complexity|delegation_overhead|context_reacquisition_cost|codex_semantic_rereview|solution_clarity|semantic_concentration|task_role|estimated_direct_work_units|estimated_delegated_work_units|delegation_to_direct_ratio|economic_recommendation|safety_eligible|recommended_owner|cost_confidence|confidence|risk_flags|reason|stop_condition|size|recommended_route|expected_files|estimator_normalizations|accepted_suggestions|ignored_suggestions|conflicts_with_claude|conflicts_with_local_evidence|acceptance_satisfied_by_spark)=' "$RESULT_FILE" 2>/dev/null || true
    else
        head_bytes=$((STDOUT_MAX_BYTES * 3 / 4))
        tail_bytes=$((STDOUT_MAX_BYTES - head_bytes))
        head -c "$head_bytes" "$RESULT_FILE"
        printf '\n... [Spark direct output deterministically truncated] ...\n'
        tail -c "$tail_bytes" "$RESULT_FILE"
        printf '\n'
    fi
    echo "spark_output_truncated=yes"
    echo "spark_output_bytes=${result_bytes}"
    echo "spark_output_max_bytes=${STDOUT_MAX_BYTES}"
}

# Conservative secret redaction for stderr excerpts.
# Replaces lines containing common credential/token patterns with a marker.
redact_secrets() {
    sed -E \
        -e 's/^(.*(api[_-]?key|api[_-]?secret|auth[_-]?token|bearer|authorization|access[_-]?token|secret[_-]?key|private[_-]?key|password|passwd|credential)[[:space:]]*[:=][[:space:]]*).*/\1[REDACTED]/i' \
        -e 's/^(.*(token|secret|key|auth)[[:space:]]*[:=][[:space:]]*).*/\1[REDACTED]/i' \
        -e 's#^(.*://[^:/@ ]+):[^@/ ]+@#\1:[REDACTED]@#' \
        -e 's/^(.*Authorization:[[:space:]]*Bearer[[:space:]]+).*/\1[REDACTED]/i' \
        -e 's/^(.*x-api-key:[[:space:]]*).*/\1[REDACTED]/i' \
        -e 's/^(.*(OPENAI|ANTHROPIC|AZURE|AWS|GCP|GITHUB|GITLAB|SLACK|SENDGRID|TWILIO|STRIPE)[[:space:]_]*(API[_]?KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)[[:space:]]*[:=][[:space:]]*).*/\1[REDACTED]/i'
}

# Validate all required estimator output fields. Returns 0 if schema is valid,
# 1 if any required field is missing or has an invalid value.
_estimator_schema_valid() {
    [ -s "$RESULT_FILE" ] || return 1
    local _val
    # Check all required cost fields that the script already parses
    for _field in predicted_diff_lines_low predicted_diff_lines_high predicted_files \
                  context_scope validation_complexity delegation_overhead \
                  context_reacquisition_cost codex_semantic_rereview \
                  solution_clarity semantic_concentration task_role \
                  estimated_direct_work_units estimated_delegated_work_units \
                  delegation_to_direct_ratio economic_recommendation \
                  safety_eligible recommended_owner; do
        _val="$(grep -m1 "^${_field}=" "$RESULT_FILE" 2>/dev/null || true)"
        _val="${_val#$_field=}"
        case "$_field" in
            predicted_diff_lines_low|predicted_diff_lines_high)
                case "$_val" in ''|*[!0-9]*) return 1 ;; esac ;;
            predicted_files)
                case "$_val" in unknown) ;; ''|*[!0-9]*) return 1 ;; esac ;;
            context_scope)
                case "$_val" in local|bounded|broad|unknown) ;; *) return 1 ;; esac ;;
            validation_complexity)
                case "$_val" in none|low|medium|high|unknown) ;; *) return 1 ;; esac ;;
            delegation_overhead)
                case "$_val" in low|medium|high) ;; *) return 1 ;; esac ;;
            context_reacquisition_cost)
                case "$_val" in none|low|medium|high) ;; *) return 1 ;; esac ;;
            codex_semantic_rereview)
                case "$_val" in none|sampled|full) ;; *) return 1 ;; esac ;;
            solution_clarity|semantic_concentration)
                case "$_val" in high|medium|low) ;; *) return 1 ;; esac ;;
            task_role)
                case "$_val" in core-semantic|auxiliary|mixed|unknown) ;; *) return 1 ;; esac ;;
            estimated_direct_work_units|estimated_delegated_work_units)
                case "$_val" in ''|*[!0-9]*|0) return 1 ;; esac ;;
            delegation_to_direct_ratio)
                [[ "$_val" =~ ^[0-9]+([.][0-9]+)?$ ]] || return 1
                [[ "$_val" =~ ^0+([.]0+)?$ ]] && return 1 ;;
            economic_recommendation)
                case "$_val" in codex-fast-path|claude-builder) ;; *) return 1 ;; esac ;;
            safety_eligible)
                case "$_val" in yes|no) ;; *) return 1 ;; esac ;;
            recommended_owner)
                case "$_val" in codex-fast-path|claude-builder|spec-first|human-clarification) ;; *) return 1 ;; esac ;;
        esac
    done
    return 0
}

normalize_estimator_output() {
    local normalized_file
    local line
    local changed="no"
    [ -s "$RESULT_FILE" ] || return 0
    case "$MODE" in
        execution-cost-estimator|task-size-classifier|preflight-bundle) ;;
        *) return 0 ;;
    esac
    normalized_file="${RESULT_FILE}.normalized"
    : > "$normalized_file"
    while IFS= read -r line || [ -n "$line" ]; do
        if [[ "$line" =~ ^predicted_files=([0-9]+)[[:space:]]*-[[:space:]]*([0-9]+)$ ]]; then
            echo "predicted_files=${BASH_REMATCH[2]}" >> "$normalized_file"
            changed="predicted_files-range-to-upper-bound"
        else
            echo "$line" >> "$normalized_file"
        fi
    done < "$RESULT_FILE"
    if [ "$changed" != "no" ]; then
        echo "estimator_normalizations=${changed}" >> "$normalized_file"
        mv "$normalized_file" "$RESULT_FILE"
    else
        rm -f "$normalized_file"
    fi
}

classify_failure() {
    local codex_exit="$1"
    local result_empty="$2"
    if [ "$codex_exit" -eq 0 ] && [ "$result_empty" = "yes" ]; then
        echo "empty-response"
    elif [ "$codex_exit" -ne 0 ] && spark_unavailable_failure; then
        echo "availability-failure"
    elif [ "$codex_exit" -ne 0 ]; then
        echo "execution-failure"
    elif [ "$result_empty" = "no" ] && [ "$MODE" = "execution-cost-estimator" ]; then
        if ! _estimator_schema_valid; then
            echo "schema-invalid"
        else
            echo "none"
        fi
    else
        echo "none"
    fi
}

write_compact_diagnostic() {
    local codex_exit="$1"
    local result_empty="$2"
    local failure_class="$3"
    local stderr_excerpt_lines=15
    local stderr_head=""
    local stderr_tail=""
    local stderr_total_lines=0

    if [ -s "$STDERR_FILE" ]; then
        stderr_total_lines="$(wc -l < "$STDERR_FILE")"
        stderr_head="$(head -n "$stderr_excerpt_lines" "$STDERR_FILE" | redact_secrets)"
        if [ "$stderr_total_lines" -gt "$stderr_excerpt_lines" ]; then
            stderr_tail="$(tail -n "$stderr_excerpt_lines" "$STDERR_FILE" | redact_secrets)"
        fi
    fi

    DIAGNOSTIC_DIR="$(mktemp -d "${REPO_ROOT}/.worktrees/spark-diagnostic-${TIMESTAMP}-XXXXXX")"

    {
        echo "# Codex Spark Compact Diagnostic"
        echo ""
        echo "| Field | Value |"
        echo "|-------|-------|"
        echo "| Resolved mode | ${MODE} |"
        echo "| Model | ${MODEL} |"
        echo "| Execution environment | ${RESOLVED_EXECUTION_ENV} |"
        echo "| Exit code | ${codex_exit} |"
        echo "| Failure classification | ${failure_class} |"
        echo "| Stdout empty | ${result_empty} |"
        echo "| Result mode | ${RESULT_MODE} |"
        echo "| Diagnostics mode | ${DIAGNOSTICS_MODE} |"
        echo "| Timestamp | ${TIMESTAMP} |"
        echo ""
        echo "## Stderr Excerpt (redacted)"
        echo ""
        echo "### Head (first ${stderr_excerpt_lines} lines)"
        echo ""
        if [ -n "$stderr_head" ]; then
            echo '```'
            echo "$stderr_head"
            echo '```'
        else
            echo "(no stderr captured)"
        fi
        if [ -n "$stderr_tail" ]; then
            echo ""
            echo "### Tail (last ${stderr_excerpt_lines} lines of ${stderr_total_lines} total)"
            echo ""
            echo '```'
            echo "$stderr_tail"
            echo '```'
        fi
        echo ""
    } > "${DIAGNOSTIC_DIR}/diagnostic.md"

    echo "Codex Spark diagnostic: ${DIAGNOSTIC_DIR}/diagnostic.md" >&2
}

write_full_diagnostic() {
    local codex_exit="$1"
    # Create a permanent diagnostic directory for full mode
    DIAGNOSTIC_DIR="$(mktemp -d "${REPO_ROOT}/.worktrees/spark-diagnostic-${TIMESTAMP}-XXXXXX")"

    # Copy all evidence files from the (possibly transient) temp dir into
    # the permanent diagnostic directory so all report paths are real.
    local diag_report="${DIAGNOSTIC_DIR}/codex-spark.report.md"
    local diag_prompt="${DIAGNOSTIC_DIR}/codex-spark.prompt.md"
    local diag_result="${DIAGNOSTIC_DIR}/codex-spark.result.txt"
    local diag_stderr="${DIAGNOSTIC_DIR}/codex-spark.stderr.log"
    local diag_status="${DIAGNOSTIC_DIR}/codex-spark.status.txt"

    if [ -f "$PROMPT_FILE" ]; then
        cp "$PROMPT_FILE" "$diag_prompt"
    fi
    # Preserve real files even when either stream is empty so report paths
    # never point at missing evidence.
    if [ -f "$RESULT_FILE" ]; then
        cp "$RESULT_FILE" "$diag_result"
    else
        : > "$diag_result"
    fi
    if [ -f "$STDERR_FILE" ]; then
        cp "$STDERR_FILE" "$diag_stderr"
    else
        : > "$diag_stderr"
    fi
    # Write a compact status/metadata artifact
    {
        echo "exit_code=${codex_exit}"
        echo "failure_class=${DIAGNOSTIC_FAILURE_CLASS}"
        echo "mode=${MODE}"
        echo "model=${MODEL}"
        echo "execution_env=${RESOLVED_EXECUTION_ENV}"
        echo "result_mode=${RESULT_MODE}"
        echo "diagnostics_mode=${DIAGNOSTICS_MODE}"
        echo "auto_disabled=${SPARK_AUTO_DISABLED}"
        echo "spark_calls_used=${SPARK_CALLS_USED}"
        echo "timestamp=${TIMESTAMP}"
    } > "$diag_status"

    # Temporarily redirect REPORT_FILE to the diagnostic directory
    local _orig_report="$REPORT_FILE"
    REPORT_FILE="$diag_report"
    write_report_header "$codex_exit" "no"
    {
        echo "## Result"
        echo ""
        echo "| Field | Value |"
        echo "|-------|-------|"
        echo "| Codex exit code | ${codex_exit} |"
        echo "| Failure classification | ${DIAGNOSTIC_FAILURE_CLASS} |"
        echo "| Execution environment | ${RESOLVED_EXECUTION_ENV} |"
        echo "| Result mode | full (diagnostics=full) |"
        echo "| Prompt | ${diag_prompt} |"
        echo "| Raw output | ${diag_result} |"
        echo "| Stderr log | ${diag_stderr} |"
        echo "| Status metadata | ${diag_status} |"
        echo "| Strong-model fallback used | no |"
        echo ""
        echo "## Codex Spark Output"
        echo ""
        if [ -s "$diag_result" ]; then
            cat "$diag_result"
        else
            echo "No stdout output captured."
        fi
        if [ "$codex_exit" -ne 0 ]; then
            echo ""
            echo "## Failure Handling"
            echo ""
            if [ "$SPARK_AUTO_DISABLED" = "yes" ]; then
                echo "Spark exited non-zero with an availability-style failure and was auto-disabled because it is auxiliary. The helper exits 0 so the main workflow may continue."
            else
                echo "Spark exited non-zero. Strong-model fallback was not used. Re-run explicitly with another model only after human approval."
            fi
        fi
    } >> "$diag_report"
    REPORT_FILE="$_orig_report"
    echo "Codex Spark diagnostic (full): ${DIAGNOSTIC_DIR}" >&2
}

# ---------------------------------------------------------------------------
# Execution environment auto-detection.
# In auto mode, a truthy CODEX_SANDBOX_NETWORK_DISABLED means the parent
# sandbox restricts network. Making a real model call would be futile.
# ---------------------------------------------------------------------------

_NETWORK_RESTRICTED="no"
if [ "$EXECUTION_ENV" = "auto" ]; then
    case "${CODEX_SANDBOX_NETWORK_DISABLED:-}" in
        1|true|TRUE|True|yes|YES|Yes)
            _NETWORK_RESTRICTED="yes" ;;
    esac
fi

if [ "$EXECUTION_ENV" = "auto" ] && [ "$_NETWORK_RESTRICTED" = "yes" ]; then
    if [ "$REQUIRE_SPARK" = "1" ]; then
        SPARK_INVOKED="no"
        SPARK_CHECKS_RUN="not run"
        HELPER_EXIT_STATUS=1
        if [ "$RESULT_MODE" = "direct" ]; then
            echo "Error: Spark required but execution environment is network-restricted (CODEX_SANDBOX_NETWORK_DISABLED is set). Re-run from a host terminal: env -u CODEX_SANDBOX_NETWORK_DISABLED bash $0 <args>" >&2
            exit 1
        fi
        write_report_header "1" "no"
        {
            echo "## Result"
            echo ""
            echo "Spark required (--require-spark) but execution environment is network-restricted."
            echo ""
            echo "CODEX_SANDBOX_NETWORK_DISABLED is set in the inherited environment."
            echo "The sandbox cannot grant external network authority."
            echo ""
            echo "Re-run from a host terminal with the marker unset:"
            echo ""
            echo '```'
            echo "env -u CODEX_SANDBOX_NETWORK_DISABLED bash $0 <args>"
            echo '```'
        } >> "$REPORT_FILE"
        echo "Error: Spark required but execution environment is network-restricted." >&2
        echo "Codex Spark report: $REPORT_FILE" >&2
        exit 1
    fi
    SPARK_INVOKED="no"
    SPARK_HOST_HANDOFF_REQUIRED="yes"
    auto_disable_spark "auto-detected restricted sandbox (CODEX_SANDBOX_NETWORK_DISABLED is set); re-run with --execution-env host from an authorized terminal or unset the marker" "not-run"
fi

if ! command -v "$CODEX_BIN" >/dev/null 2>&1; then
    SPARK_INVOKED="no"
    SPARK_CHECKS_RUN="not run"
    if [ "$REQUIRE_SPARK" = "1" ]; then
        if [ "$RESULT_MODE" = "direct" ]; then
            echo "Error: codex CLI is not installed or not in PATH." >&2
            exit 127
        fi
        write_report_header "127" "no"
        {
            echo "## Result"
            echo ""
            echo "Codex CLI is not installed or not in PATH. Spark was not run."
            echo ""
            echo "Strong-model fallback is disabled by this helper; re-run explicitly with another model only after human approval."
        } >> "$REPORT_FILE"
        echo "Error: codex CLI is not installed or not in PATH." >&2
        exit 127
    fi
    auto_disable_spark "codex CLI is not installed or not in PATH" "not-run"
fi

run_codex() {
    case "$CODEX_BIN" in
        *.sh)
            bash "$CODEX_BIN" "$@"
            ;;
        *)
            "$CODEX_BIN" "$@"
            ;;
    esac
}

append_artifact_excerpts() {
    if [ "${#ARTIFACTS[@]}" -eq 0 ]; then
        return
    fi
    {
        echo ""
        echo "## Bounded Artifact Excerpts"
        echo ""
        echo "Only the first ${ARTIFACT_LINES} line(s) of each artifact are included to control prompt size. Ask Codex/human to inspect the full artifact when needed."
    } >> "$PROMPT_FILE"
    for artifact in "${ARTIFACTS[@]}"; do
        {
            echo ""
            echo "### Artifact: ${artifact}"
            echo ""
            echo '```'
            if [ "$ARTIFACT_LINES" -eq 0 ]; then
                echo "(excerpt disabled: CODEX_SPARK_ARTIFACT_LINES=0)"
            elif [ -f "$artifact" ]; then
                sed -n "1,${ARTIFACT_LINES}p" "$artifact"
            else
                echo "(artifact is not a regular file; path exists but excerpt is unavailable)"
            fi
            echo '```'
        } >> "$PROMPT_FILE"
    done
}

if [ "$MODE" = "micro-builder" ]; then
    SOURCE_STATUS="$INITIAL_SOURCE_STATUS"
    if [ -n "$SOURCE_STATUS" ] && [ "$ALLOW_DIRTY_SOURCE" != "1" ]; then
        if [ "$RESULT_MODE" = "direct" ]; then
            echo "Error: dirty source repository blocks micro-builder mode." >&2
            exit 2
        fi
        {
            echo "## Result"
            echo ""
            echo "Blocked: source repository is dirty. Commit, stash, or pass --allow-dirty-source before micro-builder mode."
            echo ""
            echo "## Source Status"
            echo '```'
            echo "$SOURCE_STATUS"
            echo '```'
        } >> "$REPORT_FILE"
        echo "Error: dirty source repository blocks micro-builder mode." >&2
        exit 2
    fi
    WORKTREE_DIR="${OUTPUT_DIR}/worktree"
    BRANCH="codex-spark/${TIMESTAMP}"
    git -C "$REPO_ROOT" worktree add -b "$BRANCH" "$WORKTREE_DIR" HEAD >/dev/null
    RUN_DIR="$WORKTREE_DIR"
    write_report_header
fi

if [ "$MODE" = "controlled-builder" ]; then
    CONTROLLED_BUILDER_MISSING="$(controlled_builder_contract_missing || true)"
    if [ -n "$CONTROLLED_BUILDER_MISSING" ]; then
        if [ "$RESULT_MODE" = "direct" ]; then
            echo "Error: controlled-builder contract missing: ${CONTROLLED_BUILDER_MISSING}" >&2
            exit 2
        fi
        {
            echo "## Result"
            echo ""
            echo "Blocked: Spark controlled-builder requires explicit tiny-scope authorization."
            echo ""
            echo "Missing contract: ${CONTROLLED_BUILDER_MISSING}."
            echo ""
            echo "Required task-card evidence: controlled-builder authorization, source edits allowed, at most three files, no public API/data model/security/migration/permission/concurrency/cross-module contract risk, existing-pattern reference, controlled-builder allowed paths row, and narrow validation."
        } >> "$REPORT_FILE"
        echo "Error: controlled-builder contract missing: ${CONTROLLED_BUILDER_MISSING}" >&2
        exit 2
    fi

    # Compare CLI --allow-write with task-card Controlled-builder allowed paths
    TC_ALLOWED_RAW="$(markdown_table_value "Controlled-builder allowed paths")"
    TC_ALLOWED_PATHS=()
    IFS=',' read -ra _tc_parts <<< "$TC_ALLOWED_RAW"
    for _tp in "${_tc_parts[@]}"; do
        _tp="$(printf '%s' "$_tp" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
        [ -n "$_tp" ] && TC_ALLOWED_PATHS+=("$_tp")
    done

    # Validate task-card paths before comparing sets. Without this pass a glob
    # can be expanded later by platform-specific shell behavior and obscure the
    # intended "specific file" error.
    for _tp in "${TC_ALLOWED_PATHS[@]}"; do
        validate_allow_write_path "$_tp" || exit 2
    done

    # Reject duplicates in task-card paths
    _tc_unique=$(printf '%s\n' "${TC_ALLOWED_PATHS[@]}" | sort -u | wc -l)
    if [ "$_tc_unique" -ne "${#TC_ALLOWED_PATHS[@]}" ]; then
        echo "Error: task card Controlled-builder allowed paths contains duplicates." >&2
        exit 2
    fi

    # Exact set match between CLI and task-card allowlists
    _cli_sorted="$(printf '%s\n' "${ALLOWED_WRITES[@]}" | sort)"
    _tc_sorted="$(printf '%s\n' "${TC_ALLOWED_PATHS[@]}" | sort)"
    if [ "$_cli_sorted" != "$_tc_sorted" ]; then
        echo "Error: --allow-write paths do not match task card Controlled-builder allowed paths." >&2
        echo "  CLI: ${ALLOWED_WRITES[*]}" >&2
        echo "  Task card: ${TC_ALLOWED_PATHS[*]}" >&2
        exit 2
    fi

    SOURCE_STATUS="$INITIAL_SOURCE_STATUS"
    if [ -n "$SOURCE_STATUS" ] && [ "$ALLOW_DIRTY_SOURCE" != "1" ]; then
        if [ "$RESULT_MODE" = "direct" ]; then
            echo "Error: dirty source repository blocks controlled-builder mode." >&2
            exit 2
        fi
        {
            echo "## Result"
            echo ""
            echo "Blocked: source repository is dirty. Commit, stash, or pass --allow-dirty-source before controlled-builder mode."
            echo ""
            echo "## Source Status"
            echo '```'
            echo "$SOURCE_STATUS"
            echo '```'
        } >> "$REPORT_FILE"
        echo "Error: dirty source repository blocks controlled-builder mode." >&2
        exit 2
    fi
    WORKTREE_DIR="${OUTPUT_DIR}/worktree"
    BRANCH="codex-spark/${TIMESTAMP}"
    git -C "$REPO_ROOT" worktree add -b "$BRANCH" "$WORKTREE_DIR" HEAD >/dev/null
    RUN_DIR="$WORKTREE_DIR"
    write_report_header
fi

cat > "$PROMPT_FILE" <<EOF
# Codex Spark Execution Request

You are an optional Codex Spark auxiliary in a Codex/Claude workflow.

Model requested: ${MODEL}
Mode requested: ${REQUESTED_MODE}
Mode resolved: ${MODE}
Sandbox: ${SANDBOX}
Budget mode requested: ${REQUESTED_BUDGET_MODE}
Budget mode effective: ${BUDGET_MODE}
Pipeline stage: ${SPARK_PIPELINE_STAGE}
Roles executed: ${SPARK_ROLES_EXECUTED}
Repository scale detected: ${REPOSITORY_SCALE_DETECTED}
Repository routing scale: ${REPOSITORY_ROUTING_SCALE}
Repository tracked/source files: ${REPOSITORY_TRACKED_FILES}/${REPOSITORY_SOURCE_FILES}
Historical worktree cost: ${REPOSITORY_WORKTREE_COST} (median ${REPOSITORY_WORKTREE_MEDIAN_SECONDS}s, samples ${REPOSITORY_WORKTREE_SAMPLES}, io_promoted ${REPOSITORY_IO_PROMOTED})
Dynamic ordinary gate: ${FAST_PATH_MAX_DIFF_LINES} calibrated lines / ${ORDINARY_FAST_PATH_MAX_FILES} files
Dynamic concentrated gate: ${CONCENTRATED_FAST_PATH_MAX_DIFF_LINES} calibrated lines / ${CONCENTRATED_FAST_PATH_MAX_FILES} files
Large-repository full-rereview economy gate: ${FULL_REREVIEW_FAST_PATH_MAX_DIFF_LINES} calibrated lines / ${FULL_REREVIEW_FAST_PATH_MAX_FILES} files

Operating rules:
- Use the requested Spark model only. Do not silently fall back to a stronger model.
- Keep output compressed: decisions, evidence, changed files if any, checks run, and risks.
- Keep the response below 12,000 bytes. For estimator-family modes, emit machine-readable fields first and omit repository narration.
- Treat Claude-owned implementation as Claude-owned unless this task card explicitly authorizes Spark micro-builder work.
- Do not claim completion from prose alone. Cite artifacts, commands, or diffs.
- If blocked by missing context, permissions, model access, network, or auth, report the blocker instead of guessing.
- Spark output is advisory input to Codex review. It cannot replace Claude Builder ownership, cannot approve final review, and cannot by itself satisfy acceptance criteria.
- Except in monitor-triage, end your output with these fields exactly: accepted_suggestions=<none or comma-separated>; ignored_suggestions=<none or comma-separated>; conflicts_with_claude=<none or short note>; conflicts_with_local_evidence=<none or short note>; acceptance_satisfied_by_spark=no.

Mode contract:
- task-size-classifier: classify task size and routing risk using cheap Spark quota before Codex spends stronger-model context; do not edit files. Output the standard estimator fields listed by execution-cost-estimator plus size=tiny|small|medium|large|unknown; recommended_route=codex-fast-path|spark-review-only|spark-micro-builder|claude-builder|checker-test|spec-first|human-clarification; confidence=high|medium|low; expected_files=1-2|3-5|>5|unknown. Choose owner from edit size/files, context sufficiency, solution clarity, confidence, context reacquisition, mandatory Codex rereview, and delegation overhead. Risk flags and validation complexity affect downstream rigor and MUST NOT push ownership from Codex to Claude.
- execution-cost-estimator: read-only mode for routing event ${ROUTING_EVENT}. Estimate direct Codex editing cost versus Claude delegation overhead. Do not edit files. Output exactly these machine-readable fields: predicted_diff_lines_low=<integer>; predicted_diff_lines_high=<integer>; predicted_files=<integer|unknown>; context_scope=local|bounded|broad|unknown; validation_complexity=none|low|medium|high|unknown; delegation_overhead=low|medium|high; context_reacquisition_cost=none|low|medium|high; codex_semantic_rereview=none|sampled|full; solution_clarity=high|medium|low; semantic_concentration=high|medium|low; task_role=core-semantic|auxiliary|mixed|unknown; estimated_direct_work_units=<positive integer>; estimated_delegated_work_units=<positive integer>; delegation_to_direct_ratio=<decimal>; economic_recommendation=codex-fast-path|claude-builder; safety_eligible=yes|no; recommended_owner=codex-fast-path|claude-builder|spec-first|human-clarification; cost_confidence=high|medium|low; risk_flags=none|comma-separated flags; reason=<one short paragraph>; stop_condition=<one sentence>. predicted_files MUST be one integer or unknown, never a range. Classify tests/checker work, mechanical batches, long validation/log processing, evidence collection, and independent support units as auxiliary. Classify tightly coupled behavior/architecture implementation as core-semantic; mixed work should be split when practical. Work units are relative estimates, not actual/billable token measurements. Count Claude context reacquisition/handoff and mandatory Codex rereview in delegated work units, so an economic_recommendation=codex-fast-path must not claim lower delegated total work than direct work. Use the dynamic gates printed above. Concentrated routing is for core-semantic work only. In large/giant repositories, bounded core-semantic work that still requires full Codex rereview may use the full-rereview economy gate; auxiliary work above a tiny one-file/50-line edit prefers Claude. Risk flags and validation complexity MUST NOT push ownership from Codex to Claude. If a risk override is later applied, it may bias high-risk work only toward Codex. Risk changes rigor, not owner direction.
- review-only: inspect the task card and available repository context, do not edit files.
- task-card-audit: inspect the task card for missing gates, mixed responsibilities, unclear acceptance criteria, unsafe scope, and likely Claude stall risks; do not edit files.
- plan-splitter: propose smaller Builder/Checker task cards or independent parallelizable slices; do not edit files.
- validation-planner: propose exact low-noise validation commands and state whether local validation should be skipped; do not run commands unless the task card explicitly allows it.
- failure-triage: inspect provided artifacts for likely stall/failure attribution and recommend wait/re-dispatch/narrow/takeover; do not edit files.
- evidence-checker: inspect evidence artifacts and run only narrow, task-card-allowed checks; do not edit files.
- micro-builder: only when the task card explicitly authorizes tiny isolated work. Touch no more than one or two small files, avoid public API/data/security/migration/permission/concurrency/cross-module contracts, keep the diff minimal, and report narrow validation evidence.
- controlled-builder: only when the task card explicitly authorizes exact-path isolated work. Write ONLY to the explicitly allowlisted paths (see --allow-write in the prompt). Touch no more than three files total. Avoid public API/data/security/migration/permission/concurrency/cross-module contracts. Keep diff within --max-diff-lines cap. Report narrow validation evidence including all changed/untracked paths.
- parallel-planner: produce an advisory parallel scheduling proposal as strict JSON. Do not edit files. Do not dispatch or execute any tasks. Output exactly one JSON object in a fenced code block matching this schema: {"schema_version":1,"group_id":"<group-slug>","max_concurrency":<int>,"failure_policy":"skip-dependents","tasks":[{"id":"<task-id>","task_card":"<path>","depends_on":["<task-id>"]}]}. After the JSON block, end with these reconciliation fields exactly: accepted_suggestions=<none or comma-separated>; ignored_suggestions=<none or comma-separated>; conflicts_with_claude=<none or short note>; conflicts_with_local_evidence=<none or short note>; acceptance_satisfied_by_spark=no. The proposal is advisory only; Codex or a human must review and save the plan before any dispatch.
- observe-synthesizer: read-only synthesis of provided artifacts. Compress observations into structured findings. Do not edit files. Output structured observations with evidence citations.
- task-card-drafter: draft a task card from the provided context and artifacts. Do not edit files. Output a structured task card proposal.
- context-packet-builder: build a Context Packet draft from the task card and any provided artifacts. Do not edit files. Output a structured Context Packet with bounded excerpts.
- preflight-bundle: combined preflight analysis in one invocation for routing event ${ROUTING_EVENT}. Perform risk classification, bounded evidence synthesis, task-card drafting, Context Packet drafting, unknown/risk extraction, split/parallel recommendation, and execution cost estimation. Do not edit files. Output MUST contain exactly these headings in this order: Decision Summary, Risk Flags, Scope and Boundaries, Acceptance Matrix, Evidence Conflicts, Required Codex Decisions, Recommended Next Action. Under Decision Summary, include every execution-cost-estimator key=value field, including context_reacquisition_cost, codex_semantic_rereview, solution_clarity, and semantic_concentration. Use the same ordinary and concentrated deterministic owner gates; risk changes rigor, not owner direction.
- direction-precheck: check direction and boundary against the task card and provided artifacts. Do not edit files. Output direction/boundary assessment with specific risks.
- acceptance-matrix: produce an acceptance criteria matrix from the task card. Do not edit files. Map each acceptance criterion to verification method, evidence source, and pass/fail status.
- postflight-bundle: combined postflight analysis in one invocation. Perform direction/boundary/omission checks, acceptance mapping, evidence conflict detection, validation recommendations, and provisional accept/revise/split/escalate recommendation. Do not edit files. Output MUST contain exactly these headings in this order: Decision Summary, Risk Flags, Scope and Boundaries, Acceptance Matrix, Evidence Conflicts, Required Codex Decisions, Recommended Next Action.
- revision-drafter: draft a bounded revision task card from failure triage results or postflight findings. Do not edit source files. Output a structured revision task card proposal.
- lesson-extractor: extract reusable lessons from provided artifacts. Do not edit files. Output structured lessons with evidence citations.
- monitor-triage: read only the supplied compact local monitor JSON. Never request or inspect raw process listings, full progress logs, full status output, network tails, or source diffs. Return exactly these fields, one per line: decision=continue|inspect|interrupt-candidate|uncertain; confidence=high|medium|low; reason_code=<one-kebab-case-code>; codex_review_required=yes|no; interrupt_authorized=no. Prefer continue when useful artifact/progress growth exists and no explicit deviation is present. Time, PID state, or changed-file count alone never proves direction deviation. Use interrupt-candidate only for an explicit local direction-deviation signal or corroborated repeated L3 stale evidence with no growth. Use uncertain for missing or conflicting evidence. This mode is advisory and never kills a process or authorizes interruption.
EOF

# In aggressive budget mode, failure-triage additionally asks for a bounded revision task draft.
if [ "$MODE" = "failure-triage" ] && [ "$BUDGET_MODE" = "aggressive" ]; then
    cat >> "$PROMPT_FILE" <<'REVISION_EOF'

Additional failure-triage responsibilities (aggressive budget mode):
- After your failure triage analysis, also produce a bounded revision task draft.
- The revision draft should include: specific files/areas to change, minimal scope, and narrow validation commands.
- Do not edit files. Output the revision draft after your triage findings.
REVISION_EOF
fi

cat >> "$PROMPT_FILE" <<'TASK_EOF'

## Task Input

TASK_EOF
if [ "$INPUT_KIND" = "brief" ]; then
    echo "Input type: pre-task-card brief. Estimate scope and routing before requesting a full task card." >> "$PROMPT_FILE"
    echo "" >> "$PROMPT_FILE"
else
    echo "Input type: full task card." >> "$PROMPT_FILE"
    echo "" >> "$PROMPT_FILE"
fi
cat "$TASK_CARD_COPY" >> "$PROMPT_FILE"

# Add controlled-builder specific constraints to prompt
if [ "$MODE" = "controlled-builder" ]; then
    {
        echo ""
        echo "## Controlled-Builder Constraints"
        echo ""
        echo "Allowed write paths (repo-relative):"
        for aw_path in "${ALLOWED_WRITES[@]}"; do
            echo "- ${aw_path}"
        done
        echo ""
        echo "Maximum diff lines (added+deleted): ${MAX_DIFF_LINES}"
        echo ""
        echo "IMPORTANT: You may ONLY modify the files listed above. Any changes to paths outside this allowlist will be rejected. Total changed files must not exceed 3. Total added+deleted lines must not exceed ${MAX_DIFF_LINES}."
    } >> "$PROMPT_FILE"
fi

append_artifact_excerpts

# Direct callers receive a protocol header before the potentially blocking
# model call. Even an external caller timeout therefore has a usable state.
emit_direct_envelope_start

SPARK_CALL_STARTED_EPOCH="$(date +%s)"
set +e
(
    cd "$RUN_DIR"
    # In host mode, remove the inherited sandbox network restriction from the
    # real model subprocess. The caller asserts it already has outside-sandbox
    # authority. This does not affect the parent shell or escape the sandbox.
    if [ "$EXECUTION_ENV" = "host" ]; then
        unset CODEX_SANDBOX_NETWORK_DISABLED
    fi
    broker_args=(
        --role spark --stage builder
        --task-id "spark-$(basename "$RUN_DIR")"
        --timeout-seconds "$CALL_TIMEOUT_SECONDS"
        --ledger "${REPO_ROOT}/.ai-workflow/model-calls.jsonl"
        --input "$PROMPT_FILE" --output "$SPARK_EVENTS_FILE" --stderr "$STDERR_FILE"
    )
    if [ -f "execution-plan.json" ]; then
        broker_args+=(--plan "execution-plan.json")
    fi
    if [ "${AI_CODING_WORKFLOW_BYPASS_BROKER:-0}" = "1" ]; then
        # Internal bypass for tests/bootstrap to avoid broker recursion.
        if [ -n "$CODEX_RUNTIME_HOME" ]; then
            HOME="$CODEX_RUNTIME_HOME" CODEX_HOME="$CODEX_RUNTIME_HOME" run_codex exec --json --output-last-message "$RESULT_FILE" --model "$MODEL" --sandbox "$SANDBOX" - < "$PROMPT_FILE" > "$SPARK_EVENTS_FILE" 2> "$STDERR_FILE"
        else
            run_codex exec --json --output-last-message "$RESULT_FILE" --model "$MODEL" --sandbox "$SANDBOX" - < "$PROMPT_FILE" > "$SPARK_EVENTS_FILE" 2> "$STDERR_FILE"
        fi
    else
        # Broker-mediated execution for quota enforcement and audit.
        if [ -n "$CODEX_RUNTIME_HOME" ]; then
            HOME="$CODEX_RUNTIME_HOME" CODEX_HOME="$CODEX_RUNTIME_HOME" \
                python3 "${SCRIPT_DIR}/model-call-broker.py" "${broker_args[@]}" -- \
                "$CODEX_BIN" exec --json --output-last-message "$RESULT_FILE" --model "$MODEL" --sandbox "$SANDBOX" -
        else
            python3 "${SCRIPT_DIR}/model-call-broker.py" "${broker_args[@]}" -- \
                "$CODEX_BIN" exec --json --output-last-message "$RESULT_FILE" --model "$MODEL" --sandbox "$SANDBOX" -
        fi
    fi
)
CODEX_STATUS=$?
set -e
SPARK_CALL_WALL_MS="$(( ($(date +%s) - SPARK_CALL_STARTED_EPOCH) * 1000 ))"
HELPER_EXIT_STATUS="$CODEX_STATUS"
SPARK_CALLS_USED=1

# Fake/older Codex CLIs may ignore --output-last-message. Recover the final
# message from JSONL, or preserve plain stdout as a compatibility fallback.
if [ ! -s "$RESULT_FILE" ] && [ -s "$SPARK_EVENTS_FILE" ]; then
    if command -v python3 >/dev/null 2>&1; then
        python3 - "$SPARK_EVENTS_FILE" "$RESULT_FILE" <<'PYEOF' || true
import json, sys
from pathlib import Path
source, target = Path(sys.argv[1]), Path(sys.argv[2])
texts = []
plain = []
for raw in source.read_text(encoding="utf-8", errors="replace").splitlines():
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        plain.append(raw)
        continue
    if not isinstance(event, dict):
        continue
    item = event.get("item") if isinstance(event.get("item"), dict) else event
    for key in ("text", "message", "content"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            texts.append(value)
            break
content = "\n".join(texts or plain).strip()
if content:
    target.write_text(content + "\n", encoding="utf-8")
PYEOF
    else
        cp "$SPARK_EVENTS_FILE" "$RESULT_FILE"
    fi
fi

if command -v python3 >/dev/null 2>&1 && [ -f "${SCRIPT_DIR}/model-usage.py" ]; then
    SPARK_USAGE_ARGS=(
        --source codex --input "$SPARK_EVENTS_FILE"
        --ledger "${AI_WORKFLOW_MODEL_USAGE_LEDGER:-${REPO_ROOT}/.ai-workflow/model-usage.jsonl}"
        --task-id "${AI_WORKFLOW_TASK_ID:-spark-${TIMESTAMP}}" --call-id "spark-${TIMESTAMP}-$$"
        --role spark --stage "$MODE" --model "$MODEL" --result "$CODEX_STATUS"
        --wall-time-ms "$SPARK_CALL_WALL_MS"
    )
    if [ -n "${AI_WORKFLOW_RUN_ID:-}" ]; then
        SPARK_USAGE_ARGS+=(--run-id "$AI_WORKFLOW_RUN_ID")
    fi
    if [ -n "${AI_WORKFLOW_EXPERIMENT_ARM:-}" ]; then
        SPARK_USAGE_ARGS+=(--experiment-arm "$AI_WORKFLOW_EXPERIMENT_ARM")
    fi
    python3 "${SCRIPT_DIR}/model-usage.py" capture "${SPARK_USAGE_ARGS[@]}" \
        >/dev/null 2>>"$STDERR_FILE" || true
fi
if [ "$CODEX_STATUS" -eq 0 ]; then
    normalize_estimator_output
fi
if [ "$CODEX_STATUS" -eq 0 ] && [ -s "$RESULT_FILE" ]; then
    SPARK_MODEL_RESPONSE_RECEIVED="yes"
fi
if [ "$RESULT_MODE" != "direct" ] && [ -f "$REPORT_FILE" ]; then
    sed -i "s/| Spark model response received? | [^|]* |/| Spark model response received? | ${SPARK_MODEL_RESPONSE_RECEIVED} |/" "$REPORT_FILE" 2>/dev/null || true
fi

if [ "$MODE" = "micro-builder" ] || [ "$MODE" = "controlled-builder" ]; then
    git -C "$RUN_DIR" status --porcelain --untracked-files=all > "$STATUS_FILE" || true
    git -C "$RUN_DIR" diff --binary > "$DIFF_FILE" || true
    git -C "$RUN_DIR" diff --stat > "$DIFFSTAT_FILE" || true
else
    if [ "$RESULT_MODE" = "direct" ]; then
        : > "$STATUS_FILE"
    else
        git -C "$REPO_ROOT" status --porcelain --untracked-files=all > "$STATUS_FILE" || true
    fi
    : > "$DIFF_FILE"
    : > "$DIFFSTAT_FILE"
fi

# Controlled-builder boundary validation (hardened: NUL-safe, binary-aware)
CONTROLLED_BOUNDARY_FAILURE=""
if [ "$MODE" = "controlled-builder" ] && [ "$CODEX_STATUS" -eq 0 ]; then
    TOTAL_ADD_DEL=0
    # Collect tracked changes (modifications, deletions, staged adds)
    TRACKED_PATHS=()
    while IFS= read -r -d '' _line; do
        [ -n "$_line" ] || continue
        TRACKED_PATHS+=("$_line")
    done < <(git -C "$RUN_DIR" diff --no-renames --name-only -z HEAD 2>/dev/null || true)

    # Collect untracked files
    UNTRACKED_PATHS=()
    while IFS= read -r -d '' _line; do
        [ -n "$_line" ] || continue
        UNTRACKED_PATHS+=("$_line")
    done < <(git -C "$RUN_DIR" ls-files --others --exclude-standard -z 2>/dev/null || true)

    # Deduplicate all changed paths (temp-file approach for portability)
    ALL_CHANGED_PATHS=()
    _seen_tmp="$(mktemp)"
    for _p in "${TRACKED_PATHS[@]}" "${UNTRACKED_PATHS[@]}"; do
        if ! grep -qxF "$_p" "$_seen_tmp" 2>/dev/null; then
            printf '%s\n' "$_p" >> "$_seen_tmp"
            ALL_CHANGED_PATHS+=("$_p")
        fi
    done
    rm -f "$_seen_tmp"

    # Check file count (max 3)
    if [ "${#ALL_CHANGED_PATHS[@]}" -gt 3 ]; then
        CONTROLLED_BOUNDARY_FAILURE="too many changed files: ${#ALL_CHANGED_PATHS[@]} (max 3)"
    fi

    # Check each path is in the allowlist
    if [ -z "$CONTROLLED_BOUNDARY_FAILURE" ]; then
        for _changed in "${ALL_CHANGED_PATHS[@]}"; do
            _found="no"
            for _aw in "${ALLOWED_WRITES[@]}"; do
                if [ "$_changed" = "$_aw" ]; then
                    _found="yes"
                    break
                fi
            done
            if [ "$_found" = "no" ]; then
                CONTROLLED_BOUNDARY_FAILURE="changed path not in allowlist: ${_changed}"
                break
            fi
        done
    fi

    # Check for binary content and count added+deleted lines
    if [ -z "$CONTROLLED_BOUNDARY_FAILURE" ]; then
        # Count tracked changes via numstat; reject binary (shown as "-")
        if [ "${#TRACKED_PATHS[@]}" -gt 0 ]; then
            while IFS=$'\t' read -r _add _del _rest; do
                [ -n "$_add" ] || continue
                if [ "$_add" = "-" ] || [ "$_del" = "-" ]; then
                    CONTROLLED_BOUNDARY_FAILURE="binary content detected in tracked changes"
                    break
                fi
                TOTAL_ADD_DEL=$((TOTAL_ADD_DEL + _add + _del))
            done < <(git -C "$RUN_DIR" diff --no-renames --numstat HEAD 2>/dev/null || true)
        fi

        # Count untracked files with Git numstat so a final newline is not
        # required for a one-line addition and binary files are reported as '-'.
        if [ -z "$CONTROLLED_BOUNDARY_FAILURE" ] && [ "${#UNTRACKED_PATHS[@]}" -gt 0 ]; then
            for _up in "${UNTRACKED_PATHS[@]}"; do
                if [ -f "${RUN_DIR}/${_up}" ]; then
                    _untracked_numstat="$(git -C "$RUN_DIR" diff --no-index --numstat -- /dev/null "$_up" 2>/dev/null || true)"
                    IFS=$'\t' read -r _add _del _rest <<< "$_untracked_numstat"
                    if [ "${_add:-}" = "-" ] || [ "${_del:-}" = "-" ]; then
                        CONTROLLED_BOUNDARY_FAILURE="binary content detected in untracked file: ${_up}"
                        break
                    fi
                    if [[ "${_add:-}" =~ ^[0-9]+$ ]] && [[ "${_del:-}" =~ ^[0-9]+$ ]]; then
                        TOTAL_ADD_DEL=$((TOTAL_ADD_DEL + _add + _del))
                    else
                        CONTROLLED_BOUNDARY_FAILURE="unable to count untracked diff lines: ${_up}"
                        break
                    fi
                fi
            done
        fi

        # Enforce max diff lines against combined total
        if [ -z "$CONTROLLED_BOUNDARY_FAILURE" ] && [ "$TOTAL_ADD_DEL" -gt "$MAX_DIFF_LINES" ]; then
            CONTROLLED_BOUNDARY_FAILURE="diff too large: ${TOTAL_ADD_DEL} added+deleted lines (max ${MAX_DIFF_LINES})"
        fi
    fi

    # Produce full patch evidence (tracked + untracked)
    PATCH_EVIDENCE=""
    if [ "${#TRACKED_PATHS[@]}" -gt 0 ]; then
        PATCH_EVIDENCE="$(git -C "$RUN_DIR" diff --no-renames --binary HEAD 2>/dev/null || true)"
    fi
    if [ "${#UNTRACKED_PATHS[@]}" -gt 0 ]; then
        for _up in "${UNTRACKED_PATHS[@]}"; do
            if [ -f "${RUN_DIR}/${_up}" ]; then
                _untracked_patch="$(git -C "$RUN_DIR" diff --no-index -- "/dev/null" "$_up" 2>/dev/null)" || true
                if [ -n "$_untracked_patch" ]; then
                    PATCH_EVIDENCE="${PATCH_EVIDENCE}
${_untracked_patch}"
                fi
            fi
        done
    fi
    printf '%s\n' "$PATCH_EVIDENCE" > "$DIFF_FILE"
    git -C "$RUN_DIR" diff --no-renames --stat HEAD > "$DIFFSTAT_FILE" 2>/dev/null || true
    for _up in "${UNTRACKED_PATHS[@]}"; do
        git -C "$RUN_DIR" diff --no-index --stat -- /dev/null "$_up" >> "$DIFFSTAT_FILE" 2>/dev/null || true
    done

    # On boundary failure, report and exit non-zero with full isolated evidence
    if [ -n "$CONTROLLED_BOUNDARY_FAILURE" ]; then
        HELPER_EXIT_STATUS=2
        write_report_header "2" "boundary-violation"
        {
            echo "## Result"
            echo ""
            echo "Boundary violation: ${CONTROLLED_BOUNDARY_FAILURE}"
            echo ""
            echo "Changed paths (${#ALL_CHANGED_PATHS[@]}):"
            for _cp in "${ALL_CHANGED_PATHS[@]}"; do
                echo "- ${_cp}"
            done
            echo ""
            echo "Allowlisted paths:"
            for _aw in "${ALLOWED_WRITES[@]}"; do
                echo "- ${_aw}"
            done
            echo ""
            echo "Total added+deleted lines: ${TOTAL_ADD_DEL} (max ${MAX_DIFF_LINES})"
            echo ""
            echo "## Patch Evidence"
            echo ""
            echo '```diff'
            echo "$PATCH_EVIDENCE"
            echo '```'
            echo ""
            echo "The isolated worktree and evidence are preserved for review. The source repository was not modified."
            echo ""
            echo "## Codex Spark Output"
            echo ""
            if [ -s "$RESULT_FILE" ]; then
                cat "$RESULT_FILE"
            else
                echo "No stdout output captured."
            fi
        } >> "$REPORT_FILE"
        echo "Error: controlled-builder boundary violation: ${CONTROLLED_BOUNDARY_FAILURE}" >&2
        echo "Codex Spark report: $REPORT_FILE"
        exit 2
    fi
fi

DIFF_VALUE="no"
if ([ "$MODE" = "micro-builder" ] || [ "$MODE" = "controlled-builder" ]) && [ -s "$DIFF_FILE" ]; then
    if [ "$RESULT_MODE" = "minimal" ]; then
        DIFF_VALUE="yes (transient)"
    else
        DIFF_VALUE="yes: ${DIFF_FILE}"
    fi
fi
if [ "$CODEX_STATUS" -ne 0 ] && [ "$REQUIRE_SPARK" != "1" ] && spark_unavailable_failure; then
    SPARK_AUTO_DISABLED="yes"
    SPARK_DISABLE_REASON="$(spark_failure_auto_disable_reason)"
    HELPER_EXIT_STATUS=0
fi

# ---------------------------------------------------------------------------
# Parse execution-cost-estimator fields from Codex output.
# Each known key is read from at most one exact line. Values are validated
# against allowed enums or numeric forms. Missing or invalid values become
# "not recorded". No eval, source, command substitution, or dynamic variable
# names are used on model-provided text.
# ---------------------------------------------------------------------------

COST_PREDICTED_DIFF_LOW="not recorded"
COST_PREDICTED_DIFF_HIGH="not recorded"
COST_PREDICTED_FILES="not recorded"
COST_CONTEXT_SCOPE="not recorded"
COST_VALIDATION_COMPLEXITY="not recorded"
COST_DELEGATION_OVERHEAD="not recorded"
COST_CONTEXT_REACQUISITION="not recorded"
COST_CODEX_REREVIEW="not recorded"
COST_SOLUTION_CLARITY="not recorded"
COST_SEMANTIC_CONCENTRATION="not recorded"
COST_TASK_ROLE="not recorded"
COST_DIRECT_WORK_UNITS="not recorded"
COST_DELEGATED_WORK_UNITS="not recorded"
COST_RATIO="not recorded"
COST_ECONOMIC_RECOMMENDATION="not recorded"
COST_SAFETY_ELIGIBLE="not recorded"
COST_RECOMMENDED_OWNER="not recorded"
COST_CONFIDENCE="not recorded"
COST_RISK_FLAGS="not recorded"
COST_SAFETY_REASONS="not evaluated"
COST_CALIBRATION_MULTIPLIER="1.5"
COST_CALIBRATED_DIFF_HIGH="not recorded"
COST_FAST_PATH_CLASS="none"

if [ -s "$RESULT_FILE" ]; then
    # Helper: extract the value from a key=value line, exactly one match.
    _cost_val="$(grep -m1 '^predicted_diff_lines_low=' "$RESULT_FILE" 2>/dev/null || true)"
    _cost_val="${_cost_val#predicted_diff_lines_low=}"
    case "$_cost_val" in
        ''|*[!0-9]*) COST_PREDICTED_DIFF_LOW="not recorded" ;;
        *) COST_PREDICTED_DIFF_LOW="$_cost_val" ;;
    esac

    _cost_val="$(grep -m1 '^predicted_diff_lines_high=' "$RESULT_FILE" 2>/dev/null || true)"
    _cost_val="${_cost_val#predicted_diff_lines_high=}"
    case "$_cost_val" in
        ''|*[!0-9]*) COST_PREDICTED_DIFF_HIGH="not recorded" ;;
        *) COST_PREDICTED_DIFF_HIGH="$_cost_val" ;;
    esac

    _cost_val="$(grep -m1 '^predicted_files=' "$RESULT_FILE" 2>/dev/null || true)"
    _cost_val="${_cost_val#predicted_files=}"
    case "$_cost_val" in
        unknown) COST_PREDICTED_FILES="unknown" ;;
        ''|*[!0-9]*) COST_PREDICTED_FILES="not recorded" ;;
        *) COST_PREDICTED_FILES="$_cost_val" ;;
    esac

    _cost_val="$(grep -m1 '^context_scope=' "$RESULT_FILE" 2>/dev/null || true)"
    _cost_val="${_cost_val#context_scope=}"
    case "$_cost_val" in
        local|bounded|broad|unknown) COST_CONTEXT_SCOPE="$_cost_val" ;;
        *) COST_CONTEXT_SCOPE="not recorded" ;;
    esac

    _cost_val="$(grep -m1 '^validation_complexity=' "$RESULT_FILE" 2>/dev/null || true)"
    _cost_val="${_cost_val#validation_complexity=}"
    case "$_cost_val" in
        none|low|medium|high|unknown) COST_VALIDATION_COMPLEXITY="$_cost_val" ;;
        *) COST_VALIDATION_COMPLEXITY="not recorded" ;;
    esac

    _cost_val="$(grep -m1 '^delegation_overhead=' "$RESULT_FILE" 2>/dev/null || true)"
    _cost_val="${_cost_val#delegation_overhead=}"
    case "$_cost_val" in
        low|medium|high) COST_DELEGATION_OVERHEAD="$_cost_val" ;;
        *) COST_DELEGATION_OVERHEAD="not recorded" ;;
    esac

    _cost_val="$(grep -m1 '^context_reacquisition_cost=' "$RESULT_FILE" 2>/dev/null || true)"
    _cost_val="${_cost_val#context_reacquisition_cost=}"
    case "$_cost_val" in none|low|medium|high) COST_CONTEXT_REACQUISITION="$_cost_val" ;; esac

    _cost_val="$(grep -m1 '^codex_semantic_rereview=' "$RESULT_FILE" 2>/dev/null || true)"
    _cost_val="${_cost_val#codex_semantic_rereview=}"
    case "$_cost_val" in none|sampled|full) COST_CODEX_REREVIEW="$_cost_val" ;; esac

    _cost_val="$(grep -m1 '^solution_clarity=' "$RESULT_FILE" 2>/dev/null || true)"
    _cost_val="${_cost_val#solution_clarity=}"
    case "$_cost_val" in high|medium|low) COST_SOLUTION_CLARITY="$_cost_val" ;; esac

    _cost_val="$(grep -m1 '^semantic_concentration=' "$RESULT_FILE" 2>/dev/null || true)"
    _cost_val="${_cost_val#semantic_concentration=}"
    case "$_cost_val" in high|medium|low) COST_SEMANTIC_CONCENTRATION="$_cost_val" ;; esac

    _cost_val="$(grep -m1 '^task_role=' "$RESULT_FILE" 2>/dev/null || true)"
    _cost_val="${_cost_val#task_role=}"
    case "$_cost_val" in core-semantic|auxiliary|mixed|unknown) COST_TASK_ROLE="$_cost_val" ;; esac

    _cost_val="$(grep -m1 '^estimated_direct_work_units=' "$RESULT_FILE" 2>/dev/null || true)"
    _cost_val="${_cost_val#estimated_direct_work_units=}"
    case "$_cost_val" in
        ''|*[!0-9]*|0) COST_DIRECT_WORK_UNITS="not recorded" ;;
        *) COST_DIRECT_WORK_UNITS="$_cost_val" ;;
    esac

    _cost_val="$(grep -m1 '^estimated_delegated_work_units=' "$RESULT_FILE" 2>/dev/null || true)"
    _cost_val="${_cost_val#estimated_delegated_work_units=}"
    case "$_cost_val" in
        ''|*[!0-9]*|0) COST_DELEGATED_WORK_UNITS="not recorded" ;;
        *) COST_DELEGATED_WORK_UNITS="$_cost_val" ;;
    esac

    _cost_val="$(grep -m1 '^delegation_to_direct_ratio=' "$RESULT_FILE" 2>/dev/null || true)"
    _cost_val="${_cost_val#delegation_to_direct_ratio=}"
    if [[ "$_cost_val" =~ ^[0-9]+([.][0-9]+)?$ ]] && [[ ! "$_cost_val" =~ ^0+([.]0+)?$ ]]; then
        COST_RATIO="$_cost_val"
    fi

    _cost_val="$(grep -m1 '^economic_recommendation=' "$RESULT_FILE" 2>/dev/null || true)"
    _cost_val="${_cost_val#economic_recommendation=}"
    case "$_cost_val" in
        codex-fast-path|claude-builder) COST_ECONOMIC_RECOMMENDATION="$_cost_val" ;;
        *) COST_ECONOMIC_RECOMMENDATION="not recorded" ;;
    esac

    _cost_val="$(grep -m1 '^safety_eligible=' "$RESULT_FILE" 2>/dev/null || true)"
    _cost_val="${_cost_val#safety_eligible=}"
    case "$_cost_val" in
        yes|no) COST_SAFETY_ELIGIBLE="$_cost_val" ;;
        *) COST_SAFETY_ELIGIBLE="not recorded" ;;
    esac

    _cost_val="$(grep -m1 '^recommended_owner=' "$RESULT_FILE" 2>/dev/null || true)"
    _cost_val="${_cost_val#recommended_owner=}"
    case "$_cost_val" in
        codex-fast-path|claude-builder|spec-first|human-clarification) COST_RECOMMENDED_OWNER="$_cost_val" ;;
        *) COST_RECOMMENDED_OWNER="not recorded" ;;
    esac

    _cost_val="$(grep -m1 '^cost_confidence=' "$RESULT_FILE" 2>/dev/null || true)"
    _cost_val="${_cost_val#cost_confidence=}"
    if [ -z "$_cost_val" ]; then
        _cost_val="$(grep -m1 '^confidence=' "$RESULT_FILE" 2>/dev/null || true)"
        _cost_val="${_cost_val#confidence=}"
    fi
    case "$_cost_val" in
        high|medium|low) COST_CONFIDENCE="$_cost_val" ;;
        *) COST_CONFIDENCE="not recorded" ;;
    esac

    _cost_val="$(grep -m1 '^risk_flags=' "$RESULT_FILE" 2>/dev/null || true)"
    _cost_val="${_cost_val#risk_flags=}"
    if [ "$_cost_val" = "none" ]; then
        COST_RISK_FLAGS="none"
    elif [ -n "$_cost_val" ]; then
        _risk_valid="yes"
        IFS=',' read -r -a _risk_items <<< "$_cost_val"
        for _risk_item in "${_risk_items[@]}"; do
            case "$_risk_item" in
                public-api|data-model|security|migration|permission|concurrency|cross-module|broad-context|validation-complexity) ;;
                *) _risk_valid="no" ;;
            esac
        done
        if [ "$_risk_valid" = "yes" ]; then
            COST_RISK_FLAGS="$_cost_val"
        fi
    fi
fi

# Spark is intentionally optimistic on edit size. Calibrate its upper bound;
# orchestration/test/cross-platform work uses the larger historical margin.
if grep -Eiq 'test|fixture|shell|bash|process|orchestrat|cross-platform|windows|测试|夹具|脚本|进程|编排|跨平台' "$TASK_CARD_COPY" 2>/dev/null; then
    COST_CALIBRATION_MULTIPLIER="2.0"
fi
if [ "$COST_PREDICTED_DIFF_HIGH" != "not recorded" ]; then
    if [ "$COST_CALIBRATION_MULTIPLIER" = "2.0" ]; then
        COST_CALIBRATED_DIFF_HIGH=$((COST_PREDICTED_DIFF_HIGH * 2))
    else
        COST_CALIBRATED_DIFF_HIGH=$(((COST_PREDICTED_DIFF_HIGH * 15 + 9) / 10))
    fi
fi

# Compute owner eligibility deterministically; never trust the model's claim.
# The ordinary gate stays deliberately narrow.  A separate concentrated gate
# permits larger edits only when delegation would duplicate already-acquired
# context and Codex must perform a full semantic rereview anyway.
_safety_failures=()
if [ "$COST_PREDICTED_DIFF_LOW" = "not recorded" ] || [ "$COST_PREDICTED_DIFF_HIGH" = "not recorded" ]; then
    _safety_failures+=("missing-or-invalid-diff-estimate")
elif [ "$COST_PREDICTED_DIFF_LOW" -gt "$COST_PREDICTED_DIFF_HIGH" ]; then
    _safety_failures+=("diff-range-reversed")
fi
if [ "$COST_PREDICTED_FILES" = "not recorded" ] || [ "$COST_PREDICTED_FILES" = "unknown" ]; then
    _safety_failures+=("missing-or-unknown-file-count")
fi
for _required_cost_field in "$COST_DELEGATION_OVERHEAD" "$COST_CONTEXT_REACQUISITION" "$COST_CODEX_REREVIEW" "$COST_SOLUTION_CLARITY" "$COST_SEMANTIC_CONCENTRATION" "$COST_TASK_ROLE" "$COST_DIRECT_WORK_UNITS" "$COST_DELEGATED_WORK_UNITS" "$COST_RATIO" "$COST_ECONOMIC_RECOMMENDATION"; do
    [ "$_required_cost_field" != "not recorded" ] || _safety_failures+=("missing-required-cost-field")
done

_ordinary_gate="no"
if [ "$COST_CALIBRATED_DIFF_HIGH" != "not recorded" ] && \
   [ "$COST_CALIBRATED_DIFF_HIGH" -le "$FAST_PATH_MAX_DIFF_LINES" ] && \
   [ "$COST_PREDICTED_FILES" != "not recorded" ] && [ "$COST_PREDICTED_FILES" != "unknown" ] && \
   [ "$COST_PREDICTED_FILES" -le "$ORDINARY_FAST_PATH_MAX_FILES" ] && [ "$COST_CONTEXT_SCOPE" = "local" ]; then
    _ordinary_gate="yes"
    COST_FAST_PATH_CLASS="ordinary"
fi

_concentrated_gate="no"
if [ "$COST_CALIBRATED_DIFF_HIGH" != "not recorded" ] && \
   [ "$COST_CALIBRATED_DIFF_HIGH" -le "$CONCENTRATED_FAST_PATH_MAX_DIFF_LINES" ] && \
   [ "$COST_PREDICTED_FILES" != "not recorded" ] && [ "$COST_PREDICTED_FILES" != "unknown" ] && \
   [ "$COST_PREDICTED_FILES" -le "$CONCENTRATED_FAST_PATH_MAX_FILES" ] && \
   { [ "$COST_CONTEXT_SCOPE" = "local" ] || [ "$COST_CONTEXT_SCOPE" = "bounded" ]; } && \
   [ "$COST_CONTEXT_REACQUISITION" = "high" ] && \
   [ "$COST_CODEX_REREVIEW" = "full" ] && \
   [ "$COST_SOLUTION_CLARITY" = "high" ] && \
   [ "$COST_SEMANTIC_CONCENTRATION" = "high" ] && \
   [ "$COST_TASK_ROLE" = "core-semantic" ] && \
   [ "$COST_DELEGATED_WORK_UNITS" != "not recorded" ] && [ "$COST_DIRECT_WORK_UNITS" != "not recorded" ] && \
   [ $((COST_DELEGATED_WORK_UNITS * 2)) -ge $((COST_DIRECT_WORK_UNITS * 3)) ]; then
    _concentrated_gate="yes"
    COST_FAST_PATH_CLASS="concentrated-context-reuse"
fi

# Large repositories make delegation expensive when Codex already owns a
# precise semantic plan and must reread the entire implementation afterward.
# This gate is intentionally unavailable to auxiliary/mixed work and still
# requires a bounded calibrated estimate, explicit economic recommendation,
# and internally consistent total-work estimates. Medium confidence is
# accepted only here because all semantic work remains Codex-owned.
_full_rereview_economy_gate="no"
if [ "$_ordinary_gate" != "yes" ] && [ "$_concentrated_gate" != "yes" ] && \
   { [ "$REPOSITORY_ROUTING_SCALE" = "large" ] || [ "$REPOSITORY_ROUTING_SCALE" = "giant" ]; } && \
   [ "$COST_CALIBRATED_DIFF_HIGH" != "not recorded" ] && \
   [ "$COST_CALIBRATED_DIFF_HIGH" -le "$FULL_REREVIEW_FAST_PATH_MAX_DIFF_LINES" ] && \
   [ "$COST_PREDICTED_FILES" != "not recorded" ] && [ "$COST_PREDICTED_FILES" != "unknown" ] && \
   [ "$COST_PREDICTED_FILES" -le "$FULL_REREVIEW_FAST_PATH_MAX_FILES" ] && \
   { [ "$COST_CONTEXT_SCOPE" = "local" ] || [ "$COST_CONTEXT_SCOPE" = "bounded" ]; } && \
   { [ "$COST_CONTEXT_REACQUISITION" = "medium" ] || [ "$COST_CONTEXT_REACQUISITION" = "high" ]; } && \
   [ "$COST_CODEX_REREVIEW" = "full" ] && \
   [ "$COST_SOLUTION_CLARITY" = "high" ] && \
   [ "$COST_SEMANTIC_CONCENTRATION" = "high" ] && \
   [ "$COST_TASK_ROLE" = "core-semantic" ] && \
   { [ "$COST_CONFIDENCE" = "high" ] || [ "$COST_CONFIDENCE" = "medium" ]; } && \
   [ "$COST_ECONOMIC_RECOMMENDATION" = "codex-fast-path" ] && \
   [ "$COST_DELEGATED_WORK_UNITS" != "not recorded" ] && [ "$COST_DIRECT_WORK_UNITS" != "not recorded" ] && \
   [ "$COST_DELEGATED_WORK_UNITS" -ge "$COST_DIRECT_WORK_UNITS" ]; then
    _full_rereview_economy_gate="yes"
    COST_FAST_PATH_CLASS="large-full-rereview-economy"
fi

if [ "$COST_CONFIDENCE" = "low" ] || [ "$COST_CONFIDENCE" = "not recorded" ]; then
    _safety_failures+=("confidence-low-or-missing")
elif [ "$COST_CONFIDENCE" != "high" ] && [ "$_full_rereview_economy_gate" != "yes" ]; then
    _safety_failures+=("confidence-not-high")
fi

_large_auxiliary_bias="no"
if { [ "$REPOSITORY_ROUTING_SCALE" = "large" ] || [ "$REPOSITORY_ROUTING_SCALE" = "giant" ]; } && \
   [ "$COST_TASK_ROLE" = "auxiliary" ] && \
   { [ "$COST_CALIBRATED_DIFF_HIGH" = "not recorded" ] || [ "$COST_CALIBRATED_DIFF_HIGH" -gt 50 ] || [ "$COST_PREDICTED_FILES" = "unknown" ] || [ "$COST_PREDICTED_FILES" = "not recorded" ] || [ "$COST_PREDICTED_FILES" -gt 1 ]; }; then
    _ordinary_gate="no"
    _concentrated_gate="no"
    _full_rereview_economy_gate="no"
    COST_FAST_PATH_CLASS="none"
    _large_auxiliary_bias="yes"
fi

if [ "$_ordinary_gate" != "yes" ] && [ "$_concentrated_gate" != "yes" ] && [ "$_full_rereview_economy_gate" != "yes" ]; then
    if [ "$COST_CALIBRATED_DIFF_HIGH" != "not recorded" ] && [ "$COST_CALIBRATED_DIFF_HIGH" -gt "$FAST_PATH_MAX_DIFF_LINES" ]; then
        _safety_failures+=("calibrated-diff-high-exceeds-${FAST_PATH_MAX_DIFF_LINES}")
    fi
    if [ "$COST_PREDICTED_FILES" != "not recorded" ] && [ "$COST_PREDICTED_FILES" != "unknown" ] && [ "$COST_PREDICTED_FILES" -gt "$ORDINARY_FAST_PATH_MAX_FILES" ]; then
        _safety_failures+=("predicted-files-exceed-${ORDINARY_FAST_PATH_MAX_FILES}")
    fi
    [ "$_large_auxiliary_bias" = "yes" ] && _safety_failures+=("large-repo-auxiliary-prefers-claude")
    [ "$COST_CONTEXT_SCOPE" = "local" ] || _safety_failures+=("context-not-local")
    _safety_failures+=("no-fast-path-value-gate-passed")
fi

if [ "${#_safety_failures[@]}" -eq 0 ]; then
    COST_SAFETY_ELIGIBLE="yes"
    COST_SAFETY_REASONS="none"
else
    COST_SAFETY_ELIGIBLE="no"
    COST_SAFETY_REASONS="$(IFS=,; echo "${_safety_failures[*]}")"
fi

if [ "$COST_ECONOMIC_RECOMMENDATION" = "codex-fast-path" ] && [ "$COST_SAFETY_ELIGIBLE" = "yes" ]; then
    COST_RECOMMENDED_OWNER="codex-fast-path"
elif [ "$COST_RECOMMENDED_OWNER" = "codex-fast-path" ] || [ "$COST_RECOMMENDED_OWNER" = "not recorded" ]; then
    COST_RECOMMENDED_OWNER="claude-builder"
fi

# Detect schema-invalid estimator output for non-direct modes.
# For direct mode this is handled in the result emission section below.
_result_is_schema_invalid="no"
if [ "$CODEX_STATUS" -eq 0 ] && [ -s "$RESULT_FILE" ] && [ "$MODE" = "execution-cost-estimator" ] && [ "$RESULT_MODE" != "direct" ]; then
    if ! _estimator_schema_valid; then
        _result_is_schema_invalid="yes"
        HELPER_EXIT_STATUS=1
        echo "Codex Spark: estimator output is schema-invalid (missing or invalid required fields)" >&2
        if [ "$REQUIRE_SPARK" != "1" ]; then
            SPARK_AUTO_DISABLED="yes"
            SPARK_DISABLE_REASON="estimator output missing or invalid required fields"
            HELPER_EXIT_STATUS=0
        fi
    fi
fi

# Emit result based on result mode
case "$RESULT_MODE" in
    direct)
        # Direct mode: emit raw result to stdout, diagnostics to stderr
        if [ -s "$RESULT_FILE" ]; then
            emit_bounded_direct_result
            if { [ "$MODE" = "execution-cost-estimator" ] || [ "$MODE" = "task-size-classifier" ] || [ "$MODE" = "preflight-bundle" ]; } && _estimator_schema_valid; then
                echo "routing_event=${ROUTING_EVENT}"
                echo "estimate_calibration_multiplier=${COST_CALIBRATION_MULTIPLIER}"
                echo "calibrated_diff_lines_high=${COST_CALIBRATED_DIFF_HIGH}"
                echo "deterministic_owner=${COST_RECOMMENDED_OWNER}"
                echo "fast_path_class=${COST_FAST_PATH_CLASS}"
                echo "repository_scale_detected=${REPOSITORY_SCALE_DETECTED}"
                echo "repository_routing_scale=${REPOSITORY_ROUTING_SCALE}"
                echo "dynamic_ordinary_gate=${FAST_PATH_MAX_DIFF_LINES}/${ORDINARY_FAST_PATH_MAX_FILES}"
                echo "dynamic_concentrated_gate=${CONCENTRATED_FAST_PATH_MAX_DIFF_LINES}/${CONCENTRATED_FAST_PATH_MAX_FILES}"
                echo "dynamic_full_rereview_economy_gate=${FULL_REREVIEW_FAST_PATH_MAX_DIFF_LINES}/${FULL_REREVIEW_FAST_PATH_MAX_FILES}"
                echo "owner_ignores_risk_flags=yes"
                echo "risk_owner_override_direction=codex-only"
            fi
        fi
        # Classify failure for diagnostics
        _direct_result_empty="no"
        if [ ! -s "$RESULT_FILE" ]; then
            _direct_result_empty="yes"
        fi
        DIAGNOSTIC_FAILURE_CLASS="$(classify_failure "$CODEX_STATUS" "$_direct_result_empty")"
        # On failure, report reason to stderr only (no permanent report)
        if [ "$CODEX_STATUS" -ne 0 ]; then
            if [ "$SPARK_AUTO_DISABLED" = "yes" ]; then
                echo "Codex Spark auto-disabled: ${SPARK_DISABLE_REASON}" >&2
            else
                echo "Codex Spark exited with code ${CODEX_STATUS}. See stderr for details." >&2
            fi
        fi
        # Detect empty response as explicit failure (exit 0 + empty stdout)
        if [ "$CODEX_STATUS" -eq 0 ] && [ "$_direct_result_empty" = "yes" ]; then
            echo "Codex Spark: empty response (exit 0 with no usable output)" >&2
            HELPER_EXIT_STATUS=1
            if [ "$REQUIRE_SPARK" != "1" ]; then
                SPARK_AUTO_DISABLED="yes"
                SPARK_DISABLE_REASON="Spark returned an empty response"
                HELPER_EXIT_STATUS=0
            fi
        fi
        # Detect schema-invalid estimator output as explicit failure
        if [ "$DIAGNOSTIC_FAILURE_CLASS" = "schema-invalid" ]; then
            echo "Codex Spark: estimator output is schema-invalid (missing or invalid required fields)" >&2
            HELPER_EXIT_STATUS=1
            if [ "$REQUIRE_SPARK" != "1" ]; then
                SPARK_AUTO_DISABLED="yes"
                SPARK_DISABLE_REASON="estimator output missing or invalid required fields"
                HELPER_EXIT_STATUS=0
            fi
        fi
        # Write diagnostics based on mode
        if [ "$DIAGNOSTIC_FAILURE_CLASS" != "none" ]; then
            case "$DIAGNOSTICS_MODE" in
                failure)
                    write_compact_diagnostic "$CODEX_STATUS" "$_direct_result_empty" "$DIAGNOSTIC_FAILURE_CLASS"
                    ;;
                full)
                    write_full_diagnostic "$CODEX_STATUS"
                    ;;
                off)
                    # Strict zero-persistence
                    ;;
            esac
            if [ "$SPARK_AUTO_DISABLED" = "yes" ]; then
                echo "spark_status=unavailable"
            else
                echo "spark_status=failed"
            fi
            echo "spark_auto_disabled=${SPARK_AUTO_DISABLED}"
            echo "spark_disable_reason=${SPARK_DISABLE_REASON}"
            echo "spark_failure_class=${DIAGNOSTIC_FAILURE_CLASS}"
                echo "spark_model_response_received=${SPARK_MODEL_RESPONSE_RECEIVED}"
                echo "spark_protocol_end=aiwf-spark-stdout-v1"
            fi
        # Auto-disable reporting goes to stderr for direct mode
        if [ "$SPARK_AUTO_DISABLED" = "yes" ]; then
            if [ "$CODEX_STATUS" -eq 0 ]; then
                echo "Codex Spark auto-disabled: ${SPARK_DISABLE_REASON}" >&2
            fi
            echo "Codex Spark report: (direct mode, no permanent report)" >&2
            echo "Spark model response received: ${SPARK_MODEL_RESPONSE_RECEIVED}" >&2
            if [ -s "$STDERR_FILE" ]; then
                echo "Codex stderr excerpt (transient):" >&2
                tail -n 12 "$STDERR_FILE" >&2
            fi
        fi
        if [ "$DIAGNOSTIC_FAILURE_CLASS" = "none" ]; then
            echo "spark_status=success"
            echo "spark_auto_disabled=no"
            echo "spark_failure_class=none"
            echo "spark_model_response_received=${SPARK_MODEL_RESPONSE_RECEIVED}"
            echo "spark_protocol_end=aiwf-spark-stdout-v1"
        fi
        exit "$HELPER_EXIT_STATUS"
        ;;
    minimal)
        # Minimal mode: emit raw result to stdout, write compact report (no transient paths)
        if [ -s "$RESULT_FILE" ]; then
            cat "$RESULT_FILE"
        fi
        write_report_header "$CODEX_STATUS" "$DIFF_VALUE"
        {
            echo "## Result"
            echo ""
            echo "| Field | Value |"
            echo "|-------|-------|"
            echo "| Codex exit code | ${CODEX_STATUS} |"
            echo "| Result mode | ${RESULT_MODE} |"
            echo "| Strong-model fallback used | no |"
            echo "| Predicted diff lines (low) | ${COST_PREDICTED_DIFF_LOW} |"
            echo "| Predicted diff lines (high) | ${COST_PREDICTED_DIFF_HIGH} |"
            echo "| Estimate calibration multiplier | ${COST_CALIBRATION_MULTIPLIER} |"
            echo "| Calibrated diff lines (high) | ${COST_CALIBRATED_DIFF_HIGH} |"
            echo "| Routing event | ${ROUTING_EVENT} |"
            echo "| Predicted files | ${COST_PREDICTED_FILES} |"
            echo "| Context scope | ${COST_CONTEXT_SCOPE} |"
            echo "| Validation complexity | ${COST_VALIDATION_COMPLEXITY} |"
            echo "| Delegation overhead | ${COST_DELEGATION_OVERHEAD} |"
            echo "| Context reacquisition cost | ${COST_CONTEXT_REACQUISITION} |"
            echo "| Codex semantic rereview | ${COST_CODEX_REREVIEW} |"
            echo "| Solution clarity | ${COST_SOLUTION_CLARITY} |"
            echo "| Semantic concentration | ${COST_SEMANTIC_CONCENTRATION} |"
            echo "| Task role | ${COST_TASK_ROLE} |"
            echo "| Repository scale detected | ${REPOSITORY_SCALE_DETECTED} |"
            echo "| Repository routing scale | ${REPOSITORY_ROUTING_SCALE} |"
            echo "| Repository tracked/source files | ${REPOSITORY_TRACKED_FILES}/${REPOSITORY_SOURCE_FILES} |"
            echo "| Historical worktree cost | ${REPOSITORY_WORKTREE_COST}; median=${REPOSITORY_WORKTREE_MEDIAN_SECONDS}s; samples=${REPOSITORY_WORKTREE_SAMPLES}; promoted=${REPOSITORY_IO_PROMOTED} |"
            echo "| Dynamic ordinary gate | ${FAST_PATH_MAX_DIFF_LINES} lines / ${ORDINARY_FAST_PATH_MAX_FILES} files |"
            echo "| Dynamic concentrated gate | ${CONCENTRATED_FAST_PATH_MAX_DIFF_LINES} lines / ${CONCENTRATED_FAST_PATH_MAX_FILES} files |"
            echo "| Dynamic full-rereview economy gate | ${FULL_REREVIEW_FAST_PATH_MAX_DIFF_LINES} lines / ${FULL_REREVIEW_FAST_PATH_MAX_FILES} files |"
            echo "| Direct work units | ${COST_DIRECT_WORK_UNITS} |"
            echo "| Delegated work units | ${COST_DELEGATED_WORK_UNITS} |"
            echo "| Delegation-to-direct ratio | ${COST_RATIO} |"
            echo "| Economic recommendation | ${COST_ECONOMIC_RECOMMENDATION} |"
            echo "| Safety eligible | ${COST_SAFETY_ELIGIBLE} |"
            echo "| Safety gate reasons | ${COST_SAFETY_REASONS} |"
            echo "| Recommended owner | ${COST_RECOMMENDED_OWNER} |"
            echo "| Fast-path class | ${COST_FAST_PATH_CLASS} |"
            echo "| Cost confidence | ${COST_CONFIDENCE} |"
            echo "| Risk flags | ${COST_RISK_FLAGS} |"
            echo "| Risk affects owner | no; review/validation rigor only |"
            echo "| Explicit risk owner override | codex-only |"
            echo "| Codex fast path approved? | pending Codex review |"
            if [ -n "$CONTROLLED_BOUNDARY_FAILURE" ]; then
                echo "| Boundary outcome | fail: ${CONTROLLED_BOUNDARY_FAILURE} |"
            elif [ "$MODE" = "controlled-builder" ]; then
                echo "| Boundary outcome | pass |"
            fi
            if [ "$CODEX_STATUS" -ne 0 ] || [ "$_result_is_schema_invalid" = "yes" ]; then
                echo ""
                echo "## Failure Handling"
                echo ""
                if [ "$_result_is_schema_invalid" = "yes" ]; then
                    echo "Estimator output is schema-invalid: one or more required machine-readable fields are missing or have invalid values."
                    if [ "$SPARK_AUTO_DISABLED" = "yes" ]; then
                        echo "Spark was auto-disabled because it is auxiliary. The helper exits 0 so the main workflow may continue."
                    else
                        echo "With --require-spark, schema-invalid output is a hard failure."
                    fi
                elif [ "$SPARK_AUTO_DISABLED" = "yes" ]; then
                    echo "Spark exited non-zero with an availability-style failure and was auto-disabled because it is auxiliary. The helper exits 0 so the main workflow may continue."
                else
                    echo "Spark exited non-zero. Strong-model fallback was not used. Re-run explicitly with another model only after human approval."
                fi
            fi
        } >> "$REPORT_FILE"
        echo "Codex Spark report: $REPORT_FILE" >&2
        exit "$HELPER_EXIT_STATUS"
        ;;
    full)
        # Full mode: current behavior with all artifacts
        write_report_header "$CODEX_STATUS" "$DIFF_VALUE"
        {
            echo "## Result"
            echo ""
            echo "| Field | Value |"
            echo "|-------|-------|"
            echo "| Codex exit code | ${CODEX_STATUS} |"
            echo "| Result mode | ${RESULT_MODE} |"
            echo "| Prompt | ${PROMPT_FILE} |"
            echo "| Raw output | ${RESULT_FILE} |"
            echo "| Stderr log | ${STDERR_FILE} |"
            echo "| Worktree status | ${STATUS_FILE} |"
            echo "| Diff | ${DIFF_FILE} |"
            echo "| Diffstat | ${DIFFSTAT_FILE} |"
            echo "| Strong-model fallback used | no |"
            echo "| Predicted diff lines (low) | ${COST_PREDICTED_DIFF_LOW} |"
            echo "| Predicted diff lines (high) | ${COST_PREDICTED_DIFF_HIGH} |"
            echo "| Estimate calibration multiplier | ${COST_CALIBRATION_MULTIPLIER} |"
            echo "| Calibrated diff lines (high) | ${COST_CALIBRATED_DIFF_HIGH} |"
            echo "| Routing event | ${ROUTING_EVENT} |"
            echo "| Predicted files | ${COST_PREDICTED_FILES} |"
            echo "| Context scope | ${COST_CONTEXT_SCOPE} |"
            echo "| Validation complexity | ${COST_VALIDATION_COMPLEXITY} |"
            echo "| Delegation overhead | ${COST_DELEGATION_OVERHEAD} |"
            echo "| Context reacquisition cost | ${COST_CONTEXT_REACQUISITION} |"
            echo "| Codex semantic rereview | ${COST_CODEX_REREVIEW} |"
            echo "| Solution clarity | ${COST_SOLUTION_CLARITY} |"
            echo "| Semantic concentration | ${COST_SEMANTIC_CONCENTRATION} |"
            echo "| Task role | ${COST_TASK_ROLE} |"
            echo "| Repository scale detected | ${REPOSITORY_SCALE_DETECTED} |"
            echo "| Repository routing scale | ${REPOSITORY_ROUTING_SCALE} |"
            echo "| Repository tracked/source files | ${REPOSITORY_TRACKED_FILES}/${REPOSITORY_SOURCE_FILES} |"
            echo "| Historical worktree cost | ${REPOSITORY_WORKTREE_COST}; median=${REPOSITORY_WORKTREE_MEDIAN_SECONDS}s; samples=${REPOSITORY_WORKTREE_SAMPLES}; promoted=${REPOSITORY_IO_PROMOTED} |"
            echo "| Dynamic ordinary gate | ${FAST_PATH_MAX_DIFF_LINES} lines / ${ORDINARY_FAST_PATH_MAX_FILES} files |"
            echo "| Dynamic concentrated gate | ${CONCENTRATED_FAST_PATH_MAX_DIFF_LINES} lines / ${CONCENTRATED_FAST_PATH_MAX_FILES} files |"
            echo "| Dynamic full-rereview economy gate | ${FULL_REREVIEW_FAST_PATH_MAX_DIFF_LINES} lines / ${FULL_REREVIEW_FAST_PATH_MAX_FILES} files |"
            echo "| Direct work units | ${COST_DIRECT_WORK_UNITS} |"
            echo "| Delegated work units | ${COST_DELEGATED_WORK_UNITS} |"
            echo "| Delegation-to-direct ratio | ${COST_RATIO} |"
            echo "| Economic recommendation | ${COST_ECONOMIC_RECOMMENDATION} |"
            echo "| Safety eligible | ${COST_SAFETY_ELIGIBLE} |"
            echo "| Safety gate reasons | ${COST_SAFETY_REASONS} |"
            echo "| Recommended owner | ${COST_RECOMMENDED_OWNER} |"
            echo "| Fast-path class | ${COST_FAST_PATH_CLASS} |"
            echo "| Cost confidence | ${COST_CONFIDENCE} |"
            echo "| Risk flags | ${COST_RISK_FLAGS} |"
            echo "| Risk affects owner | no; review/validation rigor only |"
            echo "| Explicit risk owner override | codex-only |"
            echo "| Codex fast path approved? | pending Codex review |"
            if [ -n "$CONTROLLED_BOUNDARY_FAILURE" ]; then
                echo "| Boundary outcome | fail: ${CONTROLLED_BOUNDARY_FAILURE} |"
            elif [ "$MODE" = "controlled-builder" ]; then
                echo "| Boundary outcome | pass |"
            fi
            echo ""
            echo "## Codex Spark Output"
            echo ""
            if [ -s "$RESULT_FILE" ]; then
                cat "$RESULT_FILE"
            else
                echo "No stdout output captured."
            fi
            if [ "$CODEX_STATUS" -ne 0 ] || [ "$_result_is_schema_invalid" = "yes" ]; then
                echo ""
                echo "## Failure Handling"
                echo ""
                if [ "$_result_is_schema_invalid" = "yes" ]; then
                    echo "Estimator output is schema-invalid: one or more required machine-readable fields are missing or have invalid values."
                    if [ "$SPARK_AUTO_DISABLED" = "yes" ]; then
                        echo "Spark was auto-disabled because it is auxiliary. The helper exits 0 so the main workflow may continue."
                    else
                        echo "With --require-spark, schema-invalid output is a hard failure."
                    fi
                elif [ "$SPARK_AUTO_DISABLED" = "yes" ]; then
                    echo "Spark exited non-zero with an availability-style failure and was auto-disabled because it is auxiliary. The helper exits 0 so the main workflow may continue."
                else
                    echo "Spark exited non-zero. Strong-model fallback was not used. Re-run explicitly with another model only after human approval."
                fi
            fi
        } >> "$REPORT_FILE"
        echo "Codex Spark report: $REPORT_FILE"
        exit "$HELPER_EXIT_STATUS"
        ;;
esac
