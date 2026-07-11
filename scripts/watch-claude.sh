#!/usr/bin/env bash
# watch-claude.sh  -  Stream compact Claude dispatch progress for CLI observers.
#
# Usage: bash ai/watch-claude.sh [claude-<timestamp>] [--interval seconds] [--lines count] [--once] [--details] [--stale-after seconds] [--wait-profile small|medium|large] [--startup-grace seconds] [--interrupt-after seconds] [--escalation-confirmations count]

set -euo pipefail

# Git for Windows can be launched through bin/bash.exe without the usual Unix tool PATH.
PATH="/usr/bin:/bin:/mingw64/bin:${PATH}"
export PATH

INTERVAL=5
TAIL_LINES=24
ONCE=0
DETAILS=0
PLAIN=0
STALE_AFTER=120
STALE_AFTER_EXPLICIT=0
WAIT_PROFILE="${CLAUDE_CODE_WAIT_PROFILE:-medium}"
STARTUP_GRACE=""
INTERRUPT_AFTER=""
ESCALATION_CONFIRMATIONS="${CLAUDE_CODE_MONITOR_ESCALATION_CONFIRMATIONS:-3}"
TASK_REF=""

while [ $# -gt 0 ]; do
    case "$1" in
        --interval)
            shift
            INTERVAL="${1:-}"
            ;;
        --lines)
            shift
            TAIL_LINES="${1:-}"
            ;;
        --once)
            ONCE=1
            ;;
        --details)
            DETAILS=1
            ;;
        --plain)
            PLAIN=1
            ;;
        --stale-after)
            shift
            STALE_AFTER="${1:-}"
            STALE_AFTER_EXPLICIT=1
            ;;
        --wait-profile)
            shift
            WAIT_PROFILE="${1:-}"
            ;;
        --startup-grace)
            shift
            STARTUP_GRACE="${1:-}"
            ;;
        --interrupt-after)
            shift
            INTERRUPT_AFTER="${1:-}"
            ;;
        --escalation-confirmations)
            shift
            ESCALATION_CONFIRMATIONS="${1:-}"
            ;;
        -h|--help)
            echo "Usage: $0 [claude-<timestamp>] [--interval seconds] [--lines count] [--once] [--details] [--plain] [--stale-after seconds] [--wait-profile small|medium|large] [--startup-grace seconds] [--interrupt-after seconds] [--escalation-confirmations count]"
            exit 0
            ;;
        *)
            if [ -n "$TASK_REF" ]; then
                echo "Error: unexpected argument: $1" >&2
                exit 1
            fi
            TASK_REF="$1"
            ;;
    esac
    shift || true
done

case "$WAIT_PROFILE" in
    small)
        DEFAULT_STARTUP_GRACE=30
        DEFAULT_STALE_AFTER=60
        DEFAULT_INTERRUPT_AFTER=240
        ;;
    medium)
        DEFAULT_STARTUP_GRACE=60
        DEFAULT_STALE_AFTER=120
        DEFAULT_INTERRUPT_AFTER=600
        ;;
    large)
        DEFAULT_STARTUP_GRACE=120
        DEFAULT_STALE_AFTER=300
        DEFAULT_INTERRUPT_AFTER=1200
        ;;
    *)
        echo "Error: --wait-profile must be one of: small, medium, large." >&2
        exit 1
        ;;
esac

if [ "$STALE_AFTER_EXPLICIT" -eq 0 ]; then
    STALE_AFTER="$DEFAULT_STALE_AFTER"
fi
STARTUP_GRACE="${STARTUP_GRACE:-$DEFAULT_STARTUP_GRACE}"
INTERRUPT_AFTER="${INTERRUPT_AFTER:-$DEFAULT_INTERRUPT_AFTER}"

for value_name in INTERVAL TAIL_LINES STALE_AFTER STARTUP_GRACE INTERRUPT_AFTER ESCALATION_CONFIRMATIONS; do
    value="${!value_name}"
    case "$value" in
        ''|*[!0-9]*) echo "Error: ${value_name} must be a non-negative integer." >&2; exit 1 ;;
    esac
done
if [ "$INTERVAL" -eq 0 ] || [ "$TAIL_LINES" -eq 0 ]; then
    echo "Error: --interval and --lines must be greater than 0." >&2
    exit 1
fi
if [ "$ESCALATION_CONFIRMATIONS" -eq 0 ]; then
    echo "Error: --escalation-confirmations must be greater than 0." >&2
    exit 1
