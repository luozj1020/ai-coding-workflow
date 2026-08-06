#!/usr/bin/env python3
"""Create and validate one-use, hash-bound Claude Context Leases.

A lease extends the reviewed-continuation receipt with stable solution,
session, provider, model, role, and tool-profile identity.  It is intentionally
one-use: after every accepted slice Codex must issue a new lease bound to the
new worktree hash.  That preserves warm context without creating a long-lived
unbounded writer capability.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
CONTINUATION_KINDS = {"next-slice", "revision", "checker-followup"}
WARM_CALL_LIMIT = 3


class LeaseError(RuntimeError):
    pass


def _load_continuation_module():
    path = SCRIPT_DIR / "prepare-worktree-continuation.py"
    spec = importlib.util.spec_from_file_location("aiwf_worktree_continuation", path)
    if spec is None or spec.loader is None:
        raise LeaseError("reviewed-continuation helper is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LeaseError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise LeaseError(f"JSON object required: {path}")
    return value


def _digest_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _contract_binding(
    path: Optional[Path], digest: Optional[str], repository: Path,
) -> Dict[str, Any]:
    if (path is None) == (digest is None):
        raise LeaseError("provide exactly one of --solution-contract or --contract-hash")
    if path is not None:
        resolved = path.resolve()
        if not resolved.is_file():
            raise LeaseError(f"solution contract not found: {resolved}")
        try:
            resolved.relative_to(repository.resolve())
        except ValueError as exc:
            raise LeaseError("solution contract must be inside the source repository") from exc
        return {"path": str(resolved), "sha256": _digest_file(resolved)}
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(digest)):
        raise LeaseError("--contract-hash must be sha256:<64 lowercase hex>")
    return {"path": None, "sha256": digest}


def _validate_route_digest(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise LeaseError("provider route must be sha256:<64 lowercase hex>")
    return value


def create(args: argparse.Namespace) -> Dict[str, Any]:
    if args.continuation_kind not in CONTINUATION_KINDS:
        raise LeaseError("unsupported continuation kind")
    module = _load_continuation_module()
    repository = module.repository_root()
    parent: Optional[Dict[str, Any]] = None
    if args.parent_lease:
        parent = _load_json(args.parent_lease.resolve())
        parent_ctx = parent.get("context_lease") or {}
        if parent_ctx.get("schema") != "aiwf-context-lease-v1":
            raise LeaseError("parent lease schema is invalid")

    contract = _contract_binding(args.solution_contract, args.contract_hash, repository)
    if parent:
        parent_contract = (parent.get("context_lease") or {}).get("solution_contract")
        if parent_contract != contract:
            raise LeaseError("solution contract changed across the Context Lease lineage")
        if int((parent.get("context_lease") or {}).get("max_warm_calls", 0)) != args.max_warm_calls:
            raise LeaseError("warm-call limit changed across the Context Lease lineage")

    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{args.output.name}.prepare.", dir=str(args.output.resolve().parent)
    )
    os.close(fd)
    temporary_output = Path(temporary_name)
    try:
        prepare_args = argparse.Namespace(
            prior_task_id=args.prior_task_id,
            next_task_card=args.next_task_card,
            next_role=args.next_role,
            decision="accepted-direction",
            accepted_existing_path=args.accepted_existing_path,
            allow_new_write_path=args.allow_new_write_path,
            delta_review_packet=args.delta_review_packet,
            unresolved_finding=args.unresolved_finding,
            new_validation_ref=args.new_validation_ref,
            output=temporary_output,
        )
        approval = module.prepare(prepare_args)
    finally:
        temporary_output.unlink(missing_ok=True)
    runtime = _load_json(Path(str(approval["runtime_path"])))
    prior_lease_id = str(runtime.get("context_lease_id") or "")
    if prior_lease_id and parent is None:
        raise LeaseError("continuing an existing Context Lease requires --parent-lease")
    if parent and prior_lease_id and (
        (parent.get("context_lease") or {}).get("lease_id") != prior_lease_id
    ):
        raise LeaseError("parent lease does not match the prior runtime lineage")
    prior_approval_id = str(runtime.get("reviewed_continuation_approval_id") or "")
    if parent and prior_approval_id and parent.get("approval_id") != prior_approval_id:
        raise LeaseError("parent lease is not the immediately preceding approval")
    parent_ctx = (parent or {}).get("context_lease") or {}
    calls_used = int(parent_ctx.get("calls_used", 0)) + 1
    lease_id = str(parent_ctx.get("lease_id") or uuid.uuid4().hex)
    tool_profile = args.tool_profile or str(runtime.get("tool_profile") or "")
    if not tool_profile:
        raise LeaseError("tool profile is unavailable; pass --tool-profile")
    model = args.model if args.model is not None else runtime.get("model_hint")
    provider = _validate_route_digest(
        args.provider_route_sha256
        if args.provider_route_sha256 is not None
        else runtime.get("provider_route_sha256")
    )
    approval["context_lease"] = {
        "schema": "aiwf-context-lease-v1",
        "lease_id": lease_id,
        "parent_approval_id": parent.get("approval_id") if parent else None,
        "status": "active",
        "continuation_kind": args.continuation_kind,
        "lineage_root_task_id": runtime.get("lineage_root_task_id", args.prior_task_id),
        "session_id": runtime.get("claude_session_id"),
        "solution_contract": contract,
        "worktree_state_hash": approval["worktree_state_hash"],
        "model": model or None,
        "provider_route_sha256": provider,
        "tool_profile": tool_profile,
        "role": args.next_role,
        "calls_used": calls_used,
        "max_warm_calls": args.max_warm_calls,
    }
    module.atomic_json(args.output, approval)
    return approval


def validate(args: argparse.Namespace) -> Dict[str, Any]:
    module = _load_continuation_module()
    approval, _, _ = module.validate_common(args.context_lease, args.next_task_card)
    context = approval.get("context_lease") or {}
    if context.get("schema") != "aiwf-context-lease-v1" or context.get("status") != "active":
        raise LeaseError("Context Lease is not active or has an unsupported schema")
    if context.get("continuation_kind") != args.continuation_kind:
        raise LeaseError("continuation kind does not match the Context Lease")
    if context.get("role") != approval.get("next_role"):
        raise LeaseError("role does not match the Context Lease")
    runtime = _load_json(Path(str(approval.get("runtime_path", ""))))
    if context.get("session_id") != runtime.get("claude_session_id"):
        raise LeaseError("session identity drifted after Context Lease creation")
    if context.get("lineage_root_task_id") != runtime.get(
        "lineage_root_task_id", approval.get("prior_task_id")
    ):
        raise LeaseError("lineage root changed after Context Lease creation")
    if context.get("worktree_state_hash") != approval.get("worktree_state_hash"):
        raise LeaseError("Context Lease worktree binding is inconsistent")
    if context.get("tool_profile") != args.tool_profile:
        raise LeaseError("tool profile changed across the Context Lease")
    if context.get("model") and context.get("model") != (args.model or None):
        raise LeaseError("model changed across the Context Lease")
    if context.get("provider_route_sha256") and (
        context.get("provider_route_sha256") != (args.provider_route_sha256 or None)
    ):
        raise LeaseError("provider route changed across the Context Lease")
    contract = context.get("solution_contract") or {}
    contract_path = contract.get("path")
    if contract_path:
        resolved_contract = Path(contract_path).resolve()
        try:
            resolved_contract.relative_to(Path(str(approval["source_repository"])).resolve())
        except ValueError as exc:
            raise LeaseError("solution contract escaped the source repository") from exc
        if _digest_file(resolved_contract) != contract.get("sha256"):
            raise LeaseError("solution contract content changed after lease creation")

    calls_used = int(context.get("calls_used", 0))
    warm_limit = int(context.get("max_warm_calls", WARM_CALL_LIMIT))
    route = "warm-resume"
    checkpoint_required = False
    if args.force_fresh_session:
        route = "cold-fresh"
    elif calls_used > warm_limit:
        route = "capsule-rehydrate"
        if args.rehydrate_from is None:
            if args.allow_auto_rehydrate:
                checkpoint_required = True
            else:
                raise LeaseError(
                    "warm-call limit reached; provide --rehydrate-from or --force-fresh-session"
                )
    if args.rehydrate_from is not None:
        path = args.rehydrate_from.resolve()
        if not path.is_file():
            raise LeaseError(f"rehydration checkpoint not found: {path}")
        route = "capsule-rehydrate"
    return {
        "schema": "aiwf-context-lease-validation-v1",
        "status": "valid",
        "approval_id": approval.get("approval_id"),
        "lease_id": context.get("lease_id"),
        "route": route,
        "session_id": context.get("session_id"),
        "calls_used": calls_used,
        "max_warm_calls": warm_limit,
        "checkpoint_required": checkpoint_required,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    create_p = sub.add_parser("create")
    create_p.add_argument("--prior-task-id", required=True)
    create_p.add_argument("--next-task-card", type=Path, required=True)
    create_p.add_argument("--next-role", choices=("builder", "checker-test"), required=True)
    create_p.add_argument("--continuation-kind", choices=sorted(CONTINUATION_KINDS), required=True)
    create_p.add_argument("--solution-contract", type=Path)
    create_p.add_argument("--contract-hash")
    create_p.add_argument("--accepted-existing-path", action="append", default=[], required=True)
    create_p.add_argument("--allow-new-write-path", action="append", default=[], required=True)
    create_p.add_argument("--delta-review-packet", type=Path)
    create_p.add_argument("--unresolved-finding", action="append", default=[])
    create_p.add_argument("--new-validation-ref", action="append", default=[])
    create_p.add_argument("--tool-profile")
    create_p.add_argument("--model")
    create_p.add_argument("--provider-route-sha256")
    create_p.add_argument("--parent-lease", type=Path)
    create_p.add_argument("--max-warm-calls", type=int, default=WARM_CALL_LIMIT)
    create_p.add_argument("--output", type=Path, required=True)

    validate_p = sub.add_parser("validate")
    validate_p.add_argument("--context-lease", type=Path, required=True)
    validate_p.add_argument("--next-task-card", type=Path, required=True)
    validate_p.add_argument("--continuation-kind", choices=sorted(CONTINUATION_KINDS), required=True)
    validate_p.add_argument("--tool-profile", required=True)
    validate_p.add_argument("--model")
    validate_p.add_argument("--provider-route-sha256")
    validate_p.add_argument("--force-fresh-session", action="store_true")
    validate_p.add_argument("--rehydrate-from", type=Path)
    validate_p.add_argument(
        "--allow-auto-rehydrate", action="store_true",
        help="return a pending capsule-rehydrate route instead of requiring a caller-supplied checkpoint",
    )
    return result


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parser().parse_args(argv)
    try:
        if getattr(args, "max_warm_calls", WARM_CALL_LIMIT) < 1:
            raise LeaseError("--max-warm-calls must be positive")
        value = create(args) if args.command == "create" else validate(args)
        print(json.dumps(value, sort_keys=True))
        return 0
    # The imported reviewed-continuation helper raises its own RuntimeError
    # subclass.  Normalize that expected fail-closed path instead of exposing a
    # traceback or a different exit contract to callers.
    except (RuntimeError, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"context-lease: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
