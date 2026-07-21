#!/usr/bin/env python3
"""Record one schema-valid cross-model handoff in a run-event v2 JSONL log."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from event_writer import EventWriter, build_event


UNKNOWN = "unknown"
HANDOFF_SCHEMA_VERSION = 1
MEASUREMENT_FIELDS = (
    "payload_bytes",
    "novel_payload_bytes",
    "repeated_payload_bytes",
    "task_card_bytes",
    "review_packet_bytes",
    "receiver_reads_before_first_action",
    "receiver_searches_before_first_action",
    "seconds_to_first_meaningful_action",
    "known_facts_rediscovered",
    "rejected_hypotheses_revisited",
    "handoff_revision_count",
    "context_objects_requested",
    "context_cache_hits",
)
TEXT_FIELDS = ("sender", "receiver", "task_type", "dispatch_outcome")
Measurement = Union[int, str]


def parse_measurement(value: Any) -> Measurement:
    """Return a non-negative integer or the literal ``unknown``."""
    if value is None or value == UNKNOWN or value == "":
        return UNKNOWN
    if isinstance(value, bool):
        raise ValueError("boolean is not a measurement")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.isdigit():
        parsed = int(value)
    else:
        raise ValueError("measurement must be a non-negative integer or 'unknown'")
    if parsed < 0:
        raise ValueError("measurement must be non-negative")
    return parsed


def validate_handoff_detail(detail: Dict[str, Any]) -> List[str]:
    """Validate the strict handoff detail contract without third-party packages."""
    errors: List[str] = []
    expected = {"schema_version", *TEXT_FIELDS, *MEASUREMENT_FIELDS}
    extra = sorted(set(detail) - expected)
    missing = sorted(expected - set(detail))
    if extra:
        errors.append("unexpected fields: {}".format(", ".join(extra)))
    if missing:
        errors.append("missing fields: {}".format(", ".join(missing)))
    if detail.get("schema_version") != HANDOFF_SCHEMA_VERSION:
        errors.append("schema_version must be 1")
    for field in TEXT_FIELDS:
        value = detail.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append("{} must be a non-empty string".format(field))
    for field in MEASUREMENT_FIELDS:
        value = detail.get(field)
        if value == UNKNOWN:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            errors.append("{} must be a non-negative integer or 'unknown'".format(field))
    return errors


def build_handoff_detail(
    *,
    sender: str,
    receiver: str,
    task_type: str = UNKNOWN,
    dispatch_outcome: str = UNKNOWN,
    **measurements: Any,
) -> Dict[str, Any]:
    detail: Dict[str, Any] = {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "sender": sender,
        "receiver": receiver,
        "task_type": task_type or UNKNOWN,
        "dispatch_outcome": dispatch_outcome or UNKNOWN,
    }
    for field in MEASUREMENT_FIELDS:
        detail[field] = parse_measurement(measurements.get(field, UNKNOWN))
    errors = validate_handoff_detail(detail)
    if errors:
        raise ValueError("; ".join(errors))
    return detail


def record_handoff(
    events_path: Path,
    *,
    run_id: str,
    task_id: str,
    detail: Dict[str, Any],
) -> str:
    if events_path.suffix.lower() != ".jsonl":
        raise ValueError("events path must end in .jsonl")
    if events_path.exists() and events_path.is_dir():
        raise ValueError("events path must be a file")
    errors = validate_handoff_detail(detail)
    if errors:
        raise ValueError("; ".join(errors))
    event = build_event(
        run_id=run_id,
        task_id=task_id,
        event="handoff_recorded",
        phase="dispatch",
        role="dispatch",
        detail=detail,
    )
    return EventWriter(events_path).append(event)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events-path", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--sender", required=True)
    parser.add_argument("--receiver", required=True)
    parser.add_argument("--task-type", default=UNKNOWN)
    parser.add_argument("--dispatch-outcome", default=UNKNOWN)
    for field in MEASUREMENT_FIELDS:
        parser.add_argument("--" + field.replace("_", "-"), default=UNKNOWN)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        detail = build_handoff_detail(
            sender=args.sender,
            receiver=args.receiver,
            task_type=args.task_type,
            dispatch_outcome=args.dispatch_outcome,
            **{field: getattr(args, field) for field in MEASUREMENT_FIELDS},
        )
        event_id = record_handoff(
            args.events_path,
            run_id=args.run_id,
            task_id=args.task_id,
            detail=detail,
        )
    except (OSError, ValueError) as exc:
        print("Error: {}".format(exc), file=sys.stderr)
        return 1
    print(json.dumps({"event_id": event_id, "status": "recorded"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
