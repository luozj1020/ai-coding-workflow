#!/usr/bin/env python3
"""resume-run.py — Validate run state and output a resume plan.

Reads events, manifest, and hashes from a run directory.
Finds the latest safe phase/iteration and outputs a machine-readable
resume plan plus human summary.

NEVER executes dispatch by default. --apply may only prepare next
task/context, not invoke Claude/Codex.

Python 3.9+ compatible. No third-party dependencies.

Usage:
    python scripts/resume-run.py <run_dir> [--apply] [--output FILE]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Make scripts/ importable
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from event_writer import EventWriter, SCHEMA_VERSION as EVENT_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SAFE_PHASE_ORDER = ["setup", "dispatch", "review", "decision", "finalization"]

PHASE_COMPLETION_EVENTS = {
    "setup": "setup_complete",
    "dispatch": "dispatch_complete",
    "review": "review_complete",
    "decision": "decision",
    "finalization": "run_complete",
}


# ---------------------------------------------------------------------------
# Manifest validation
# ---------------------------------------------------------------------------

def validate_manifest(run_dir: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Load and validate the artifact manifest.

    Returns (entries, errors).
    """
    manifest_path = run_dir / "artifact-manifest.json"
    if not manifest_path.exists():
        return [], ["artifact-manifest.json not found"]

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return [], [f"artifact-manifest.json: {e}"]

    if data.get("schema_version") != 1:
        return [], [f"artifact-manifest.json: schema_version={data.get('schema_version')}, expected 1"]

    entries = data.get("entries", [])
    errors: List[str] = []
    if not isinstance(entries, list):
        return [], ["artifact-manifest.json: entries must be an array"]

    for entry in entries:
        if not isinstance(entry, dict):
            errors.append("artifact-manifest.json: entry must be an object")
            continue
        path = entry.get("path", "")
        expected_hash = entry.get("sha256", "")
        expected_size = entry.get("size", -1)

        candidate = Path(path)
        if not path or candidate.is_absolute() or ".." in candidate.parts:
            errors.append(f"Unsafe artifact path: {path!r}")
            continue
        artifact_path = (run_dir / candidate).resolve()
        try:
            artifact_path.relative_to(run_dir.resolve())
        except ValueError:
            errors.append(f"Artifact path escapes run directory: {path}")
            continue
        if not artifact_path.exists():
            if entry.get("required"):
                errors.append(f"Required artifact missing: {path}")
            continue

        # Hash check
        if expected_hash:
            actual_hash = sha256_file(artifact_path)
            if actual_hash != expected_hash:
                errors.append(f"Hash mismatch for {path}: expected {expected_hash[:16]}..., got {actual_hash[:16]}...")

        # Size check
        if expected_size >= 0:
            actual_size = artifact_path.stat().st_size
            if actual_size != expected_size:
                errors.append(f"Size mismatch for {path}: expected {expected_size}, got {actual_size}")

    return entries, errors


def sha256_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Event analysis
# ---------------------------------------------------------------------------

