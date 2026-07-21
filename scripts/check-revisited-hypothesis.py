#!/usr/bin/env python3
"""Check and record whether a proposed hypothesis revisits rejected knowledge.

Exit codes: 0 novel, 2 rejected repeat, 3 explicit reopen required,
4 possible semantic repeat requiring review.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hypothesis_ledger import (  # noqa: E402
    HypothesisLedgerError, check_proposal, ledger_lock, record_revisit,
    validate_ledger,
)
from workflow_state import atomic_write_json, load_json  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--proposal", type=Path, required=True)
    parser.add_argument("--review-output", type=Path)
    parser.add_argument("--max-relevant", type=int, default=8)
    args = parser.parse_args(argv)
    try:
        with ledger_lock(args.ledger):
            ledger = load_json(args.ledger)
            errors = validate_ledger(ledger)
            if errors:
                raise HypothesisLedgerError("invalid ledger: " + "; ".join(errors))
            proposal = load_json(args.proposal)
            result, event, relevant = check_proposal(
                ledger, proposal, max_relevant=args.max_relevant,
            )
            if event is not None:
                ledger = record_revisit(ledger, event)
                ledger_errors = validate_ledger(ledger)
                if ledger_errors:
                    raise HypothesisLedgerError("invalid updated ledger: " + "; ".join(ledger_errors))
                atomic_write_json(args.ledger, ledger)
                result["revisit_event_id"] = event["event_id"]
                result["ledger_id"] = ledger["ledger_id"]
            if args.review_output:
                atomic_write_json(args.review_output, {
                    "schema_version": 1,
                    "ledger_id": ledger["ledger_id"],
                    "task_id": ledger["task_id"],
                    "scope_refs": proposal["scope_refs"],
                    "items": relevant,
                })
                result["review_output"] = str(args.review_output)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return {
                "novel": 0,
                "rejected-repeat": 2,
                "reopen-required": 3,
                "possible-repeat": 4,
            }[result["status"]]
    except (OSError, HypothesisLedgerError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
