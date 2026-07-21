#!/usr/bin/env python3
"""Deterministically render a compatible Markdown Task Card from State IR."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workflow_state import WorkflowStateError, load_json, validate_state  # noqa: E402


def bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "_(none)_"


def render(state: dict) -> str:
    goal = state["goal"]
    constraints = [f"{item['id']} [frozen={str(item['frozen']).lower()}]: {item['statement']}" for item in state["constraints"]]
    decisions = [f"{item['id']} [{item['status']}]: {item['statement']}" for item in state["accepted_decisions"]]
    rejected = [f"{item['id']}: {item['statement']} — rejected because {item['reason']}" for item in state["rejected_hypotheses"]]
    questions = [f"{item['id']}: {item['question']}" for item in state["open_questions"]]
    acceptance_rows = [
        f"| {key} | {value['description']} | {value['status']} | {', '.join(value['evidence_refs']) or '_(none)_'} |"
        for key, value in sorted(state["acceptance_status"].items())
    ]
    action = state["next_action"]
    sections = [
        f"<!-- workflow-state: schema=1; state_id={state['state_id']}; revision={state['revision']} -->",
        f"# Task Card: {goal['statement']}",
        "## Task Identity\n\n| Field | Value |\n| --- | --- |\n"
        f"| ID | {state['task_id']} |\n| Phase | {state['phase']} |\n| State ID | `{state['state_id']}` |\n"
        f"| Repository State | `{state['repository_state_hash']}` |",
        f"## Goal\n\n{goal['statement']}",
        "## Scope\n\n**Write paths:**\n" + bullets(action["allowed_paths"]),
        "## Frozen Constraints\n\n" + bullets(constraints),
        "## Accepted Decisions\n\n" + bullets(decisions),
        "## Rejected Hypotheses\n\n" + bullets(rejected),
        "## Open Questions\n\n" + bullets(questions),
        "## Evidence References\n\n" + bullets(state["evidence_refs"]),
        "## Acceptance Criteria\n\n| ID | Description | Status | Evidence |\n| --- | --- | --- | --- |\n" + "\n".join(acceptance_rows),
        f"## Next Action\n\n- Owner: {action['owner']}\n- Operation: {action['operation']}",
    ]
    return "\n\n".join(sections) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", "-o", type=Path)
    args = parser.parse_args(argv)
    try:
        state = load_json(args.state)
        errors = validate_state(state)
        if errors:
            raise WorkflowStateError("; ".join(errors))
        content = render(state)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
        else:
            sys.stdout.write(content)
        return 0
    except (OSError, WorkflowStateError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
