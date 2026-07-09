#!/usr/bin/env bash
# run-parallel-loop.sh  -  Experimental parallel dispatch helper.
#
# Usage:
#   bash ai/run-parallel-loop.sh [--max-concurrency N] [--allow-overlap] <task-card>...
#
# This helper dispatches multiple task cards concurrently and writes an aggregate
# summary. It never merges worktrees and does not perform final review.

set -euo pipefail

PATH="/usr/bin:/bin:/mingw64/bin:${PATH}"
export PATH

usage() {
    cat >&2 <<'EOF'
Usage: run-parallel-loop.sh [options] <task-card>...

Experimental helper for parallel Claude dispatches.

Options:
  --max-concurrency N    Maximum concurrent dispatches (default: 2)
  --output DIR           Artifact directory (default: .worktrees/parallel-<timestamp>)
  --allow-overlap        Allow overlapping Allowed files/modules scopes
  --allow-ungated        Allow task cards without Parallel allowed? = yes
  -h, --help             Show this help

Environment:
  AI_CODING_WORKFLOW_DISPATCH_BIN   Override dispatch script path for tests
  AI_CODING_WORKFLOW_PARALLEL_MAX   Default max concurrency

Safety:
  - Requires Parallel Execution Gate unless --allow-ungated is set.
  - Refuses overlapping Allowed files/modules by default.
  - Does not merge automatically.
  - Review and merge remain serial Codex/human decisions.
EOF
}

MAX_CONCURRENCY="${AI_CODING_WORKFLOW_PARALLEL_MAX:-2}"
OUTPUT_DIR=""
ALLOW_OVERLAP=0
ALLOW_UNGATED=0
TASK_CARDS=()

while [ $# -gt 0 ]; do
    case "$1" in
        --max-concurrency)
            [ $# -ge 2 ] || { echo "Error: --max-concurrency requires a value." >&2; exit 1; }
            MAX_CONCURRENCY="$2"
            shift 2
            ;;
        --output)
            [ $# -ge 2 ] || { echo "Error: --output requires a value." >&2; exit 1; }
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --allow-overlap)
            ALLOW_OVERLAP=1
            shift
            ;;
        --allow-ungated)
            ALLOW_UNGATED=1
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
            TASK_CARDS+=("$1")
            shift
            ;;
    esac
done

case "$MAX_CONCURRENCY" in
    ''|*[!0-9]*)
        echo "Error: --max-concurrency must be a positive integer." >&2
        exit 1
        ;;
esac
if [ "$MAX_CONCURRENCY" -lt 1 ]; then
    echo "Error: --max-concurrency must be greater than 0." >&2
    exit 1
fi

if [ "${#TASK_CARDS[@]}" -lt 2 ]; then
    echo "Error: provide at least two task cards for parallel dispatch." >&2
    usage
    exit 1
fi

for tool in git awk sed; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "Error: $tool is not installed or not in PATH." >&2
        exit 1
    fi
done

REPO_ROOT="$(git rev-parse --show-toplevel)"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DISPATCH_BIN="${AI_CODING_WORKFLOW_DISPATCH_BIN:-${SCRIPT_DIR}/dispatch-to-claude.sh}"

if [ ! -f "$DISPATCH_BIN" ] && ! command -v "$DISPATCH_BIN" >/dev/null 2>&1; then
    echo "Error: dispatch helper not found: $DISPATCH_BIN" >&2
    exit 1
fi

run_dispatch() {
    case "$DISPATCH_BIN" in
        *.sh)
            bash "$DISPATCH_BIN" "$@"
            ;;
        *)
            "$DISPATCH_BIN" "$@"
            ;;
    esac
}

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
if [ -z "$OUTPUT_DIR" ]; then
    OUTPUT_DIR="${REPO_ROOT}/.worktrees/parallel-${TIMESTAMP}"
fi
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"

SUMMARY_FILE="${OUTPUT_DIR}/parallel-summary.md"
EVENTS_FILE="${OUTPUT_DIR}/parallel-events.jsonl"
MANIFEST_FILE="${OUTPUT_DIR}/parallel-manifest.tsv"

normalize_field_name() {
    printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/_/g; s/^_//; s/_$//'
}

table_field() {
    local file="$1"
    local wanted
    wanted="$(normalize_field_name "$2")"
    awk -F'|' -v wanted="$wanted" '
        function trim(s) { gsub(/^[ \t]+|[ \t]+$/, "", s); return s }
        function norm(s) {
            s = tolower(s)
            gsub(/[^a-z0-9]+/, "_", s)
            gsub(/^_+|_+$/, "", s)
            return s
        }
        /^\|/ && $0 !~ /---/ {
            key = trim($2)
            val = trim($3)
            if (norm(key) == wanted) {
                print val
                exit
            }
        }
    ' "$file"
}

