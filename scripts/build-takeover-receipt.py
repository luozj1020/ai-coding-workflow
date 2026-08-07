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
from typing import Any, Dict, List, Optional


COUNTED_FAILURES = {
    "model-no-progress", "acknowledgement-only", "direction-deviation",
    "report-evidence-mismatch",
}
IDENTITY_KEYS = (
    "task_id", "lineage_root_task_id", "task_card_sha256",
    "source_base_commit", "execution_base_commit", "source_repository",
    "worktree", "claude_session_id", "retry_of",
)


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


def _load_runtime(path: Path, label: str) -> Dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"{label} runtime receipt is required")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label} runtime receipt") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} runtime receipt must be an object")
    return value


def _runtime_binding(
    runtime: Dict[str, Any], runtime_path: Path, task_id: str, card_path: Path, label: str,
) -> Dict[str, Optional[str]]:
    if runtime.get("task_id") != task_id:
        raise ValueError(f"{label} runtime task identity mismatch")
    worktree_text = str(runtime.get("worktree") or "")
    source_text = str(runtime.get("source_repository") or "")
    source_base = str(runtime.get("source_base_commit") or runtime.get("base_commit") or "")
    execution_base = str(
        runtime.get("execution_base_commit") or runtime.get("worktree_start_commit") or ""
    )
    lineage_root = str(runtime.get("lineage_root_task_id") or "")
    session_id = str(runtime.get("claude_session_id") or "")
    if not all((
        worktree_text, source_text, source_base, execution_base, lineage_root, session_id,
    )):
        raise ValueError(f"{label} runtime lineage binding is incomplete")
    worktree = Path(worktree_text).resolve()
    source = Path(source_text).resolve()
    expected_card = (worktree / "TASK_CARD_FULL.md").resolve()
    if not worktree.is_dir() or not expected_card.is_file():
        raise ValueError(f"{label} runtime worktree/card is unavailable")
    if card_path.resolve() != expected_card:
        raise ValueError(f"{label} task card is not the runtime worktree card")
    return {
        "task_id": task_id,
        "lineage_root_task_id": lineage_root,
        "task_card_sha256": _hash(expected_card),
        "source_base_commit": source_base,
        "execution_base_commit": execution_base,
        "source_repository": str(source),
        "worktree": str(worktree),
        "claude_session_id": session_id,
        "retry_of": str(runtime.get("retry_of") or "") or None,
        "runtime_receipt": str(runtime_path.resolve()),
        "runtime_receipt_object": _hash(runtime_path),
    }


def _validate_attempt_identity(
    value: Dict[str, object], expected: Dict[str, Optional[str]], label: str,
) -> None:
    identity = value.get("attempt_identity")
    if not isinstance(identity, dict) or identity.get("schema") != "aiwf-attempt-identity-v1":
        raise ValueError(f"{label} attempt classification lacks a bound identity")
    for key in IDENTITY_KEYS:
        if identity.get(key) != expected.get(key):
            raise ValueError(f"{label} attempt identity mismatch: {key}")


