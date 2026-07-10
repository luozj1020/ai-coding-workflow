#!/usr/bin/env bash
# run-codex-spark.sh  -  Optional Codex Spark auxiliary execution for the workflow.
#
# Usage:
#   bash ai/run-codex-spark.sh <task-card> [--mode auto|task-size-classifier|review-only|task-card-audit|plan-splitter|validation-planner|failure-triage|evidence-checker|micro-builder|parallel-planner|observe-synthesizer|task-card-drafter|context-packet-builder|preflight-bundle|direction-precheck|acceptance-matrix|postflight-bundle|revision-drafter|lesson-extractor]
#       [--model gpt-5.3-codex-spark] [--sandbox read-only|workspace-write]
#       [--budget-mode aggressive|balanced|conservative]
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

Options:
  --mode MODE       auto, task-size-classifier, review-only, task-card-audit,
                    plan-splitter, validation-planner, failure-triage,
                    evidence-checker, micro-builder, controlled-builder,
                    parallel-planner, observe-synthesizer, task-card-drafter,
                    context-packet-builder, preflight-bundle, direction-precheck,
                    acceptance-matrix, postflight-bundle, revision-drafter,
                    or lesson-extractor
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
  --allow-dirty-source
                    Allow micro-builder dispatch from a dirty source repo
  --require-spark   Treat Spark unavailability as a hard helper failure
  -h, --help        Show this help

Environment:
  CODEX_SPARK_CODEX_BIN
  CODEX_SPARK_MODEL
  CODEX_SPARK_MODE
  CODEX_SPARK_SANDBOX
  CODEX_SPARK_OUTPUT_DIR
  CODEX_SPARK_RESULT_MODE=direct|minimal|full
  CODEX_SPARK_ARTIFACT_LINES=160
  CODEX_SPARK_ALLOW_DIRTY_SOURCE=1
  CODEX_SPARK_REQUIRED=1
  AI_SPARK_BUDGET_MODE=aggressive|balanced|conservative
EOF
}

TASK_CARD=""
CODEX_BIN="${CODEX_SPARK_CODEX_BIN:-codex}"
MODE="${CODEX_SPARK_MODE:-auto}"
REQUESTED_MODE="$MODE"
MODEL="${CODEX_SPARK_MODEL:-gpt-5.3-codex-spark}"
SANDBOX="${CODEX_SPARK_SANDBOX:-read-only}"
OUTPUT_DIR="${CODEX_SPARK_OUTPUT_DIR:-}"
ARTIFACT_LINES="${CODEX_SPARK_ARTIFACT_LINES:-160}"
ALLOW_DIRTY_SOURCE="${CODEX_SPARK_ALLOW_DIRTY_SOURCE:-0}"
REQUIRE_SPARK="${CODEX_SPARK_REQUIRED:-0}"
BUDGET_MODE="${AI_SPARK_BUDGET_MODE:-balanced}"
REQUESTED_BUDGET_MODE="$BUDGET_MODE"
SPARK_INVOKED="yes"
SPARK_AUTO_DISABLED="no"
SPARK_DISABLE_REASON="not applicable"
SPARK_CHECKS_RUN="codex exec"
HELPER_EXIT_STATUS=0
SPARK_PIPELINE_STAGE=""
SPARK_ROLES_EXECUTED=""
SPARK_CALLS_USED=0
SPARK_PROVISIONAL_ACCEPTANCE="not applicable"
ARTIFACTS=()
RESULT_MODE="${CODEX_SPARK_RESULT_MODE:-}"
EXPLICIT_RESULT_MODE="no"
ALLOWED_WRITES=()
MAX_DIFF_LINES=""
EXPLICIT_OUTPUT="no"

while [ $# -gt 0 ]; do
    case "$1" in
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
        --output)
            [ $# -ge 2 ] || { echo "Error: --output requires a value." >&2; exit 1; }
            OUTPUT_DIR="$2"
            EXPLICIT_OUTPUT="yes"
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

if [ -z "$TASK_CARD" ]; then
    usage
    exit 1
fi

case "$MODE" in
    auto|task-size-classifier|review-only|task-card-audit|plan-splitter|validation-planner|failure-triage|evidence-checker|micro-builder|controlled-builder|parallel-planner|observe-synthesizer|task-card-drafter|context-packet-builder|preflight-bundle|direction-precheck|acceptance-matrix|postflight-bundle|revision-drafter|lesson-extractor) ;;
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

if [ ! -f "$TASK_CARD" ]; then
    echo "Error: task card not found: $TASK_CARD" >&2
    exit 1
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