section_table_field() {
    local file="$1"
    local section="$2"
    local wanted
    wanted="$(normalize_field_name "$3")"
    awk -F'|' -v wanted="$wanted" -v section="$section" '
        function trim(s) { gsub(/^[ \t]+|[ \t]+$/, "", s); return s }
        function norm(s) {
            s = tolower(s)
            gsub(/[^a-z0-9]+/, "_", s)
            gsub(/^_+|_+$/, "", s)
            return s
        }
        /^##[ \t]+/ {
            current = $0
            sub(/^##[ \t]+/, "", current)
            current = trim(current)
            in_section = (current == section)
            next
        }
        in_section && /^\|/ && $0 !~ /---/ {
            key = trim($2)
            val = trim($3)
            if (norm(key) == wanted) {
                print val
                exit
            }
        }
    ' "$file"
}

scope_tokens() {
    printf '%s\n' "$1" \
        | tr ',;' '\n\n' \
        | sed -E 's/[`*"]//g; s/^[[:space:]]+//; s/[[:space:]]+$//' \
        | sed '/^$/d' \
        | sort -u
}

write_event() {
    local event="$1"
    local task="$2"
    local detail="$3"
    printf '{"time":"%s","event":"%s","task":"%s","detail":"%s"}\n' \
        "$(date '+%Y-%m-%dT%H:%M:%S%z')" \
        "$(printf '%s' "$event" | sed 's/"/\\"/g')" \
        "$(printf '%s' "$task" | sed 's/"/\\"/g')" \
        "$(printf '%s' "$detail" | sed 's/"/\\"/g')" >> "$EVENTS_FILE"
}

TASK_NAMES=()
TASK_SCOPES=()
TASK_ALLOWED=()

for task in "${TASK_CARDS[@]}"; do
    if [ ! -f "$task" ]; then
        echo "Error: task card not found: $task" >&2
        exit 1
    fi
    allowed="$(section_table_field "$task" "Parallel Execution Gate" "Parallel allowed?")"
    allowed_lc="$(printf '%s' "$allowed" | tr '[:upper:]' '[:lower:]')"
    if [ "$ALLOW_UNGATED" != "1" ] && [ "$allowed_lc" != "yes" ]; then
        echo "Error: task card is not parallel-enabled: $task" >&2
        echo "Fill Parallel Execution Gate with 'Parallel allowed? | yes' or pass --allow-ungated for an explicit experiment." >&2
        exit 2
    fi
    scope="$(section_table_field "$task" "Parallel Execution Gate" "Allowed files/modules")"
    if [ -z "$scope" ]; then
        scope="$(section_table_field "$task" "Parallel Execution Gate" "Conflict files/modules")"
    fi
    TASK_NAMES+=("$(basename "$task")")
    TASK_SCOPES+=("$scope")
    TASK_ALLOWED+=("$allowed")
done