def build(
    current: Dict[str, object], current_path: Path,
    prior: Dict[str, object], prior_path: Path, card_path: Path,
    runtime_path: Path, prior_runtime_path: Path, current_task_id: str, prior_task_id: str,
    lineage_root_task_id: str,
) -> Dict[str, object]:
    attempts = [(prior_task_id, prior), (current_task_id, current)]
    eligible = all(
        value.get("counts_toward_takeover") is True
        and value.get("failure_class") in COUNTED_FAILURES
        for _, value in attempts
    )
    if not eligible:
        raise ValueError("two consecutive counted model failures are required")
    if current_task_id == prior_task_id:
        raise ValueError("takeover attempts must have distinct task identities")
    current_runtime = _load_runtime(runtime_path, "current")
    prior_runtime = _load_runtime(prior_runtime_path, "prior")
    current_binding = _runtime_binding(
        current_runtime, runtime_path, current_task_id, card_path, "current",
    )
    prior_card = Path(str(prior_runtime.get("worktree") or "")) / "TASK_CARD_FULL.md"
    prior_binding = _runtime_binding(
        prior_runtime, prior_runtime_path, prior_task_id, prior_card, "prior",
    )
    if current_binding["lineage_root_task_id"] != lineage_root_task_id or \
       prior_binding["lineage_root_task_id"] != lineage_root_task_id:
        raise ValueError("attempt lineage root mismatch")
    for key in (
        "source_repository", "source_base_commit", "execution_base_commit",
        "worktree", "task_card_sha256", "claude_session_id",
    ):
        if current_binding[key] != prior_binding[key]:
            raise ValueError(f"attempt lineage mismatch: {key}")
    if current_runtime.get("strategy") != "retry-in-place" or \
       current_binding["retry_of"] != prior_task_id:
        raise ValueError("current attempt is not an explicit retry of the prior task")
    try:
        current_ordinal = int(current_runtime.get("retry_ordinal", -1))
        prior_ordinal = int(prior_runtime.get("retry_ordinal", -1))
    except (TypeError, ValueError) as exc:
        raise ValueError("attempt retry ordinal is invalid") from exc
    if current_ordinal != prior_ordinal + 1:
        raise ValueError("attempt retry ordinal is not consecutive")
    _validate_attempt_identity(current, current_binding, "current")
    _validate_attempt_identity(prior, prior_binding, "prior")
    card = card_path.read_text(encoding="utf-8", errors="replace")
    allowed = _scope(card, "Write paths")
    if not allowed:
        raise ValueError("task card has no bounded Write paths")
    return {
        "schema_version": 3,
        "status": "preparation-required",
        "authorization": "codex-takeover-candidate",
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "lineage_root_task_id": lineage_root_task_id,
        "current_task_id": current_task_id,
        "runtime_receipt": str(runtime_path.resolve()),
        "runtime_receipt_object": _hash(runtime_path),
        "attempt_lineage": {
            "schema": "aiwf-takeover-attempt-lineage-v1",
            "relation": "retry-in-place",
            "prior_runtime_receipt": str(prior_runtime_path.resolve()),
            "prior_runtime_receipt_object": _hash(prior_runtime_path),
            "current_runtime_receipt": str(runtime_path.resolve()),
            "current_runtime_receipt_object": _hash(runtime_path),
            "binding": {
                key: current_binding[key] for key in IDENTITY_KEYS if key != "retry_of"
            },
            "prior_task_id": prior_task_id,
            "current_task_id": current_task_id,
        },
        "attempts": [
            {
                "task_id": task_id,
                "failure_class": value.get("failure_class"),
                "classification_object": _hash(path),
                "runtime_receipt": binding["runtime_receipt"],
                "runtime_receipt_object": binding["runtime_receipt_object"],
            }
            for (task_id, value), path, binding in zip(
                attempts, (prior_path, current_path), (prior_binding, current_binding)
            )
        ],
        "task_card_object": _hash(card_path),
        "allowed_write_paths": allowed,
        "forbidden_paths": _scope(card, "Forbidden paths"),
        "remaining_work": "Apply only the unresolved deterministic correction inside allowed_write_paths.",
        "required_validation": "Run the exact narrow validation from the bound task card.",
        "takeover_preparation_required": True,
        "required_preparation": [
            "revoke-or-explicitly-declare-absent-owner-lease",
            "terminate-identity-matched-prior-process-tree",
            "confirm-all-recorded-processes-inactive",
            "sample-stable-worktree-content-hash",
            "issue-single-writer-codex-grant",
        ],
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
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--prior-runtime", type=Path, required=True)
    parser.add_argument("--current-task-id", required=True)
    parser.add_argument("--prior-task-id", required=True)
    parser.add_argument("--lineage-root-task-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        current = json.loads(args.current.read_text(encoding="utf-8"))
        prior = json.loads(args.prior.read_text(encoding="utf-8"))
        value = build(
            current, args.current, prior, args.prior, args.task_card, args.runtime,
            args.prior_runtime,
            args.current_task_id, args.prior_task_id, args.lineage_root_task_id,
        )
        atomic_write(args.output, value)
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
