#!/usr/bin/env bash
# status-claude.sh  -  Show status for a Claude Code dispatch run.
#
# Usage: bash ai/status-claude.sh [claude-<timestamp>|/path/to/worktree]

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROCESS_STATE_HELPER="${SCRIPT_DIR}/claude-process-state.py"

# Git for Windows can be launched through bin/bash.exe without the usual Unix tool PATH.
# Prepending these paths is harmless on Unix and makes helper scripts stable on Windows.
PATH="/usr/bin:/bin:/mingw64/bin:${PATH}"
export PATH

resolve_repo() {
    git rev-parse --show-toplevel 2>/dev/null || pwd
}

REPO_ROOT="$(resolve_repo)"
WORKTREE_ROOT="${REPO_ROOT}/.worktrees"
TASK_REF="${1:-}"
WAIT_PROFILE="${CLAUDE_CODE_WAIT_PROFILE:-medium}"

case "$WAIT_PROFILE" in
    small)
        STARTUP_GRACE=30
        STALE_AFTER=60
        INTERRUPT_AFTER=240
        ;;
    medium)
        STARTUP_GRACE=60
        STALE_AFTER=120
        INTERRUPT_AFTER=600
        ;;
    large)
        STARTUP_GRACE=120
        STALE_AFTER=300
        INTERRUPT_AFTER=1200
        ;;
    *)
        WAIT_PROFILE="medium"
        STARTUP_GRACE=60
        STALE_AFTER=120
        INTERRUPT_AFTER=600
        ;;
esac

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
    TASK_ID="${TASK_ID%.pid}"
fi

PREFIX="${WORKTREE_ROOT}/${TASK_ID}"
WORKTREE_DIR="${WORKTREE_ROOT}/${TASK_ID}"
PID_FILE="${PREFIX}.pid"
DISPATCHER_PID_FILE="${PREFIX}.dispatcher.pid"
CLAUDE_PID_FILE="${PREFIX}.claude.pid"
CHECKER_PID_FILE="${PREFIX}.checker.pid"
PROGRESS_FILE="${PREFIX}.progress.log"
RESULT_FILE="${PREFIX}.result.json"
STATUS_FILE="${PREFIX}.status.txt"
NETWORK_FILE="${PREFIX}.network.log"
DIFF_FILE="${PREFIX}.diff"
REPORT_FILE="${PREFIX}.report.md"
CLAUDE_PROGRESS_FILE="${PREFIX}.claude-progress.md"
WORKTREE_STATUS_FILE="${PREFIX}.worktree-status.txt"
RUNTIME_JSON="${PREFIX}.runtime.json"
SEEDED_REPORT_MARKER="AI-CODING-WORKFLOW:DISPATCH-SEEDED-REPORT"
FALLBACK_REPORT_MARKER="AI-CODING-WORKFLOW:DISPATCH-FALLBACK-REPORT"
RUNTIME_DIAGNOSTIC=""

# --- Spec item 2: resolve worktree from runtime identity artifact ---
# If a runtime.json exists, use its recorded worktree path (validated to be
# inside the same .worktrees/ boundary). Falls back to default TASK_ID path
# if artifact is missing, malformed, or points outside the boundary.
if [ -f "$RUNTIME_JSON" ]; then
    _rt_wt="$(sed -n 's/.*"worktree"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$RUNTIME_JSON" 2>/dev/null | head -1)"
    if [ -z "$_rt_wt" ]; then
        RUNTIME_DIAGNOSTIC="runtime.json present but worktree field missing; using task-id fallback"
    else
        case "$_rt_wt" in
            "${WORKTREE_ROOT}/"*)
                if [ "$_rt_wt" = "$WORKTREE_ROOT" ] || [ "$_rt_wt" = "$WORKTREE_ROOT/" ]; then
                    RUNTIME_DIAGNOSTIC="runtime.json worktree is .worktrees/ root; using task-id fallback"
                elif [ -d "$_rt_wt" ]; then
                    WORKTREE_DIR="$_rt_wt"
                else
                    RUNTIME_DIAGNOSTIC="runtime.json worktree directory missing: ${_rt_wt}; using task-id fallback"
                fi
                ;;
            *)
                RUNTIME_DIAGNOSTIC="runtime.json worktree outside .worktrees/ boundary: ${_rt_wt}; using task-id fallback"
                ;;
        esac
    fi