if [ "$ALLOW_OVERLAP" != "1" ]; then
    for ((i = 0; i < ${#TASK_CARDS[@]}; i++)); do
        for ((j = i + 1; j < ${#TASK_CARDS[@]}; j++)); do
            overlap="$(comm -12 <(scope_tokens "${TASK_SCOPES[$i]}") <(scope_tokens "${TASK_SCOPES[$j]}") || true)"
            if [ -n "$overlap" ]; then
                echo "Error: parallel task scopes overlap between ${TASK_CARDS[$i]} and ${TASK_CARDS[$j]}." >&2
                echo "$overlap" | sed 's/^/  overlap: /' >&2
                echo "Use --allow-overlap only for an explicit manual-reconcile experiment." >&2
                exit 3
            fi
        done
    done
fi

: > "$EVENTS_FILE"
{
    echo -e "task\tallowed\tscope"
    for ((i = 0; i < ${#TASK_CARDS[@]}; i++)); do
        echo -e "${TASK_CARDS[$i]}\t${TASK_ALLOWED[$i]}\t${TASK_SCOPES[$i]}"
    done
} > "$MANIFEST_FILE"

echo "Experimental parallel run: $OUTPUT_DIR"
echo "Task count: ${#TASK_CARDS[@]}"
echo "Max concurrency: $MAX_CONCURRENCY"
echo "Automatic merge: disabled"

EXIT_CODES=()

run_one() {
    local index="$1"
    local task="${TASK_CARDS[$index]}"
    local name
    name="$(basename "$task")"
    local safe_name
    safe_name="$(printf '%s' "$name" | sed -E 's/[^A-Za-z0-9_.-]+/-/g')"
    local out="${OUTPUT_DIR}/${safe_name}.dispatch.out"
    local err="${OUTPUT_DIR}/${safe_name}.dispatch.err"
    local exit_file="${OUTPUT_DIR}/${safe_name}.exit"
    write_event "dispatch_start" "$task" "$out"
    set +e
    run_dispatch "$task" > "$out" 2> "$err"
    local status=$?
    set -e
    printf '%s\n' "$status" > "$exit_file"
    write_event "dispatch_complete" "$task" "exit=${status}"
    exit "$status"
}

running_jobs() {
    jobs -pr | sed '/^$/d' | wc -l | tr -d '[:space:]'
}

for ((i = 0; i < ${#TASK_CARDS[@]}; i++)); do
    while [ "$(running_jobs)" -ge "$MAX_CONCURRENCY" ]; do
        sleep 1
    done
    run_one "$i" &
done

set +e
wait
set -e

for task in "${TASK_CARDS[@]}"; do
    safe_name="$(basename "$task" | sed -E 's/[^A-Za-z0-9_.-]+/-/g')"
    exit_file="${OUTPUT_DIR}/${safe_name}.exit"
    if [ -f "$exit_file" ]; then
        status="$(cat "$exit_file")"
    else
        status="127"
    fi
    EXIT_CODES+=("${task}:${status}")
done

{
    echo "# Experimental Parallel Dispatch Summary"
    echo ""
    echo "| Field | Value |"
    echo "|-------|-------|"
    echo "| Artifact directory | ${OUTPUT_DIR} |"
    echo "| Task count | ${#TASK_CARDS[@]} |"
    echo "| Max concurrency | ${MAX_CONCURRENCY} |"
    echo "| Overlap allowed | $([ "$ALLOW_OVERLAP" = "1" ] && echo yes || echo no) |"
    echo "| Ungated allowed | $([ "$ALLOW_UNGATED" = "1" ] && echo yes || echo no) |"
    echo "| Automatic merge | no |"
    echo ""
    echo "## Task Results"
    echo ""
    echo "| Task | Exit | Dispatch Output | Dispatch Stderr | Result | Diff | Report |"
    echo "|------|------|-----------------|-----------------|--------|------|--------|"
    for entry in "${EXIT_CODES[@]}"; do
        task="${entry%:*}"
        status="${entry##*:}"
        safe_name="$(basename "$task" | sed -E 's/[^A-Za-z0-9_.-]+/-/g')"
        out="${OUTPUT_DIR}/${safe_name}.dispatch.out"
        err="${OUTPUT_DIR}/${safe_name}.dispatch.err"
        result="$(sed -n 's/^Result:[[:space:]]*//p' "$out" | tail -1)"
        diff="$(sed -n 's/^Diff:[[:space:]]*//p' "$out" | tail -1)"
        report="$(sed -n 's/^Report:[[:space:]]*//p' "$out" | tail -1)"
        echo "| ${task} | ${status} | ${out} | ${err} | ${result:-n/a} | ${diff:-n/a} | ${report:-n/a} |"
    done
    echo ""
    echo "## Review Contract"
    echo ""
    echo "- Review task results serially before merging any worktree."
    echo "- Check for diff overlap even when task-card scopes did not overlap."
    echo "- Prefer accepting independent low-risk diffs one at a time."
    echo "- If conflicts or shared API changes appear, stop and create a manual reconcile task card."
} > "$SUMMARY_FILE"

FAILED=0
for entry in "${EXIT_CODES[@]}"; do
    status="${entry##*:}"
    if [ "$status" -ne 0 ]; then
        FAILED=1
    fi
done

SUCCESS_COUNT=0
FAIL_COUNT=0
for entry in "${EXIT_CODES[@]}"; do
    status="${entry##*:}"
    if [ "$status" -eq 0 ]; then
        SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
    else
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
done

{
    echo ""
    echo "## Parallel Execution Follow-up"
    echo ""
    echo "| Field | Value |"
    echo "|-------|-------|"
    echo "| Parallel enabled in task card? | yes |"
    echo "| Parallel helper invoked? | yes |"
    echo "| Parallel group id | experimental-${TIMESTAMP} |"
    echo "| Aggregate artifact | ${SUMMARY_FILE} |"
    echo "| Max concurrency used | ${MAX_CONCURRENCY} |"
    echo "| Dispatches succeeded | ${SUCCESS_COUNT} |"
    echo "| Dispatches failed | ${FAIL_COUNT} |"
    echo "| Scope overlap detected? | $([ "$ALLOW_OVERLAP" = "1" ] && echo allowed-by-override || echo no) |"
    echo "| Overlap allowed by task card? | $([ "$ALLOW_OVERLAP" = "1" ] && echo yes || echo no) |"
    echo "| Merge/review strategy followed? | serial review |"
    echo "| Automatic merge performed? | no |"
    echo "| Follow-up reconcile task needed? | pending review |"
} >> "$SUMMARY_FILE"

echo "Parallel summary: $SUMMARY_FILE"
echo "Parallel events:  $EVENTS_FILE"
echo "Parallel manifest: $MANIFEST_FILE"
if [ "$FAILED" -ne 0 ]; then
    echo "One or more parallel dispatches failed." >&2
    exit 1
fi
exit 0
