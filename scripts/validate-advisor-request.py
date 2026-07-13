#!/usr/bin/env python3
"""Validate ADVISOR_REQUEST.json against the v1 schema contract.

Reads the request file, enforces the phase-1 contract, and emits normalized
JSON on success or a compact diagnostic with a stable reason code on failure.

Python 3.9-compatible, no external dependencies.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Tuple

REQUIRED_TOP_LEVEL = {
    "schema_version", "task_id", "direction", "blocker",
    "completed_work", "advisor_used",
}
VALID_DIRECTIONS = {"on-plan", "off-plan"}
VALID_BLOCKER_KINDS = {"semantic", "transport", "approval", "direction", "unknown"}
REQUIRED_BLOCKER_FIELDS = {"kind", "question", "blocking"}


def validate_request(
    path: str,
    *,
    expected_task_id: Optional[str] = None,
) -> Tuple[bool, Optional[dict], Optional[dict]]:
    """Validate an ADVISOR_REQUEST.json file.

    Returns (ok, normalized_data, diagnostic).
    On success: (True, normalized_dict, None).
    On failure: (False, None, diagnostic_dict).
    """
    p = Path(path)

    # --- Load and parse ---
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False, None, {"ok": False, "reason": "file-not-found"}
    except OSError as exc:
        return False, None, {"ok": False, "reason": "io-error", "detail": str(exc)[:120]}

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return False, None, {"ok": False, "reason": "malformed-json", "detail": str(exc)[:120]}

    if not isinstance(data, dict):
        return False, None, {"ok": False, "reason": "not-an-object"}

    # --- Check for unknown top-level fields ---
    unknown = set(data.keys()) - REQUIRED_TOP_LEVEL
    if unknown:
        return False, None, {
            "ok": False,
            "reason": "unknown-fields",
            "detail": sorted(unknown),
        }

    # --- Check all required fields present ---
    missing = REQUIRED_TOP_LEVEL - set(data.keys())
    if missing:
        return False, None, {
            "ok": False,
            "reason": "missing-fields",
            "detail": sorted(missing),
        }

    # --- Validate schema_version ---
    schema_version = data["schema_version"]
    if not isinstance(schema_version, int) or schema_version != 1:
        return False, None, {
            "ok": False,
            "reason": "unsupported-schema-version",
            "detail": schema_version,
        }

    # --- Validate task_id ---
    task_id = data["task_id"]
    if not isinstance(task_id, str) or not task_id.strip():
        return False, None, {"ok": False, "reason": "invalid-task-id"}

    if expected_task_id is not None and task_id != expected_task_id:
        return False, None, {
            "ok": False,
            "reason": "task-id-mismatch",
            "detail": {"expected": expected_task_id, "actual": task_id},
        }

    # --- Validate direction ---
    direction = data["direction"]
    if direction not in VALID_DIRECTIONS:
        return False, None, {
            "ok": False,
            "reason": "invalid-direction",
            "detail": direction,
        }

    # --- Validate blocker ---
    blocker = data["blocker"]
    if not isinstance(blocker, dict):
        return False, None, {"ok": False, "reason": "blocker-not-an-object"}

    blocker_unknown = set(blocker.keys()) - REQUIRED_BLOCKER_FIELDS
    if blocker_unknown:
        return False, None, {
            "ok": False,
            "reason": "blocker-unknown-fields",
            "detail": sorted(blocker_unknown),
        }

    blocker_missing = REQUIRED_BLOCKER_FIELDS - set(blocker.keys())
    if blocker_missing:
        return False, None, {
            "ok": False,
            "reason": "blocker-missing-fields",
            "detail": sorted(blocker_missing),
        }

    blocker_kind = blocker["kind"]
    if blocker_kind not in VALID_BLOCKER_KINDS:
        return False, None, {
            "ok": False,
            "reason": "invalid-blocker-kind",
            "detail": blocker_kind,
        }

    blocker_question = blocker["question"]
    if not isinstance(blocker_question, str) or not blocker_question.strip():
        return False, None, {"ok": False, "reason": "invalid-blocker-question"}

    blocker_blocking = blocker["blocking"]
    if not isinstance(blocker_blocking, bool):
        return False, None, {
            "ok": False,
            "reason": "blocker-blocking-not-boolean",
            "detail": blocker_blocking,
        }
    if not blocker_blocking:
        return False, None, {
            "ok": False,
            "reason": "blocker-blocking-false",
        }

    # --- Validate completed_work ---
    completed_work = data["completed_work"]
    if not isinstance(completed_work, str) or not completed_work.strip():
        return False, None, {"ok": False, "reason": "invalid-completed-work"}

    # --- Validate advisor_used ---
    advisor_used = data["advisor_used"]
    if not isinstance(advisor_used, bool):
        return False, None, {
            "ok": False,
            "reason": "advisor-used-not-boolean",
            "detail": advisor_used,
        }

    # --- All valid — return normalized data ---
    normalized = {
        "schema_version": 1,
        "task_id": task_id,
        "direction": direction,
        "blocker": {
            "kind": blocker_kind,
            "question": blocker_question,
            "blocking": True,
        },
        "completed_work": completed_work,
        "advisor_used": advisor_used,
    }
    return True, normalized, None


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("request_file", help="Path to ADVISOR_REQUEST.json to validate")
    p.add_argument(
        "--expected-task-id", default=None,
        help="If set, reject requests whose task_id does not match exactly",
    )
    p.add_argument(
        "--archive-valid", type=Path, default=None,
        help="If set, write normalized valid request to this path",
    )
    p.add_argument(
        "--archive-invalid", type=Path, default=None,
        help="If set, write raw input and diagnostic to this path on failure",
    )
    args = p.parse_args(argv)

    ok, normalized, diagnostic = validate_request(
        args.request_file,
        expected_task_id=args.expected_task_id,
    )

    if ok:
        print(json.dumps(normalized, indent=2, sort_keys=True))
        if args.archive_valid:
            args.archive_valid.parent.mkdir(parents=True, exist_ok=True)
            args.archive_valid.write_text(
                json.dumps(normalized, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        return 0
    else:
        print(json.dumps(diagnostic, sort_keys=True))
        if args.archive_invalid:
            args.archive_invalid.parent.mkdir(parents=True, exist_ok=True)
            raw = ""
            try:
                raw = Path(args.request_file).read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
            archive = {"raw_input": raw, "diagnostic": diagnostic}
            args.archive_invalid.write_text(
                json.dumps(archive, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
