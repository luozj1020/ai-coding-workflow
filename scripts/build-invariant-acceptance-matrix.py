#!/usr/bin/env python3
"""Build a deterministic invariant/acceptance/slice/test coverage matrix."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional


class MatrixError(RuntimeError):
    pass


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise MatrixError(f"{path} must contain a JSON object")
    return value


def build(contract: Dict[str, Any], test_evidence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    acceptance = {
        row.get("id"): row for row in contract.get("acceptance", [])
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    slices = [row for row in contract.get("slices", []) if isinstance(row, dict)]
    tests = []
    if test_evidence is not None:
        tests = test_evidence.get("tests", [])
        if not isinstance(tests, list):
            raise MatrixError("test evidence tests must be an array")
    rows: List[Dict[str, Any]] = []
    errors: List[str] = []
    for index, invariant in enumerate(contract.get("invariants", [])):
        if not isinstance(invariant, dict):
            errors.append(f"invariants[{index}] is legacy/unmapped")
            continue
        invariant_id = invariant.get("id")
        description = invariant.get("description")
        acceptance_ids = invariant.get("acceptance_ids")
        if not isinstance(invariant_id, str) or not invariant_id.strip():
            errors.append(f"invariants[{index}].id is invalid")
            continue
        if not isinstance(description, str) or not description.strip():
            errors.append(f"invariants[{index}].description is invalid")
        if not isinstance(acceptance_ids, list) or not acceptance_ids:
            errors.append(f"invariant {invariant_id} has no acceptance_ids")
            acceptance_ids = []
        unknown = sorted({item for item in acceptance_ids if item not in acceptance})
        if unknown:
            errors.append(f"invariant {invariant_id} references unknown acceptance: {unknown}")
        slice_ids = sorted({
            str(row.get("id")) for row in slices
            if set(row.get("acceptance_ids") or []) & set(acceptance_ids)
        })
        if not slice_ids:
            errors.append(f"invariant {invariant_id} has no implementation slice")
        matched_tests = [
            row for row in tests if isinstance(row, dict)
            and (
                invariant_id in (row.get("invariant_ids") or [])
                or bool(set(row.get("acceptance_ids") or []) & set(acceptance_ids))
            )
        ]
        contradictions = sorted({
            str(row.get("name")) for row in matched_tests
            if row.get("outcome") in {"fail", "contradicted"}
        })
        passing = sorted({
            str(row.get("name")) for row in matched_tests
            if row.get("outcome") in {"pass", "passed", "success"}
        })
        coverage = (
            "contradicted" if contradictions else
            ("covered" if passing else ("planned" if test_evidence is None else "uncovered"))
        )
        rows.append({
            "invariant_id": invariant_id,
            "description": description,
            "acceptance_ids": acceptance_ids,
            "slice_ids": slice_ids,
            "tests": sorted(str(row.get("name")) for row in matched_tests),
            "passing_tests": passing,
            "contradictions": contradictions,
            "coverage_status": coverage,
        })
    return {
        "schema_version": 1,
        "task_id": contract.get("task_id"),
        "contract_hash": contract.get("contract_hash"),
        "rows": rows,
        "errors": errors,
        "all_invariants_mapped": not errors and bool(rows),
        "test_coverage_complete": bool(rows) and all(row["coverage_status"] == "covered" for row in rows),
        "contradiction_free": all(not row["contradictions"] for row in rows),
    }


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--test-evidence", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-test-coverage", action="store_true")
    args = parser.parse_args()
    try:
        value = build(load(args.contract), load(args.test_evidence) if args.test_evidence else None)
        atomic_json(args.output, value)
    except (OSError, ValueError, json.JSONDecodeError, MatrixError) as exc:
        print(f"acceptance matrix: {exc}", file=os.sys.stderr)
        return 2
    print(json.dumps(value, sort_keys=True))
    if value["errors"] or not value["contradiction_free"]:
        return 1
    if args.require_test_coverage and not value["test_coverage_complete"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
