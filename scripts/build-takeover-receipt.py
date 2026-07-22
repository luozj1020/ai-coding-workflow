#!/usr/bin/env python3
"""Authorize a bounded Codex salvage after two consecutive counted rounds."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List


COUNTED_FAILURES = {"model-no-progress", "acknowledgement-only", "direction-deviation"}


def _hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _scope(card: str, label: str) -> List[str]:
    match = re.search(rf"(?mi)^-[ \t]*{re.escape(label)}:[ \t]*(.+)$", card)
    if not match:
        return []
    value = match.group(1).strip()
    if value.lower() in {"", "none", "not assigned"}:
        return []
    return [item.strip().strip("`") for item in value.split(",") if item.strip()]


def build(
    current: Dict[str, object], current_path: Path,
    prior: Dict[str, object], prior_path: Path, card_path: Path,
    current_task_id: str, prior_task_id: str, lineage_root_task_id: str,
) -> Dict[str, object]:
    attempts = [(prior_task_id, prior), (current_task_id, current)]
    eligible = all(
        value.get("counts_toward_takeover") is True
        and value.get("failure_class") in COUNTED_FAILURES
        for _, value in attempts
    )
    if not eligible:
        raise ValueError("two consecutive counted model failures are required")
    card = card_path.read_text(encoding="utf-8", errors="replace")
    allowed = _scope(card, "Write paths")
    if not allowed:
        raise ValueError("task card has no bounded Write paths")
    return {
        "schema_version": 1,
        "status": "authorized",
        "authorization": "codex-bounded-takeover",
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "lineage_root_task_id": lineage_root_task_id,
        "attempts": [
            {
                "task_id": task_id,
                "failure_class": value.get("failure_class"),
                "classification_object": _hash(path),
            }
            for (task_id, value), path in zip(attempts, (prior_path, current_path))
        ],
        "task_card_object": _hash(card_path),
        "allowed_write_paths": allowed,
        "forbidden_paths": _scope(card, "Forbidden paths"),
        "remaining_work": "Apply only the unresolved deterministic correction inside allowed_write_paths.",
        "required_validation": "Run the exact narrow validation from the bound task card.",
        "another_claude_retry_recommended": False,
        "merge_authorized": False,
    }


def atomic_write(path: Path, value: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--prior", type=Path, required=True)
    parser.add_argument("--task-card", type=Path, required=True)
    parser.add_argument("--current-task-id", required=True)
    parser.add_argument("--prior-task-id", required=True)
    parser.add_argument("--lineage-root-task-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        current = json.loads(args.current.read_text(encoding="utf-8"))
        prior = json.loads(args.prior.read_text(encoding="utf-8"))
        value = build(current, args.current, prior, args.prior, args.task_card, args.current_task_id,
                      args.prior_task_id, args.lineage_root_task_id)
        atomic_write(args.output, value)
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
