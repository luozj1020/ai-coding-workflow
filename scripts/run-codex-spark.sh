#!/usr/bin/env bash
# run-codex-spark.sh  -  Optional Codex Spark auxiliary execution for the workflow.
#
# Usage:
#   bash ai/run-codex-spark.sh <task-card> [--mode review-only|evidence-checker|micro-builder]
#       [--model gpt-5.3-codex-spark] [--sandbox read-only|workspace-write]
#       [--output .worktrees/codex-spark-...]
#
# Defaults are intentionally conservative: review-only, read-only, optional Spark,
# and no strong-model fallback.

set -euo pipefail

PATH="/usr/bin:/bin:/mingw64/bin:${PATH}"
export PATH

usage() {
    cat >&2 <<'EOF'
Usage: run-codex-spark.sh <task-card> [options]

Options:
  --mode MODE       review-only, evidence-checker, or micro-builder
  --model MODEL     Codex model slug (default: gpt-5.3-codex-spark)
  --sandbox MODE    read-only or workspace-write (default: read-only)
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
  CODEX_SPARK_ALLOW_DIRTY_SOURCE=1
  CODEX_SPARK_REQUIRED=1
EOF
}

TASK_CARD=""
CODEX_BIN="${CODEX_SPARK_CODEX_BIN:-codex}"
MODE="${CODEX_SPARK_MODE:-review-only}"
MODEL="${CODEX_SPARK_MODEL:-gpt-5.3-codex-spark}"
SANDBOX="${CODEX_SPARK_SANDBOX:-read-only}"
OUTPUT_DIR="${CODEX_SPARK_OUTPUT_DIR:-}"
ALLOW_DIRTY_SOURCE="${CODEX_SPARK_ALLOW_DIRTY_SOURCE:-0}"
REQUIRE_SPARK="${CODEX_SPARK_REQUIRED:-0}"
SPARK_INVOKED="yes"
SPARK_AUTO_DISABLED="no"
SPARK_DISABLE_REASON="not applicable"
SPARK_CHECKS_RUN="codex exec"
HELPER_EXIT_STATUS=0

while [ $# -gt 0 ]; do
    case "$1" in
        --mode)
            [ $# -ge 2 ] || { echo "Error: --mode requires a value." >&2; exit 1; }
            MODE="$2"
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
        --output)
            [ $# -ge 2 ] || { echo "Error: --output requires a value." >&2; exit 1; }
            OUTPUT_DIR="$2"
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
    review-only|evidence-checker|micro-builder) ;;
    *)
        echo "Error: invalid --mode: $MODE" >&2
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

if [ "$MODE" = "micro-builder" ] && [ "$SANDBOX" != "workspace-write" ]; then
    echo "Error: micro-builder mode requires --sandbox workspace-write." >&2
    exit 1
fi

if [ ! -f "$TASK_CARD" ]; then
    echo "Error: task card not found: $TASK_CARD" >&2
    exit 1
fi

if ! command -v git >/dev/null 2>&1; then
    echo "Error: git is not installed or not in PATH." >&2
    exit 1
fi

REPO_ROOT="$(git -C "$(dirname "$TASK_CARD")" rev-parse --show-toplevel 2>/dev/null || git rev-parse --show-toplevel 2>/dev/null || pwd)"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

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
WORKTREE_DIR=""
RUN_DIR="$REPO_ROOT"
CODEX_STATUS=0

cp "$TASK_CARD" "$TASK_CARD_COPY"

write_report_header() {
    local exit_value="${1:-pending}"
    local diff_value="${2:-pending}"
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
        echo "| Spark model used | ${MODEL} |"
        echo "| Invocation command or artifact | ${PROMPT_FILE} |"
        echo "| Sandbox used | ${SANDBOX} |"
        echo "| Isolated worktree used? | $([ -n "$WORKTREE_DIR" ] && echo yes || echo no) |"
        echo "| Source diff produced? | ${diff_value} |"
        echo "| Spark checks run | ${SPARK_CHECKS_RUN} |"
        echo "| Spark exit code | ${exit_value} |"
        echo "| Spark auto-disabled? | ${SPARK_AUTO_DISABLED} |"
        echo "| Auto-disable reason | ${SPARK_DISABLE_REASON} |"
        echo "| Helper exit behavior | $([ "$REQUIRE_SPARK" = "1" ] && echo require-spark || echo optional-spark) |"
        echo "| Strong-model fallback used | no |"
        echo "| Spark result accepted by Codex? | pending review |"
        echo "| Conflict with Claude or local evidence? | pending review |"
        echo "| Remaining Spark-related risk | pending review |"
        echo "| Artifact directory | ${OUTPUT_DIR} |"
        echo "| Task card copy | ${TASK_CARD_COPY} |"
        if [ -n "$WORKTREE_DIR" ]; then
            echo "| Worktree | ${WORKTREE_DIR} |"
        fi
        echo ""
    } > "$REPORT_FILE"
}

