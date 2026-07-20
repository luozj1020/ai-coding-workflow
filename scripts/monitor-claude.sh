#!/usr/bin/env bash
# Run Claude monitoring as a local background process and persist only compact
# material events. Controllers should read the tail once instead of polling.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATH="/usr/bin:/bin:/mingw64/bin:${PATH}"
export PATH

usage() {
    echo "Usage: $0 start|status|tail|decision|stop <claude-task-id> [--interval seconds] [--lines count] [--json] [--spark auto|on|off]" >&2
}

if [ $# -lt 2 ]; then
    usage
    exit 1
fi

ACTION="$1"
TASK_ID="$(basename "$2")"
TASK_ID="${TASK_ID%.pid}"
INTERVAL=5
LINES=3
JSON_OUTPUT=0
SPARK_MODE="auto"
shift 2

while [ $# -gt 0 ]; do
    case "$1" in
        --interval)
            shift
            INTERVAL="${1:-}"
            ;;
        --lines)
            shift
            LINES="${1:-}"
            ;;
        --json)
            JSON_OUTPUT=1
            ;;
        --spark)
            shift
            SPARK_MODE="${1:-}"
            ;;
        *)
            echo "Error: unknown argument: $1" >&2
            usage
            exit 1
            ;;
    esac
    shift
done

case "$SPARK_MODE" in auto|on|off) ;; *) echo "Error: --spark must be auto, on, or off." >&2; exit 1 ;; esac
if [ "$LINES" -gt 10 ]; then
    echo "Error: --lines is capped at 10 to keep monitor output bounded." >&2
    exit 1
fi

for value_name in INTERVAL LINES; do
    value="${!value_name}"
    case "$value" in
        ''|*[!0-9]*|0)
            echo "Error: ${value_name} must be a positive integer." >&2
            exit 1
            ;;
    esac
done

case "$TASK_ID" in
    claude-*) ;;
    *)
        echo "Error: task id must start with claude-." >&2
        exit 1
        ;;
esac

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
if command -v python3 >/dev/null 2>&1; then PYTHON_CMD=python3; else PYTHON_CMD=python; fi
WORKTREE_ROOT="${REPO_ROOT}/.worktrees"
WATCH_SCRIPT="${SCRIPT_DIR}/watch-claude.sh"
SUPERVISOR_SCRIPT="${SCRIPT_DIR}/claude-monitor-supervisor.py"
DECISION_HELPER="${SCRIPT_DIR}/claude-monitor-decision.py"
EVENT_LOG="${WORKTREE_ROOT}/${TASK_ID}.monitor-events.log"
MONITOR_PID_FILE="${WORKTREE_ROOT}/${TASK_ID}.monitor.pid"

monitor_pid() {
    if [ -f "$MONITOR_PID_FILE" ]; then
        tr -d '[:space:]' < "$MONITOR_PID_FILE"
    fi
}

