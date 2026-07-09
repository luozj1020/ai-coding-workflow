#!/usr/bin/env bash
# check-worktree.sh  -  Run project validation checks without editing files.
#
# Usage: bash ai/check-worktree.sh [--report <path>] [--logs-dir <dir>]
#       [--task-card <path>] [--command <label=command>] [--discover|--no-discover]
#
# The checker runs explicitly assigned validation commands and, when discovery
# is enabled, common project validation commands. It writes a concise report and
# treats checker-induced worktree mutations as failures.

set -euo pipefail

PATH="/usr/bin:/bin:/mingw64/bin:${PATH}"
export PATH

REPORT_FILE=""
LOGS_DIR=""
TASK_CARD_FILE=""
DISCOVER="${AI_CHECK_WORKTREE_DISCOVER:-1}"
COMMANDS=()
COMMAND_LABELS=()

add_command() {
    local label="$1"
    local command="$2"
    COMMAND_LABELS+=("$label")
    COMMANDS+=("$command")
}

add_command_arg() {
    local value="$1"
    local label=""
    local command=""
    case "$value" in
        *=*)
            label="${value%%=*}"
            command="${value#*=}"
            ;;
        *)
            label="custom-${#COMMANDS[@]}"
            command="$value"
            ;;
    esac
    if [ -z "$label" ] || [ -z "$command" ]; then
        echo "Error: --command requires label=command or a non-empty command." >&2
        exit 1
    fi
    add_command "$label" "$command"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --report)
            if [ $# -lt 2 ]; then
                echo "Error: --report requires a path" >&2
                exit 1
            fi
            REPORT_FILE="$2"
            shift 2
            ;;
        --logs-dir)
            if [ $# -lt 2 ]; then
                echo "Error: --logs-dir requires a path" >&2
                exit 1
            fi
            LOGS_DIR="$2"
            shift 2
            ;;
        --command)
            if [ $# -lt 2 ]; then
                echo "Error: --command requires label=command" >&2
                exit 1
            fi
            add_command_arg "$2"
            shift 2
            ;;
        --task-card)
            if [ $# -lt 2 ]; then
                echo "Error: --task-card requires a path" >&2
                exit 1
            fi
            TASK_CARD_FILE="$2"
            shift 2
            ;;
        --discover)
            DISCOVER=1
            shift
            ;;
        --no-discover)
            DISCOVER=0
            shift
            ;;
        -h|--help)
            sed -n '1,20p' "$0"
            exit 0
            ;;
        *)
            echo "Error: unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

case "$DISCOVER" in
    0|1) ;;
    *)
        echo "Error: AI_CHECK_WORKTREE_DISCOVER must be 0 or 1." >&2
        exit 1
        ;;
esac

if ! command -v git >/dev/null 2>&1; then
    echo "Error: git is not installed or not in PATH." >&2
    exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"

if [ -z "$LOGS_DIR" ]; then
    LOGS_DIR="${REPO_ROOT}/.worktrees/checker-logs-$(date +%Y%m%d-%H%M%S)"
fi
mkdir -p "$LOGS_DIR"

if [ -z "$REPORT_FILE" ]; then
    REPORT_FILE="${LOGS_DIR}/checker-report.md"
fi
mkdir -p "$(dirname "$REPORT_FILE")"

task_card_local_validation_disabled() {
    [ -n "$TASK_CARD_FILE" ] || return 1
    [ -f "$TASK_CARD_FILE" ] || return 1
    awk '
        BEGIN { found = 0 }
        {
            line = tolower($0)
            if (index(line, "local validation allowed") > 0 && line ~ /\|[[:space:]]*no[[:space:]]*(\||\/|;|,)/) {
                found = 1
            }
        }
        END { exit(found ? 0 : 1) }
    ' "$TASK_CARD_FILE"
}

