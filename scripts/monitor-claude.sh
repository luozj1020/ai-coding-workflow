#!/usr/bin/env bash
# Run Claude monitoring as a local background process and persist only compact
# material events. Controllers should read the tail once instead of polling.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATH="/usr/bin:/bin:/mingw64/bin:${PATH}"
export PATH

usage() {
    echo "Usage: $0 start|status|tail|stop <claude-task-id> [--interval seconds] [--lines count]" >&2
}

if [ $# -lt 2 ]; then
    usage
    exit 1
fi

ACTION="$1"
TASK_ID="$(basename "$2")"
TASK_ID="${TASK_ID%.pid}"
INTERVAL=5
LINES=20
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
        *)
            echo "Error: unknown argument: $1" >&2
            usage
            exit 1
            ;;
    esac
    shift
done

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
WORKTREE_ROOT="${REPO_ROOT}/.worktrees"
WATCH_SCRIPT="${SCRIPT_DIR}/watch-claude.sh"
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
        nohup bash "$WATCH_SCRIPT" "$TASK_ID" --plain --interval "$INTERVAL" \
            > "$EVENT_LOG" 2>&1 < /dev/null &
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
        echo "Error: action must be start, status, tail, or stop." >&2
        usage
        exit 1
        ;;
esac
