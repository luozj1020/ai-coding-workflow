#!/usr/bin/env python3
"""Return a bounded local decision snapshot for one Claude dispatch."""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claude_task_id import normalize_task_id  # noqa: E402

TERMINAL_RE = re.compile(
    r"Claude (?:child exited|subprocess ended|finished|completed)|Final dispatch outcome:|Dispatch Complete",
    re.I,
)
DISPATCH_RE = re.compile(
    r"Claude still running|Claude child exited|Claude subprocess ended|Claude finished|Stopping Claude|Final dispatch outcome",
    re.I,
)
FIELD_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(?:\"([^\"]*)\"|([^\s]+))")


def bounded_tail(path: Path, limit: int = 65536) -> str:
    try:
        with path.open("rb") as handle:
            size = path.stat().st_size
            if size > limit:
                handle.seek(size - limit)
                handle.readline()
            return handle.read(limit).decode("utf-8", errors="replace")
    except OSError:
        return ""


def clean(value: Any, limit: int) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", "" if value is None else str(value))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def integer(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).rstrip("s"))
    except (TypeError, ValueError):
        return default


def observed_at(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        return ""


def read_json_object(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def fields(line: str) -> Dict[str, str]:
    return {match.group(1): match.group(2) or match.group(3) or "" for match in FIELD_RE.finditer(line)}


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )


def repo_root(start: Path) -> Path:
    result = git(start, "rev-parse", "--show-toplevel")
    if result.returncode:
        raise ValueError("not inside a Git repository")
    source = Path(result.stdout.strip()).resolve()
    common_result = git(source, "rev-parse", "--git-common-dir")
    if common_result.returncode:
        return source
    common = Path(common_result.stdout.strip())
    if not common.is_absolute():
        common = source / common
    common = common.resolve()
    if common.name == ".git":
        return common.parent
    worktrees = git(source, "worktree", "list", "--porcelain")
    if not worktrees.returncode:
        for line in worktrees.stdout.splitlines():
            if line.startswith("worktree "):
                return Path(line[len("worktree "):]).resolve()
    return source


def latest_task(worktrees: Path) -> Optional[str]:
    candidates = sorted(worktrees.glob("*.progress.log"), key=lambda path: path.stat().st_mtime)
    for candidate in reversed(candidates):
        try:
            return normalize_task_id(str(candidate), artifact_input=True)
        except ValueError:
            continue
    return None


def normalize_task(value: Optional[str], worktrees: Path) -> str:
    if not value:
        value = latest_task(worktrees)
    if not value:
        raise ValueError("no Claude task found")
    return normalize_task_id(value, artifact_input=True)


def inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def runtime_worktree(worktrees: Path, task_id: str) -> Tuple[Path, List[str]]:
    fallback = worktrees / task_id
    runtime = worktrees / f"{task_id}.runtime.json"
    conflicts: List[str] = []
    if not runtime.is_file():
        return fallback, conflicts
    try:
        value = json.loads(runtime.read_text(encoding="utf-8"))
        candidate = Path(str(value.get("worktree", ""))).resolve()
    except (OSError, ValueError, json.JSONDecodeError):
        conflicts.append("runtime-invalid")
        return fallback, conflicts
    if not inside(candidate, worktrees) or candidate == worktrees or not candidate.is_dir():
        conflicts.append("runtime-worktree-invalid")
        return fallback, conflicts
    return candidate, conflicts


def role_state(helper: Path, pid_file: Path, progress: Path, identity_file: Optional[Path] = None) -> str:
    if helper.is_file():
        command = [sys.executable, str(helper), "--pid-file", str(pid_file),
                   "--progress-file", str(progress)]
        if identity_file is not None:
            command.extend(("--identity-file", str(identity_file)))
        result = subprocess.run(
            command,
            capture_output=True, text=True, timeout=10,
        )
        state = result.stdout.strip()
        if state in {"running", "not-running", "missing", "visibility-unknown"}:
            return state
    if identity_file is not None:
        # A requested identity check must never degrade to PID-only liveness.
        return "visibility-unknown"
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return "running"
    except FileNotFoundError:
        return "missing"
    except (OSError, ValueError):
        return "not-running"


def last_matching(text: str, pattern: re.Pattern[str]) -> str:
    matches = [line for line in text.splitlines() if pattern.search(line)]
    return matches[-1] if matches else ""


def last_monitor_event(path: Path) -> Dict[str, str]:
    for line in reversed(bounded_tail(path, 32768).splitlines()):
        if line.startswith("monitor_event "):
            try:
                tokens = shlex.split(line[len("monitor_event "):])
            except ValueError:
                return {}
            result: Dict[str, str] = {}
            for token in tokens:
                if "=" in token:
                    key, value = token.split("=", 1)
                    result[key] = value
            return result
        if "machine:" in line:
            return fields(line)
    return {}


def verified_product_event(event: Dict[str, str]) -> str:
    """Summarize only dispatcher-issued product boundary events."""
    kind = event.get("event", "")
    if kind not in {
        "active-window-refreshed", "material-change",
        "first-progress-reconciled", "terminal",
    }:
        return "none"
    if not any(
        key in event for key in (
            "product_changes", "product_delta_from_baseline", "product_hash"
        )
    ):
        return "none"
    fields = [kind]
    for key in (
        "execution_state", "product_changes", "product_delta_from_baseline",
        "active_window_refreshed", "product_hash",
    ):
        if event.get(key) not in {None, ""}:
            fields.append(f"{key}={event[key]}")
    return clean(";".join(fields), 240)


def last_verified_product_event(path: Path) -> str:
    """Find the newest product boundary even if later advisory events exist."""
    for line in reversed(bounded_tail(path, 32768).splitlines()):
        if not line.startswith("monitor_event "):
            continue
        try:
            tokens = shlex.split(line[len("monitor_event "):])
        except ValueError:
            continue
        event = {}
        for token in tokens:
            if "=" in token:
                key, value = token.split("=", 1)
                event[key] = value
        summary = verified_product_event(event)
        if summary != "none":
            return summary
    return "none"


def progress_fields(worktree: Path, worktrees: Path, task_id: str, limit: int) -> Dict[str, Any]:
    live = worktree / "CLAUDE_PROGRESS.md"
    archived = worktrees / f"{task_id}.claude-progress.md"
    text = bounded_tail(live if live.is_file() else archived, 32768)
    result: Dict[str, Any] = {
        "phase": "", "execution_phase": "", "next_check": "", "blocker": "",
        "last_update": "", "implementation_complete": "unknown",
        "assigned_tail_work": "", "tail_work_complete": "unknown",
        "completion_ready": "unknown",
    }
    labels = {
        "Current Phase": "phase", "Next Check": "next_check",
        "Blocker": "blocker", "Last Update": "last_update",
        "Execution Phase": "execution_phase",
        "Implementation Complete": "implementation_complete",
        "Assigned Tail Work": "assigned_tail_work",
        "Tail Work Complete": "tail_work_complete",
        "Completion Ready": "completion_ready",
    }
    for label, key in labels.items():
        found = re.findall(rf"(?im)^-?\s*{re.escape(label)}:\s*(.+)$", text)
        if found:
            result[key] = clean(found[-1], limit)
    checks = re.findall(r"(?m)^\s*-\s*\[([ xX])\]", text)
    result["checklist_done"] = sum(mark.lower() == "x" for mark in checks)
    result["checklist_total"] = len(checks)
    return result


def canonical_product_state(worktree: Path, baseline_state: Optional[Path] = None) -> Dict[str, Any]:
    """Read product state from the same helper used by the dispatcher."""
    helper = Path(__file__).resolve().with_name("worktree_state_hash.py")
    if not helper.is_file():
        return {}
    command = [sys.executable, str(helper), "--worktree", str(worktree),
               "--ignore-empty-untracked", "--json"]
    if baseline_state is not None and baseline_state.is_file():
        command.extend(("--baseline-state", str(baseline_state)))
    result = subprocess.run(
        command,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30,
    )
    if result.returncode:
        return {}
    try:
        value = json.loads(result.stdout)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) and value.get("status") == "ready" else {}