load_task_card_validation_commands() {
    [ -n "$TASK_CARD_FILE" ] || return 0
    [ -f "$TASK_CARD_FILE" ] || return 0
    local index=1
    while IFS= read -r command; do
        [ -z "$command" ] && continue
        add_command "task-card-${index}" "$command"
        index=$((index + 1))
    done < <(
        awk '
            BEGIN { in_block = 0 }
            /^```/ {
                fence = $0
                sub(/^```[[:space:]]*/, "", fence)
                fence = tolower(fence)
                if (in_block) {
                    in_block = 0
                    next
                }
                if (fence ~ /validation|check/) {
                    in_block = 1
                }
                next
            }
            in_block {
                line = $0
                sub(/^[[:space:]]+/, "", line)
                sub(/[[:space:]]+$/, "", line)
                if (line == "" || line ~ /^#/) {
                    next
                }
                print line
            }
        ' "$TASK_CARD_FILE"
    )
}

if task_card_local_validation_disabled; then
    {
        echo "# Checker Report"
        echo ""
        echo "Repository: ${REPO_ROOT}"
        echo "Report: ${REPORT_FILE}"
        echo "Logs directory: ${LOGS_DIR}"
        echo "Task card: ${TASK_CARD_FILE}"
        echo ""
        echo "## Result"
        echo ""
        echo "SKIPPED"
        echo ""
        echo "Local validation is disabled by the task card. Provide commands only; do not run local validation."
    } > "$REPORT_FILE"
    cat "$REPORT_FILE"
    exit 0
fi

load_task_card_validation_commands

has_script() {
    local script="$1"
    [ -f package.json ] && grep -Eq "\"${script}\"[[:space:]]*:" package.json
}

detect_node_runner() {
    if [ -f pnpm-lock.yaml ] && command -v pnpm >/dev/null 2>&1; then
        echo "pnpm"
    elif [ -f yarn.lock ] && command -v yarn >/dev/null 2>&1; then
        echo "yarn"
    elif [ -f package-lock.json ] && command -v npm >/dev/null 2>&1; then
        echo "npm"
    elif [ -f package.json ] && command -v pnpm >/dev/null 2>&1; then
        echo "pnpm"
    elif [ -f package.json ] && command -v npm >/dev/null 2>&1; then
        echo "npm"
    else
        echo ""
    fi
}

node_run() {
    local runner="$1"
    local script="$2"
    case "$runner" in
        pnpm) echo "pnpm run ${script}" ;;
        yarn) echo "yarn run ${script}" ;;
        npm) echo "npm run ${script}" ;;
    esac
}

if [ "$DISCOVER" -eq 1 ]; then
    NODE_RUNNER="$(detect_node_runner)"
    if [ -n "$NODE_RUNNER" ]; then
        if has_script check; then
            add_command "check" "$(node_run "$NODE_RUNNER" check)"
        else
            for script in test lint typecheck type-check tsc build format:check; do
                if has_script "$script"; then
                    add_command "$script" "$(node_run "$NODE_RUNNER" "$script")"
                fi
            done
        fi
    fi

    if [ -f pyproject.toml ] || [ -f pytest.ini ] || [ -d tests ]; then
        if command -v pytest >/dev/null 2>&1; then
            add_command "pytest" "pytest"
        elif command -v python >/dev/null 2>&1; then
            add_command "unittest" "python -m unittest discover -s tests"
        elif command -v python3 >/dev/null 2>&1; then
            add_command "unittest" "python3 -m unittest discover -s tests"
        fi
        if command -v ruff >/dev/null 2>&1; then
            add_command "ruff" "ruff check ."
        fi
        if command -v mypy >/dev/null 2>&1; then
            add_command "mypy" "mypy ."
        fi
    fi

    if [ -f Cargo.toml ] && command -v cargo >/dev/null 2>&1; then
        add_command "cargo test" "cargo test"
    fi

    if [ -f go.mod ] && command -v go >/dev/null 2>&1; then
        add_command "go test" "go test ./..."
    fi
