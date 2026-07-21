#!/usr/bin/env python3
"""Initialize WORKFLOW_STATE.json and WORKFLOW_EVENTS.jsonl from a Task JSON."""
from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workflow_state import (  # noqa: E402
    EVENTS_FILE, SCHEMA_VERSION, STATE_FILE, WorkflowStateError, append_events,
    atomic_write_json, event_id_for, load_json, state_id_for, validate_event,
    validate_state,
)


def constraint_items(task: dict) -> list[dict]:
    items = []
    handoff = task.get("handoff") if isinstance(task.get("handoff"), dict) else {}
    groups = (
        ("must_do", "C-MUST", "user-specified"),
        ("must_not_do", "C-MUST-NOT", "user-specified"),
        ("stop_condition", "C-HANDOFF-STOP", "user-specified"),
    )
    for field, prefix, source in groups:
        values = handoff.get(field, [])
        if isinstance(values, list):
            for index, statement in enumerate(values, 1):
                if isinstance(statement, str) and statement:
                    items.append({"id": f"{prefix}-{index}", "statement": statement, "source": source, "frozen": True})
    for index, statement in enumerate(task.get("stop_conditions", []), 1):
        if isinstance(statement, str) and statement:
            items.append({"id": f"C-STOP-{index}", "statement": statement, "source": "user-specified", "frozen": True})
    scope = task.get("scope") if isinstance(task.get("scope"), dict) else {}
    for index, path in enumerate(scope.get("forbidden_paths", []), 1):
        if isinstance(path, str) and path:
            items.append({"id": f"C-FORBIDDEN-{index}", "statement": f"Do not modify {path}", "source": "task-scope", "frozen": True})
    return sorted(items, key=lambda item: item["id"])


def build_initial_state(task: dict, repository_state_hash: str, phase: str) -> dict:
    task_id = task.get("id")
    goal_statement = task.get("goal")
    if not isinstance(task_id, str) or not task_id:
        raise WorkflowStateError("task.id must be a non-empty string")
    if not isinstance(goal_statement, str) or not goal_statement:
        raise WorkflowStateError("task.goal must be a non-empty string")
    acceptance = task.get("acceptance")
    if not isinstance(acceptance, list) or not acceptance:
        raise WorkflowStateError("task.acceptance must be a non-empty array")
    acceptance_status = {}
    for item in acceptance:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            raise WorkflowStateError("each task.acceptance item requires a non-empty id")
        if item["id"] in acceptance_status:
            raise WorkflowStateError(f"duplicate acceptance id: {item['id']}")
        description = item.get("description")
        if not isinstance(description, str) or not description:
            raise WorkflowStateError(f"acceptance {item['id']} requires a description")
        acceptance_status[item["id"]] = {"description": description, "status": "pending", "evidence_refs": []}
    scope = task.get("scope") if isinstance(task.get("scope"), dict) else {}
    allowed_paths = scope.get("write_paths", [])
    if not isinstance(allowed_paths, list) or not all(isinstance(path, str) and path for path in allowed_paths):
        raise WorkflowStateError("task.scope.write_paths must be an array of non-empty strings")
    owner = {
        "builder": "execution-builder",
        "checker-test": "checker-test",
        "mixed-exception": "execution-builder",
        "control-plane": "control-plane",
    }.get(task.get("mode"), str(task.get("mode") or "execution-builder"))
    state = {
        "schema_version": SCHEMA_VERSION,
        "state_id": "",
        "parent_state_id": None,
        "revision": 0,
        "task_id": task_id,
        "phase": phase,
        "repository_state_hash": repository_state_hash,
        "goal": {"id": "G-1", "statement": goal_statement, "acceptance_ids": sorted(acceptance_status)},
        "constraints": constraint_items(task),
        "accepted_decisions": [],
        "rejected_hypotheses": [],
        "open_questions": [],
        "evidence_refs": [],
        "acceptance_status": dict(sorted(acceptance_status.items())),
        "next_action": {"owner": owner, "operation": goal_statement, "allowed_paths": list(allowed_paths)},
    }
    state["state_id"] = state_id_for(state)
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=Path, required=True, help="Existing Task JSON")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--repository-state-hash", required=True, help="Hash from worktree_state_hash.py or another explicit repository binding")
    parser.add_argument("--phase", choices=("planning", "implementation", "verification", "review", "revision", "complete"), default="planning")
    parser.add_argument("--actor", default="planner")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    state_path = args.run_dir / STATE_FILE
    events_path = args.run_dir / EVENTS_FILE
    try:
        if (state_path.exists() or events_path.exists()) and not args.force:
            raise WorkflowStateError("state artifacts already exist; use --force to replace them")
        task = load_json(args.task)
        if not isinstance(task, dict):
            raise WorkflowStateError("task must be a JSON object")
        state = build_initial_state(task, args.repository_state_hash, args.phase)
        errors = validate_state(state)
        if errors:
            raise WorkflowStateError("; ".join(errors))
        initial_material = deepcopy(state)
        initial_material.pop("state_id")
        event = {
            "schema_version": SCHEMA_VERSION,
            "event_id": "",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "actor": args.actor,
            "event_type": "state-initialized",
            "base_state_id": None,
            "new_state_id": state["state_id"],
            "payload": {"initial_state": initial_material, "task_source": str(args.task)},
        }
        event["event_id"] = event_id_for(event)
        event_errors = validate_event(event)
        if event_errors:
            raise WorkflowStateError("; ".join(event_errors))
        args.run_dir.mkdir(parents=True, exist_ok=True)
        if args.force:
            events_path.unlink(missing_ok=True)
        append_events(events_path, [event])
        atomic_write_json(state_path, state)
        print(json.dumps({"state": str(state_path), "events": str(events_path), "state_id": state["state_id"]}, sort_keys=True))
        return 0
    except (OSError, json.JSONDecodeError, WorkflowStateError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