def changed_state(
    worktree: Path, maximum: int, known_count: Optional[int],
    canonical: Optional[Dict[str, Any]] = None,
) -> Tuple[int, List[str], str]:
    """Return product-only changes; human Markdown is never machine input."""
    state = canonical or canonical_product_state(worktree)
    raw_paths = state.get(
        "incremental_product_changed_paths",
        state.get("product_changed_paths", []),
    ) if state else []
    paths = [clean(path, 160) for path in raw_paths if isinstance(path, str)]
    paths = sorted(set(paths))
    state_count = state.get(
        "incremental_product_change_count",
        state.get("product_change_count"),
    ) if state else None
    count = integer(state_count, -1)
    if count < 0:
        count = known_count if known_count is not None else len(paths)
    diffstat = f"{count} product changed paths" if count else "no product changes"
    return count, paths[:maximum], diffstat


def error_categories(progress: str, status: str) -> List[str]:
    text = "\n".join((progress, status))
    categories: List[str] = []
    patterns = {
        "api-connection": r"API Error|Connection closed|ECONN|TLS|DNS|rate limit|HTTP (?:4|5)\d\d",
        "timeout": r"timed out|runtime timeout|TimeoutError",
        "approval-permission": r"approval (?:blocked|required)|permission denied|requires permission",
        "validation": r"(?:test|validation) (?:failed|blocked)|AssertionError|SyntaxError",
        "exception": r"Traceback \(most recent call last\)|uncaught exception|segmentation fault|panic:",
        "direction-deviation": r"(?:confirmed|explicit|detected) (?:direction|scope) deviation|direction_review=(?:deviated|reject)|scope_violation=(?:yes|true)",
    }
    for name, pattern in patterns.items():
        if re.search(pattern, text, re.I):
            categories.append(name)
    return categories


