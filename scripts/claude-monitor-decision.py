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
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Tuple

CONTROL = {
    "TASK_CARD.md", "TASK_CARD_FULL.md", "CLAUDE_TASK_CARD.md",
    "CLAUDE_PROMPT.md", "CLAUDE_PROGRESS.md", "CLAUDE_REPORT.md",
    "ADVISOR_REQUEST.json",
}
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
    return Path(result.stdout.strip()).resolve()


def latest_task(worktrees: Path) -> Optional[str]:
    candidates = sorted(worktrees.glob("claude-*.progress.log"), key=lambda path: path.stat().st_mtime)
    return candidates[-1].name[: -len(".progress.log")] if candidates else None


def normalize_task(value: Optional[str], worktrees: Path) -> str:
    if not value:
        value = latest_task(worktrees)
    if not value:
        raise ValueError("no Claude task found")
    path = Path(value)
    name = path.name
    for suffix in (".progress.log", ".runtime.json", ".pid"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    if not re.fullmatch(r"claude-[A-Za-z0-9._-]+", name):
        raise ValueError("unsafe Claude task id")
    return name


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


def changed_state(
    worktree: Path, maximum: int, known_count: Optional[int], cached_status: Path,
) -> Tuple[int, List[str], str]:
    cached = bounded_tail(cached_status, 32768)
    if cached:
        lines = cached.splitlines()
    else:
        # Avoid a full untracked-file walk in large repositories. The persistent
        # watcher already records the authoritative aggregate count.
        result = git(worktree, "status", "--porcelain", "--untracked-files=no")
        lines = result.stdout.splitlines() if result.returncode == 0 else []
    paths: List[str] = []
    for line in lines:
        raw = line[3:].strip().strip('"').replace("\\", "/")
        if " -> " in raw:
            raw = raw.split(" -> ", 1)[1]
        if not raw or PurePosixPath(raw).name in CONTROL:
            continue
        paths.append(clean(raw, 160))
    paths = sorted(set(paths))
    diff = git(worktree, "diff", "--shortstat")
    diffstat = clean(diff.stdout, 180) if diff.returncode == 0 else "git-diff-unavailable"
    count = known_count if known_count is not None else len(paths)
    if not diffstat and count:
        diffstat = f"{count} changed paths"
    return count, paths[:maximum], diffstat or "no implementation changes"


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


def snapshot(args: argparse.Namespace) -> Dict[str, Any]:
    collected_at = datetime.now(timezone.utc).isoformat()
    root = repo_root(args.repo_root)
    worktrees = root / ".worktrees"
    task_id = normalize_task(args.task_id, worktrees)
    prefix = worktrees / task_id
    progress_file = worktrees / f"{task_id}.progress.log"
    monitor_event_file = worktrees / f"{task_id}.monitor-events.log"
    status_file = worktrees / f"{task_id}.status.txt"
    result_file = worktrees / f"{task_id}.result.json"
    worktree, conflicts = runtime_worktree(worktrees, task_id)
    progress_tail = bounded_tail(progress_file)
    status_tail = bounded_tail(status_file)
    terminal = bool(TERMINAL_RE.search(progress_tail))
    last_line = last_matching(progress_tail, DISPATCH_RE)
    dispatch_fields = fields(last_line)
    event = last_monitor_event(monitor_event_file)
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
    known_changes = integer(event["worktree_changes"]) if "worktree_changes" in event else None
    changes, paths, diffstat = changed_state(
        worktree, args.max_changed_paths, known_changes,
        worktrees / f"{task_id}.worktree-status.txt",
    ) if worktree.is_dir() else (known_changes or 0, [], "worktree unavailable")
    progress = progress_fields(worktree, worktrees, task_id, args.max_summary_chars)
    errors = error_categories(progress_tail, status_tail)
    report_path = worktree / "CLAUDE_REPORT.md"
    if not report_path.is_file():
        report_path = worktrees / f"{task_id}.report.md"
    report_text = bounded_tail(report_path, 32768)
    result_size = result_file.stat().st_size if result_file.is_file() else 0
    evidence = event.get("evidence_state") or evidence_state(changes, result_size, report_text)
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

    if visibility and not terminal:
        decision, confidence, reason = "visibility-unknown", "high", "process-visibility-restricted"
    elif terminal and not overall_running:
        decision, confidence, reason = "terminal", "high", "terminal-evidence"
    elif running and finish_expected:
        # Completion is a voluntary-exit signal, never an interruption grant.
        # Keep waiting for the child to flush its report/result and exit itself.
        decision, confidence, reason = "continue", "high", "completion-ready-awaiting-voluntary-exit"
    elif direction_deviation:
        decision, confidence, reason = "interrupt-candidate", "high", "explicit-direction-deviation"
    elif running and execution_state == "implementation-ready":
        decision, confidence, reason = "continue", "high", "editing-ready-awaiting-durable-write"
    elif running and execution_state == "implementation-idle":
        decision, confidence, reason = "inspect", "high", "product-edit-idle-candidate"
    elif running and level == "L3" and quiet >= args.interrupt_after and suspect >= args.confirmations and growth != "yes":
        decision, confidence, reason = "interrupt-candidate", "medium", "corroborated-l3-stall"
    elif conflicts or errors or (
        running and level in {"L1", "L2", "L3"}
        and quiet >= args.stale_after and growth != "yes"
    ):
        decision, confidence, reason = "inspect", "medium", "bounded-review-needed"
    elif running:
        decision, confidence, reason = "continue", "high", "recent-or-insufficient-stop-evidence"
    elif result_size or changes or report_text:
        decision, confidence, reason = "terminal", "medium", "stopped-with-evidence"
    else:
        decision, confidence, reason = "inspect", "low", "stopped-without-evidence"

    codex_review = decision in {"inspect", "interrupt-candidate"} or direction_deviation or bool(conflicts)
    summary = clean(
        f"{decision}: {reason}; level={level}; running={'yes' if running else 'no'}; "
        f"elapsed={elapsed}s quiet={quiet}s changes={changes}; state={execution_state}; "
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
            "report": observed_at(report_path),
            "worktree": collected_at,
        },
        "decision": decision,
        "confidence": confidence, "reason_code": reason,
        "codex_review_required": "yes" if codex_review else "no",
        "interrupt_authorized": "no", "monitor_level": level,
        "finish_expected": "yes" if finish_expected else "no",
        "finish_recommended": "yes" if finish_expected and running else "no",
        "monitor_action": monitor_action, "running": "yes" if running else ("unknown" if visibility else "no"),
        "overall_running": "yes" if overall_running else ("unknown" if visibility else "no"),
        "dispatcher": states["dispatcher"], "claude": states["claude"], "checker": states["checker"],
        "elapsed_seconds": elapsed, "quiet_seconds": quiet, "suspect_count": suspect,
        "execution_activity_state": execution_state,
        "edit_ready": "yes" if edit_ready else "no",
        "product_idle_seconds": product_idle_seconds,
        "idle_confirmations": idle_confirmations,
        "evidence_state": evidence, "artifact_growth": growth,
        "worktree_changes": changes, "product_changes": changes,
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
    keys = ("decision", "confidence", "reason_code", "codex_review_required",
            "interrupt_authorized", "finish_expected", "finish_recommended",
            "execution_phase", "implementation_complete", "completion_ready",
            "execution_activity_state", "edit_ready", "product_idle_seconds", "idle_confirmations",
            "monitor_level", "running", "collected_at", "elapsed_seconds",
            "quiet_seconds", "suspect_count", "artifact_growth", "worktree_changes", "summary")
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