fi

# Live report/progress files depend on the resolved WORKTREE_DIR.
LIVE_REPORT_FILE="${WORKTREE_DIR}/CLAUDE_REPORT.md"
LIVE_CLAUDE_PROGRESS_FILE="${WORKTREE_DIR}/CLAUDE_PROGRESS.md"

file_size() {
    local file="$1"
    if [ -f "$file" ]; then
        wc -c < "$file" 2>/dev/null | tr -d '[:space:]' || echo 0
    else
        echo 0
    fi
}

field_from_line() {
    local line="$1"
    local key="$2"
    echo "$line" | sed -n "s/.*${key}=\([0-9][0-9]*\).*/\1/p"
}

file_contains() {
    local file="$1"
    local pattern="$2"
    [ -f "$file" ] && grep -qE "$pattern" "$file" 2>/dev/null
}

role_state() {
    local pid_file="$1"
    if [ -f "$PROCESS_STATE_HELPER" ] && command -v python3 >/dev/null 2>&1; then
        python3 "$PROCESS_STATE_HELPER" --pid-file "$pid_file" --progress-file "$PROGRESS_FILE"
    else
        if [ -f "$pid_file" ] && kill -0 "$(tr -d '[:space:]' < "$pid_file")" 2>/dev/null; then
            echo "running"
        else
            echo "not-running"
        fi
    fi
}

select_report_file() {
    if [ -f "$LIVE_REPORT_FILE" ]; then
        echo "$LIVE_REPORT_FILE"
    else
        echo "$REPORT_FILE"
    fi
}

select_claude_progress_file() {
    if [ -f "$LIVE_CLAUDE_PROGRESS_FILE" ]; then
        echo "$LIVE_CLAUDE_PROGRESS_FILE"
    else
        echo "$CLAUDE_PROGRESS_FILE"
    fi
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
            | head -20 || true)"
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

monitor_policy() {
    local running="$1"
    local elapsed="$2"
    local quiet="$3"
    local worktree_changes="$4"
    local action="$5"

    if [ "$running" != "yes" ]; then
        echo "L0 exited: inspect artifacts; no stop action applies."
    elif [ "$elapsed" -lt "$STARTUP_GRACE" ]; then
        echo "L0 startup: prefer watch heartbeat/progress; do not interrupt."
    elif [ "$quiet" -lt "$STALE_AFTER" ]; then
        echo "L0 active: compact watch is sufficient; do not run heavier diagnostics."
    elif [ "$worktree_changes" -gt 0 ] && [ "$quiet" -lt "$INTERRUPT_AFTER" ]; then
        echo "L1 partial diff: review direction; continue waiting if aligned with the task card."
    elif [ "$action" = "LIKELY_STUCK" ]; then
        echo "L3 suspected stuck: corroborate with progress, status, diff, and network evidence before considering kill."
    else
        echo "L2 diagnostic: status is advisory; prefer repeated watch confirmations before interrupting."
    fi
}

monitor_level() {
    local running="$1"
    local elapsed="$2"
    local quiet="$3"
    local worktree_changes="$4"
    local action="$5"

    if [ "$running" != "yes" ]; then
        echo "L0"
    elif [ "$elapsed" -lt "$STARTUP_GRACE" ] || [ "$quiet" -lt "$STALE_AFTER" ]; then
        echo "L0"
    elif [ "$worktree_changes" -gt 0 ] && [ "$quiet" -lt "$INTERRUPT_AFTER" ]; then
        echo "L1"
    elif [ "$action" = "LIKELY_STUCK" ] || [ "$quiet" -ge "$INTERRUPT_AFTER" ]; then
        echo "L3"
    else
        echo "L2"
    fi
}

print_file() {
    local label="$1"
    local file="$2"
    if [ -e "$file" ]; then
        echo "$label: $file ($(file_size "$file") bytes)"
    else
        echo "$label: missing ($file)"
    fi
}

echo "# Claude Dispatch Status"
echo "Task ID: $TASK_ID"
echo "Worktree: $WORKTREE_DIR"
if [ -n "$RUNTIME_DIAGNOSTIC" ]; then
    echo "Runtime: $RUNTIME_DIAGNOSTIC"
fi
echo "Wait Policy: profile=${WAIT_PROFILE} startup_grace=${STARTUP_GRACE}s stale_after=${STALE_AFTER}s interrupt_after=${INTERRUPT_AFTER}s"