def evidence_state(changes: int, result_size: int, report_text: str) -> str:
    seeded = "AI-CODING-WORKFLOW:DISPATCH-SEEDED-REPORT" in report_text
    fallback = "AI-CODING-WORKFLOW:DISPATCH-FALLBACK-REPORT" in report_text
    valid_report = bool(report_text.strip()) and not seeded and not fallback
    if changes and valid_report:
        return "diff + valid report"
    if changes:
        return "diff without report"
    if valid_report:
        return "valid report without diff"
    if seeded:
        return "seeded report only"
    if result_size:
        return "result without valid report"
    return "no valid report"


def evidence_label(value: str) -> str:
    labels = {
        "diff-plus-valid-report": "diff + valid report",
        "diff-without-report": "diff without report",
        "acknowledgement-only": "acknowledgement only",
        "seeded-report-only": "seeded report only",
        "valid-report-without-diff": "valid report without diff",
        "no-valid-report": "no valid report",
    }
    return labels.get(value, value)


def lifecycle_state(
    *, terminal: bool, running: bool, overall_running: bool, progress: str, status: str,
) -> tuple[str, str]:
    """Classify the only operator-visible lifecycle and startup outcome."""
    text = "\n".join((progress, status)).lower()
    if terminal:
        return "terminal", "completed"
    if "capability" in text and ("blocked" in text or "mismatch" in text):
        return "preflight-blocked", "capability-blocked"
    if any(token in text for token in ("needs_host_execution", "transport failure", "network is restricted")):
        return "preflight-blocked", "transport-blocked"
    if any(token in text for token in ("approval blocked", "permission denied", "read-only file system")):
        return "preflight-blocked", "approval-blocked"
    if running:
        return "running", "started"
    if overall_running:
        return "finalizing", "started"
    if progress or status:
        return "stopped-without-terminal-receipt", "started"
    return "not-started", "not-started"