fi

resolve_repo() {
    git rev-parse --show-toplevel 2>/dev/null || pwd
}

file_size() {
    local file="$1"
    if [ -f "$file" ]; then
        wc -c < "$file" 2>/dev/null | tr -d '[:space:]' || echo 0
    else
        echo 0
    fi
}

count_matches() {
    local pattern="$1"
    local file="$2"
    if [ ! -f "$file" ]; then
        echo 0
        return
    fi
    grep -cE "$pattern" "$file" 2>/dev/null | tr -d '[:space:]' || true
}

is_running() {
    local pid_file="$1"
    if [ ! -f "$pid_file" ]; then
        return 1
    fi
    local pid
    pid="$(tr -d '[:space:]' < "$pid_file")"
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

role_state() {
    local pid_file="$1"
    if [ ! -f "$pid_file" ]; then
        echo "missing"
        return
    fi
    local pid
    pid="$(tr -d '[:space:]' < "$pid_file")"
    if [ -z "$pid" ]; then
        echo "missing"
    elif kill -0 "$pid" 2>/dev/null; then
        echo "running"
    else
        echo "not-running"
    fi
}

# Loop exit check: stay alive as long as any role (dispatcher/Claude/checker) is active.
# Falls back to legacy .pid file when new PID files don't exist.
any_role_is_running() {
    if [ -f "$DISPATCHER_PID_FILE" ] && is_running "$DISPATCHER_PID_FILE"; then return 0; fi
    if [ -f "$CLAUDE_PID_FILE" ]; then
        if is_running "$CLAUDE_PID_FILE"; then return 0; fi
    elif is_running "$PID_FILE"; then
        return 0
    fi
    if [ -f "$CHECKER_PID_FILE" ] && is_running "$CHECKER_PID_FILE"; then return 0; fi
    return 1
}

field_from_line() {
    local line="$1"
    local key="$2"
    echo "$line" | sed -n "s/.*${key}=\([0-9][0-9]*\).*/\1/p"
}

worktree_change_count() {
    if [ ! -d "$WORKTREE_DIR" ]; then
        echo 0
        return
    fi
    {
        git -c "safe.directory=$WORKTREE_DIR" -C "$WORKTREE_DIR" status --porcelain --untracked-files=all 2>/dev/null \
            | grep -v -E '^(.. )?(TASK_CARD|CLAUDE_PROMPT|CLAUDE_REPORT|CLAUDE_PROGRESS)(\.md)?$' || true
    } | wc -l 2>/dev/null | tr -d '[:space:]' || echo 0
}

partial_diffstat() {
    if [ ! -d "$WORKTREE_DIR" ]; then
        echo "(worktree unavailable)"
        return
    fi
    local out
    out="$(git -c "safe.directory=$WORKTREE_DIR" -C "$WORKTREE_DIR" diff --shortstat 2>/dev/null || true)"
    if [ -z "$out" ]; then
        out="$(git -c "safe.directory=$WORKTREE_DIR" -C "$WORKTREE_DIR" status --short --untracked-files=all 2>/dev/null \
            | grep -v -E ' (TASK_CARD|CLAUDE_PROMPT|CLAUDE_REPORT|CLAUDE_PROGRESS)(\.md)?$' \
            | head -5 || true)"
    fi
    if [ -z "$out" ]; then
        echo "(no implementation changes detected)"
    else
        echo "$out"
    fi
}

partial_risk_summary() {
    if [ ! -d "$WORKTREE_DIR" ]; then
        echo "files=0 tests_touched=0 high_risk=0"
        return
    fi
    local files file_count test_count high_risk_count
    files="$(git -c "safe.directory=$WORKTREE_DIR" -C "$WORKTREE_DIR" status --porcelain --untracked-files=all 2>/dev/null \
        | grep -v -E ' (TASK_CARD|CLAUDE_PROMPT|CLAUDE_REPORT|CLAUDE_PROGRESS)(\.md)?$' \
        | sed 's/^...//' || true)"
    if [ -z "$files" ]; then
        echo "files=0 tests_touched=0 high_risk=0"
        return
    fi
    file_count="$(printf '%s\n' "$files" | sed '/^$/d' | wc -l 2>/dev/null | tr -d '[:space:]')"
    test_count="$(printf '%s\n' "$files" | grep -Eic '(^|/)(test|tests|spec|__tests__)/|(_test|\.test|\.spec)\.' || true)"
    high_risk_count="$(printf '%s\n' "$files" | grep -Eic '(^|/)(migrations?|infra|deploy|auth|security|billing|payment)(/|$)|package-lock\.json|pnpm-lock\.yaml|yarn\.lock|Cargo\.lock|go\.sum|requirements.*\.txt' || true)"
    echo "files=${file_count:-0} tests_touched=${test_count:-0} high_risk=${high_risk_count:-0}"
}

progress_bar() {
    local percent="$1"
    local width=20
    local filled=$((percent * width / 100))
    local empty=$((width - filled))
    local bar=""
    local i=0
    while [ "$i" -lt "$filled" ]; do bar="${bar}#"; i=$((i + 1)); done
    i=0
    while [ "$i" -lt "$empty" ]; do bar="${bar}-"; i=$((i + 1)); done
    printf '[%s] %s%%' "$bar" "$percent"
}

REPO_ROOT="$(resolve_repo)"
WORKTREE_ROOT="${REPO_ROOT}/.worktrees"

if [ -z "$TASK_REF" ]; then
    latest="$(find "$WORKTREE_ROOT" -maxdepth 1 -type f -name 'claude-*.progress.log' 2>/dev/null | sort | tail -1 || true)"
    if [ -z "$latest" ]; then
        echo "No Claude progress logs found under $WORKTREE_ROOT" >&2
        exit 1
    fi
    TASK_ID="$(basename "$latest" .progress.log)"
elif [ -d "$TASK_REF" ]; then
    TASK_ID="$(basename "$TASK_REF")"
else
    TASK_ID="$(basename "$TASK_REF")"
    TASK_ID="${TASK_ID%.progress.log}"
    TASK_ID="${TASK_ID%.claude-progress.md}"
    TASK_ID="${TASK_ID%.pid}"
fi

PREFIX="${WORKTREE_ROOT}/${TASK_ID}"
WORKTREE_DIR="${WORKTREE_ROOT}/${TASK_ID}"
PID_FILE="${PREFIX}.pid"
DISPATCHER_PID_FILE="${PREFIX}.dispatcher.pid"
CLAUDE_PID_FILE="${PREFIX}.claude.pid"
CHECKER_PID_FILE="${PREFIX}.checker.pid"
PROGRESS_FILE="${PREFIX}.progress.log"
ARCHIVED_CLAUDE_PROGRESS_FILE="${PREFIX}.claude-progress.md"
LIVE_CLAUDE_PROGRESS_FILE="${WORKTREE_DIR}/CLAUDE_PROGRESS.md"
STATUS_FILE="${PREFIX}.status.txt"
NETWORK_FILE="${PREFIX}.network.log"
REPORT_FILE="${PREFIX}.report.md"
LIVE_REPORT_FILE="${WORKTREE_DIR}/CLAUDE_REPORT.md"
RESULT_FILE="${PREFIX}.result.json"
DIFF_FILE="${PREFIX}.diff"
SEEDED_REPORT_MARKER="AI-CODING-WORKFLOW:DISPATCH-SEEDED-REPORT"
SEEDED_PROGRESS_MARKER="AI-CODING-WORKFLOW:DISPATCH-SEEDED-PROGRESS"
FALLBACK_REPORT_MARKER="AI-CODING-WORKFLOW:DISPATCH-FALLBACK-REPORT"

last_digest=""
printed_header=0
monitor_suspect_count=0

select_claude_progress_file() {
    if [ -f "$LIVE_CLAUDE_PROGRESS_FILE" ]; then
        echo "$LIVE_CLAUDE_PROGRESS_FILE"
    else
        echo "$ARCHIVED_CLAUDE_PROGRESS_FILE"
    fi
}

select_report_file() {
    if [ -f "$LIVE_REPORT_FILE" ]; then
        echo "$LIVE_REPORT_FILE"
    else
        echo "$REPORT_FILE"
    fi
}

file_contains() {
    local file="$1"
    local pattern="$2"
    [ -f "$file" ] && grep -qE "$pattern" "$file" 2>/dev/null
}

valid_report_file() {
    local file="$1"
    [ -s "$file" ] || return 1
    if file_contains "$file" "$SEEDED_REPORT_MARKER|$FALLBACK_REPORT_MARKER"; then
        return 1
    fi
    if file_contains "$file" "Dispatcher-created draft|fallback report was generated|did not produce a valid Claude-owned CLAUDE_REPORT.md|did not produce a Claude-owned CLAUDE_REPORT.md"; then
        return 1
    fi
    return 0
}

acknowledgement_only_evidence() {
    local progress_file="$1"
    local report_file="$2"
    local changes="$3"
    local valid_report="$4"
    [ "$changes" -eq 0 ] || return 1
    [ "$valid_report" -eq 0 ] || return 1
    {
        [ -f "$progress_file" ] && cat "$progress_file"
        [ -f "$report_file" ] && cat "$report_file"
    } | grep -Eiq 'Direction / Boundary Acknowledgement|My understanding:|Planned scope:|Recommendation:[[:space:]]*(proceed|narrow|split|stop-and-report|stop)' 2>/dev/null
}

evidence_state() {
    local changes="$1"
    local progress_file="$2"
    local report_file="$3"
    local valid_report=0
    if valid_report_file "$report_file"; then
        valid_report=1
    fi
    if [ "$changes" -gt 0 ] && [ "$valid_report" -eq 1 ]; then
        echo "diff + valid report"
    elif [ "$changes" -gt 0 ]; then
        echo "diff without report"
    elif acknowledgement_only_evidence "$progress_file" "$report_file" "$changes" "$valid_report"; then
        echo "acknowledgement only"
    elif [ -f "$report_file" ] && file_contains "$report_file" "$SEEDED_REPORT_MARKER"; then
        echo "seeded report only"
    elif [ "$valid_report" -eq 1 ]; then
        echo "valid report without diff"
    else
        echo "no valid report"
    fi
}

last_dispatch_line() {
    if [ -f "$PROGRESS_FILE" ]; then
        grep -E 'Claude still running|Claude completed|Claude exited|Claude finished|Stopping Claude' "$PROGRESS_FILE" | tail -1 || true
    fi
}

latest_network_summary() {
    if [ -f "$NETWORK_FILE" ]; then
        local summary
        summary="$(grep -E '^Summary: ' "$NETWORK_FILE" | tail -1 | sed 's/^Summary: //' || true)"
        if [ -n "$summary" ]; then
            echo "$summary"
            return
        fi
        if grep -q 'Network monitoring is metadata-only' "$NETWORK_FILE" 2>/dev/null; then
            echo "network_monitor=on no_snapshot"
            return
        fi
    fi
    echo "network_monitor=off_or_missing"
}

current_milestone() {
    local file="$1"
    if [ ! -f "$file" ]; then
        echo "No Claude self-reported milestone yet."
        return
    fi
    if file_contains "$file" "$SEEDED_PROGRESS_MARKER" && ! grep -qE '^- \[[xX]\]' "$file" 2>/dev/null; then
        local phase
        phase="$(sed -n 's/^- Current Phase:[[:space:]]*//p' "$file" | tail -1 || true)"
        if [ -z "$phase" ] || [ "$phase" = "dispatch-started" ]; then
            echo "seeded progress only"
            return
        fi
    fi
    local line
    line="$(grep -E '^- \[[xX]\]' "$file" | tail -1 || true)"
    if [ -z "$line" ]; then
        line="$(grep -E '^- Current Phase:' "$file" | tail -1 || true)"
    fi
    if [ -z "$line" ]; then
        line="$(grep -E '^- ' "$file" | grep -vE '^- \[[ xX]\]' | tail -1 || true)"
    fi
    if [ -z "$line" ]; then
        line="$(grep -v -E '^#|^$' "$file" | tail -1 || true)"
    fi
    if [ -z "$line" ]; then
        echo "Progress file exists, but no milestone text was found."
    else
        echo "$line" | sed 's/^- \[[xX ]\] *//; s/^- Current Phase:[[:space:]]*//; s/^- *//'
    fi
}

progress_percent() {
    local file="$1"
    if [ ! -f "$file" ]; then
        echo 0
        return
    fi
    local total done
    total="$(count_matches '^- \[[ xX]\]' "$file")"
    done="$(count_matches '^- \[[xX]\]' "$file")"
    total="${total:-0}"
    done="${done:-0}"
    if [ "$total" -gt 0 ]; then
        echo $((done * 100 / total))
    elif [ "$(file_size "$file")" -gt 0 ]; then
        echo 10
    else
        echo 0
    fi
}

stuck_reason() {
    local running="$1"
    local quiet="$2"
    local result_bytes="$3"
    local status_bytes="$4"
    local report_bytes="$5"
    local claude_progress_bytes="$6"
    local claude_progress_source="$7"
    local worktree_changes="$8"

    if [ "$running" = "no" ]; then
        if [ "$result_bytes" -gt 0 ]; then
            echo "complete-or-exited: Claude process is not running and result JSON exists."
        else
            echo "exited-without-result: inspect status/report artifacts."
        fi
        return
    fi

    if [ "$quiet" -lt "$STALE_AFTER" ]; then
        echo "none: progress changed recently or stale threshold not reached."
    elif [ "$worktree_changes" -gt 0 ]; then
        echo "partial-implementation-present: worktree has changes; review partial diff and continue waiting if it aligns with the plan."
    elif [ "$claude_progress_bytes" -eq 0 ] && [ "$result_bytes" -eq 0 ] && [ "$status_bytes" -eq 0 ]; then
        echo "startup/network/auth wait suspected: no result, stderr, or progress after ${quiet}s."
    elif [ "$claude_progress_bytes" -gt 0 ] && [ -f "$claude_progress_source" ]; then
        echo "long-running current milestone: Claude progress file has not changed for ${quiet}s."
    elif [ "$status_bytes" -gt 0 ]; then
        echo "stderr changed earlier; inspect status tail for tool or CLI errors."
    else
        echo "no artifact growth for ${quiet}s; process may be waiting on Claude CLI/model/tool execution."
    fi
}

recommended_action() {
    local running="$1"
    local elapsed="$2"
    local quiet="$3"
    local result_bytes="$4"
    local status_bytes="$5"
    local claude_progress_bytes="$6"
    local worktree_changes="$7"
    local evidence="$8"

    if [ "$running" = "no" ]; then
        if [ "$evidence" = "diff + valid report" ] || [ "$evidence" = "valid report without diff" ]; then
            echo "COMPLETE"
        elif [ "$evidence" = "diff without report" ]; then
            echo "REVIEW_DIFF_WITH_EVIDENCE_GAP"
        elif [ "$evidence" = "acknowledgement only" ]; then
            echo "ACK_ONLY_RETRY_OR_TAKEOVER"
        elif [ "$result_bytes" -gt 0 ]; then
            echo "INSPECT_ARTIFACTS_NO_VALID_REPORT"
        else
            echo "INSPECT_ARTIFACTS"
        fi
        return
    fi

    if [ "$elapsed" -lt "$STARTUP_GRACE" ]; then
        echo "CONTINUE_WAITING"
    elif [ "$quiet" -lt "$STALE_AFTER" ]; then
        echo "CONTINUE_WAITING"
    elif [ "$worktree_changes" -gt 0 ] && [ "$quiet" -lt "$INTERRUPT_AFTER" ]; then
        echo "REVIEW_PARTIAL_DIFF"
    elif [ "$worktree_changes" -gt 0 ]; then
        echo "CONSIDER_INTERRUPT"
    elif [ "$result_bytes" -eq 0 ] && [ "$status_bytes" -eq 0 ] && [ "$claude_progress_bytes" -eq 0 ]; then
        echo "LIKELY_STUCK"
    elif [ "$quiet" -ge "$INTERRUPT_AFTER" ]; then
        echo "LIKELY_STUCK"
    else
        echo "CONSIDER_INTERRUPT"
    fi
}

monitor_suspect_snapshot() {
    local running="$1"
    local elapsed="$2"
    local quiet="$3"
    local worktree_changes="$4"
    local action="$5"

    [ "$running" = "yes" ] || return 1
    [ "$elapsed" -ge "$STARTUP_GRACE" ] || return 1
    [ "$quiet" -ge "$STALE_AFTER" ] || return 1
    case "$action" in
        CONTINUE_WAITING) return 1 ;;
    esac
    if [ "$worktree_changes" -gt 0 ] && [ "$quiet" -lt "$INTERRUPT_AFTER" ]; then
        return 1
    fi
    return 0
}