REPO_ROOT="$(git -C "$(dirname "$TASK_CARD")" rev-parse --show-toplevel 2>/dev/null || git rev-parse --show-toplevel 2>/dev/null || pwd)"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

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
        observe-synthesizer|task-card-drafter|context-packet-builder|preflight-bundle|direction-precheck|acceptance-matrix|postflight-bundle|revision-drafter|lesson-extractor)
            return 0 ;;
        *)
            return 1 ;;
    esac
}

is_checker_task() {
    grep -Eiq 'checker-test|Validation Contract|Local validation allowed|Test-First / TDD|TDD mode' "$TASK_CARD"
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
        task-size-classifier|plan-splitter)
            SPARK_PIPELINE_STAGE="planning" ;;
        validation-planner)
            SPARK_PIPELINE_STAGE="validation" ;;
        micro-builder|controlled-builder)
            SPARK_PIPELINE_STAGE="builder" ;;
        lesson-extractor)
            SPARK_PIPELINE_STAGE="learning" ;;
        review-only|task-card-audit|evidence-checker|parallel-planner)
            SPARK_PIPELINE_STAGE="standalone" ;;
    esac
}

resolve_roles_executed() {
    case "$MODE" in
        preflight-bundle)
            SPARK_ROLES_EXECUTED="risk-classifier,evidence-synthesizer,task-card-drafter,context-packet-builder,unknown-extractor,split-advisor" ;;
        postflight-bundle)
            SPARK_ROLES_EXECUTED="direction-checker,boundary-checker,acceptance-mapper,evidence-conflict-detector,validation-advisor,acceptance-advisor" ;;
        failure-triage)
            if [ "$BUDGET_MODE" = "aggressive" ]; then
                SPARK_ROLES_EXECUTED="failure-triage,revision-drafter"
            else
                SPARK_ROLES_EXECUTED="$MODE"
            fi ;;
        observe-synthesizer|task-card-drafter|context-packet-builder|direction-precheck|acceptance-matrix|revision-drafter|lesson-extractor)
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

WORKTREE_DIR=""
RUN_DIR="$REPO_ROOT"
CODEX_STATUS=0

cp "$TASK_CARD" "$TASK_CARD_COPY"
: > "$ARTIFACT_MANIFEST"
for artifact in "${ARTIFACTS[@]}"; do
    printf '%s\n' "$artifact" >> "$ARTIFACT_MANIFEST"
done

# Read-only synthesis/bundle modes run from artifact dir with workspace-write
# when the requested sandbox is read-only.  In direct/minimal modes the temp
# dir serves as the writable cwd; in full mode OUTPUT_DIR is used.
if is_read_only_synthesis_mode && [ "$SANDBOX" = "read-only" ]; then
    SANDBOX="workspace-write"
    if [ "$RESULT_MODE" = "direct" ] || [ "$RESULT_MODE" = "minimal" ]; then
        RUN_DIR="$TEMP_WORK_DIR"
    else
        RUN_DIR="$OUTPUT_DIR"
    fi
    SPARK_CHECKS_RUN="codex exec (${MODE} in artifact dir)"
fi

if [ "$MODE" = "task-size-classifier" ] && [ "$SANDBOX" = "read-only" ]; then
    SANDBOX="workspace-write"
    if [ "$RESULT_MODE" = "direct" ] || [ "$RESULT_MODE" = "minimal" ]; then
        RUN_DIR="$TEMP_WORK_DIR"
    else
        RUN_DIR="$OUTPUT_DIR"
    fi
    SPARK_CHECKS_RUN="codex exec (classifier in artifact dir)"
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
    if printf '%s' "$path" | LC_ALL=C grep -q '[\x00-\x1f\x7f]' 2>/dev/null; then
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