# Spec item 3/4: role-aware PID reporting.
DISPATCHER_STATE="$(role_state "$DISPATCHER_PID_FILE")"
CLAUDE_ROLE_STATE="$(role_state "$CLAUDE_PID_FILE")"
CHECKER_STATE="$(role_state "$CHECKER_PID_FILE")"

# Backward compatibility: if new PID files don't exist, fall back to legacy .pid
if [ "$CLAUDE_ROLE_STATE" = "missing" ] && [ -f "$PID_FILE" ]; then
    CLAUDE_ROLE_STATE="$(role_state "$PID_FILE")"
fi

# Backward-compatible PID display using legacy .pid file
if [ -f "$PID_FILE" ]; then
    PID="$(tr -d '[:space:]' < "$PID_FILE")"
    echo "PID: $PID"
else
    PID=""
    echo "PID: missing"
fi

echo "Dispatcher: ${DISPATCHER_STATE}"
echo "Claude: ${CLAUDE_ROLE_STATE}"
echo "Checker: ${CHECKER_STATE}"

# Overall running: any role (dispatcher/Claude/checker) still active.
OVERALL_RUNNING="no"
if [ "$DISPATCHER_STATE" = "running" ] || [ "$CLAUDE_ROLE_STATE" = "running" ] || [ "$CHECKER_STATE" = "running" ]; then
    OVERALL_RUNNING="yes"
elif [ "$DISPATCHER_STATE" = "visibility-unknown" ] || [ "$CLAUDE_ROLE_STATE" = "visibility-unknown" ] || [ "$CHECKER_STATE" = "visibility-unknown" ]; then
    OVERALL_RUNNING="unknown"
fi
echo "Overall running: ${OVERALL_RUNNING}"

# Execution running: Claude or Checker still active (dispatcher-only = finalizing).
RUNNING="no"
if [ "$CLAUDE_ROLE_STATE" = "running" ] || [ "$CHECKER_STATE" = "running" ]; then
    RUNNING="yes"
elif [ "$CLAUDE_ROLE_STATE" = "visibility-unknown" ] || [ "$CHECKER_STATE" = "visibility-unknown" ]; then
    RUNNING="unknown"
fi

# Backward-compatible PROCESS_STATE
if [ "$RUNNING" = "yes" ]; then
    PROCESS_STATE="running"
elif [ "$RUNNING" = "unknown" ]; then
    PROCESS_STATE="visibility-unknown"
else
    PROCESS_STATE="not-running"
fi

echo ""
echo "## Artifacts"
print_file "Progress" "$PROGRESS_FILE"
print_file "Result" "$RESULT_FILE"
print_file "Status" "$STATUS_FILE"
print_file "Network" "$NETWORK_FILE"
print_file "Diff" "$DIFF_FILE"
print_file "Report" "$REPORT_FILE"
if [ -f "$LIVE_REPORT_FILE" ]; then
    print_file "Live Report" "$LIVE_REPORT_FILE"
fi
print_file "Claude Progress" "$CLAUDE_PROGRESS_FILE"
if [ -f "$LIVE_CLAUDE_PROGRESS_FILE" ]; then
    print_file "Live Claude Progress" "$LIVE_CLAUDE_PROGRESS_FILE"
fi
print_file "Worktree Status" "$WORKTREE_STATUS_FILE"

