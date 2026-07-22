#!/usr/bin/env bash
# Block on dispatcher-owned material events or request one bounded decision.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATH="/usr/bin:/bin:/mingw64/bin:${PATH}"
export PATH

usage() {
    echo "Usage: $0 wait|decision <claude-task-id> [--interval seconds] [--until material|terminal] [--timeout seconds] [--json] [--spark auto|on|off]" >&2
}

if [ $# -lt 2 ]; then
    usage
    exit 1
fi

ACTION="$1"
TASK_ID="$(basename "$2")"
TASK_ID="${TASK_ID%.pid}"
INTERVAL=5
JSON_OUTPUT=0
SPARK_MODE="auto"
WAIT_UNTIL="material"
WAIT_TIMEOUT=1800
shift 2

while [ $# -gt 0 ]; do
    case "$1" in
        --interval)
            shift
            INTERVAL="${1:-}"
            ;;
        --json)
            JSON_OUTPUT=1
            ;;
        --spark)
            shift
            SPARK_MODE="${1:-}"
            ;;
        --until)
            shift
            WAIT_UNTIL="${1:-}"
            ;;
        --timeout)
            shift
            WAIT_TIMEOUT="${1:-}"
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
case "$WAIT_UNTIL" in material|terminal) ;; *) echo "Error: --until must be material or terminal." >&2; exit 1 ;; esac
for value_name in INTERVAL WAIT_TIMEOUT; do
    value="${!value_name}"
    case "$value" in
        ''|*[!0-9]*)
            echo "Error: ${value_name} must be a non-negative integer." >&2
            exit 1
            ;;
    esac
done
if [ "$INTERVAL" -eq 0 ]; then
    echo "Error: INTERVAL must be a positive integer." >&2
    exit 1
fi

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
DECISION_HELPER="${SCRIPT_DIR}/claude-monitor-decision.py"
EVENT_LOG="${WORKTREE_ROOT}/${TASK_ID}.monitor-events.log"
DISPATCHER_PID_FILE="${WORKTREE_ROOT}/${TASK_ID}.dispatcher.pid"

dispatcher_running() {
    local pid=""
    if [ -f "$DISPATCHER_PID_FILE" ]; then
        pid="$(tr -d '[:space:]' < "$DISPATCHER_PID_FILE")"
    fi
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

event_count() {
    if [ -f "$EVENT_LOG" ]; then
        wc -l < "$EVENT_LOG" 2>/dev/null | tr -d '[:space:]'
    else
        echo 0
    fi
}

last_event() {
    if [ -f "$EVENT_LOG" ]; then
        awk 'NF { line=$0 } END { print line }' "$EVENT_LOG"
    fi
}

emit_boundary_summary() {
    local boundary="$1"
    printf '%s\n' "$boundary"
    # Compression happens only after a dispatcher boundary. Stable local
    # states avoid a model call; inspect/candidate states may use Spark in auto.
    if ! bash "${SCRIPT_DIR}/monitor-claude.sh" decision "$TASK_ID" --spark "$SPARK_MODE"; then
        echo "monitor_summary status=unavailable task_id=${TASK_ID}"
    fi
}

case "$ACTION" in
    wait)
        # One blocking tool call replaces agent-side ps/tail/clock polling.
        # The dispatcher is the sampling owner and appends only material and
        # terminal boundary events to this file.
        _seen="$(event_count)"
        _last="$(last_event)"
        if printf '%s\n' "$_last" | grep -q ' terminal=yes\([[:space:]]\|$\)'; then
            emit_boundary_summary "$_last"
            exit 0
        fi
        _started="$(date +%s)"
        while true; do
            sleep "$INTERVAL"
            _now="$(date +%s)"
            _count="$(event_count)"
            if [ "$_count" -gt "$_seen" ]; then
                _new_events="$(sed -n "$((_seen + 1)),${_count}p" "$EVENT_LOG")"
                _terminal="$(printf '%s\n' "$_new_events" | grep ' terminal=yes\([[:space:]]\|$\)' || true)"
                if [ -n "$_terminal" ]; then
                    emit_boundary_summary "$_terminal"
                    exit 0
                fi
                if [ "$WAIT_UNTIL" = "material" ]; then
                    _material="$(printf '%s\n' "$_new_events" | grep -E ' event=material-change([[:space:]]|$)' || true)"
                    if [ -n "$_material" ]; then
                        emit_boundary_summary "$_material"
                        exit 0
                    fi
                fi
                _seen="$_count"
            fi
            if [ "$WAIT_TIMEOUT" -gt 0 ] && [ $((_now - _started)) -ge "$WAIT_TIMEOUT" ]; then
                echo "monitor_wait status=timeout task_id=${TASK_ID} until=${WAIT_UNTIL} timeout_seconds=${WAIT_TIMEOUT}"
                exit 124
            fi
            if ! dispatcher_running; then
                _last="$(last_event)"
                if printf '%s\n' "$_last" | grep -q ' terminal=yes\([[:space:]]\|$\)'; then
                    emit_boundary_summary "$_last"
                    exit 0
                fi
                echo "monitor_wait status=visibility-unknown task_id=${TASK_ID} reason=dispatcher-not-running-without-terminal-event"
                exit 2
            fi
        done
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
                _spark_decision="$(awk -F= '$1=="decision" {v=$2} END {print v}' "$_spark_output")"
                _spark_confidence="$(awk -F= '$1=="confidence" {v=$2} END {print v}' "$_spark_output")"
                _spark_reason="$(awk -F= '$1=="reason_code" {v=$2} END {print substr(v,1,160)}' "$_spark_output")"
                _spark_summary="$(awk -F= '$1=="summary" {sub(/^[^=]*=/, ""); v=$0} END {print substr(v,1,240)}' "$_spark_output" | tr '\r\n' '  ')"
                case "$_spark_confidence" in high|medium|low) ;; *) _spark_confidence=low ;; esac
                case "$_spark_reason" in ''|*[!a-z0-9-]*) _spark_reason=spark-monitor-triage ;; esac
                case "$_spark_decision" in continue|inspect|interrupt-candidate|uncertain)
                    echo "decision=${_spark_decision}"
                    echo "confidence=${_spark_confidence:-low}"
                    echo "reason_code=${_spark_reason:-spark-monitor-triage}"
                    echo "summary=${_spark_summary:-Spark compressed the bounded local monitor snapshot.}"
                    echo "triage_source=spark"
                    echo "compression_source=spark"
                    echo "raw_evidence_forwarded=no"
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
            echo "compression_source=local-bounded"
            echo "raw_evidence_forwarded=no"
            if [ "$_use_spark" -eq 1 ]; then
                echo "spark_status=unavailable-or-invalid"
            elif [ "$SPARK_MODE" = "off" ]; then
                echo "spark_status=disabled"
            else
                echo "spark_status=not-needed"
            fi
        fi
        ;;
    *)
        echo "Error: action must be wait or decision." >&2
        usage
        exit 1
        ;;
esac