def load_events(run_dir: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Load events from the run directory.

    Returns (events, errors).
    """
    events_path = run_dir / "loop-events.jsonl"
    if not events_path.exists():
        return [], ["loop-events.jsonl not found"]

    writer = EventWriter(events_path)
    try:
        events = writer.read_all()
        errors: List[str] = []
        expected_run = None
        expected_task = None
        previous_id = None
        for index, event in enumerate(events, 1):
            run_id, task_id = event.get("run_id"), event.get("task_id")
            if expected_run is None:
                expected_run, expected_task = run_id, task_id
            if run_id != expected_run or task_id != expected_task:
                errors.append(f"Event identity mismatch at line {index}")
            if event.get("parent_event_id") != previous_id:
                errors.append(f"Broken event parent chain at line {index}")
            previous_id = event.get("event_id")
        return events, errors
    except Exception as e:
        return [], [str(e)]


def find_latest_safe_point(events: List[Dict[str, Any]]) -> Tuple[str, Optional[int], Dict[str, Any]]:
    """Find the latest safe phase and iteration to resume from.

    Returns (phase, iteration, detail).
    """
    # Track completed phases
    completed_phases: Dict[str, Dict[str, Any]] = {}
    last_iteration: Optional[int] = None

    for event in events:
        if event.get("schema_version") != EVENT_SCHEMA_VERSION:
            continue

        phase = event.get("phase", "")
        event_name = event.get("event", "")
        iteration = event.get("iteration")

        if iteration is not None:
            last_iteration = iteration

        # Check if this event completes a phase
        expected = PHASE_COMPLETION_EVENTS.get(phase)
        if expected and event_name == expected:
            completed_phases[phase] = {
                "event_id": event.get("event_id"),
                "iteration": iteration,
                "timestamp": event.get("timestamp"),
                "detail": event.get("detail", {}),
            }

    # Find the latest safe phase in order
    latest_safe_phase = "setup"
    latest_detail: Dict[str, Any] = {}

    for phase in SAFE_PHASE_ORDER:
        if phase in completed_phases:
            latest_safe_phase = phase
            latest_detail = completed_phases[phase]

    return latest_safe_phase, last_iteration, latest_detail


# ---------------------------------------------------------------------------
# Resume plan
# ---------------------------------------------------------------------------

def build_resume_plan(
    run_dir: Path,
    events: List[Dict[str, Any]],
    manifest_entries: List[Dict[str, Any]],
    manifest_errors: List[str],
    event_errors: List[str],
) -> Dict[str, Any]:
    """Build a machine-readable resume plan."""
    safe_phase, last_iteration, phase_detail = find_latest_safe_point(events)

    # Determine next phase
    try:
        current_idx = SAFE_PHASE_ORDER.index(safe_phase)
        next_phase = SAFE_PHASE_ORDER[current_idx + 1] if current_idx + 1 < len(SAFE_PHASE_ORDER) else None
    except ValueError:
        next_phase = "setup"

    # Check for interrupted state
    has_interrupted = any(
        e.get("event") == "dispatch_incomplete" or e.get("event") == "review_failed"
        for e in events
        if e.get("schema_version") == EVENT_SCHEMA_VERSION
    )

    # Check for missing required artifacts
    missing_required = [
        err for err in manifest_errors
        if "Required artifact missing" in err
    ]

    # Determine if resume is safe
    resume_safe = (
        not event_errors
        and not manifest_errors
        and not has_interrupted
    )

    plan = {
        "schema_version": 1,
        "run_dir": str(run_dir),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "resume_safe": resume_safe,
        "latest_safe_phase": safe_phase,
        "latest_safe_iteration": last_iteration,
        "next_phase": next_phase,
        "phase_detail": phase_detail,
        "event_validation": {
            "total_events": len(events),
            "errors": event_errors[:20],
        },
        "manifest_validation": {
            "total_entries": len(manifest_entries),
            "errors": manifest_errors[:20],
        },
        "blockers": [],
    }

    if has_interrupted:
        plan["blockers"].append("Run was interrupted (dispatch_incomplete or review_failed)")
    if missing_required:
        plan["blockers"].extend(missing_required[:5])
    if manifest_errors and not missing_required:
        plan["blockers"].append(f"{len(manifest_errors)} manifest integrity error(s)")
    if event_errors:
        plan["blockers"].append(f"{len(event_errors)} event validation error(s)")

    return plan


def render_human_summary(plan: Dict[str, Any]) -> str:
    """Render a human-readable summary from the resume plan."""
    lines = [
        "# Run Resume Summary",
        "",
        f"Run directory: `{plan['run_dir']}`",
        f"Generated: {plan['generated_at']}",
        "",
        "## Status",
        "",
        f"- Resume safe: **{'yes' if plan['resume_safe'] else 'NO'}**",
        f"- Latest safe phase: **{plan['latest_safe_phase']}**",
        f"- Latest iteration: {plan['latest_safe_iteration'] or 'N/A'}",
        f"- Next phase: {plan['next_phase'] or 'run complete'}",
        "",
    ]

    blockers = plan.get("blockers", [])
    if blockers:
        lines.append("## Blockers")
        lines.append("")
        for b in blockers:
            lines.append(f"- {b}")
        lines.append("")

    # Event validation
    ev = plan["event_validation"]
    lines.append("## Event Validation")
    lines.append("")
    lines.append(f"- Total events: {ev['total_events']}")
    lines.append(f"- Errors: {len(ev['errors'])}")
    if ev["errors"]:
        for err in ev["errors"][:5]:
            lines.append(f"  - {err}")
    lines.append("")

    # Manifest validation
    mv = plan["manifest_validation"]
    lines.append("## Manifest Validation")
    lines.append("")
    lines.append(f"- Total entries: {mv['total_entries']}")
    lines.append(f"- Errors: {len(mv['errors'])}")
    if mv["errors"]:
        for err in mv["errors"][:5]:
            lines.append(f"  - {err}")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Apply (prepare next context only)
# ---------------------------------------------------------------------------

def apply_resume(run_dir: Path, plan: Dict[str, Any]) -> int:
    """Prepare the next task/context based on the resume plan.

    Does NOT invoke Claude/Codex. Only writes a resume-context file.
    """
    if not plan["resume_safe"]:
        print("Error: Cannot apply resume — blockers present.", file=sys.stderr)
        return 1

    context_path = run_dir / "resume-context.md"
    summary = render_human_summary(plan)
    context_path.write_text(summary, encoding="utf-8")
    print(f"Resume context written: {context_path}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate run state and output a resume plan."
    )
    parser.add_argument("run_dir", help="Run directory to analyze.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Prepare next task/context (does NOT invoke Claude/Codex).",
    )
    parser.add_argument("--output", help="Write JSON plan to this file.")
    parser.add_argument("--summary-output", help="Write human summary to this file.")
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir).resolve()
    if not run_dir.is_dir():
        print(f"Error: Run directory not found: {run_dir}", file=sys.stderr)
        return 1

    # Load and validate
    events, event_errors = load_events(run_dir)
    manifest_entries, manifest_errors = validate_manifest(run_dir)

    # Build plan
    plan = build_resume_plan(run_dir, events, manifest_entries, manifest_errors, event_errors)

    # Output JSON plan
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = run_dir / "resume-plan.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(plan, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Resume plan: {output_path}")

    # Output human summary
    summary = render_human_summary(plan)
    if args.summary_output:
        summary_path = Path(args.summary_output)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(summary, encoding="utf-8")
        print(f"Human summary: {summary_path}")
    else:
        print(summary)

    # Apply if requested
    if args.apply:
        return apply_resume(run_dir, plan)

    # Exit code: 0 if safe, 1 if blockers
    return 0 if plan["resume_safe"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