fi

status_snapshot() {
    git status --porcelain 2>/dev/null | grep -v -F "$REPORT_FILE" | grep -v -F "$LOGS_DIR" || true
}

BEFORE_STATUS="$(status_snapshot)"

{
    echo "# Checker Report"
    echo ""
    echo "Repository: ${REPO_ROOT}"
    echo "Report: ${REPORT_FILE}"
    echo "Logs directory: ${LOGS_DIR}"
    if [ -n "$TASK_CARD_FILE" ]; then
        echo "Task card: ${TASK_CARD_FILE}"
    fi
    echo ""
    echo "## Discovered Commands"
    echo ""
    if [ "${#COMMANDS[@]}" -eq 0 ]; then
        echo "(none)"
    else
        for i in "${!COMMANDS[@]}"; do
            echo "- ${COMMAND_LABELS[$i]}: \`${COMMANDS[$i]}\`"
        done
    fi
    echo ""
} > "$REPORT_FILE"

FAILED=0

if [ "${#COMMANDS[@]}" -eq 0 ]; then
    if [ "$DISCOVER" -eq 0 ]; then
        {
            echo "## Result"
            echo ""
            echo "SKIPPED"
            echo ""
            echo "No explicit validation commands were provided and broad discovery is disabled."
            echo "Pass --command 'label=command' for task-card-assigned checks, or pass --discover to run broad project discovery."
        } >> "$REPORT_FILE"
        cat "$REPORT_FILE"
        exit 0
    fi
    FAILED=1
    {
        echo "## Result"
        echo ""
        echo "FAILED"
        echo ""
        echo "No validation commands were discovered. Add project-specific commands to the task card or run checks manually."
    } >> "$REPORT_FILE"
else
    {
        echo "## Command Results"
        echo ""
    } >> "$REPORT_FILE"

    for i in "${!COMMANDS[@]}"; do
        label="${COMMAND_LABELS[$i]}"
        command="${COMMANDS[$i]}"
        safe_label="$(printf '%s' "$label" | tr -c 'A-Za-z0-9_.-' '_')"
        log_file="${LOGS_DIR}/${safe_label}.log"

        set +e
        bash -lc "$command" > "$log_file" 2>&1
        rc=$?
        set -e

        {
            echo "### ${label}"
            echo ""
            echo "- Command: \`${command}\`"
            echo "- Exit code: ${rc}"
            echo "- Log: ${log_file}"
            echo ""
            echo "Key output:"
            echo '```'
            if [ -s "$log_file" ]; then
                tail -80 "$log_file"
            else
                echo "(no output)"
            fi
            echo '```'
            echo ""
        } >> "$REPORT_FILE"

        if [ "$rc" -ne 0 ]; then
            FAILED=1
        fi
    done
fi

AFTER_STATUS="$(status_snapshot)"
if [ "$AFTER_STATUS" != "$BEFORE_STATUS" ]; then
    FAILED=1
    {
        echo "## Checker Mutation Guard"
        echo ""
        echo "FAILED"
        echo ""
        echo "The checker run changed the worktree. Checker commands must be read-only validation commands."
        echo ""
        echo "### Before"
        echo '```'
        if [ -z "$BEFORE_STATUS" ]; then echo "(clean)"; else echo "$BEFORE_STATUS"; fi
        echo '```'
        echo ""
        echo "### After"
        echo '```'
        if [ -z "$AFTER_STATUS" ]; then echo "(clean)"; else echo "$AFTER_STATUS"; fi
        echo '```'
        echo ""
    } >> "$REPORT_FILE"
fi

{
    echo "## Result"
    echo ""
    if [ "$FAILED" -eq 0 ]; then
        echo "ALL GREEN"
    else
        echo "FAILED"
    fi
} >> "$REPORT_FILE"

cat "$REPORT_FILE"
exit "$FAILED"