monitor_plan() {
    local running="$1"
    local elapsed="$2"
    local quiet="$3"
    local worktree_changes="$4"
    local action="$5"
    local suspect_count="$6"

    if [ "$running" != "yes" ]; then
        echo "L0 exited: inspect artifacts; do not kill"
    elif [ "$elapsed" -lt "$STARTUP_GRACE" ]; then
        echo "L0 startup: heartbeat/progress only"
    elif [ "$quiet" -lt "$STALE_AFTER" ]; then
        echo "L0 active: compact heartbeat/progress only"
    elif [ "$worktree_changes" -gt 0 ] && [ "$quiet" -lt "$INTERRUPT_AFTER" ]; then
        echo "L1 partial diff: review direction; avoid interrupt when aligned"
    elif [ "$suspect_count" -lt "$ESCALATION_CONFIRMATIONS" ]; then
        echo "L1 repeat compact watch: ${suspect_count}/${ESCALATION_CONFIRMATIONS} suspect confirmations"
    elif [ "$quiet" -lt "$INTERRUPT_AFTER" ]; then
        echo "L2 details allowed: inspect progress/status tails; continue waiting unless corroborated"
    else
        echo "L3 corroborate before stop: require status/progress/diff/network evidence, then decide"
    fi
}

monitor_level() {
    local running="$1"
    local elapsed="$2"
    local quiet="$3"
    local worktree_changes="$4"
    local suspect_count="$5"

    if [ "$running" != "yes" ]; then
        echo "L0"
    elif [ "$elapsed" -lt "$STARTUP_GRACE" ] || [ "$quiet" -lt "$STALE_AFTER" ]; then
        echo "L0"
    elif [ "$worktree_changes" -gt 0 ] && [ "$quiet" -lt "$INTERRUPT_AFTER" ]; then
        echo "L1"
    elif [ "$suspect_count" -lt "$ESCALATION_CONFIRMATIONS" ] || [ "$quiet" -lt "$INTERRUPT_AFTER" ]; then
        echo "L2"
    else
        echo "L3"
    fi
}

