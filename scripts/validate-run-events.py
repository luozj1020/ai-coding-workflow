#!/usr/bin/env python3
"""validate-run-events.py — Validate a run's event log for v2 schema compliance.

Checks:
- Schema validation for each event
- Causal parent existence and ordering
- Duplicate event IDs
- Referenced artifact presence (optional, --check-artifacts)

Python 3.9+ compatible. No third-party dependencies.

Usage:
    python scripts/validate-run-events.py <events_path> [--run-dir DIR] [--check-artifacts]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Set

# Make scripts/ importable
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from event_writer import SCHEMA_VERSION, validate_event


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ValidationError:
    """A single validation finding."""
    def __init__(self, line_num: int, event_id: str, message: str, severity: str = "error"):
        self.line_num = line_num
        self.event_id = event_id
        self.message = message
        self.severity = severity

    def __str__(self) -> str:
        prefix = "ERROR" if self.severity == "error" else "WARN"
        return f"[{prefix}] line {self.line_num} (event_id={self.event_id}): {self.message}"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_events_log(
    events_path: Path,
    run_dir: Path | None = None,
    check_artifacts: bool = False,
) -> List[ValidationError]:
    """Validate all events in a JSONL log.

    Returns a list of ValidationErrors (empty if all valid).
    """
    errors: List[ValidationError] = []

    if not events_path.exists():
        errors.append(ValidationError(0, "", f"Events file not found: {events_path}"))
        return errors

    lines = events_path.read_text(encoding="utf-8").splitlines()
    seen_ids: Set[str] = set()
    events_by_line: Dict[int, Dict] = {}

    # Pass 1: parse and validate each event individually
    for line_num, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue

        import json
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as e:
            errors.append(ValidationError(line_num, "", f"Malformed JSON: {e}"))
            continue

        events_by_line[line_num] = data

        event_id = data.get("event_id", "<missing>")

        # Schema validation
        schema_errors = validate_event(data)
        for err in schema_errors:
            errors.append(ValidationError(line_num, event_id, err))

        # Duplicate event_id
        if event_id in seen_ids:
            errors.append(ValidationError(line_num, event_id, f"Duplicate event_id: {event_id}"))
        seen_ids.add(event_id)

        # Legacy format detection
        sv = data.get("schema_version")
        if sv is not None and sv != SCHEMA_VERSION:
            errors.append(ValidationError(
                line_num, event_id,
                f"schema_version={sv} is not v2; legacy events should be reported, not validated",
                severity="warn",
            ))

    # Pass 2: check causal parent existence
    event_ids_at_line: Dict[str, int] = {}
    for line_num, data in events_by_line.items():
        eid = data.get("event_id", "")
        if eid:
            event_ids_at_line[eid] = line_num

    for line_num, data in events_by_line.items():
        event_id = data.get("event_id", "<missing>")
        parent_id = data.get("parent_event_id")

        if parent_id is not None:
            if parent_id not in event_ids_at_line:
                errors.append(ValidationError(
                    line_num, event_id,
                    f"parent_event_id '{parent_id}' not found in event log",
                ))
            else:
                parent_line = event_ids_at_line[parent_id]
                if parent_line >= line_num:
                    errors.append(ValidationError(
                        line_num, event_id,
                        f"parent_event_id '{parent_id}' appears at line {parent_line} "
                        f"(must appear before line {line_num})",
                    ))

    # Pass 3: check referenced artifacts exist (if requested)
    if check_artifacts and run_dir:
        for line_num, data in events_by_line.items():
            event_id = data.get("event_id", "<missing>")
            refs = data.get("artifact_refs", [])
            for ref in refs:
                artifact_path = run_dir / ref
                if not artifact_path.exists():
                    errors.append(ValidationError(
                        line_num, event_id,
                        f"Referenced artifact not found: {ref}",
                        severity="warn",
                    ))

    return errors


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a run's event log for v2 schema compliance."
    )
    parser.add_argument("events_path", help="Path to the JSONL events file.")
    parser.add_argument("--run-dir", help="Run directory for artifact existence checks.")
    parser.add_argument(
        "--check-artifacts",
        action="store_true",
        help="Verify that referenced artifact files exist.",
    )
    parser.add_argument(
        "--legacy-report",
        action="store_true",
        help="Report legacy (v1) events instead of validating v2.",
    )
    args = parser.parse_args(argv)

    events_path = Path(args.events_path)

    if args.legacy_report:
        from event_writer import report_legacy_events
        legacy = report_legacy_events(events_path)
        if legacy:
            print(f"Found {len(legacy)} legacy event(s):")
            for event in legacy:
                print(f"  event_id={event.get('event_id', '?')} event={event.get('event', '?')}")
            return 1
        else:
            print("No legacy events found.")
            return 0

    run_dir = Path(args.run_dir) if args.run_dir else None
    errors = validate_events_log(events_path, run_dir, args.check_artifacts)

    if not errors:
        print("ALL VALID")
        return 0

    error_count = sum(1 for e in errors if e.severity == "error")
    warn_count = sum(1 for e in errors if e.severity == "warn")

    for err in errors:
        print(str(err), file=sys.stderr)

    print(f"\n{error_count} error(s), {warn_count} warning(s)")
    return 1 if error_count > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