controlled_builder_contract_missing() {
    if ! grep -Eiq 'controlled-builder' "$TASK_CARD_COPY"; then
        echo "controlled-builder mode is not explicitly authorized in the task card"
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
    # Require Existing pattern or Source-of-truth reference with a non-empty value
    if ! grep -Eiq 'Existing pattern|Source-of-truth reference' "$TASK_CARD_COPY"; then
        echo "task card does not provide an existing pattern or source-of-truth reference"
        return 0
    fi
    local _ref_line
    _ref_line="$(grep -Ei 'Existing pattern|Source-of-truth reference' "$TASK_CARD_COPY" | head -1)"
    local _ref_value
    _ref_value="$(printf '%s' "$_ref_line" | sed 's/.*|//' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    case "$_ref_value" in
        ''|none|None|NONE|n/a|N/A|'-')
            echo "task card existing-pattern or source-of-truth reference field is empty or none"
            return 0
            ;;
    esac
    # Require Controlled-builder allowed paths row
    if ! grep -Eiq 'Controlled-builder allowed paths' "$TASK_CARD_COPY"; then
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
        echo "| Spark purpose used | ${MODE} |"
        echo "| Spark requested mode | ${REQUESTED_MODE} |"
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
    echo "Codex Spark report: $REPORT_FILE"
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
    if printf '%s\n' "$text" | grep -Eiq 'read-only file system|os error 30|app-server|failed to initialize'; then
        echo "codex exec failed during local app-server/helper initialization that requires write access"
    else
        echo "codex exec reported model, quota, auth, network, or access unavailability"
    fi
}

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
    SOURCE_STATUS="$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all)"
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
    cp "$TASK_CARD_COPY" "${WORKTREE_DIR}/CODEX_SPARK_TASK_CARD.md"
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
    TC_ALLOWED_LINE="$(grep -Ei 'Controlled-builder allowed paths' "$TASK_CARD_COPY" | head -1)"
    TC_ALLOWED_RAW="${TC_ALLOWED_LINE#*|}"
    TC_ALLOWED_PATHS=()
    IFS=',' read -ra _tc_parts <<< "$TC_ALLOWED_RAW"
    for _tp in "${_tc_parts[@]}"; do
        _tp="$(printf '%s' "$_tp" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
        [ -n "$_tp" ] && TC_ALLOWED_PATHS+=("$_tp")
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

    SOURCE_STATUS="$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all)"
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
    cp "$TASK_CARD_COPY" "${WORKTREE_DIR}/CODEX_SPARK_TASK_CARD.md"
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

Operating rules:
- Use the requested Spark model only. Do not silently fall back to a stronger model.
- Keep output compressed: decisions, evidence, changed files if any, checks run, and risks.
- Treat Claude-owned implementation as Claude-owned unless this task card explicitly authorizes Spark micro-builder work.
- Do not claim completion from prose alone. Cite artifacts, commands, or diffs.
- If blocked by missing context, permissions, model access, network, or auth, report the blocker instead of guessing.
- Spark output is advisory input to Codex review. It cannot replace Claude Builder ownership, cannot approve final review, and cannot by itself satisfy acceptance criteria.
- End your output with these fields exactly: accepted_suggestions=<none or comma-separated>; ignored_suggestions=<none or comma-separated>; conflicts_with_claude=<none or short note>; conflicts_with_local_evidence=<none or short note>; acceptance_satisfied_by_spark=no.

Mode contract:
- task-size-classifier: classify task size and routing risk using cheap Spark quota before Codex spends stronger-model context; do not edit files. Output exactly these fields: size=tiny|small|medium|large|unknown; recommended_route=codex-fast-path|spark-review-only|spark-micro-builder|claude-builder|checker-test|spec-first|human-clarification; confidence=high|medium|low; expected_files=1-2|3-5|>5|unknown; risk_flags=none or comma-separated public-api,data-model,security,migration,permission,concurrency,cross-module,broad-context,validation-complexity; reason=one short paragraph; stop_condition=one sentence.
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
- preflight-bundle: combined preflight analysis in one invocation. Perform risk classification, bounded evidence synthesis, task-card drafting, Context Packet drafting, unknown/risk extraction, and split/parallel recommendation. Do not edit files. Output MUST contain exactly these headings in this order: Decision Summary, Risk Flags, Scope and Boundaries, Acceptance Matrix, Evidence Conflicts, Required Codex Decisions, Recommended Next Action.
- direction-precheck: check direction and boundary against the task card and provided artifacts. Do not edit files. Output direction/boundary assessment with specific risks.
- acceptance-matrix: produce an acceptance criteria matrix from the task card. Do not edit files. Map each acceptance criterion to verification method, evidence source, and pass/fail status.
- postflight-bundle: combined postflight analysis in one invocation. Perform direction/boundary/omission checks, acceptance mapping, evidence conflict detection, validation recommendations, and provisional accept/revise/split/escalate recommendation. Do not edit files. Output MUST contain exactly these headings in this order: Decision Summary, Risk Flags, Scope and Boundaries, Acceptance Matrix, Evidence Conflicts, Required Codex Decisions, Recommended Next Action.
- revision-drafter: draft a bounded revision task card from failure triage results or postflight findings. Do not edit source files. Output a structured revision task card proposal.
- lesson-extractor: extract reusable lessons from provided artifacts. Do not edit files. Output structured lessons with evidence citations.
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

