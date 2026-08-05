#!/usr/bin/env python3
"""Verify capsule file identities and repository binding without reading bodies."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from evidence_capsule import compact_bytes, repository_head, sha256_file


def verify_ref(label: str, ref: Any) -> dict[str, Any]:
    if ref is None:
        return {"label": label, "status": "not-declared"}
    if not isinstance(ref, dict) or not ref.get("path"):
        return {"label": label, "status": "invalid-reference"}
    path = Path(str(ref["path"]))
    if not path.is_file():
        return {"label": label, "status": "missing", "path": str(path)}
    actual_hash = sha256_file(path)
    actual_size = path.stat().st_size
    expected_hash = ref.get("sha256")
    expected_size = ref.get("size_bytes")
    matched = actual_hash == expected_hash and actual_size == expected_size
    return {
        "label": label,
        "status": "matched" if matched else "mismatch",
        "path": str(path.resolve()),
        "expected_sha256": expected_hash,
        "actual_sha256": actual_hash,
        "expected_size_bytes": expected_size,
        "actual_size_bytes": actual_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capsule", type=Path)
    args = parser.parse_args()
    value = json.loads(args.capsule.read_text(encoding="utf-8"))

    references = []
    for key in ("evidence", "task_card", "diff", "source"):
        if key in value:
            references.append(verify_ref(key, value.get(key)))
    tool_request = value.get("compression_route", {}).get("tool_request", {})
    if tool_request:
        references.append(verify_ref("tool_request.task_card", tool_request.get("task_card")))
        references.append(verify_ref("tool_request.input_artifact", tool_request.get("input_artifact")))

    declared_head = value.get("repository_head")
    head_status = "not-declared"
    actual_head = None
    task_ref = value.get("task_card") or value.get("evidence")
    if declared_head and isinstance(task_ref, dict) and task_ref.get("path"):
        actual_head = repository_head(Path(str(task_ref["path"])).parent)
        head_status = "matched" if actual_head == declared_head else "mismatch"

    mismatches = [item["label"] for item in references if item["status"] in {"missing", "mismatch", "invalid-reference"}]
    if head_status == "mismatch":
        mismatches.append("repository_head")
    receipt = {
        "schema_version": 1,
        "kind": "aiwf-evidence-capsule-verification",
        "capsule_path": str(args.capsule.resolve()),
        "valid": not mismatches,
        "mismatches": mismatches,
        "references": references,
        "repository_head": {
            "status": head_status,
            "expected": declared_head,
            "actual": actual_head,
        },
    }
    print(compact_bytes(receipt).decode("utf-8"))
    return 0 if receipt["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
