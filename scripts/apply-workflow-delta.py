#!/usr/bin/env python3
"""Apply semantic state events to Workflow State IR.

Delta shape: {"schema_version": 1, "base_state_id": "sha256:...",
"events": [{"event_type": "...", "payload": {...}}, ...]}.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workflow_state import (  # noqa: E402
    WorkflowStateError, append_events, apply_mutation, atomic_write_json,
    exclusive_file_lock, finalize_transition, load_events, load_json,
    validate_event, validate_state,
)


def apply_delta(args: argparse.Namespace) -> dict:
    state = load_json(args.state)
    errors = validate_state(state)
    if errors:
        raise WorkflowStateError("invalid current state: " + "; ".join(errors))
    prior_events = load_events(args.events)
    if not prior_events or prior_events[-1].get("new_state_id") != state["state_id"]:
        raise WorkflowStateError("event log tail does not match current state")
    delta = load_json(args.delta)
    if set(delta) != {"schema_version", "base_state_id", "events"}:
        raise WorkflowStateError("delta must contain exactly schema_version, base_state_id, and events")
    if delta.get("schema_version") != 1:
        raise WorkflowStateError("delta schema_version must be 1")
    if delta.get("base_state_id") != state["state_id"]:
        raise WorkflowStateError("delta base_state_id does not match current state")
    mutations = delta.get("events")
    if not isinstance(mutations, list) or not mutations:
        raise WorkflowStateError("delta.events must be a non-empty array")
    new_events = []
    current = state
    for index, mutation in enumerate(mutations):
        if not isinstance(mutation, dict) or set(mutation) != {"event_type", "payload"}:
            raise WorkflowStateError(f"delta.events[{index}] must contain exactly event_type and payload")
        event_type = mutation["event_type"]
        payload = mutation["payload"]
        mutated = apply_mutation(current, event_type, payload)
        current, event = finalize_transition(
            current, mutated, actor=args.actor, event_type=event_type, payload=payload,
        )
        state_errors = validate_state(current)
        event_errors = validate_event(event)
        if state_errors or event_errors:
            raise WorkflowStateError("invalid transition: " + "; ".join(state_errors + event_errors))
        new_events.append(event)
    # Event-first persistence is recoverable: validation identifies a tail
    # ahead of state after interruption, while no state can exist untraced.
    append_events(args.events, new_events)
    atomic_write_json(args.state, current)
    return {
        "state_id": current["state_id"], "revision": current["revision"],
        "events_applied": len(new_events),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--delta", type=Path, required=True)
    parser.add_argument("--actor", required=True)
    args = parser.parse_args(argv)
    try:
        with exclusive_file_lock(args.state):
            result = apply_delta(args)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, WorkflowStateError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
