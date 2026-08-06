#!/usr/bin/env python3
"""Build a compact, classification-bound recovery envelope for a revised card.

The helper deliberately does not summarize model output or invent a narrower
implementation request.  Codex's revised Task Card remains the source of the
requested delta.  This packet only proves that a retry is permitted for one of
the deterministic failure classes and tells the next execution not to replay
the prior cold-start discovery work.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable, Optional


SCHEMA = "aiwf-recovery-delta-v1"
SUPPORTED_FAILURES = {
    "model-no-progress": "narrow-and-redispatch-once",
    "acknowledgement-only": "narrow-and-redispatch-once",
    "report-evidence-mismatch": "narrow-and-redispatch-once",
}
REQUIRED_DELTA_SECTIONS = ("Revision Delta", "Required Revisions")


class RecoveryDeltaError(RuntimeError):
    """Raised when a recovery envelope cannot be safely constructed."""


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _atomic_write(path: Path, content: str) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RecoveryDeltaError("cannot read {}: {}".format(label, exc)) from exc
    if not isinstance(value, dict):
        raise RecoveryDeltaError("{} must contain a JSON object".format(label))
    return value


def _sections(text: str) -> set[str]:
    return {
        match.group(1).strip()
        for match in re.finditer(r"(?m)^##[ \t]+([^\n]+?)\s*$", text)
    }


def _validate_classification(value: dict[str, Any]) -> tuple[str, str]:
    if value.get("schema_version") != 1:
        raise RecoveryDeltaError("attempt classification has an unsupported schema")
    failure_class = value.get("failure_class")
    recommended_action = value.get("recommended_action")
    if not isinstance(failure_class, str) or failure_class not in SUPPORTED_FAILURES:
        raise RecoveryDeltaError(
            "attempt classification cannot create a bounded recovery delta for failure class {!r}".format(
                failure_class
            )
        )
    if recommended_action != SUPPORTED_FAILURES[failure_class]:
        raise RecoveryDeltaError(
            "attempt classification action is not eligible for a bounded recovery delta"
        )
    if value.get("same_worktree_retry_eligible") is True:
        raise RecoveryDeltaError(
            "transport retries must use retry-in-place, not a recovery delta"
        )
    return failure_class, recommended_action


def build_recovery_delta(
    task_card: Path, attempt_classification: Path,
) -> tuple[str, dict[str, Any]]:
    task_card = task_card.resolve()
    attempt_classification = attempt_classification.resolve()
    if not task_card.is_file():
        raise RecoveryDeltaError("task card not found: {}".format(task_card))
    if not attempt_classification.is_file():
        raise RecoveryDeltaError(
            "attempt classification not found: {}".format(attempt_classification)
        )
    task_card_bytes = task_card.read_bytes()
    task_card_text = task_card_bytes.decode("utf-8", errors="replace")
    sections = _sections(task_card_text)
    present_delta_sections = [name for name in REQUIRED_DELTA_SECTIONS if name in sections]
    if not present_delta_sections:
        raise RecoveryDeltaError(
            "recovery task card needs Revision Delta or Required Revisions; do not infer a retry scope"
        )
    if "Goal" not in sections:
        raise RecoveryDeltaError("recovery task card lacks Goal")
    classification = _load_json(attempt_classification, "attempt classification")
    failure_class, recommended_action = _validate_classification(classification)
    task_card_sha256 = _sha256_bytes(task_card_bytes)
    classification_sha256 = _sha256_file(attempt_classification)
    lines = [
        "<!-- {} -->".format(SCHEMA),
        "## Bounded Recovery Delta",
        "",
        "This deterministic envelope binds a permitted narrow redispatch to the current Task Card. It adds no product requirements and never copies model output, logs, source bodies, or prior diffs.",
        "",
        "### Recovery Basis",
        "",
        "- Previous failure class: `{}`.".format(failure_class),
        "- Approved route: `{}`.".format(recommended_action),
        "- Execute only the current `Goal` plus `{}` from this Task Card.".format(
            "`, `".join(present_delta_sections)
        ),
        "",
        "### Boundaries",
        "",
        "- Do not replay broad repository discovery, reproduce the failed session, or treat this envelope as permission to expand paths, authority, acceptance, validation, or stop conditions.",
        "- Read named targets first. If a required fact is absent or stale, report the exact mismatch instead of reconstructing the old conversation.",
        "- The full Task Card and its execution capsule remain authoritative; this envelope is only a compact recovery cue.",
        "",
        "### Binding",
        "",
        "- Current task-card digest: `{}`".format(task_card_sha256),
        "- Attempt-classification digest: `{}`".format(classification_sha256),
        "",
    ]
    content = "\n".join(lines)
    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "schema_version": 1,
        "status": "created",
        "task_card": str(task_card),
        "task_card_sha256": task_card_sha256,
        "attempt_classification": {
            "path": str(attempt_classification),
            "sha256": classification_sha256,
        },
        "failure_class": failure_class,
        "recommended_action": recommended_action,
        "delta_sections": present_delta_sections,
        "delta_sha256": _sha256_bytes(content.encode("utf-8")),
        "delta_bytes": len(content.encode("utf-8")),
        "model_generated": False,
    }
    return content, receipt


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--task-card", type=Path, required=True)
    result.add_argument("--attempt-classification", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--receipt", type=Path, required=True)
    return result


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parser().parse_args(argv)
    try:
        content, receipt = build_recovery_delta(args.task_card, args.attempt_classification)
        _atomic_write(args.output, content)
        receipt["output"] = str(args.output.resolve())
        _atomic_write(args.receipt, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except (RecoveryDeltaError, OSError, UnicodeError, ValueError) as exc:
        print("recovery-delta: {}".format(exc), file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