monitor_running() {
    local pid
    pid="$(monitor_pid)"
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

case "$ACTION" in
    start)
        if [ ! -x "$WATCH_SCRIPT" ]; then
            echo "Error: watch helper is unavailable or not executable: $WATCH_SCRIPT" >&2
            exit 1
        fi
        mkdir -p "$WORKTREE_ROOT"
        if monitor_running; then
            echo "Monitor already running: pid=$(monitor_pid)"
            echo "Event log: $EVENT_LOG"
            exit 0
        fi
        : > "$EVENT_LOG"
        if [ -f "$SUPERVISOR_SCRIPT" ] && [ -f "$DECISION_HELPER" ]; then
            nohup "$PYTHON_CMD" "$SUPERVISOR_SCRIPT" \
                --task-id "$TASK_ID" --repo-root "$REPO_ROOT" \
                --watch-script "$WATCH_SCRIPT" --monitor-script "${SCRIPT_DIR}/monitor-claude.sh" \
                --decision-helper "$DECISION_HELPER" --event-log "$EVENT_LOG" \
                --interval "$INTERVAL" --spark "$SPARK_MODE" \
                --spark-min-interval "${CLAUDE_MONITOR_SPARK_MIN_INTERVAL_SECONDS:-120}" \
                >/dev/null 2>&1 < /dev/null &
        else
            nohup bash "$WATCH_SCRIPT" "$TASK_ID" --machine --interval "$INTERVAL" \
                > "$EVENT_LOG" 2>&1 < /dev/null &
        fi
        pid=$!
        echo "$pid" > "$MONITOR_PID_FILE"
        echo "Monitor started: pid=$pid"
        echo "Event log: $EVENT_LOG"
        echo "Read once later: bash $0 tail $TASK_ID --lines $LINES"
        ;;
    status)
        if monitor_running; then
            echo "running=yes pid=$(monitor_pid)"
        else
            echo "running=no pid=$(monitor_pid)"
        fi
        echo "event_log=$EVENT_LOG"
        ;;
    tail)
        if [ -f "$EVENT_LOG" ]; then
            tail -n "$LINES" "$EVENT_LOG"
        else
            echo "No monitor event log: $EVENT_LOG" >&2
            exit 1
        fi
        ;;
    decision)
        SPARK_HELPER="${SCRIPT_DIR}/run-codex-spark.sh"
        if [ ! -f "$DECISION_HELPER" ]; then
            echo "Error: monitor decision helper is unavailable: $DECISION_HELPER" >&2
            exit 1
        fi
        _tmp_dir="$(mktemp -d)"
        trap 'rm -rf "$_tmp_dir"' EXIT
        _local_json="${_tmp_dir}/local.json"
        "$PYTHON_CMD" "$DECISION_HELPER" snapshot --task-id "$TASK_ID" --format json > "$_local_json"
        _local_decision="$("$PYTHON_CMD" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("decision","inspect"))' "$_local_json")"
        _local_completion_fields="$("$PYTHON_CMD" -c 'import json,sys; d=json.load(open(sys.argv[1], encoding="utf-8")); print("execution_phase="+str(d.get("execution_phase","unknown"))); print("implementation_complete="+str(d.get("implementation_complete","unknown"))); print("completion_ready="+str(d.get("completion_ready","unknown"))); print("finish_recommended="+str(d.get("finish_recommended","no")))' "$_local_json")"
        _use_spark=0
        if [ "$SPARK_MODE" = "on" ]; then
            _use_spark=1
        elif [ "$SPARK_MODE" = "auto" ]; then
            case "$_local_decision" in inspect|interrupt-candidate) _use_spark=1 ;; esac
        fi
        if [ "$JSON_OUTPUT" -eq 1 ]; then
            _use_spark=0
        fi
        if [ "$_use_spark" -eq 1 ] && [ -x "$SPARK_HELPER" ]; then
            _spark_output="${_tmp_dir}/spark.txt"
            if timeout "${CLAUDE_MONITOR_SPARK_TIMEOUT_SECONDS:-90}s" bash "$SPARK_HELPER" \
                --brief-file "$_local_json" --mode monitor-triage --result-mode direct \
                --diagnostics failure > "$_spark_output" 2>/dev/null; then
                _spark_decision="$(sed -n 's/^decision=//p' "$_spark_output" | tail -1)"
                _spark_confidence="$(sed -n 's/^confidence=//p' "$_spark_output" | tail -1)"
                _spark_reason="$(sed -n 's/^reason_code=//p' "$_spark_output" | tail -1 | cut -c1-160)"
                case "$_spark_confidence" in high|medium|low) ;; *) _spark_confidence=low ;; esac
                case "$_spark_reason" in ''|*[!a-z0-9-]*) _spark_reason=spark-monitor-triage ;; esac
                case "$_spark_decision" in continue|inspect|interrupt-candidate|uncertain)
                    echo "decision=${_spark_decision}"
                    echo "confidence=${_spark_confidence:-low}"
                    echo "reason_code=${_spark_reason:-spark-monitor-triage}"
                    echo "triage_source=spark"
                    if [ "$_spark_decision" = "continue" ]; then
                        echo "codex_review_required=no"
                    else
                        echo "codex_review_required=yes"
                    fi
                    echo "interrupt_authorized=no"
                    printf '%s\n' "$_local_completion_fields"
                    exit 0
                    ;;
                esac
            fi
        fi
        if [ "$JSON_OUTPUT" -eq 1 ]; then
            cat "$_local_json"
        else
            "$PYTHON_CMD" "$DECISION_HELPER" snapshot --task-id "$TASK_ID" --format text
            echo "triage_source=local"
            if [ "$_use_spark" -eq 1 ]; then
                echo "spark_status=unavailable-or-invalid"
            fi
        fi
        ;;
    stop)
        if monitor_running; then
            pid="$(monitor_pid)"
            kill "$pid" 2>/dev/null || true
            echo "Monitor stop requested: pid=$pid"
        else
            echo "Monitor is not running."
        fi
        ;;
    *)
        echo "Error: action must be start, status, tail, decision, or stop." >&2
        usage
        exit 1
        ;;
esac