## Task Card

TASK_EOF
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

set +e
(
    cd "$RUN_DIR"
    run_codex exec --model "$MODEL" --sandbox "$SANDBOX" - < "$PROMPT_FILE" > "$RESULT_FILE" 2> "$STDERR_FILE"
)
CODEX_STATUS=$?
set -e
HELPER_EXIT_STATUS="$CODEX_STATUS"
SPARK_CALLS_USED=1

if [ "$MODE" = "micro-builder" ] || [ "$MODE" = "controlled-builder" ]; then
    git -C "$RUN_DIR" status --porcelain --untracked-files=all > "$STATUS_FILE" || true
    git -C "$RUN_DIR" diff --binary > "$DIFF_FILE" || true
    git -C "$RUN_DIR" diff --stat > "$DIFFSTAT_FILE" || true
else
    git -C "$REPO_ROOT" status --porcelain --untracked-files=all > "$STATUS_FILE" || true
    : > "$DIFF_FILE"
    : > "$DIFFSTAT_FILE"
fi

# Controlled-builder boundary validation (hardened: NUL-safe, binary-aware)
CONTROLLED_BOUNDARY_FAILURE=""
if [ "$MODE" = "controlled-builder" ] && [ "$CODEX_STATUS" -eq 0 ]; then
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
        TOTAL_ADD_DEL=0

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

        # Count untracked files; reject binary content
        if [ -z "$CONTROLLED_BOUNDARY_FAILURE" ] && [ "${#UNTRACKED_PATHS[@]}" -gt 0 ]; then
            for _up in "${UNTRACKED_PATHS[@]}"; do
                if [ -f "${RUN_DIR}/${_up}" ]; then
                    # Reject binary: compare byte counts with and without NUL
                    _file_bytes=$(wc -c < "${RUN_DIR}/${_up}" 2>/dev/null || echo 0)
                    _text_bytes=$(tr -d '\0' < "${RUN_DIR}/${_up}" 2>/dev/null | wc -c || echo 0)
                    if [ "${_file_bytes:-0}" -ne "${_text_bytes:-0}" ]; then
                        CONTROLLED_BOUNDARY_FAILURE="binary content detected in untracked file: ${_up}"
                        break
                    fi
                    _lines=$(wc -l < "${RUN_DIR}/${_up}" 2>/dev/null || echo 0)
                    TOTAL_ADD_DEL=$((TOTAL_ADD_DEL + _lines))
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

# Emit result based on result mode
case "$RESULT_MODE" in
    direct)
        # Direct mode: emit raw result to stdout, diagnostics to stderr
        if [ -s "$RESULT_FILE" ]; then
            cat "$RESULT_FILE"
        fi
        # On failure, report reason to stderr only (no permanent report)
        if [ "$CODEX_STATUS" -ne 0 ]; then
            if [ "$SPARK_AUTO_DISABLED" = "yes" ]; then
                echo "Codex Spark auto-disabled: ${SPARK_DISABLE_REASON}" >&2
            else
                echo "Codex Spark exited with code ${CODEX_STATUS}. See stderr for details." >&2
            fi
        fi
        # Auto-disable reporting goes to stderr for direct mode
        if [ "$SPARK_AUTO_DISABLED" = "yes" ]; then
            echo "Codex Spark report: (direct mode, no permanent report)" >&2
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
            if [ -n "$CONTROLLED_BOUNDARY_FAILURE" ]; then
                echo "| Boundary outcome | fail: ${CONTROLLED_BOUNDARY_FAILURE} |"
            elif [ "$MODE" = "controlled-builder" ]; then
                echo "| Boundary outcome | pass |"
            fi
            if [ "$CODEX_STATUS" -ne 0 ]; then
                echo ""
                echo "## Failure Handling"
                echo ""
                if [ "$SPARK_AUTO_DISABLED" = "yes" ]; then
                    echo "Spark exited non-zero with an availability-style failure and was auto-disabled because it is auxiliary. The helper exits 0 so the main workflow may continue."
                else
                    echo "Spark exited non-zero. Strong-model fallback was not used. Re-run explicitly with another model only after human approval."
                fi
            fi
        } >> "$REPORT_FILE"
        echo "Codex Spark report: $REPORT_FILE"
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
            if [ "$CODEX_STATUS" -ne 0 ]; then
                echo ""
                echo "## Failure Handling"
                echo ""
                if [ "$SPARK_AUTO_DISABLED" = "yes" ]; then
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