def usability(
    *, lifecycle: str, overall_running: bool, outcome: Dict[str, Any], conflicts: List[str],
) -> tuple[str, List[str]]:
    """Return a strict, evidence-only handoff readiness result."""
    reasons: List[str] = []
    if lifecycle != "terminal":
        reasons.append("terminal-receipt-required")
    if overall_running:
        reasons.append("no-active-writer-required")
    if outcome.get("dispatch_success") is not True:
        reasons.append("dispatch-success-required")
    if outcome.get("artifact_valid") is not True:
        reasons.append("artifact-validation-required")
    if outcome.get("validation_success") not in {True, "passed", "success"}:
        reasons.append("specified-validation-required")
    if outcome.get("semantic_acceptance") not in {True, "accepted", "accept"}:
        reasons.append("codex-semantic-acceptance-required")
    if conflicts:
        reasons.append("evidence-conflicts-must-be-resolved")
    return ("yes" if not reasons else "no"), reasons


def snapshot(args: argparse.Namespace) -> Dict[str, Any]:
    collected_at = datetime.now(timezone.utc).isoformat()
    root = repo_root(args.repo_root)
    worktrees = root / ".worktrees"
    task_id = normalize_task(args.task_id, worktrees)
    progress_file = worktrees / f"{task_id}.progress.log"
    monitor_event_file = worktrees / f"{task_id}.monitor-events.log"
    status_file = worktrees / f"{task_id}.status.txt"
    result_file = worktrees / f"{task_id}.result.json"
    outcome_file = worktrees / f"{task_id}.outcome.json"
    outcome = read_json_object(outcome_file)
    worktree, conflicts = runtime_worktree(worktrees, task_id)
    progress_tail = bounded_tail(progress_file)
    status_tail = bounded_tail(status_file)
    event = last_monitor_event(monitor_event_file)
    product_event = last_verified_product_event(monitor_event_file)
    terminal = bool(TERMINAL_RE.search(progress_tail)) or (
        event.get("terminal") == "yes" and event.get("running") == "no"
    )
    last_line = last_matching(progress_tail, DISPATCH_RE)
    dispatch_fields = fields(last_line)
    helper = Path(__file__).resolve().with_name("claude-process-state.py")
    states = {
        role: role_state(
            helper, worktrees / f"{task_id}.{role}.pid", progress_file,
            worktrees / f"{task_id}.{role}.process.json",
        )
        for role in ("dispatcher", "claude", "checker")
    }
    if states["claude"] == "missing":
        states["claude"] = role_state(
            helper, worktrees / f"{task_id}.pid", progress_file,
            worktrees / f"{task_id}.claude.process.json",
        )
    visibility = any(value == "visibility-unknown" for value in states.values())
    running = states["claude"] == "running" or states["checker"] == "running"
    overall_running = any(value == "running" for value in states.values())
    # In container/host PID namespace splits the external identity helper may be
    # unable to see the child even though the dispatcher has just observed it.
    # The dispatcher's persisted event is useful liveness evidence, but it never
    # grants interruption authority and terminal evidence still wins.
    dispatcher_observed_running = event.get("running") == "yes" and not terminal
    effective_running = running or dispatcher_observed_running
    effective_overall_running = overall_running or dispatcher_observed_running
    elapsed = integer(event.get("elapsed_seconds") or dispatch_fields.get("elapsed_seconds"))
    quiet = integer(event.get("quiet_seconds") or dispatch_fields.get("quiet_seconds"))
    suspect = integer(event.get("suspect_count"))
    level = event.get("monitor_level", "unknown")
    monitor_action = event.get("action", "unknown")
    growth = event.get("artifact_growth", "unknown")
    execution_state = event.get("execution_state", "unknown")
    edit_ready = event.get("edit_ready", "0") in {"1", "yes", "true"}
    product_idle_seconds = integer(event.get("product_idle_seconds"))
    idle_confirmations = integer(event.get("idle_confirmations"))
    known_product_changes = integer(event["product_changes"]) if "product_changes" in event else (
        integer(event["worktree_changes"]) if "worktree_changes" in event else None
    )
    known_control_changes = integer(event.get("control_changes"), 0)
    known_total_product_changes = integer(
        event.get("total_product_changes"), known_product_changes or 0,
    )
    known_all_changes = integer(event.get("worktree_changes"), -1)
    canonical_path = worktrees / f"{task_id}.product-state.json"
    canonical = read_json_object(canonical_path)
    if not canonical or canonical.get("status") != "ready":
        canonical = canonical_product_state(
            worktree, worktrees / f"{task_id}.product-baseline.json",
        ) if worktree.is_dir() else {}
    changes, paths, diffstat = changed_state(
        worktree, args.max_changed_paths, known_product_changes, canonical,
    ) if worktree.is_dir() else (known_product_changes or 0, [], "worktree unavailable")
    if known_product_changes is not None and canonical and changes != known_product_changes:
        conflicts.append("terminal-product-count-mismatch")
    control_changes = integer(canonical.get("control_change_count"), known_control_changes) if canonical else known_control_changes
    total_product_changes = integer(
        canonical.get("product_change_count"), known_total_product_changes,
    ) if canonical else known_total_product_changes
    all_changes = known_all_changes if known_all_changes >= 0 else total_product_changes + control_changes
    progress = progress_fields(worktree, worktrees, task_id, args.max_summary_chars)
    errors = error_categories(progress_tail, status_tail)
    report_path = worktree / "CLAUDE_REPORT.md"
    if not report_path.is_file():
        report_path = worktrees / f"{task_id}.report.md"
    report_text = bounded_tail(report_path, 32768)
    result_size = result_file.stat().st_size if result_file.is_file() else 0
    evidence = evidence_label(event.get("evidence_state") or str(outcome.get("evidence_state") or ""))
    if not evidence:
        evidence = evidence_state(changes, result_size, report_text)
    operator_state = str(outcome.get("operator_state") or "")
    if not operator_state:
        if terminal and evidence == "diff without report" and changes > 0:
            operator_state = "implementation-stable-awaiting-review"
        elif terminal and outcome.get("completion_state") in {
            "needs-review", "semantic-review-required"
        }:
            operator_state = "terminal-awaiting-review"
        elif terminal:
            operator_state = "terminal"
        else:
            operator_state = "running-or-unresolved"
    if terminal and overall_running:
        conflicts.append("terminal-marker-with-live-role")
    if event.get("running") == "yes" and not running and not visibility and not terminal:
        conflicts.append("monitor-role-state-conflict")
    direction_deviation = "direction-deviation" in errors
    completion_ready = str(progress["completion_ready"]).strip().lower() == "yes"
    implementation_complete = str(progress["implementation_complete"]).strip().lower() == "yes"
    tail_work_complete = str(progress["tail_work_complete"]).strip().lower() == "yes"
    finish_expected = completion_ready or (
        implementation_complete and tail_work_complete
        and progress["next_check"].strip().lower() == "exit"
    )
    lifecycle, startup = lifecycle_state(
        terminal=terminal, running=effective_running, overall_running=effective_overall_running,
        progress=progress_tail, status=status_tail,
    )
    usable, usability_reasons = usability(
        lifecycle=lifecycle, overall_running=effective_overall_running,
        outcome=outcome, conflicts=conflicts,
    )

    if visibility and not terminal and not dispatcher_observed_running:
        decision, confidence, reason = "visibility-unknown", "high", "process-visibility-restricted"
    elif terminal and not overall_running:
        reason = (
            "implementation-stable-awaiting-review"
            if operator_state == "implementation-stable-awaiting-review"
            else "terminal-evidence"
        )
        decision, confidence = "terminal", "high"
    elif effective_running and finish_expected:
        # Completion is a voluntary-exit signal, never an interruption grant.
        # Keep waiting for the child to flush its report/result and exit itself.
        decision, confidence, reason = "continue", "high", "completion-ready-awaiting-voluntary-exit"
    elif direction_deviation:
        decision, confidence, reason = "interrupt-candidate", "high", "explicit-direction-deviation"
    elif effective_running and execution_state == "external-blocked":
        decision, confidence, reason = "inspect", "high", "confirmed-external-blocker"
    elif effective_running and execution_state == "semantic-blocked":
        decision, confidence, reason = "inspect", "high", "reported-semantic-blocker"
    elif effective_running and execution_state == "waiting-tool":
        decision, confidence, reason = "continue", "medium", "named-tool-wait"
    elif effective_running and execution_state == "implementation-ready":
        decision, confidence, reason = "continue", "high", "editing-ready-awaiting-durable-write"
    elif effective_running and execution_state == "implementation-idle":
        decision, confidence, reason = "inspect", "high", "product-edit-idle-candidate"
    elif effective_running and level == "L3" and quiet >= args.interrupt_after and suspect >= args.confirmations and growth != "yes":
        decision, confidence, reason = "interrupt-candidate", "medium", "corroborated-l3-stall"
    elif conflicts or errors or (
        effective_running and level in {"L1", "L2", "L3"}
        and quiet >= args.stale_after and growth != "yes"
    ):
        decision, confidence, reason = "inspect", "medium", "bounded-review-needed"
    elif effective_running:
        decision, confidence, reason = "continue", "high", "recent-or-insufficient-stop-evidence"
    elif result_size or changes or report_text:
        decision, confidence, reason = "terminal", "medium", "stopped-with-evidence"
    else:
        decision, confidence, reason = "inspect", "low", "stopped-without-evidence"

    codex_review = (
        decision in {"inspect", "interrupt-candidate"}
        or operator_state.endswith("awaiting-review")
        or direction_deviation
        or bool(conflicts)
    )
    summary = clean(
        f"{decision}: {reason}; level={level}; running={'yes' if effective_running else 'no'}; "
        f"elapsed={elapsed}s quiet={quiet}s product_changes={changes} control_changes={control_changes}; state={execution_state}; "
        f"product_idle={product_idle_seconds}s confirmations={idle_confirmations}; evidence={evidence}",
        args.max_summary_chars,
    )
    return {
        "schema_version": 1, "task_id": task_id, "collected_at": collected_at,
        "observed_at": {
            "processes": collected_at,
            "progress_log": observed_at(progress_file),
            "monitor_event": observed_at(monitor_event_file),
            "status": observed_at(status_file),
            "result": observed_at(result_file),
            "outcome": observed_at(outcome_file),
            "report": observed_at(report_path),
            "worktree": collected_at,
        },
        "decision": decision,
        "lifecycle_state": lifecycle, "startup_state": startup,
        "usable": usable, "usability_reasons": usability_reasons,
        "confidence": confidence, "reason_code": reason,
        "codex_review_required": "yes" if codex_review else "no",
        "interrupt_authorized": "no", "monitor_level": level,
        "finish_expected": "yes" if finish_expected else "no",
        "finish_recommended": "yes" if finish_expected and effective_running else "no",
        "monitor_action": monitor_action,
        "running": "yes" if effective_running else ("unknown" if visibility else "no"),
        "overall_running": "yes" if effective_overall_running else ("unknown" if visibility else "no"),
        "dispatcher_observed_running": "yes" if dispatcher_observed_running else "no",
        "process_visibility": "restricted" if visibility else "direct",
        "dispatcher": states["dispatcher"], "claude": states["claude"], "checker": states["checker"],
        "elapsed_seconds": elapsed, "quiet_seconds": quiet, "suspect_count": suspect,
        "execution_activity_state": execution_state,
        "last_verified_product_event": product_event,
        "operator_state": operator_state,
        "edit_ready": "yes" if edit_ready else "no",
        "product_idle_seconds": product_idle_seconds,
        "idle_confirmations": idle_confirmations,
        "evidence_state": evidence, "artifact_growth": growth,
        "dispatch_success": outcome.get("dispatch_success"),
        "artifact_valid": outcome.get("artifact_valid"),
        "validation_success": outcome.get("validation_success", "unknown"),
        "semantic_acceptance": outcome.get("semantic_acceptance", "pending-codex-review"),
        "completion_state": outcome.get("completion_state", "unknown"),
        "worktree_changes": all_changes, "product_changes": changes,
        "total_product_changes": total_product_changes,
        "control_changes": control_changes,
        "changed_paths": paths, "diffstat": diffstat,
        "phase": progress["phase"], "execution_phase": progress["execution_phase"],
        "implementation_complete": progress["implementation_complete"],
        "assigned_tail_work": progress["assigned_tail_work"],
        "tail_work_complete": progress["tail_work_complete"],
        "completion_ready": progress["completion_ready"],
        "next_check": progress["next_check"],
        "blocker": progress["blocker"], "checklist_done": progress["checklist_done"],
        "checklist_total": progress["checklist_total"], "error_categories": errors,
        "evidence_conflicts": sorted(set(conflicts)), "summary": summary,
    }


