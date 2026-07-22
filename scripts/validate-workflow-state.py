#!/usr/bin/env python3
"""Validate Workflow State IR, its canonical hash, and complete event replay."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workflow_state import (  # noqa: E402
    WorkflowStateError, load_events, load_json, replay_events, validate_event,
    validate_state,
)


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
        replayed = replay_events(events)
        if replayed != state:
            raise WorkflowStateError("replayed state does not equal WORKFLOW_STATE.json")
        print(json.dumps({"valid": True, "state_id": state["state_id"], "revision": state["revision"], "event_count": len(events)}, sort_keys=True))
        return 0
    except (OSError, WorkflowStateError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