write_report_header

auto_disable_spark() {
    local reason="$1"
    local codex_exit="${2:-not-run}"
    SPARK_AUTO_DISABLED="yes"
    SPARK_DISABLE_REASON="$reason"
    SPARK_CHECKS_RUN="not run"
    HELPER_EXIT_STATUS=0
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
        echo "codex exec failed during read-only sandbox helper initialization"
    else
        echo "codex exec reported model, quota, auth, network, or access unavailability"
    fi
}

if ! command -v "$CODEX_BIN" >/dev/null 2>&1; then
    SPARK_INVOKED="no"
    SPARK_CHECKS_RUN="not run"
    if [ "$REQUIRE_SPARK" = "1" ]; then
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

if [ "$MODE" = "micro-builder" ]; then
    SOURCE_STATUS="$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all)"
    if [ -n "$SOURCE_STATUS" ] && [ "$ALLOW_DIRTY_SOURCE" != "1" ]; then
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

cat > "$PROMPT_FILE" <<EOF
# Codex Spark Execution Request

You are an optional Codex Spark auxiliary in a Codex/Claude workflow.

Model requested: ${MODEL}
Mode: ${MODE}
Sandbox: ${SANDBOX}

Operating rules:
- Use the requested Spark model only. Do not silently fall back to a stronger model.
- Keep output compressed: decisions, evidence, changed files if any, checks run, and risks.
- Treat Claude-owned implementation as Claude-owned unless this task card explicitly authorizes Spark micro-builder work.
- Do not claim completion from prose alone. Cite artifacts, commands, or diffs.
- If blocked by missing context, permissions, model access, network, or auth, report the blocker instead of guessing.

Mode contract:
- review-only: inspect the task card and available repository context, do not edit files.
- evidence-checker: inspect evidence artifacts and run only narrow, task-card-allowed checks; do not edit files.
- micro-builder: make only the explicitly scoped source edits in this isolated worktree, keep the diff minimal, and report validation evidence.

## Task Card

$(cat "$TASK_CARD_COPY")
EOF

set +e
(
    cd "$RUN_DIR"
    run_codex exec --model "$MODEL" --sandbox "$SANDBOX" - < "$PROMPT_FILE" > "$RESULT_FILE" 2> "$STDERR_FILE"
)
CODEX_STATUS=$?
set -e
HELPER_EXIT_STATUS="$CODEX_STATUS"

if [ "$MODE" = "micro-builder" ]; then
    git -C "$RUN_DIR" status --porcelain --untracked-files=all > "$STATUS_FILE" || true
    git -C "$RUN_DIR" diff --binary > "$DIFF_FILE" || true
    git -C "$RUN_DIR" diff --stat > "$DIFFSTAT_FILE" || true
else
    git -C "$REPO_ROOT" status --porcelain --untracked-files=all > "$STATUS_FILE" || true
    : > "$DIFF_FILE"
    : > "$DIFFSTAT_FILE"
fi

DIFF_VALUE="no"
if [ "$MODE" = "micro-builder" ] && [ -s "$DIFF_FILE" ]; then
    DIFF_VALUE="yes: ${DIFF_FILE}"
fi
if [ "$CODEX_STATUS" -ne 0 ] && [ "$REQUIRE_SPARK" != "1" ] && spark_unavailable_failure; then
    SPARK_AUTO_DISABLED="yes"
    SPARK_DISABLE_REASON="$(spark_failure_auto_disable_reason)"
    HELPER_EXIT_STATUS=0
fi
write_report_header "$CODEX_STATUS" "$DIFF_VALUE"

{
    echo "## Result"
    echo ""
    echo "| Field | Value |"
    echo "|-------|-------|"
    echo "| Codex exit code | ${CODEX_STATUS} |"
    echo "| Prompt | ${PROMPT_FILE} |"
    echo "| Raw output | ${RESULT_FILE} |"
    echo "| Stderr log | ${STDERR_FILE} |"
    echo "| Worktree status | ${STATUS_FILE} |"
    echo "| Diff | ${DIFF_FILE} |"
    echo "| Diffstat | ${DIFFSTAT_FILE} |"
    echo "| Strong-model fallback used | no |"
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
