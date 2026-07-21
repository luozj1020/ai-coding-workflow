#!/usr/bin/env python3
"""Validate Workflow State IR, its canonical hash, and complete event replay."""
from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workflow_state import (  # noqa: E402
    WorkflowStateError, apply_mutation, load_events, load_json, state_id_for,
    validate_event, validate_state,
)


def replay(events: list[dict]) -> dict:
    if not events:
        raise WorkflowStateError("event log is empty")
    first = events[0]
    if first.get("event_type") != "state-initialized" or first.get("base_state_id") is not None:
        raise WorkflowStateError("first event must be state-initialized with null base_state_id")
    material = first.get("payload", {}).get("initial_state")
    if not isinstance(material, dict):
        raise WorkflowStateError("initial event must contain payload.initial_state")
    current = deepcopy(material)
    current["state_id"] = state_id_for(current)
    if current["state_id"] != first.get("new_state_id"):
        raise WorkflowStateError("initial event new_state_id does not match replayed state")
    for index, event in enumerate(events[1:], 2):
        if event.get("base_state_id") != current["state_id"]:
            raise WorkflowStateError(f"event {index} base_state_id breaks the state chain")
        mutated = apply_mutation(current, event["event_type"], event["payload"])
        mutated["parent_state_id"] = current["state_id"]
        mutated["revision"] = current["revision"] + 1
        mutated["state_id"] = state_id_for(mutated)
        if mutated["state_id"] != event.get("new_state_id"):
            raise WorkflowStateError(f"event {index} new_state_id does not match replayed state")
        current = mutated
    return current


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        state = load_json(args.state)
        events = load_events(args.events)
        errors = validate_state(state)
        for index, event in enumerate(events, 1):
            errors.extend(f"event {index}: {error}" for error in validate_event(event))
        if errors:
            raise WorkflowStateError("; ".join(errors))
        replayed = replay(events)
        if replayed != state:
            raise WorkflowStateError("replayed state does not equal WORKFLOW_STATE.json")
        print(json.dumps({"valid": True, "state_id": state["state_id"], "revision": state["revision"], "event_count": len(events)}, sort_keys=True))
        return 0
    except (OSError, WorkflowStateError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