print_header() {
    if [ "$printed_header" -eq 0 ]; then
        if [ "$PLAIN" -eq 1 ]; then
            echo "# Claude Watch"
            echo "Task ID: $TASK_ID"
            echo "Mode: plain compact (use --details to show full progress tails)"
            echo "Wait policy: profile=${WAIT_PROFILE} startup_grace=${STARTUP_GRACE}s stale_after=${STALE_AFTER}s interrupt_after=${INTERRUPT_AFTER}s escalation_confirmations=${ESCALATION_CONFIRMATIONS}"
        else
            echo "============================================================"
            echo " CLAUDE CODE WATCH"
            echo " task: ${TASK_ID}"
            echo " mode: status panel (use --details for full tails, --plain for compact text)"
            echo " wait policy: profile=${WAIT_PROFILE} startup_grace=${STARTUP_GRACE}s stale_after=${STALE_AFTER}s interrupt_after=${INTERRUPT_AFTER}s escalation_confirmations=${ESCALATION_CONFIRMATIONS}"
            echo "============================================================"
        fi
        echo ""
        printed_header=1
    fi
}

print_details_if_needed() {
    local show_details="$1"
    local claude_progress_source="$2"
    if [ "$show_details" -ne 1 ]; then
        return
    fi
    echo ""
    echo "## Dispatch Progress Tail"
    if [ -f "$PROGRESS_FILE" ]; then
        tail -n "$TAIL_LINES" "$PROGRESS_FILE"
    else
        echo "(none)"
    fi
    echo ""
    echo "## Claude Progress Tail"
    if [ -f "$claude_progress_source" ]; then
        echo "Source: $claude_progress_source"
        tail -n "$TAIL_LINES" "$claude_progress_source"
    else
        echo "(none)"
    fi
    if [ -f "$STATUS_FILE" ] && [ "$(file_size "$STATUS_FILE")" -gt 0 ]; then
        echo ""
        echo "## Status Tail"
        tail -n "$TAIL_LINES" "$STATUS_FILE"
    fi
    if [ -f "$NETWORK_FILE" ] && [ "$(file_size "$NETWORK_FILE")" -gt 0 ]; then
        echo ""
        echo "## Network Tail"
        tail -n "$TAIL_LINES" "$NETWORK_FILE"
    fi
    if [ -d "$WORKTREE_DIR" ]; then
        echo ""
        echo "## Partial Worktree"
        echo "Change count: $(worktree_change_count)"
        echo "Diffstat/status:"
        partial_diffstat
    fi
}