echo ""
echo "## Partial Worktree Triage"
if [ -d "$WORKTREE_DIR" ]; then
    CHANGE_COUNT="$(worktree_change_count)"
    LAST_LINE="$(last_dispatch_line)"
    ELAPSED="$(field_from_line "$LAST_LINE" "elapsed_seconds")"; ELAPSED="${ELAPSED:-0}"
    QUIET="$(field_from_line "$LAST_LINE" "quiet_seconds")"; QUIET="${QUIET:-0}"
    RESULT_BYTES="$(file_size "$RESULT_FILE")"
    STATUS_BYTES="$(file_size "$STATUS_FILE")"
    CLAUDE_PROGRESS_SOURCE="$(select_claude_progress_file)"
    CLAUDE_PROGRESS_BYTES="$(file_size "$CLAUDE_PROGRESS_SOURCE")"
    REPORT_SOURCE="$(select_report_file)"
    EVIDENCE_STATE="$(evidence_state "$CHANGE_COUNT" "$CLAUDE_PROGRESS_SOURCE" "$REPORT_SOURCE")"
    if [ "$OVERALL_RUNNING" = "unknown" ]; then
        ACTION="CHECK_OUTSIDE_SANDBOX_DO_NOT_REDISPATCH"
    else
        ACTION="$(recommended_action "$RUNNING" "$ELAPSED" "$QUIET" "$RESULT_BYTES" "$STATUS_BYTES" "$CLAUDE_PROGRESS_BYTES" "$CHANGE_COUNT" "$EVIDENCE_STATE")"
    fi
    RISK_SUMMARY="$(partial_risk_summary)"
    NETWORK_SUMMARY="$(latest_network_summary)"
    MONITOR_POLICY="$(monitor_policy "$RUNNING" "$ELAPSED" "$QUIET" "$CHANGE_COUNT" "$ACTION")"
    MONITOR_LEVEL="$(monitor_level "$RUNNING" "$ELAPSED" "$QUIET" "$CHANGE_COUNT" "$ACTION")"
    echo "Action: $ACTION"
    echo "Evidence: $EVIDENCE_STATE"
    echo "Network: $NETWORK_SUMMARY"
    echo "Monitor policy: $MONITOR_POLICY"
    echo "Machine monitor: monitor_level=${MONITOR_LEVEL} action=${ACTION} evidence_state=\"${EVIDENCE_STATE}\" quiet_seconds=${QUIET} suspect_count=0 running=${RUNNING} overall_running=${OVERALL_RUNNING} dispatcher=${DISPATCHER_STATE} claude=${CLAUDE_ROLE_STATE} checker=${CHECKER_STATE} elapsed_seconds=${ELAPSED} worktree_changes=${CHANGE_COUNT} network=\"${NETWORK_SUMMARY}\""
    if [ "$OVERALL_RUNNING" = "unknown" ]; then
        echo "Process visibility: restricted; PID absence is inconclusive. Re-run outside the sandbox and do not start a duplicate dispatch."
    fi
    echo "Report source: $REPORT_SOURCE"
    echo "Claude progress source: $CLAUDE_PROGRESS_SOURCE"
    echo "Elapsed: ${ELAPSED}s"
    echo "Quiet: ${QUIET}s"
    echo "Implementation change count: $CHANGE_COUNT"
    echo "Risk summary: $RISK_SUMMARY"
    echo "Diffstat/status:"
    partial_diffstat
    if [ "$CHANGE_COUNT" -gt 0 ]; then
        echo ""
        echo "Recommendation: review the partial diff against the task card before interrupting Claude. Continue waiting if the changes match the plan; stop Claude only if the implementation is off-plan, risky, or no longer making useful progress."
    else
        echo ""
        echo "Recommendation: no implementation changes are visible yet. Use repeated compact watch confirmations before escalating to details/network evidence; stop Claude only after progress, status, diff, and network/process evidence agree that useful progress is unlikely."
    fi
else
    echo "(worktree unavailable)"
fi

echo ""
echo "## Progress Tail"
if [ -f "$PROGRESS_FILE" ]; then
    tail -20 "$PROGRESS_FILE"
else
    echo "(none)"
fi

echo ""
echo "## Claude Progress Tail"
TAIL_CLAUDE_PROGRESS_FILE="$(select_claude_progress_file)"
if [ -f "$TAIL_CLAUDE_PROGRESS_FILE" ]; then
    echo "Source: $TAIL_CLAUDE_PROGRESS_FILE"
    tail -40 "$TAIL_CLAUDE_PROGRESS_FILE"
else
    echo "(none)"
fi

echo ""
echo "## Status Tail"
if [ -f "$STATUS_FILE" ]; then
    tail -20 "$STATUS_FILE"
else
    echo "(none)"
fi

echo ""
echo "## Network Tail"
if [ -f "$NETWORK_FILE" ] && [ "$(file_size "$NETWORK_FILE")" -gt 0 ]; then
    tail -40 "$NETWORK_FILE"
else
    echo "(none)"
fi

if [ -d "$WORKTREE_DIR" ]; then
    echo ""
    echo "## Worktree Git Status"
    git -c "safe.directory=$WORKTREE_DIR" -C "$WORKTREE_DIR" status --short --untracked-files=all 2>/dev/null || echo "(git status unavailable)"
fi
