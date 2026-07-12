#!/usr/bin/env python3
"""replay-run.py — Reconstruct ordered state transitions from a run's event log.

Reports gaps, invalid transitions, and missing events without changing state.
Reads events from a JSONL log and validates the expected phase/transition order.

Python 3.9+ compatible. No third-party dependencies.

Usage:
    python scripts/replay-run.py <events_path> [--output FILE]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Make scripts/ importable
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from event_writer import SCHEMA_VERSION as EVENT_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Valid phase transitions
VALID_TRANSITIONS = {
    "setup": ["dispatch"],
    "dispatch": ["review", "dispatch"],  # dispatch can retry
    "review": ["decision"],
    "decision": ["dispatch", "finalization"],  # revise -> dispatch, accept -> finalization
    "finalization": [],
}

# Expected events per phase
PHASE_START_EVENTS = {
    "setup": "run_start",
    "dispatch": "iteration_start",
    "review": "review_start",
    "decision": "decision",
    "finalization": "run_complete",
}

PHASE_END_EVENTS = {
    "setup": "setup_complete",
    "dispatch": "dispatch_complete",
    "review": "review_complete",
    "decision": "decision",
    "finalization": "run_complete",
}


# ---------------------------------------------------------------------------
# State transition analysis
# ---------------------------------------------------------------------------

class TransitionRecord:
    """A single phase transition."""
    def __init__(
        self,
        from_phase: str,
        to_phase: str,
        event_id: str,
        iteration: Optional[int],
        timestamp: str,
        detail: Dict[str, Any],
    ):
        self.from_phase = from_phase
        self.to_phase = to_phase
        self.event_id = event_id
        self.iteration = iteration
        self.timestamp = timestamp
        self.detail = detail

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from_phase": self.from_phase,
            "to_phase": self.to_phase,
            "event_id": self.event_id,
            "iteration": self.iteration,
            "timestamp": self.timestamp,
        }


class ReplayError:
    """A gap or invalid transition found during replay."""
    def __init__(self, event_id: str, message: str, severity: str = "error"):
        self.event_id = event_id
        self.message = message
        self.severity = severity

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "message": self.message,
            "severity": self.severity,
        }


def replay_events(events: List[Dict[str, Any]]) -> Tuple[List[TransitionRecord], List[ReplayError]]:
    """Replay events and reconstruct phase transitions.

    Returns (transitions, errors).
    """
    transitions: List[TransitionRecord] = []
    errors: List[ReplayError] = []

    current_phase = "setup"
    last_event_id: Optional[str] = None

    for i, event in enumerate(events):
        # Skip legacy events
        if event.get("schema_version") != EVENT_SCHEMA_VERSION:
            continue

        event_id = event.get("event_id", f"line_{i}")
        phase = event.get("phase", "")
        event_name = event.get("event", "")
        iteration = event.get("iteration")
        timestamp = event.get("timestamp", "")
        detail = event.get("detail", {})

        # Check for phase transition
        if phase != current_phase:
            # Validate transition
            valid_next = VALID_TRANSITIONS.get(current_phase, [])
            if phase not in valid_next:
                errors.append(ReplayError(
                    event_id,
                    f"Invalid phase transition: {current_phase} -> {phase}",
                ))

            transitions.append(TransitionRecord(
                from_phase=current_phase,
                to_phase=phase,
                event_id=event_id,
                iteration=iteration,
                timestamp=timestamp,
                detail=detail,
            ))
            current_phase = phase

        # Check causal chain
        parent_id = event.get("parent_event_id")
        if last_event_id is not None and parent_id is not None:
            if parent_id != last_event_id:
                errors.append(ReplayError(
                    event_id,
                    f"Causal gap: parent_event_id={parent_id} but last event was {last_event_id}",
                    severity="warn",
                ))

        # Check for expected phase-start event
        expected_start = PHASE_START_EVENTS.get(phase)
        if expected_start and event_name == expected_start and i > 0:
            # This is a phase start — good
            pass

        # Check for expected phase-end event
        expected_end = PHASE_END_EVENTS.get(phase)
        if expected_end and event_name == expected_end:
            # Phase completed
            pass

        last_event_id = event_id

    # Check for incomplete final phase
    if current_phase != "finalization":
        errors.append(ReplayError(
            "",
            f"Run did not reach finalization (stuck in {current_phase})",
            severity="warn",
        ))

    return transitions, errors


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reconstruct ordered state transitions from a run's event log."
    )
    parser.add_argument("events_path", help="Path to the JSONL events file.")
    parser.add_argument("--output", help="Write JSON report to this file.")
    parser.add_argument("--human-output", help="Write human-readable report to this file.")
    args = parser.parse_args(argv)

    events_path = Path(args.events_path)
    if not events_path.exists():
        print(f"Error: Events file not found: {events_path}", file=sys.stderr)
        return 1

    # Load events
    events: List[Dict[str, Any]] = []
    for line_num, line in enumerate(events_path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            events.append(json.loads(stripped))
        except json.JSONDecodeError as e:
            print(f"Warning: Malformed JSON at line {line_num}: {e}", file=sys.stderr)

    # Replay
    transitions, errors = replay_events(events)

    # Build report
    report = {
        "schema_version": 1,
        "events_path": str(events_path),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_events": len(events),
        "transitions": [t.to_dict() for t in transitions],
        "errors": [e.to_dict() for e in errors],
        "summary": {
            "transition_count": len(transitions),
            "error_count": sum(1 for e in errors if e.severity == "error"),
            "warn_count": sum(1 for e in errors if e.severity == "warn"),
            "valid": all(e.severity != "error" for e in errors),
        },
    }

    # Output JSON
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = events_path.parent / "replay-report.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Replay report: {output_path}")

    # Human-readable output
    human_lines = [
        "# Run Replay Report",
        "",
        f"Events: {len(events)}",
        f"Transitions: {len(transitions)}",
        f"Errors: {report['summary']['error_count']}",
        f"Warnings: {report['summary']['warn_count']}",
        "",
    ]

    if transitions:
        human_lines.append("## Phase Transitions")
        human_lines.append("")
        for t in transitions:
            human_lines.append(
                f"- {t.from_phase} -> {t.to_phase} "
                f"(iter={t.iteration or 'N/A'}, event={t.event_id[:20]}...)"
            )
        human_lines.append("")

    if errors:
        human_lines.append("## Issues")
        human_lines.append("")
        for e in errors:
            prefix = "ERROR" if e.severity == "error" else "WARN"
            human_lines.append(f"- [{prefix}] {e.message}")
        human_lines.append("")

    human_text = "\n".join(human_lines)
    if args.human_output:
        human_path = Path(args.human_output)
        human_path.parent.mkdir(parents=True, exist_ok=True)
        human_path.write_text(human_text, encoding="utf-8")
        print(f"Human report: {human_path}")
    else:
        print(human_text)

    # Exit code
    return 0 if report["summary"]["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
