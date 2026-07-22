#!/usr/bin/env python3
"""Preview or recover Workflow State from its authoritative event chain."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workflow_state import (  # noqa: E402
    WorkflowStateError, atomic_write_json, canonical_json, exclusive_file_lock,
    load_events, load_json, replay_events, validate_event, validate_state,
)


RECOVERY_RECEIPT_FILE = "WORKFLOW_RECOVERY_RECEIPT.json"


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _same_path(first: Path, second: Path) -> bool:
    return first.resolve(strict=False) == second.resolve(strict=False)


def assess(state_path: Path, events_path: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if events_path.is_symlink():
        raise WorkflowStateError("events path must not be a symlink")
    events = load_events(events_path)
    event_errors: List[str] = []
    for index, event in enumerate(events, 1):
        event_errors.extend(
            "event {}: {}".format(index, error) for error in validate_event(event)
        )
    if event_errors:
        raise WorkflowStateError("invalid event chain: " + "; ".join(event_errors))
    recovered = replay_events(events)

    current: Optional[Dict[str, Any]] = None
    if state_path.exists():
        if state_path.is_symlink():
            raise WorkflowStateError("state path must not be a symlink")
        current = load_json(state_path)
        state_errors = validate_state(current)
        if state_errors:
            raise WorkflowStateError("invalid current state: " + "; ".join(state_errors))

    if current is None:
        classification = "state-missing"
        recoverable = True
    elif current == recovered:
        classification = "in-sync"
        recoverable = False
    else:
        chain_state_ids = [event.get("new_state_id") for event in events]
        current_id = current.get("state_id")
        if current_id in chain_state_ids[:-1]:
            classification = "event-ahead"
            recoverable = True
        elif (
            current.get("parent_state_id") == recovered.get("state_id")
            or current.get("revision", -1) > recovered.get("revision", -1)
        ):
            classification = "state-ahead"
            recoverable = False
        else:
            classification = "diverged"
            recoverable = False

    report = {
        "schema_version": 1,
        "classification": classification,
        "recoverable": recoverable,
        "recovery_required": classification in {"event-ahead", "state-missing"},
        "current_state_id": current.get("state_id") if current else None,
        "current_revision": current.get("revision") if current else None,
        "event_tail_state_id": recovered["state_id"],
        "event_tail_revision": recovered["revision"],
        "event_count": len(events),
    }
    return report, recovered


def build_receipt(
    report: Dict[str, Any], events_path: Path, state_path: Path,
) -> Dict[str, Any]:
    receipt = {
        "schema_version": 1,
        "receipt_id": "",
        "recovered_at": datetime.now(timezone.utc).isoformat(),
        "classification": report["classification"],
        "before_state_id": report["current_state_id"],
        "after_state_id": report["event_tail_state_id"],
        "before_revision": report["current_revision"],
        "after_revision": report["event_tail_revision"],
        "event_count": report["event_count"],
        "event_log_sha256": _sha256_bytes(events_path.read_bytes()),
        "state_path": str(state_path),
        "events_path": str(events_path),
    }
    material = dict(receipt)
    material.pop("receipt_id")
    receipt["receipt_id"] = _sha256_bytes(canonical_json(material).encode("utf-8"))
    return receipt


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preview", action="store_true", help="Inspect only; this is the default")
    mode.add_argument("--apply", action="store_true", help="Atomically rebuild recoverable state")
    parser.add_argument("--receipt", type=Path, help="Apply-mode receipt path")
    args = parser.parse_args(argv)

    receipt_path = args.receipt or args.state.parent / RECOVERY_RECEIPT_FILE
    try:
        if _same_path(args.state, args.events):
            raise WorkflowStateError("state and events paths must be distinct")
        if _same_path(receipt_path, args.state) or _same_path(receipt_path, args.events):
            raise WorkflowStateError("receipt path must be distinct from state and events paths")
        if receipt_path.exists() and receipt_path.is_symlink():
            raise WorkflowStateError("receipt path must not be a symlink")
        with exclusive_file_lock(args.state):
            report, recovered = assess(args.state, args.events)
            classification = report["classification"]
            if args.apply:
                if classification in {"state-ahead", "diverged"}:
                    raise WorkflowStateError(
                        "{} state cannot be recovered from the event log".format(classification)
                    )
                if report["recovery_required"]:
                    atomic_write_json(args.state, recovered)
                    receipt = build_receipt(report, args.events, args.state)
                    atomic_write_json(receipt_path, receipt)
                    report["applied"] = True
                    report["receipt"] = str(receipt_path)
                    report["receipt_id"] = receipt["receipt_id"]
                else:
                    report["applied"] = False
            else:
                report["applied"] = False
                report["mode"] = "preview"
                if classification in {"state-ahead", "diverged"}:
                    print(json.dumps(report, sort_keys=True))
                    return 1
        print(json.dumps(report, sort_keys=True))
        return 0
    except (OSError, WorkflowStateError) as exc:
        print("Error: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