print_snapshot() {
    print_header

    local claude_progress_source report_source running last_line elapsed quiet result_bytes status_bytes network_bytes report_bytes claude_progress_bytes diff_bytes worktree_changes partial_summary risk_summary network_summary percent bar milestone reason action evidence monitor level digest show_details dispatcher_running claude_running checker_running
    claude_progress_source="$(select_claude_progress_file)"
    report_source="$(select_report_file)"

    # Spec item 3/4: role-aware running state
    dispatcher_running="$(role_state "$DISPATCHER_PID_FILE")"
    if [ -f "$CLAUDE_PID_FILE" ]; then
        claude_running="$(role_state "$CLAUDE_PID_FILE")"
    elif is_running "$PID_FILE"; then
        claude_running="running"
    else
        claude_running="not-running"
    fi
    checker_running="$(role_state "$CHECKER_PID_FILE")"
    # Overall: Claude or Checker active (dispatcher alone = finalizing, not running)
    if [ "$claude_running" = "running" ] || [ "$checker_running" = "running" ]; then
        running="yes"
    else
        running="no"
    fi
    last_line="$(last_dispatch_line)"
    elapsed="$(field_from_line "$last_line" "elapsed_seconds")"; elapsed="${elapsed:-0}"
    quiet="$(field_from_line "$last_line" "quiet_seconds")"; quiet="${quiet:-0}"
    result_bytes="$(file_size "$RESULT_FILE")"
    status_bytes="$(file_size "$STATUS_FILE")"
    network_bytes="$(file_size "$NETWORK_FILE")"
    report_bytes="$(file_size "$report_source")"
    claude_progress_bytes="$(file_size "$claude_progress_source")"
    diff_bytes="$(file_size "$DIFF_FILE")"
    worktree_changes="$(field_from_line "$last_line" "worktree_changes")"
    worktree_changes="${worktree_changes:-$(worktree_change_count)}"
    partial_summary="$(partial_diffstat | head -1)"
    risk_summary="$(partial_risk_summary)"
    network_summary="$(latest_network_summary)"
    percent="$(progress_percent "$claude_progress_source")"
    bar="$(progress_bar "$percent")"
    milestone="$(current_milestone "$claude_progress_source")"
    evidence="$(evidence_state "$worktree_changes" "$claude_progress_source" "$report_source")"
    reason="$(stuck_reason "$running" "$quiet" "$result_bytes" "$status_bytes" "$report_bytes" "$claude_progress_bytes" "$claude_progress_source" "$worktree_changes")"
    action="$(recommended_action "$running" "$elapsed" "$quiet" "$result_bytes" "$status_bytes" "$claude_progress_bytes" "$worktree_changes" "$evidence")"
    if monitor_suspect_snapshot "$running" "$elapsed" "$quiet" "$worktree_changes" "$action"; then
        monitor_suspect_count=$((monitor_suspect_count + 1))
    else
        monitor_suspect_count=0
    fi
    monitor="$(monitor_plan "$running" "$elapsed" "$quiet" "$worktree_changes" "$action" "$monitor_suspect_count")"
    level="$(monitor_level "$running" "$elapsed" "$quiet" "$worktree_changes" "$monitor_suspect_count")"

    digest="${running}|${elapsed}|${quiet}|${result_bytes}|${status_bytes}|${network_bytes}|${report_bytes}|${claude_progress_bytes}|${worktree_changes}|${partial_summary}|${risk_summary}|${network_summary}|${percent}|${milestone}|${evidence}|${reason}|${action}|${monitor}|${level}|${monitor_suspect_count}|${dispatcher_running}|${claude_running}|${checker_running}"
    if [ "$digest" != "$last_digest" ]; then
        if [ "$running" = "yes" ]; then
            if [ "$checker_running" = "running" ]; then
                state="CHECKER"
            else
                state="RUNNING"
            fi
        elif [ "$evidence" = "acknowledgement only" ]; then
            state="ACK_ONLY"
        elif [ "$evidence" = "diff without report" ]; then
            state="EVIDENCE_GAP"
        elif [ "$evidence" = "seeded report only" ] || [ "$evidence" = "no valid report" ]; then
            state="NO_VALID_REPORT"
        elif [ "$result_bytes" -gt 0 ]; then
            state="COMPLETE"
        else
            state="STOPPED"
        fi

        if [ "$PLAIN" -eq 1 ]; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] state=${state} elapsed=${elapsed}s quiet=${quiet}s ${bar}"
            echo "current: ${milestone}"
            echo "evidence: ${evidence}"
            echo "artifacts: result=${result_bytes}B status=${status_bytes}B network=${network_bytes}B report=${report_bytes}B progress=${claude_progress_bytes}B diff=${diff_bytes}B changes=${worktree_changes}"
            echo "partial: ${partial_summary}"
            echo "risk: ${risk_summary}"
            echo "network: ${network_summary}"
            echo "monitor: ${monitor}"
            echo "machine: monitor_level=${level} action=${action} evidence_state=\"${evidence}\" running=${running} overall_running=${running} dispatcher=${dispatcher_running} claude=${claude_running} checker=${checker_running} suspect_count=${monitor_suspect_count} escalation_confirmations=${ESCALATION_CONFIRMATIONS} elapsed_seconds=${elapsed} quiet_seconds=${quiet} worktree_changes=${worktree_changes} network=\"${network_summary}\""
            echo "action: ${action}"
            echo "analysis: ${reason}"
            echo ""
        else
            echo "------------------------------------------------------------"
            printf ' STATUS   : %s\n' "$state"
            printf ' TIME     : elapsed=%ss  quiet=%ss\n' "$elapsed" "$quiet"
            printf ' PROGRESS : %s\n' "$bar"
            printf ' CURRENT  : %s\n' "$milestone"
            printf ' EVIDENCE : %s\n' "$evidence"
            printf ' FILES    : result=%sB  status=%sB  network=%sB  report=%sB  progress=%sB  diff=%sB  changes=%s\n' "$result_bytes" "$status_bytes" "$network_bytes" "$report_bytes" "$claude_progress_bytes" "$diff_bytes" "$worktree_changes"
            printf ' PARTIAL  : %s\n' "$partial_summary"
            printf ' RISK     : %s\n' "$risk_summary"
            printf ' NETWORK  : %s\n' "$network_summary"
            printf ' MONITOR  : %s\n' "$monitor"
            printf ' MACHINE  : monitor_level=%s action=%s evidence_state="%s" running=%s overall_running=%s dispatcher=%s claude=%s checker=%s suspect_count=%s escalation_confirmations=%s elapsed_seconds=%s quiet_seconds=%s worktree_changes=%s network="%s"\n' "$level" "$action" "$evidence" "$running" "$running" "$dispatcher_running" "$claude_running" "$checker_running" "$monitor_suspect_count" "$ESCALATION_CONFIRMATIONS" "$elapsed" "$quiet" "$worktree_changes" "$network_summary"
            printf ' ACTION   : %s\n' "$action"
            if [ "$state" = "COMPLETE" ]; then
                printf ' RESULT   : %s\n' "$reason"
            elif echo "$reason" | grep -q '^none:'; then
                printf ' ANALYSIS : %s\n' "$reason"
            else
                printf ' ATTENTION: %s\n' "$reason"
            fi
            echo "------------------------------------------------------------"
            echo ""
        fi
        last_digest="$digest"
    else
        # Unchanged terminal state: still emit a machine snapshot line
        if [ "$PLAIN" -eq 1 ]; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] machine: monitor_level=${level} action=${action} evidence_state=\"${evidence}\" running=${running} overall_running=${running} dispatcher=${dispatcher_running} claude=${claude_running} checker=${checker_running} suspect_count=${monitor_suspect_count} elapsed_seconds=${elapsed} quiet_seconds=${quiet}"
        else
            printf ' MACHINE  : monitor_level=%s action=%s evidence_state="%s" running=%s overall_running=%s dispatcher=%s claude=%s checker=%s suspect_count=%s elapsed_seconds=%s quiet_seconds=%s\n' "$level" "$action" "$evidence" "$running" "$running" "$dispatcher_running" "$claude_running" "$checker_running" "$monitor_suspect_count" "$elapsed" "$quiet"
        fi
    fi

    show_details=0
    if [ "$DETAILS" -eq 1 ]; then
        show_details=1
    elif [ "$running" = "yes" ] && [ "$monitor_suspect_count" -ge "$ESCALATION_CONFIRMATIONS" ]; then
        show_details=1
    fi
    print_details_if_needed "$show_details" "$claude_progress_source"
}

while true; do
    print_snapshot
    if [ "$ONCE" -eq 1 ]; then
        break
    fi
    if ! any_role_is_running; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Claude process is not running; watch complete."
        break
    fi
    sleep "$INTERVAL"
done
