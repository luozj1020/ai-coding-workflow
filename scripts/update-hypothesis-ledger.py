#!/usr/bin/env python3
"""Reject or explicitly reopen a hypothesis while synchronizing State IR."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hypothesis_ledger import (  # noqa: E402
    HypothesisLedgerError, add_rejected_item, empty_ledger, ledger_lock,
    reopen_item, validate_ledger, validate_reject_input, validate_reopen_input,
)
from workflow_state import (  # noqa: E402
    append_events, apply_mutation, atomic_write_json, finalize_transition,
    load_events, load_json, validate_event, validate_state,
)


def state_hypothesis(state: dict, hypothesis_id: str):
    return next((item for item in state["rejected_hypotheses"] if item["id"] == hypothesis_id), None)


def rejected_state_id(events: list[dict], hypothesis_id: str, fallback: str) -> str:
    for event in reversed(events):
        if event.get("event_type") == "hypothesis-rejected" and event.get("payload", {}).get("id") == hypothesis_id:
            return event["new_state_id"]
    return fallback


def has_matching_reopen_event(events: list[dict], value: dict) -> bool:
    expected = {
        "id": value["hypothesis_id"],
        "reason": value["reason"],
        "evidence_refs": value["new_evidence_refs"],
    }
    return any(
        event.get("event_type") == "hypothesis-reopened"
        and event.get("payload") == expected
        for event in events
    )


def persist_state_transition(state_path, events_path, state, event_type, payload, actor):
    mutated = apply_mutation(state, event_type, payload)
    updated, event = finalize_transition(
        state, mutated, actor=actor, event_type=event_type, payload=payload,
    )
    errors = validate_state(updated) + validate_event(event)
    if errors:
        raise HypothesisLedgerError("invalid state transition: " + "; ".join(errors))
    append_events(events_path, [event])
    atomic_write_json(state_path, updated)
    return updated, event


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation", choices=("reject", "reopen"), required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--state-events", type=Path, required=True)
    parser.add_argument("--actor", required=True)
    args = parser.parse_args(argv)
    try:
        with ledger_lock(args.ledger):
            state = load_json(args.state)
            events = load_events(args.state_events)
            state_errors = validate_state(state)
            if state_errors:
                raise HypothesisLedgerError("invalid State IR: " + "; ".join(state_errors))
            if not events or events[-1].get("new_state_id") != state["state_id"]:
                raise HypothesisLedgerError("state event tail does not match State IR")
            value = load_json(args.input)
            input_errors = (
                validate_reject_input(value) if args.operation == "reject"
                else validate_reopen_input(value)
            )
            if input_errors:
                raise HypothesisLedgerError("; ".join(input_errors))
            if value["repository_state_hash"] != state["repository_state_hash"]:
                raise HypothesisLedgerError("input repository_state_hash does not match State IR")
            evidence_refs = (
                value["evidence_refs"] if args.operation == "reject"
                else value["new_evidence_refs"]
            )
            missing_evidence = sorted(set(evidence_refs) - set(state["evidence_refs"]))
            if missing_evidence:
                raise HypothesisLedgerError(
                    "evidence refs are absent from State IR: " + ", ".join(missing_evidence)
                )
            if args.ledger.exists():
                ledger = load_json(args.ledger)
                ledger_errors = validate_ledger(ledger)
                if ledger_errors:
                    raise HypothesisLedgerError("invalid ledger: " + "; ".join(ledger_errors))
                if ledger["task_id"] != state["task_id"]:
                    raise HypothesisLedgerError("ledger task_id does not match State IR")
            else:
                ledger = empty_ledger(state["task_id"], state["repository_state_hash"])

            before_ledger_id = ledger["ledger_id"]
            state_changed = False
            if args.operation == "reject":
                existing = state_hypothesis(state, value["id"])
                state_payload = {
                    key: value[key] for key in ("id", "statement", "reason", "evidence_refs")
                }
                if existing is not None and existing != state_payload:
                    raise HypothesisLedgerError("State IR contains a conflicting hypothesis with this id")
                if existing is None:
                    state, event = persist_state_transition(
                        args.state, args.state_events, state, "hypothesis-rejected",
                        state_payload, args.actor,
                    )
                    events.append(event)
                    state_changed = True
                source_state_id = rejected_state_id(events, value["id"], state["state_id"])
                ledger = add_rejected_item(ledger, value, source_state_id)
            else:
                ledger_item = next(
                    (item for item in ledger["items"] if item["id"] == value["hypothesis_id"]),
                    None,
                )
                if ledger_item is None:
                    raise HypothesisLedgerError("hypothesis does not exist in ledger")
                existing = state_hypothesis(state, value["hypothesis_id"])
                already_reopened = ledger_item["status"] == "reopened"
                if not already_reopened:
                    if existing is None and not has_matching_reopen_event(events, value):
                        raise HypothesisLedgerError(
                            "State IR lacks the rejected hypothesis without a matching reopen event"
                        )
                    ledger = reopen_item(ledger, value)
                else:
                    if (
                        ledger_item["reopened_reason"] != value["reason"]
                        or ledger_item["reopened_by"] != value["producer"]
                        or ledger_item["reopened_evidence_refs"]
                        != sorted(set(value["new_evidence_refs"]) - set(ledger_item["evidence_refs"]))
                    ):
                        raise HypothesisLedgerError("hypothesis was reopened with different metadata")
                if existing is not None:
                    state, event = persist_state_transition(
                        args.state, args.state_events, state, "hypothesis-reopened",
                        {"id": value["hypothesis_id"], "reason": value["reason"], "evidence_refs": value["new_evidence_refs"]},
                        args.actor,
                    )
                    state_changed = True
            atomic_write_json(args.ledger, ledger)
            ledger_changed = ledger["ledger_id"] != before_ledger_id
            print(json.dumps({
                "status": "updated" if state_changed or ledger_changed else "unchanged",
                "operation": args.operation,
                "ledger_id": ledger["ledger_id"],
                "ledger_revision": ledger["revision"],
                "state_id": state["state_id"],
            }, sort_keys=True))
            return 0
    except (OSError, HypothesisLedgerError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
