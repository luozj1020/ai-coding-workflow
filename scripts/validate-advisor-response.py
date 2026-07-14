#!/usr/bin/env python3
"""Validate a structured advisor response against the v1 schema contract.

Reads the response JSON, enforces the P0 advisor response schema, and emits
normalized JSON on success or a compact diagnostic with a stable reason code
on failure.

Python 3.9-compatible, no external dependencies.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional, Tuple

REQUIRED_TOP_LEVEL = {
    "schema_version", "request_id", "advisor", "reservation_id",
    "evidence_hash", "decision", "answer", "allowed_changes",
    "forbidden_changes", "new_validation", "risk_changed", "resume_allowed",
}
VALID_ADVISORS = {"spark", "codex", "human"}
VALID_DECISIONS = {"continue", "narrow", "split", "stop"}
RESUME_DECISIONS = {"continue", "narrow"}


def _is_within(child: Path, parent: Path) -> bool:
    """Return True if child is inside parent (resolved)."""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _path_is_safe(path_str: str) -> bool:
    """Check that a path string is a valid relative path (no absolute, no traversal).

    Uses cross-flavour detection so paths like /etc/passwd (POSIX absolute),
    C:\\... or C:/... (Windows drive), and \\\\server\\... (UNC) are rejected
    on every host OS, not only on the native platform.
    """
    if not path_str or not path_str.strip():
        return False
    # Reject native-platform absolute paths
    if Path(path_str).is_absolute():
        return False
    # Reject POSIX-style absolute paths (starting with /) on any platform.
    if path_str.startswith("/"):
        return False
    # Reject Windows drive-absolute paths (C:\\..., C:/...) and UNC paths (\\\\...)
    if re.match(r'[A-Za-z]:[/\\]|\\\\', path_str):
        return False
    # Reject path traversal
    try:
        parts = Path(path_str).parts
        if any(part == ".." for part in parts):
            return False
        if not parts:
            return False
    except (ValueError, OSError):
        return False
    return True


def _has_overlap(list_a: list, list_b: list) -> bool:
    """Check if two lists share any element."""
    set_a = set(list_a)
    set_b = set(list_b)
    return bool(set_a & set_b)


def validate_response(
    path: str,
    *,
    expected_request_id: Optional[str] = None,
    expected_evidence_hash: Optional[str] = None,
    expected_reservation_id: Optional[str] = None,
    original_allowed_changes: Optional[list] = None,
    original_forbidden_changes: Optional[list] = None,
) -> Tuple[bool, Optional[dict], Optional[dict]]:
    """Validate an advisor response file.

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

    # --- Validate request_id ---
    request_id = data["request_id"]
    if not isinstance(request_id, str) or not request_id.strip():
        return False, None, {"ok": False, "reason": "invalid-request-id"}

    if expected_request_id is not None and request_id != expected_request_id:
        return False, None, {
            "ok": False,
            "reason": "request-id-mismatch",
            "detail": {"expected": expected_request_id, "actual": request_id},
        }

    # --- Validate advisor ---
    advisor = data["advisor"]
    if advisor not in VALID_ADVISORS:
        return False, None, {
            "ok": False,
            "reason": "invalid-advisor",
            "detail": advisor,
        }

    # --- Validate reservation_id ---
    reservation_id = data["reservation_id"]
    if not isinstance(reservation_id, str) or not reservation_id.strip():
        return False, None, {"ok": False, "reason": "invalid-reservation-id"}

    if expected_reservation_id is not None and reservation_id != expected_reservation_id:
        return False, None, {
            "ok": False,
            "reason": "reservation-id-mismatch",
            "detail": {"expected": expected_reservation_id, "actual": reservation_id},
        }

    # --- Validate evidence_hash ---
    evidence_hash = data["evidence_hash"]
    if not isinstance(evidence_hash, str) or not evidence_hash.strip():
        return False, None, {"ok": False, "reason": "invalid-evidence-hash"}

    if expected_evidence_hash is not None and evidence_hash != expected_evidence_hash:
        return False, None, {
            "ok": False,
            "reason": "evidence-hash-mismatch",
            "detail": {"expected": expected_evidence_hash, "actual": evidence_hash},
        }

    # --- Validate decision ---
    decision = data["decision"]
    if decision not in VALID_DECISIONS:
        return False, None, {
            "ok": False,
            "reason": "invalid-decision",
            "detail": decision,
        }

    # --- Validate answer ---
    answer = data["answer"]
    if not isinstance(answer, str) or not answer.strip():
        return False, None, {"ok": False, "reason": "empty-answer"}

    # --- Validate allowed_changes ---
    allowed_changes = data["allowed_changes"]
    if not isinstance(allowed_changes, list):
        return False, None, {"ok": False, "reason": "allowed-changes-not-list"}

    for i, path_str in enumerate(allowed_changes):
        if not isinstance(path_str, str):
            return False, None, {
                "ok": False,
                "reason": "allowed-change-not-string",
                "detail": {"index": i, "value": path_str},
            }
        if not _path_is_safe(path_str):
            return False, None, {
                "ok": False,
                "reason": "allowed-change-unsafe-path",
                "detail": {"index": i, "value": path_str},
            }

    # Check for duplicates in allowed_changes
    if len(allowed_changes) != len(set(allowed_changes)):
        return False, None, {"ok": False, "reason": "allowed-changes-duplicate"}

    # --- Validate forbidden_changes ---
    forbidden_changes = data["forbidden_changes"]
    if not isinstance(forbidden_changes, list):
        return False, None, {"ok": False, "reason": "forbidden-changes-not-list"}

    for i, path_str in enumerate(forbidden_changes):
        if not isinstance(path_str, str):
            return False, None, {
                "ok": False,
                "reason": "forbidden-change-not-string",
                "detail": {"index": i, "value": path_str},
            }
        if not _path_is_safe(path_str):
            return False, None, {
                "ok": False,
                "reason": "forbidden-change-unsafe-path",
                "detail": {"index": i, "value": path_str},
            }

    # Check for duplicates in forbidden_changes
    if len(forbidden_changes) != len(set(forbidden_changes)):
        return False, None, {"ok": False, "reason": "forbidden-changes-duplicate"}

    # --- Check no overlap between allowed and forbidden ---
    if _has_overlap(allowed_changes, forbidden_changes):
        return False, None, {
            "ok": False,
            "reason": "allowed-forbidden-overlap",
        }

    # --- Validate new_validation ---
    new_validation = data["new_validation"]
    if not isinstance(new_validation, list):
        return False, None, {"ok": False, "reason": "new-validation-not-list"}

    for i, cmd in enumerate(new_validation):
        if not isinstance(cmd, str) or not cmd.strip():
            return False, None, {
                "ok": False,
                "reason": "new-validation-empty-command",
                "detail": {"index": i},
            }

    # --- Validate risk_changed ---
    risk_changed = data["risk_changed"]
    if not isinstance(risk_changed, bool):
        return False, None, {
            "ok": False,
            "reason": "risk-changed-not-boolean",
            "detail": risk_changed,
        }

    # --- Validate resume_allowed ---
    resume_allowed = data["resume_allowed"]
    if not isinstance(resume_allowed, bool):
        return False, None, {
            "ok": False,
            "reason": "resume-allowed-not-boolean",
            "detail": resume_allowed,
        }

    # --- Scope validation against original packet ---
    # allowed_changes must be a subset of original allowed paths
    if original_allowed_changes is not None:
        orig_set = set(original_allowed_changes)
        for path_str in allowed_changes:
            # Check exact match or prefix match (path is inside an original allowed path)
            if not any(
                path_str == orig.rstrip("/")
                or path_str.startswith(orig.rstrip("/") + "/")
                for orig in orig_set
            ):
                return False, None, {
                    "ok": False,
                    "reason": "scope-expansion",
                    "detail": {"path": path_str, "original_allowed": sorted(orig_set)},
                }

    # Response cannot relax forbidden paths (any original forbidden must remain forbidden)
    if original_forbidden_changes is not None:
        orig_forbidden = set(original_forbidden_changes)
        new_forbidden = set(forbidden_changes)
        # Every original forbidden path must still be in the new forbidden list
        relaxed = orig_forbidden - new_forbidden
        if relaxed:
            return False, None, {
                "ok": False,
                "reason": "forbidden-relaxation",
                "detail": {"relaxed_paths": sorted(relaxed)},
            }

    # --- Determine resume eligibility ---
    # Resume is eligible only for decision continue/narrow, resume_allowed=true,
    # and risk_changed=false.
    eligible_for_resume = (
        decision in RESUME_DECISIONS
        and resume_allowed
        and not risk_changed
    )

    # --- All valid — return normalized data ---
    normalized = {
        "schema_version": 1,
        "request_id": request_id,
        "advisor": advisor,
        "reservation_id": reservation_id,
        "evidence_hash": evidence_hash,
        "decision": decision,
        "answer": answer,
        "allowed_changes": allowed_changes,
        "forbidden_changes": forbidden_changes,
        "new_validation": new_validation,
        "risk_changed": risk_changed,
        "resume_allowed": resume_allowed,
        "resume_eligible": eligible_for_resume,
    }
    return True, normalized, None


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("response_file", help="Path to advisor response JSON to validate")
    p.add_argument(
        "--expected-request-id", default=None,
        help="If set, reject responses whose request_id does not match",
    )
    p.add_argument(
        "--expected-evidence-hash", default=None,
        help="If set, reject responses whose evidence_hash does not match",
    )
    p.add_argument(
        "--expected-reservation-id", default=None,
        help="If set, reject responses whose reservation_id does not match",
    )
    p.add_argument(
        "--original-allowed-changes", type=Path, default=None,
        help="JSON file with original allowed changes list from the request packet",
    )
    p.add_argument(
        "--original-forbidden-changes", type=Path, default=None,
        help="JSON file with original forbidden changes list from the request packet",
    )
    p.add_argument(
        "--archive-valid", type=Path, default=None,
        help="If set, write normalized valid response to this path",
    )
    p.add_argument(
        "--archive-invalid", type=Path, default=None,
        help="If set, write raw input and diagnostic to this path on failure",
    )
    args = p.parse_args(argv)

    # Load optional scope constraints
    original_allowed = None
    original_forbidden = None
    if args.original_allowed_changes:
        try:
            original_allowed = json.loads(
                args.original_allowed_changes.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError) as exc:
            print(json.dumps({"ok": False, "reason": "invalid-original-allowed", "detail": str(exc)[:120]}))
            return 1
    if args.original_forbidden_changes:
        try:
            original_forbidden = json.loads(
                args.original_forbidden_changes.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError) as exc:
            print(json.dumps({"ok": False, "reason": "invalid-original-forbidden", "detail": str(exc)[:120]}))
            return 1

    ok, normalized, diagnostic = validate_response(
        args.response_file,
        expected_request_id=args.expected_request_id,
        expected_evidence_hash=args.expected_evidence_hash,
        expected_reservation_id=args.expected_reservation_id,
        original_allowed_changes=original_allowed,
        original_forbidden_changes=original_forbidden,
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
                raw = Path(args.response_file).read_text(encoding="utf-8", errors="replace")
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
