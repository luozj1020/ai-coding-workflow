#!/usr/bin/env python3
"""Build a bounded, deterministic rehydration checkpoint for a Context Lease.

The checkpoint is deliberately a statement of accepted lineage facts, current
write boundaries, and immutable evidence references.  It never summarizes a
conversation, copies source/diff content, or grants new authority.  A new
Claude session can therefore resume a compatible sequential slice without
replaying an unbounded transcript.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


SCHEMA = "aiwf-context-checkpoint-v1"
MAX_BYTES = 12 * 1024
MAX_PATHS = 64
MAX_FINDINGS = 20
MAX_FINDING_BYTES = 240


class CheckpointError(RuntimeError):
    """Raised when a checkpoint cannot be safely derived from current evidence."""


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


def _load_continuation_module():
    path = Path(__file__).resolve().with_name("prepare-worktree-continuation.py")
    spec = importlib.util.spec_from_file_location("aiwf_worktree_continuation", path)
    if spec is None or spec.loader is None:
        raise CheckpointError("reviewed-continuation helper is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _runtime_artifact_path(path: Path, root: Path, worktree: Path, label: str) -> Path:
    resolved = path.resolve()
    artifact_root = (root / ".worktrees").resolve()
    if not _within(resolved, artifact_root):
        raise CheckpointError(f"{label} must be under the common .worktrees directory")
    if _within(resolved, worktree):
        raise CheckpointError(f"{label} must not be written inside the product worktree")
    return resolved


def _sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise CheckpointError(f"{label} must be a sha256 digest")
    return value


def _hex_or_sha256(value: object, label: str) -> str:
    """Normalize legacy raw content hashes without weakening the binding.

    ``prepare-worktree-continuation.py`` predates immutable-reference strings
    and deliberately stores file/worktree hashes as bare hexadecimal values.
    Context Lease contract and evidence references use the newer ``sha256:``
    spelling.  A checkpoint has to bind both representations, but it always
    emits the unambiguous ``sha256:`` form.
    """
    if not isinstance(value, str):
        raise CheckpointError(f"{label} must be a sha256 digest")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        return value
    if re.fullmatch(r"[0-9a-f]{64}", value):
        return "sha256:" + value
    raise CheckpointError(f"{label} must be a sha256 digest")


def _bounded_paths(value: object, label: str) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise CheckpointError(f"{label} must be a non-empty path array")
    values = sorted(set(value))
    if len(values) > MAX_PATHS:
        raise CheckpointError(f"{label} exceeds the {MAX_PATHS}-path checkpoint limit")
    return values


def _bounded_findings(value: object) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CheckpointError("unresolved findings must be a string array")
    if len(value) > MAX_FINDINGS or any(len(item.encode("utf-8")) > MAX_FINDING_BYTES for item in value):
        raise CheckpointError("unresolved findings exceed the bounded checkpoint limit")
    return sorted(set(item.strip() for item in value if item.strip()))


def _bounded_refs(value: object, label: str) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise CheckpointError(f"{label} must be an array")
    values = sorted(set(str(item) for item in value if str(item)))
    if len(values) > MAX_PATHS or any(not re.fullmatch(r"sha256:[0-9a-f]{64}", item) for item in values):
        raise CheckpointError(f"{label} must contain at most {MAX_PATHS} sha256 references")
    return values


def _state_summary(approval: Dict[str, Any], accepted_paths: Iterable[str]) -> List[Dict[str, Any]]:
    states = approval.get("accepted_path_state")
    if not isinstance(states, dict):
        raise CheckpointError("accepted path state is unavailable")
    result: List[Dict[str, Any]] = []
    for path in accepted_paths:
        state = states.get(path)
        if not isinstance(state, dict) or not isinstance(state.get("kind"), str):
            raise CheckpointError(f"accepted path state is malformed: {path}")
        item: Dict[str, Any] = {"path": path, "kind": state["kind"]}
        if state.get("sha256") is not None:
            item["sha256"] = _hex_or_sha256(state["sha256"], "accepted path digest")
        if isinstance(state.get("size"), int) and state["size"] >= 0:
            item["size"] = state["size"]
        result.append(item)
    return result


def _canonical(value: Dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _checkpoint_facts(approval: Dict[str, Any], next_card: Path) -> Dict[str, Any]:
    context = approval.get("context_lease")
    if not isinstance(context, dict) or context.get("schema") != "aiwf-context-lease-v1":
        raise CheckpointError("Context Lease schema is unavailable")
    if context.get("status") != "active":
        raise CheckpointError("Context Lease is not active")
    calls_used = context.get("calls_used")
    warm_limit = context.get("max_warm_calls")
    if not isinstance(calls_used, int) or not isinstance(warm_limit, int) or calls_used <= warm_limit:
        raise CheckpointError("checkpoint is only valid after the Context Lease warm limit")
    contract = context.get("solution_contract")
    if not isinstance(contract, dict):
        raise CheckpointError("Context Lease solution contract is unavailable")
    delta = approval.get("delta_continuation")
    if not isinstance(delta, dict):
        raise CheckpointError("continuation delta evidence is unavailable")
    accepted_paths = _bounded_paths(approval.get("accepted_existing_paths"), "accepted existing paths")
    new_paths = _bounded_paths(approval.get("allow_new_write_paths"), "new write paths")
    packet = delta.get("delta_review_packet")
    if packet is not None and not isinstance(packet, dict):
        raise CheckpointError("delta review packet binding is malformed")
    packet_summary = None
    if packet:
        packet_summary = {
            "packet_id": _sha256(packet.get("packet_id"), "delta review packet id"),
            "sha256": _sha256(packet.get("sha256"), "delta review packet digest"),
            "acceptance_ids": sorted(set(str(item) for item in packet.get("acceptance_ids", []) if str(item))),
        }
    facts = {
        "schema": SCHEMA,
        "lease_id": str(context.get("lease_id") or ""),
        "continuation_kind": str(context.get("continuation_kind") or ""),
        "calls_used": calls_used,
        "max_warm_calls": warm_limit,
        "prior_task_id": str(approval.get("prior_task_id") or ""),
        "next_role": str(approval.get("next_role") or ""),
        "solution_contract_sha256": _sha256(contract.get("sha256"), "solution contract digest"),
        "prior_worktree_state_sha256": _hex_or_sha256(
            approval.get("worktree_state_hash"), "approved worktree state digest"
        ),
        "next_task_card_sha256": _sha256_file(next_card),
        "accepted_paths": _state_summary(approval, accepted_paths),
        "new_write_paths": new_paths,
        "unresolved_findings": _bounded_findings(delta.get("unresolved_findings")),
        "new_validation_refs": _bounded_refs(delta.get("new_validation_refs"), "new validation references"),
        "delta_review": packet_summary,
    }
    if not facts["lease_id"] or not facts["prior_task_id"] or not facts["next_role"]:
        raise CheckpointError("Context Lease identity is incomplete")
    return facts


def _render(facts: Dict[str, Any], checkpoint_id: str) -> str:
    lines = [
        "<!-- aiwf-context-checkpoint-v1 -->",
        "## Accepted Context Checkpoint",
        "",
        "This deterministic checkpoint rehydrates a new Claude session. It records accepted lineage facts only; the current Task Card remains authoritative for scope, acceptance, validation, authority, and stop conditions.",
        "",
        "### Lineage",
        "",
        "- Lease: `{}`; continuation: `{}`; warm calls: `{}/{}`.".format(
            facts["lease_id"], facts["continuation_kind"], facts["calls_used"], facts["max_warm_calls"]
        ),
        "- Prior task: `{}`; assigned role: `{}`.".format(facts["prior_task_id"], facts["next_role"]),
        "- Frozen solution contract: `{}`.".format(facts["solution_contract_sha256"]),
        "- Approved prior worktree state: `{}`.".format(facts["prior_worktree_state_sha256"]),
        "- Current task-card digest: `{}`.".format(facts["next_task_card_sha256"]),
        "",
        "### Accepted State",
        "",
    ]
    for item in facts["accepted_paths"]:
        suffix = item.get("sha256", "state-without-content-digest")
        size = " size={}".format(item["size"]) if "size" in item else ""
        lines.append("- `{}`: {} {}{}.".format(item["path"], item["kind"], suffix, size))
    lines.extend(["", "### Current Delta Boundary", ""])
    for path in facts["new_write_paths"]:
        lines.append("- New-write path: `{}`.".format(path))
    if facts["unresolved_findings"]:
        lines.extend(["", "### Review Findings Still Open", ""])
        for finding in facts["unresolved_findings"]:
            lines.append("- {}".format(finding))
    if facts["new_validation_refs"]:
        lines.extend(["", "### New Validation Evidence References", ""])
        for reference in facts["new_validation_refs"]:
            lines.append("- `{}`".format(reference))
    if facts["delta_review"]:
        lines.extend(["", "### Delta Review Binding", ""])
        lines.append("- Packet: `{}`; digest: `{}`.".format(
            facts["delta_review"]["packet_id"], facts["delta_review"]["sha256"]
        ))
        for acceptance_id in facts["delta_review"]["acceptance_ids"]:
            lines.append("- Current acceptance item: `{}`.".format(acceptance_id))
    lines.extend([
        "",
        "Do not treat previous conversation text as authoritative. Inspect only named targets needed for this delta; stop and report a stale accepted fact instead of rebuilding repository-wide context.",
        "",
        "- Checkpoint id: `{}`".format(checkpoint_id),
        "",
    ])
    return "\n".join(lines)


def build_checkpoint(
    context_lease: Path, next_task_card: Path, output: Path, receipt_path: Path,
    max_bytes: int = MAX_BYTES,
) -> Dict[str, Any]:
    if max_bytes < 1024:
        raise CheckpointError("checkpoint byte budget must be at least 1024")
    continuation = _load_continuation_module()
    try:
        approval, root, worktree = continuation.validate_common(context_lease, next_task_card)
    except (RuntimeError, OSError, TypeError, ValueError) as exc:
        raise CheckpointError("Context Lease validation failed: {}".format(exc)) from exc
    common_root = continuation.runtime_repository_root(root)
    output = _runtime_artifact_path(output, common_root, worktree, "checkpoint output")
    receipt_path = _runtime_artifact_path(receipt_path, common_root, worktree, "checkpoint receipt")
    if output == receipt_path:
        raise CheckpointError("checkpoint output and receipt must differ")
    facts = _checkpoint_facts(approval, next_task_card.resolve())
    checkpoint_id = _sha256_bytes(_canonical(facts).encode("utf-8"))
    content = _render(facts, checkpoint_id)
    encoded = content.encode("utf-8")
    if len(encoded) > max_bytes:
        raise CheckpointError("checkpoint exceeds the {}-byte budget".format(max_bytes))
    _atomic_write(output, content)
    receipt = {
        "schema": SCHEMA,
        "schema_version": 1,
        "status": "created",
        "checkpoint_id": checkpoint_id,
        "context_lease_path": str(context_lease.resolve()),
        "context_lease_sha256": _sha256_file(context_lease.resolve()),
        "next_task_card": str(next_task_card.resolve()),
        "next_task_card_sha256": facts["next_task_card_sha256"],
        "approved_worktree_state_sha256": facts["prior_worktree_state_sha256"],
        "checkpoint_sha256": _sha256_bytes(encoded),
        "checkpoint_bytes": len(encoded),
        "model_generated": False,
        "output": str(output),
    }
    _atomic_write(receipt_path, json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return receipt


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--context-lease", type=Path, required=True)
    result.add_argument("--next-task-card", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--receipt", type=Path, required=True)
    result.add_argument("--max-bytes", type=int, default=MAX_BYTES)
    return result


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parser().parse_args(argv)
    try:
        receipt = build_checkpoint(
            args.context_lease, args.next_task_card, args.output, args.receipt,
            max_bytes=args.max_bytes,
        )
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
        return 0
    except (CheckpointError, OSError, UnicodeError, ValueError) as exc:
        print("context-checkpoint: {}".format(exc), file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