def render_text(value: Dict[str, Any]) -> str:
    keys = ("lifecycle_state", "startup_state", "operator_state", "usable", "decision", "confidence", "reason_code", "codex_review_required",
            "interrupt_authorized", "finish_expected", "finish_recommended",
            "execution_phase", "implementation_complete", "completion_ready",
            "execution_activity_state", "last_verified_product_event", "edit_ready", "product_idle_seconds", "idle_confirmations",
            "dispatch_success", "artifact_valid", "validation_success", "semantic_acceptance", "completion_state",
            "evidence_state", "monitor_level", "running", "collected_at", "elapsed_seconds",
            "quiet_seconds", "suspect_count", "artifact_growth", "worktree_changes",
            "product_changes", "total_product_changes", "control_changes", "summary")
    return "\n".join(f"{key}={clean(value.get(key), 240)}" for key in keys) + "\n"


def render_shell(value: Dict[str, Any]) -> str:
    return "\n".join(f"{key}={shlex.quote(str(item))}" for key, item in value.items() if not isinstance(item, (list, dict))) + "\n"


def atomic_write(path: Path, text: str) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot")
    snap.add_argument("--task-id")
    snap.add_argument("--repo-root", type=Path, default=Path.cwd())
    snap.add_argument("--format", choices=("text", "json", "shell"), default="text")
    snap.add_argument("--max-changed-paths", type=int, default=8)
    snap.add_argument("--max-summary-chars", type=int, default=240)
    snap.add_argument("--stale-after", type=int, default=120)
    snap.add_argument("--interrupt-after", type=int, default=600)
    snap.add_argument("--confirmations", type=int, default=3)
    snap.add_argument("--output", type=Path)
    return result


def main(argv: Optional[List[str]] = None) -> int:
    args = parser().parse_args(argv)
    if not 1 <= args.max_changed_paths <= 20 or not 80 <= args.max_summary_chars <= 1000:
        print("monitor-decision: invalid output bound", file=sys.stderr)
        return 2
    try:
        value = snapshot(args)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        value = {
            "schema_version": 1, "task_id": clean(args.task_id, 80),
            "decision": "inspect", "confidence": "low",
            "reason_code": "malformed-or-missing-evidence",
            "codex_review_required": "yes", "interrupt_authorized": "no",
            "summary": clean(exc, args.max_summary_chars),
        }
    text = json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n" if args.format == "json" else (
        render_shell(value) if args.format == "shell" else render_text(value)
    )
    if args.output:
        atomic_write(args.output, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
