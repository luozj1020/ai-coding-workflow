#!/usr/bin/env bash
# run-codex-spark.sh  -  Optional Codex Spark auxiliary execution for the workflow.
#
# Usage:
#   bash ai/run-codex-spark.sh <task-card> [--mode auto|task-size-classifier|review-only|task-card-audit|plan-splitter|validation-planner|failure-triage|evidence-checker|micro-builder|parallel-planner]
#       [--model gpt-5.3-codex-spark] [--sandbox read-only|workspace-write]
#       [--artifact .worktrees/claude-....report.md] [--output .worktrees/codex-spark-...]
#
# Defaults are intentionally conservative: auto-selected auxiliary role,
# read-only, optional Spark, and no strong-model fallback.

set -euo pipefail

PATH="/usr/bin:/bin:/mingw64/bin:${PATH}"
export PATH

usage() {
    cat >&2 <<'EOF'
Usage: run-codex-spark.sh <task-card> [options]

Options:
  --mode MODE       auto, task-size-classifier, review-only, task-card-audit,
                    plan-splitter, validation-planner, failure-triage,
                    evidence-checker, micro-builder, or parallel-planner
  --model MODEL     Codex model slug (default: gpt-5.3-codex-spark)
  --sandbox MODE    read-only or workspace-write (default: read-only)
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
  CODEX_SPARK_ARTIFACT_LINES=160
  CODEX_SPARK_ALLOW_DIRTY_SOURCE=1
  CODEX_SPARK_REQUIRED=1
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
SPARK_INVOKED="yes"
SPARK_AUTO_DISABLED="no"
SPARK_DISABLE_REASON="not applicable"
SPARK_CHECKS_RUN="codex exec"
HELPER_EXIT_STATUS=0
ARTIFACTS=()

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
        --artifact)
            [ $# -ge 2 ] || { echo "Error: --artifact requires a value." >&2; exit 1; }
            ARTIFACTS+=("$2")
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
    auto|task-size-classifier|review-only|task-card-audit|plan-splitter|validation-planner|failure-triage|evidence-checker|micro-builder|parallel-planner) ;;
    *)
        echo "Error: invalid --mode: $MODE" >&2
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
WORKTREE_DIR=""
RUN_DIR="$REPO_ROOT"
CODEX_STATUS=0

cp "$TASK_CARD" "$TASK_CARD_COPY"
: > "$ARTIFACT_MANIFEST"
for artifact in "${ARTIFACTS[@]}"; do
    printf '%s\n' "$artifact" >> "$ARTIFACT_MANIFEST"
done

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

resolve_auto_mode() {
    if [ "$MODE" != "auto" ]; then
        return
    fi
    if [ "${#ARTIFACTS[@]}" -gt 0 ]; then
        if artifact_failure_signals; then
            MODE="failure-triage"
        elif artifact_name_matches "*.diff" || artifact_name_matches "*.diffstat.txt"; then
            MODE="review-only"
        else
            MODE="evidence-checker"
        fi
    elif grep -Eiq 'checker-test|Validation Contract|Local validation allowed|Test-First / TDD|TDD mode' "$TASK_CARD_COPY"; then
        MODE="validation-planner"
    else
        MODE="task-size-classifier"
    fi
}

resolve_auto_mode

if [ "$MODE" = "task-size-classifier" ] && [ "$SANDBOX" = "read-only" ]; then
    # Codex Spark task-size classification only needs the rendered prompt. Running
    # from the artifact directory with workspace-write prevents source-repo writes
    # and gives local helper initialization a writable working directory.
    SANDBOX="workspace-write"
    RUN_DIR="$OUTPUT_DIR"
    SPARK_CHECKS_RUN="codex exec (classifier in artifact dir)"
fi

if [ "$MODE" = "micro-builder" ] && [ "$SANDBOX" != "workspace-write" ]; then
    echo "Error: micro-builder mode requires --sandbox workspace-write." >&2
    exit 1
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
        echo "| Spark requested mode | ${REQUESTED_MODE} |"
        echo "| Spark model used | ${MODEL} |"
        echo "| Task size classification | $([ "$MODE" = "task-size-classifier" ] && echo "see Spark output" || echo "not used") |"
        echo "| Spark routing recommendation | $([ "$MODE" = "task-size-classifier" ] && echo "see Spark output" || echo "not used") |"
        echo "| Spark classification confidence | $([ "$MODE" = "task-size-classifier" ] && echo "see Spark output" || echo "not used") |"
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
        echo "| Spark output can satisfy acceptance? | no, advisory only unless Codex separately verifies and records acceptance |"
        echo "| Spark result accepted by Codex? | pending review |"
        echo "| accepted_suggestions | pending Codex review |"
        echo "| ignored_suggestions | pending Codex review |"
        echo "| conflicts_with_claude | pending review |"
        echo "| conflicts_with_local_evidence | pending review |"
        echo "| acceptance_satisfied_by_spark | no |"
        echo "| Remaining Spark-related risk | pending review |"
        echo "| Artifact directory | ${OUTPUT_DIR} |"
        echo "| Task card copy | ${TASK_CARD_COPY} |"
        echo "| Artifact inputs | $([ "${#ARTIFACTS[@]}" -gt 0 ] && echo "${ARTIFACT_MANIFEST}" || echo none) |"
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
Mode requested: ${REQUESTED_MODE}
Mode resolved: ${MODE}
Sandbox: ${SANDBOX}

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
- parallel-planner: produce an advisory parallel scheduling proposal as strict JSON. Do not edit files. Do not dispatch or execute any tasks. Output exactly one JSON object in a fenced code block matching this schema: {"schema_version":1,"group_id":"<group-slug>","max_concurrency":<int>,"failure_policy":"skip-dependents","tasks":[{"id":"<task-id>","task_card":"<path>","depends_on":["<task-id>"]}]}. After the JSON block, end with these reconciliation fields exactly: accepted_suggestions=<none or comma-separated>; ignored_suggestions=<none or comma-separated>; conflicts_with_claude=<none or short note>; conflicts_with_local_evidence=<none or short note>; acceptance_satisfied_by_spark=no. The proposal is advisory only; Codex or a human must review and save the plan before any dispatch.

## Task Card

$(cat "$TASK_CARD_COPY")
EOF
append_artifact_excerpts

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
