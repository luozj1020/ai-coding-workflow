#!/usr/bin/env python3
"""Evaluate or persist Evidence Object validity against current repository facts.

Exit codes: 0 all valid, 2 at least one stale, 3 unknown dependencies only,
1 malformed input/reference.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evidence_store import (  # noqa: E402
    EvidenceStoreError, evaluate_validity, iter_object_ids, load_object,
    object_path, validity_path, validate_current_context,
)
from workflow_state import atomic_write_json, load_json  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=Path(".ai-workflow/objects"))
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--object-id", action="append", default=[])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        current = load_json(args.current)
        errors = validate_current_context(current)
        if errors:
            raise EvidenceStoreError("; ".join(errors))
        object_ids = sorted(set(args.object_id or list(iter_object_ids(args.store))))
        if not object_ids:
            raise EvidenceStoreError("no Evidence Objects selected")
        records = []
        for object_id in object_ids:
            obj = load_object(args.store, object_id, check_validity=False)
            record = evaluate_validity(obj, current)
            records.append(record)
            if args.apply:
                atomic_write_json(validity_path(object_path(args.store, object_id)), record)
        counts = {
            status: sum(1 for record in records if record["status"] == status)
            for status in ("valid", "stale", "unknown")
        }
        result = {
            "schema_version": 1,
            "applied": args.apply,
            "counts": counts,
            "objects": records,
        }
        if args.output:
            atomic_write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        if counts["stale"]:
            return 2
        if counts["unknown"]:
            return 3
        return 0
    except (OSError, EvidenceStoreError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
