#!/usr/bin/env bash
# run-parallel-loop.sh  -  Parallel dispatch helper with DAG scheduling.
#
# Usage (flat - unchanged):
#   bash scripts/run-parallel-loop.sh [--max-concurrency N] [--allow-overlap] <task-card>...
#
# Usage (DAG plan):
#   bash scripts/run-parallel-loop.sh --plan <json> [--max-concurrency N] [--allow-overlap]
#
# This helper dispatches multiple task cards concurrently, respecting DAG
# dependencies when a plan is provided, and writes an aggregate summary.
# It never merges worktrees and does not perform final review.

set -euo pipefail

PATH="/usr/bin:/bin:/mingw64/bin:${PATH}"
export PATH

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

usage() {
    cat >&2 <<'EOF'
Usage: run-parallel-loop.sh [options] [--plan <json>] [<task-card>...]

Experimental helper for parallel Claude dispatches.

Options:
  --plan JSON            Path to a reviewed JSON plan (schema v1)
  --max-concurrency N    Maximum concurrent dispatches (default: 2 or plan value)
  --output DIR           Artifact directory (default: .worktrees/parallel-<timestamp>)
  --allow-overlap        Allow overlapping Allowed files/modules scopes
  --allow-ungated        Allow task cards without Parallel Execution Gate = yes
  -h, --help             Show this help

Environment:
  AI_CODING_WORKFLOW_DISPATCH_BIN   Override dispatch script path for tests
  AI_CODING_WORKFLOW_PARALLEL_MAX   Default max concurrency
  AI_CODING_WORKFLOW_RAND_SUFFIX    Override random suffix for collision resistance

Safety:
  - Requires Parallel Execution Gate unless --allow-ungated is set.
  - Refuses overlapping Allowed files/modules by default.
  - Does not merge automatically.
  - Review and merge remain serial Codex/human decisions.
EOF
}

MAX_CONCURRENCY=""
MAX_CONCURRENCY_SET_BY_CLI=0
OUTPUT_DIR=""
ALLOW_OVERLAP=0
ALLOW_UNGATED=0
PLAN_FILE=""
TASK_CARDS=()

