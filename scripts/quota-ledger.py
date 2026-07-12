#!/usr/bin/env python3
"""Append-only model/run ledger with budget and duplicate-evidence guards.

Backward-compatible CLI that delegates to the broker's atomic ledger
implementation. Eliminates read-check-append races by using cross-process
file locking.

Python 3.9+ compatible. No third-party dependencies.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import time
import uuid
from pathlib import Path

# Import broker primitives by adjacent filesystem path (hyphenated filename
# cannot be loaded with a plain import statement).  Register in sys.modules
# so dataclass forward references resolve correctly.
_SCRIPTS_DIR = Path(__file__).resolve().parent
_broker_spec = importlib.util.spec_from_file_location(
    "model_call_broker", _SCRIPTS_DIR / "model-call-broker.py"
)
_broker = importlib.util.module_from_spec(_broker_spec)
sys.modules["model_call_broker"] = _broker
_broker_spec.loader.exec_module(_broker)

LedgerLock = _broker.LedgerLock
load_ledger = _broker.load_ledger
append_ledger = _broker.append_ledger
budget_consuming = _broker.budget_consuming
fold_by_reservation = _broker.fold_by_reservation
validate_ledger_history = _broker.validate_ledger_history


def main() -> int:
    p = argparse.ArgumentParser(
        description="Append-only model/run ledger with budget and duplicate-evidence guards"
    )
    p.add_argument("action", choices=["check", "record"])
    p.add_argument("--ledger", default=".ai-workflow/run-ledger.jsonl")
    p.add_argument("--run-id", required=True)
    p.add_argument("--task-id", required=True)
    p.add_argument("--stage", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--max-calls", type=int, required=True)
    p.add_argument("--input", default="")
    p.add_argument("--evidence", default="")
    p.add_argument("--elapsed-seconds", type=float, default=0)
    p.add_argument("--result", default="pending")
    p.add_argument("--next-action", default="")
    a = p.parse_args()

    path = Path(a.ledger)
    ih = hashlib.sha256(a.input.encode()).hexdigest()
    eh = hashlib.sha256(a.evidence.encode()).hexdigest()

    # Use cross-process lock for atomic check-and-record
    lock = LedgerLock(path.with_suffix(".lock"))

    with lock:
        old = load_ledger(path)
        validate_ledger_history(old)

        # Fold by reservation_id so each reservation counts once
        folded = fold_by_reservation(old, a.task_id, a.model)
        calls = [
            r for r in folded.values()
            if budget_consuming(r)
        ]
        duplicate = any(
            r.get("input_hash") == ih and r.get("evidence_hash") == eh
            for r in folded.values()
        )

        decision = {
            "allowed": len(calls) < a.max_calls and not duplicate,
            "calls_used": len(calls),
            "max_calls": a.max_calls,
            "duplicate_evidence": duplicate,
        }

        if a.action == "check":
            print(json.dumps(decision, sort_keys=True))
            return 0 if decision["allowed"] else 2

        if not decision["allowed"]:
            print(json.dumps(decision, sort_keys=True))
            return 2

        reservation_id = "legacy-{}".format(uuid.uuid4().hex[:12])
        base_item = {
            "schema_version": 1,
            "timestamp": int(time.time()),
            "run_id": a.run_id,
            "reservation_id": reservation_id,
            "task_id": a.task_id,
            "stage": a.stage,
            "role": a.model,
            "model": a.model,
            "call_index": len(calls) + 1,
            "input_hash": ih,
            "evidence_hash": eh,
        }
        append_ledger(path, {**base_item, "state": "reserved"})
        append_ledger(path, {**base_item, "state": "running"})
        item = {
            **base_item,
            "state": "succeeded",
            "elapsed_seconds": a.elapsed_seconds,
            "result": a.result,
            "next_action": a.next_action,
        }
        append_ledger(path, item)

    print(json.dumps(item, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
