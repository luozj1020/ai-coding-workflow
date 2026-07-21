#!/usr/bin/env python3
"""Summarize schema-valid handoff_recorded events without fabricating data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


UNKNOWN = "unknown"
MAX_EVENT_INPUT_BYTES = 64 * 1024 * 1024
MAX_EVENT_LINE_BYTES = 4 * 1024 * 1024
TEXT_FIELDS = ("sender", "receiver", "task_type", "dispatch_outcome")
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


def _known_number(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _validate_detail(detail: Any, path: Path, line_number: int) -> Dict[str, Any]:
    if not isinstance(detail, dict):
        raise ValueError("{}:{}: handoff detail must be an object".format(path, line_number))
    expected = {"schema_version", *TEXT_FIELDS, *MEASUREMENT_FIELDS}
    if set(detail) != expected or detail.get("schema_version") != 1:
        raise ValueError("{}:{}: invalid handoff detail fields".format(path, line_number))
    if any(not isinstance(detail.get(field), str) or not detail[field] for field in TEXT_FIELDS):
        raise ValueError("{}:{}: invalid handoff text field".format(path, line_number))
    if any(
        detail.get(field) != UNKNOWN and not _known_number(detail.get(field))
        for field in MEASUREMENT_FIELDS
    ):
        raise ValueError("{}:{}: invalid handoff measurement".format(path, line_number))
    return detail


def read_handoff_events(paths: Iterable[Path]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    seen_paths = set()
    for path in paths:
        try:
            resolved = str(path.resolve())
        except OSError:
            resolved = str(path)
        if resolved in seen_paths or not path.is_file():
            continue
        if path.stat().st_size > MAX_EVENT_INPUT_BYTES:
            raise ValueError(f"{path}: exceeds {MAX_EVENT_INPUT_BYTES} byte input limit")
        seen_paths.add(resolved)
        for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not raw.strip():
                continue
            if len(raw.encode("utf-8")) > MAX_EVENT_LINE_BYTES:
                raise ValueError(
                    f"{path}:{line_number}: exceeds {MAX_EVENT_LINE_BYTES} byte event limit"
                )
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError("{}:{}: malformed JSON: {}".format(path, line_number, exc))
            if isinstance(value, dict) and value.get("event") == "handoff_recorded":
                _validate_detail(value.get("detail"), path, line_number)
                events.append(value)
    return events


def _total_when_known(details: List[Dict[str, Any]], field: str) -> Any:
    values = [detail.get(field, UNKNOWN) for detail in details]
    if not values or any(not _known_number(value) for value in values):
        return UNKNOWN
    return sum(values)


def _ratio(numerator: Any, denominator: Any) -> Any:
    if not _known_number(numerator) or not _known_number(denominator) or denominator == 0:
        return UNKNOWN
    return round(numerator / denominator, 6)


def summarize_events(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    details = [event.get("detail", {}) for event in events if isinstance(event.get("detail"), dict)]
    totals = {field: _total_when_known(details, field) for field in MEASUREMENT_FIELDS}
    unknown_counts = {
        field: sum(1 for detail in details if not _known_number(detail.get(field)))
        for field in MEASUREMENT_FIELDS
    }
    task_types: Dict[str, int] = {}
    for detail in details:
        task_type = detail.get("task_type")
        if not isinstance(task_type, str) or not task_type:
            task_type = UNKNOWN
        task_types[task_type] = task_types.get(task_type, 0) + 1

    known_revision_events = [
        detail["handoff_revision_count"]
        for detail in details
        if _known_number(detail.get("handoff_revision_count"))
    ]
    revision_rate: Any = UNKNOWN
    if known_revision_events:
        revision_rate = round(
            sum(1 for value in known_revision_events if value > 0) / len(known_revision_events),
            6,
        )

    return {
        "schema_version": 1,
        "handoff_count": len(details),
        "totals": totals,
        "unknown_counts": unknown_counts,
        "by_task_type": dict(sorted(task_types.items())),
        "payload_redundancy_rate": _ratio(
            totals["repeated_payload_bytes"], totals["payload_bytes"]
        ),
        "context_cache_hit_rate": _ratio(
            totals["context_cache_hits"], totals["context_objects_requested"]
        ),
        "handoff_induced_revision_rate": revision_rate,
    }


def summarize_paths(paths: Iterable[Path]) -> Dict[str, Any]:
    return summarize_events(read_handoff_events(paths))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("events", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        summary = summarize_paths(args.events)
    except (OSError, ValueError) as exc:
        print("Error: {}".format(exc), file=sys.stderr)
        return 1
    rendered = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