while [ $# -gt 0 ]; do
    case "$1" in
        --max-concurrency)
            [ $# -ge 2 ] || { echo "Error: --max-concurrency requires a value." >&2; exit 1; }
            MAX_CONCURRENCY="$2"
            MAX_CONCURRENCY_SET_BY_CLI=1
            shift 2
            ;;
        --output)
            [ $# -ge 2 ] || { echo "Error: --output requires a value." >&2; exit 1; }
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --plan)
            [ $# -ge 2 ] || { echo "Error: --plan requires a value." >&2; exit 1; }
            PLAN_FILE="$2"
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

# --- Validate argument combinations ---
if [ -n "$PLAN_FILE" ] && [ "${#TASK_CARDS[@]}" -gt 0 ]; then
    echo "Error: --plan and positional task cards are mutually exclusive." >&2
    exit 1
fi
if [ -z "$PLAN_FILE" ] && [ "${#TASK_CARDS[@]}" -lt 2 ]; then
    echo "Error: provide at least two task cards for parallel dispatch, or use --plan." >&2
    usage
    exit 1
fi

# --- Set default concurrency ---
if [ -z "$MAX_CONCURRENCY" ]; then
    MAX_CONCURRENCY="${AI_CODING_WORKFLOW_PARALLEL_MAX:-2}"
fi

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

for tool in git awk sed; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "Error: $tool is not installed or not in PATH." >&2
        exit 1
    fi
done

REPO_ROOT="$(git rev-parse --show-toplevel)"
DISPATCH_BIN="${AI_CODING_WORKFLOW_DISPATCH_BIN:-${SCRIPT_DIR}/dispatch-to-claude.sh}"

if [ ! -f "$DISPATCH_BIN" ] && ! command -v "$DISPATCH_BIN" >/dev/null 2>&1; then
    echo "Error: dispatch helper not found: $DISPATCH_BIN" >&2
    exit 1
fi

# --- Validate Python availability for plan mode ---
if [ -n "$PLAN_FILE" ]; then
    PYTHON_CMD=""
    if command -v python3 >/dev/null 2>&1; then
        PYTHON_CMD="python3"
    elif command -v python >/dev/null 2>&1; then
        PYTHON_CMD="python"
    fi
    if [ -z "$PYTHON_CMD" ]; then
        echo "Error: python3 or python is required for --plan mode." >&2
        exit 1
    fi
fi

# --- Resolve plan file path ---
PLAN_DIR=""
if [ -n "$PLAN_FILE" ]; then
    if [ ! -f "$PLAN_FILE" ]; then
        echo "Error: plan file not found: $PLAN_FILE" >&2
        exit 1
    fi
    PLAN_DIR="$(cd "$(dirname "$PLAN_FILE")" && pwd)"
    PLAN_FILE="${PLAN_DIR}/$(basename "$PLAN_FILE")"
fi

# --- Output directory setup ---
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
if [ -z "$OUTPUT_DIR" ]; then
    OUTPUT_DIR="${REPO_ROOT}/.worktrees/parallel-${TIMESTAMP}"
fi
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"

SUMMARY_FILE="${OUTPUT_DIR}/parallel-summary.md"
EVENTS_FILE="${OUTPUT_DIR}/parallel-events.jsonl"
MANIFEST_FILE="${OUTPUT_DIR}/parallel-manifest.tsv"

# --- Collision resistance suffix ---
RAND_SUFFIX="${AI_CODING_WORKFLOW_RAND_SUFFIX:-}"
if [ -z "$RAND_SUFFIX" ]; then
    if [ -r /dev/urandom ]; then
        RAND_SUFFIX="$(od -An -tx1 -N3 /dev/urandom | tr -d ' ')"
    else
        RAND_SUFFIX="$$"
    fi
fi

# --- Helper functions ---

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

running_jobs() {
    jobs -pr | sed '/^$/d' | wc -l | tr -d '[:space:]'
}

# =============================================================================
# FLAT MODE (existing positional-card interface — unchanged behavior)
# =============================================================================

run_flat_mode() {
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
}

# =============================================================================
# DAG MODE (--plan)
# =============================================================================

run_dag_mode() {
    # --- Validate plan and consume normalized output ---
    VALIDATOR="${SCRIPT_DIR}/validate-parallel-plan.py"
    if [ ! -f "$VALIDATOR" ]; then
        echo "Error: validator not found: $VALIDATOR" >&2
        exit 1
    fi

    VALIDATOR_OUTPUT_FILE="${OUTPUT_DIR}/.validator-output.tsv"
    set +e
    "$PYTHON_CMD" "$VALIDATOR" --plan "$PLAN_FILE" > "$VALIDATOR_OUTPUT_FILE" 2>"${OUTPUT_DIR}/.validator-errors.txt"
    VALIDATOR_EXIT=$?
    set -e

    if [ "$VALIDATOR_EXIT" -ne 0 ]; then
        echo "Error: plan validation failed (exit=$VALIDATOR_EXIT)." >&2
        if [ -s "${OUTPUT_DIR}/.validator-errors.txt" ]; then
            cat "${OUTPUT_DIR}/.validator-errors.txt" >&2
        fi
        exit 1
    fi

    # --- Parse META records ---
    PLAN_GROUP_ID=""
    PLAN_MAX_CONCURRENCY=""
    PLAN_FAILURE_POLICY=""
    PLAN_PATH=""
    PLAN_DIR_ACTUAL=""

    while IFS=$'\t' read -r record_type key value; do
        [ "$record_type" = "META" ] || continue
        case "$key" in
            group_id) PLAN_GROUP_ID="$value" ;;
            max_concurrency) PLAN_MAX_CONCURRENCY="$value" ;;
            failure_policy) PLAN_FAILURE_POLICY="$value" ;;
            plan_path) PLAN_PATH="$value" ;;
            plan_dir) PLAN_DIR_ACTUAL="$value" ;;
        esac
    done < "$VALIDATOR_OUTPUT_FILE"

    # CLI --max-concurrency overrides plan value
    if [ "${MAX_CONCURRENCY_SET_BY_CLI:-0}" = "1" ]; then
        : # CLI value already in MAX_CONCURRENCY
    elif [ -n "$PLAN_MAX_CONCURRENCY" ]; then
        MAX_CONCURRENCY="$PLAN_MAX_CONCURRENCY"
    fi

    # --- Parse TASK records ---
    DAG_IDS=()
    DAG_CARDS=()
    DAG_DEPS=()
    DAG_RESOLVED=()

    while IFS=$'\t' read -r record_type id task_card depends_csv resolved; do
        [ "$record_type" = "TASK" ] || continue
        DAG_IDS+=("$id")
        DAG_CARDS+=("$task_card")
        DAG_DEPS+=("$depends_csv")
        DAG_RESOLVED+=("$resolved")
    done < "$VALIDATOR_OUTPUT_FILE"

    TASK_COUNT="${#DAG_IDS[@]}"

    if [ "$TASK_COUNT" -eq 0 ]; then
        echo "Error: plan contains no tasks." >&2
        exit 1
    fi

    # --- Scope-gate and overlap checks on all task cards ---
    DAG_SCOPES=()
    DAG_ALLOWED=()
    for ((i = 0; i < TASK_COUNT; i++)); do
        card="${DAG_RESOLVED[$i]}"
        if [ ! -f "$card" ]; then
            echo "Error: task card not found: $card (task ${DAG_IDS[$i]})" >&2
            exit 1
        fi
        allowed="$(section_table_field "$card" "Parallel Execution Gate" "Parallel allowed?")"
        allowed_lc="$(printf '%s' "$allowed" | tr '[:upper:]' '[:lower:]')"
        if [ "$ALLOW_UNGATED" != "1" ] && [ "$allowed_lc" != "yes" ]; then
            echo "Error: task card is not parallel-enabled: $card (task ${DAG_IDS[$i]})" >&2
            echo "Fill Parallel Execution Gate with 'Parallel allowed? | yes' or pass --allow-ungated." >&2
            exit 2
        fi
        scope="$(section_table_field "$card" "Parallel Execution Gate" "Allowed files/modules")"
        if [ -z "$scope" ]; then
            scope="$(section_table_field "$card" "Parallel Execution Gate" "Conflict files/modules")"
        fi
        DAG_SCOPES+=("$scope")
        DAG_ALLOWED+=("$allowed")
    done

    if [ "$ALLOW_OVERLAP" != "1" ]; then
        for ((i = 0; i < TASK_COUNT; i++)); do
            for ((j = i + 1; j < TASK_COUNT; j++)); do
                overlap="$(comm -12 <(scope_tokens "${DAG_SCOPES[$i]}") <(scope_tokens "${DAG_SCOPES[$j]}") || true)"
                if [ -n "$overlap" ]; then
                    echo "Error: parallel task scopes overlap between ${DAG_IDS[$i]} and ${DAG_IDS[$j]}." >&2
                    echo "$overlap" | sed 's/^/  overlap: /' >&2
                    exit 3
                fi
            done
        done
    fi

    # --- Initialize DAG state ---
    # States: pending, running, completed, failed, skipped
    mkdir -p "${OUTPUT_DIR}/.dag"
    for ((i = 0; i < TASK_COUNT; i++)); do
        echo "pending" > "${OUTPUT_DIR}/.dag/${DAG_IDS[$i]}.state"
        echo "-1" > "${OUTPUT_DIR}/.dag/${DAG_IDS[$i]}.exit"
        echo "" > "${OUTPUT_DIR}/.dag/${DAG_IDS[$i]}.pid"
    done

    # Build dependency-children map (for skip propagation)
    # Each .children file contains one child task ID per line
    for ((i = 0; i < TASK_COUNT; i++)); do
        : > "${OUTPUT_DIR}/.dag/${DAG_IDS[$i]}.children"
    done
    for ((i = 0; i < TASK_COUNT; i++)); do
        deps_csv="${DAG_DEPS[$i]}"
        if [ -n "$deps_csv" ]; then
            IFS=',' read -ra dep_list <<< "$deps_csv"
            for dep in "${dep_list[@]}"; do
                printf '%s\n' "${DAG_IDS[$i]}" >> "${OUTPUT_DIR}/.dag/${dep}.children"
            done
        fi
    done

    : > "$EVENTS_FILE"
    {
        echo -e "id\ttask_card\tdepends_on\tresolved\tallowed\tscope"
        for ((i = 0; i < TASK_COUNT; i++)); do
            echo -e "${DAG_IDS[$i]}\t${DAG_CARDS[$i]}\t${DAG_DEPS[$i]}\t${DAG_RESOLVED[$i]}\t${DAG_ALLOWED[$i]:-}\t${DAG_SCOPES[$i]}"
        done
    } > "$MANIFEST_FILE"

    echo "DAG parallel run: $OUTPUT_DIR"
    echo "Plan: $PLAN_PATH"
    echo "Group: $PLAN_GROUP_ID"
    echo "Task count: $TASK_COUNT"
    echo "Max concurrency: $MAX_CONCURRENCY"
    echo "Failure policy: $PLAN_FAILURE_POLICY"
    echo "Automatic merge: disabled"

    # --- DAG Scheduler ---
    DISPATCH_PIDS=()
    DISPATCH_TASK_IDS=()

    dispatch_dag_task() {
        local task_id="$1"
        local card="$2"
        local safe_id
        safe_id="$(printf '%s' "$task_id" | sed -E 's/[^A-Za-z0-9_.-]+/-/g')"
        local out="${OUTPUT_DIR}/${safe_id}.dispatch.out"
        local err="${OUTPUT_DIR}/${safe_id}.dispatch.err"
        local exit_file="${OUTPUT_DIR}/.dag/${task_id}.exit"
        local pid_file="${OUTPUT_DIR}/.dag/${task_id}.pid"

        # Collision-resistant branch: <group_id>-<task_id>-<timestamp>-<rand>
        local branch_name="${PLAN_GROUP_ID}-${task_id}-${TIMESTAMP}-${RAND_SUFFIX}"

        write_event "dispatch_start" "$task_id" "card=${card} branch=${branch_name}"
        echo "running" > "${OUTPUT_DIR}/.dag/${task_id}.state"

        set +e
        (
            AI_CODING_WORKFLOW_DAG_TASK_ID="$task_id" \
            AI_CODING_WORKFLOW_DAG_GROUP_ID="$PLAN_GROUP_ID" \
            AI_CODING_WORKFLOW_DAG_BRANCH_NAME="$branch_name" \
            run_dispatch "$card" > "$out" 2> "$err"
            local status=$?
            printf '%s\n' "$status" > "$exit_file"
            write_event "dispatch_complete" "$task_id" "exit=${status}"
            exit "$status"
        ) &
        local bg_pid=$!
        echo "$bg_pid" > "$pid_file"
        DISPATCH_PIDS+=("$bg_pid")
        DISPATCH_TASK_IDS+=("$task_id")
    }

    get_state() {
        cat "${OUTPUT_DIR}/.dag/${1}.state" 2>/dev/null || echo "unknown"
    }

    set_state() {
        echo "$2" > "${OUTPUT_DIR}/.dag/${1}.state"
    }

    # Recursive skip: mark task and all transitive dependents as skipped
    skip_dependents() {
        local failed_id="$1"
        local children_file="${OUTPUT_DIR}/.dag/${failed_id}.children"
        if [ -f "$children_file" ]; then
            while IFS= read -r child_id; do
                [ -z "$child_id" ] && continue
                child_state="$(get_state "$child_id")"
                if [ "$child_state" = "pending" ]; then
                    set_state "$child_id" "skipped"
                    write_event "task_skipped" "$child_id" "prerequisite_failed=${failed_id}"
                    skip_dependents "$child_id"
                fi
            done < "$children_file"
        fi
    }

    # Harvest completed background jobs
    harvest_completed() {
        local new_pids=()
        local new_ids=()
        for ((k = 0; k < ${#DISPATCH_PIDS[@]}; k++)); do
            local pid="${DISPATCH_PIDS[$k]}"
            local tid="${DISPATCH_TASK_IDS[$k]}"
            if ! kill -0 "$pid" 2>/dev/null; then
                # Process finished; collect exit code from status file
                wait "$pid" 2>/dev/null || true
                local exit_code=0
                local exit_file="${OUTPUT_DIR}/.dag/${tid}.exit"
                if [ -f "$exit_file" ]; then
                    exit_code="$(cat "$exit_file")"
                fi
                if [ "$exit_code" -eq 0 ]; then
                    set_state "$tid" "completed"
                    write_event "task_completed" "$tid" "exit=${exit_code}"
                else
                    set_state "$tid" "failed"
                    write_event "task_failed" "$tid" "exit=${exit_code}"
                    skip_dependents "$tid"
                fi
            else
                new_pids+=("$pid")
                new_ids+=("$tid")
            fi
        done
        DISPATCH_PIDS=("${new_pids[@]+"${new_pids[@]}"}")
        DISPATCH_TASK_IDS=("${new_ids[@]+"${new_ids[@]}"}")
    }

    # Count tasks in a given state
    count_state() {
        local target="$1"
        local count=0
        for ((i = 0; i < TASK_COUNT; i++)); do
            if [ "$(get_state "${DAG_IDS[$i]}")" = "$target" ]; then
                count=$((count + 1))
            fi
        done
        echo "$count"
    }

    # Check if all prerequisites of a task are completed
    all_deps_met() {
        local idx="$1"
        local deps_csv="${DAG_DEPS[$idx]}"
        if [ -z "$deps_csv" ]; then
            return 0
        fi
        IFS=',' read -ra dep_list <<< "$deps_csv"
        for dep in "${dep_list[@]}"; do
            if [ "$(get_state "$dep")" != "completed" ]; then
                return 1
            fi
        done
        return 0
    }

    # Main DAG scheduling loop
    while true; do
        # Harvest completed jobs first
        harvest_completed

        # Check if all tasks are in terminal state
        pending_count="$(count_state pending)"
        running_count="$(count_state running)"
        if [ "$pending_count" -eq 0 ] && [ "$running_count" -eq 0 ]; then
            break
        fi

        # Launch ready tasks up to concurrency cap
        current_running="$running_count"
        for ((i = 0; i < TASK_COUNT; i++)); do
            if [ "$current_running" -ge "$MAX_CONCURRENCY" ]; then
                break
            fi
            task_state="$(get_state "${DAG_IDS[$i]}")"
            if [ "$task_state" != "pending" ]; then
                continue
            fi
            if all_deps_met "$i"; then
                dispatch_dag_task "${DAG_IDS[$i]}" "${DAG_RESOLVED[$i]}"
                current_running=$((current_running + 1))
            fi
        done

        # Brief sleep to avoid busy-waiting
        if [ "${#DISPATCH_PIDS[@]}" -gt 0 ]; then
            sleep 1
        fi
    done

    # --- Build summary ---
    EXIT_CODES=()
    SUCCESS_COUNT=0
    FAIL_COUNT=0
    SKIP_COUNT=0

    {
        echo "# DAG Parallel Dispatch Summary"
        echo ""
        echo "| Field | Value |"
        echo "|-------|-------|"
        echo "| Artifact directory | ${OUTPUT_DIR} |"
        echo "| Plan | ${PLAN_PATH} |"
        echo "| Group | ${PLAN_GROUP_ID} |"
        echo "| Task count | ${TASK_COUNT} |"
        echo "| Max concurrency | ${MAX_CONCURRENCY} |"
        echo "| Failure policy | ${PLAN_FAILURE_POLICY} |"
        echo "| Overlap allowed | $([ "$ALLOW_OVERLAP" = "1" ] && echo yes || echo no) |"
        echo "| Ungated allowed | $([ "$ALLOW_UNGATED" = "1" ] && echo yes || echo no) |"
        echo "| Automatic merge | no |"
        echo ""
        echo "## Task Results"
        echo ""
        echo "| Task ID | Card | State | Exit | Dispatch Output | Dispatch Stderr |"
        echo "|---------|------|-------|------|-----------------|-----------------|"
        for ((i = 0; i < TASK_COUNT; i++)); do
            tid="${DAG_IDS[$i]}"
            card="${DAG_CARDS[$i]}"
            state="$(get_state "$tid")"
            exit_file="${OUTPUT_DIR}/.dag/${tid}.exit"
            exit_val="-1"
            if [ -f "$exit_file" ]; then
                exit_val="$(cat "$exit_file")"
            fi
            safe_id="$(printf '%s' "$tid" | sed -E 's/[^A-Za-z0-9_.-]+/-/g')"
            out="${OUTPUT_DIR}/${safe_id}.dispatch.out"
            err="${OUTPUT_DIR}/${safe_id}.dispatch.err"
            if [ "$state" = "completed" ]; then
                SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
            elif [ "$state" = "failed" ]; then
                FAIL_COUNT=$((FAIL_COUNT + 1))
            elif [ "$state" = "skipped" ]; then
                SKIP_COUNT=$((SKIP_COUNT + 1))
            fi
            echo "| ${tid} | ${card} | ${state} | ${exit_val} | ${out} | ${err} |"
        done
        echo ""
        echo "## Review Contract"
        echo ""
        echo "- Review task results serially before merging any worktree."
        echo "- Check for diff overlap even when task-card scopes did not overlap."
        echo "- Prefer accepting independent low-risk diffs one at a time."
        echo "- If conflicts or shared API changes appear, stop and create a manual reconcile task card."
        echo ""
        echo "## Parallel Execution Follow-up"
        echo ""
        echo "| Field | Value |"
        echo "|-------|-------|"
        echo "| Parallel enabled in task card? | yes |"
        echo "| Parallel helper invoked? | yes |"
        echo "| Parallel group id | ${PLAN_GROUP_ID} |"
        echo "| Aggregate artifact | ${SUMMARY_FILE} |"
        echo "| Max concurrency used | ${MAX_CONCURRENCY} |"
        echo "| Dispatches succeeded | ${SUCCESS_COUNT} |"
        echo "| Dispatches failed | ${FAIL_COUNT} |"
        echo "| Dispatches skipped | ${SKIP_COUNT} |"
        echo "| Scope overlap detected? | $([ "$ALLOW_OVERLAP" = "1" ] && echo allowed-by-override || echo no) |"
        echo "| Merge/review strategy followed? | serial review |"
        echo "| Automatic merge performed? | no |"
        echo "| Follow-up reconcile task needed? | pending review |"
    } > "$SUMMARY_FILE"

    echo "Parallel summary: $SUMMARY_FILE"
    echo "Parallel events:  $EVENTS_FILE"
    echo "Parallel manifest: $MANIFEST_FILE"

    if [ "$FAIL_COUNT" -gt 0 ] || [ "$SKIP_COUNT" -gt 0 ]; then
        echo "One or more parallel dispatches failed or were skipped." >&2
        exit 1
    fi
    exit 0
}

# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

if [ -n "$PLAN_FILE" ]; then
    # MAX_CONCURRENCY_SET_BY_CLI is tracked during argument parsing above
    run_dag_mode
else
    run_flat_mode
fi
