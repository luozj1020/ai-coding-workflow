#!/usr/bin/env python3
"""Prepare and validate a one-use reviewed dirty-worktree continuation.

The helper is deterministic and performs no Git mutation.  ``prepare`` binds a
Codex decision to the exact dirty state and next task card.  ``validate`` is
used by the dispatcher immediately before reserving the worktree.  ``post-run``
checks that a continuation did not change paths outside its declared boundary.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

SCRIPT_DIR = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(SCRIPT_DIR))
from worktree_state_hash import compute_worktree_state_hash

SCHEMA_VERSION = 1
CONTROL_FILES = {
    "TASK_CARD.md", "TASK_CARD_FULL.md", "CLAUDE_TASK_CARD.md",
    "CLAUDE_PROMPT.md", "CLAUDE_PROGRESS.md", "CLAUDE_REPORT.md",
    "ADVISOR_REQUEST.json",
}
ALLOWED_PRIOR_STRATEGIES = {"fresh", "reviewed-continuation"}
ALLOWED_ROLES = {"builder", "checker-test"}
ALLOWED_BUILDER_MODES = {
    "standard", "execution-only", "solution-planning", "batch", "exploratory",
}
ROLE_BUILDER_MODES = {
    "solution-planner": "solution-planning",
    "batch-builder": "batch",
    "exploratory-builder": "exploratory",
}


class ContinuationError(RuntimeError):
    pass


def git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        timeout=30,
    )
    if proc.returncode:
        raise ContinuationError(
            f"git {' '.join(args)} failed: {(proc.stderr or '').strip()}"
        )
    return proc.stdout


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def normalize_path(raw: str, worktree: Path) -> str:
    if not raw or any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise ContinuationError(f"unsafe path: {raw!r}")
    raw = raw.replace("\\", "/")
    pure = PurePosixPath(raw)
    if pure.is_absolute() or raw in {".", ""} or ".." in pure.parts:
        raise ContinuationError(f"path must be repository-relative: {raw!r}")
    if pure.parts[0] in {".git", ".worktrees"}:
        raise ContinuationError(f"workflow control path is not allowed: {raw!r}")
    normalized = pure.as_posix()
    if not is_within(worktree / normalized, worktree):
        raise ContinuationError(f"path escapes worktree: {raw!r}")
    return normalized


def normalize_paths(values: Iterable[str], worktree: Path) -> List[str]:
    return sorted({normalize_path(value, worktree) for value in values})


def changed_paths(worktree: Path) -> List[str]:
    values: Set[str] = set()
    for args in (
        ("diff", "--name-only"),
        ("diff", "--cached", "--name-only"),
        ("ls-files", "--others", "--exclude-standard"),
    ):
        for value in git(worktree, *args).splitlines():
            value = value.strip()
            if value and PurePosixPath(value.replace("\\", "/")).name not in CONTROL_FILES:
                values.add(normalize_path(value, worktree))
    return sorted(values)


def path_state(worktree: Path, paths: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for rel in paths:
        path = worktree / rel
        try:
            info = path.lstat()
        except FileNotFoundError:
            result[rel] = {"kind": "missing"}
            continue
        mode = stat.S_IMODE(info.st_mode)
        if path.is_symlink():
            target = os.readlink(path)
            digest = hashlib.sha256(target.encode("utf-8", errors="surrogateescape")).hexdigest()
            result[rel] = {"kind": "symlink", "mode": mode, "sha256": digest}
        elif path.is_file():
            result[rel] = {
                "kind": "file", "mode": mode, "size": info.st_size,
                "sha256": sha256_file(path),
            }
        else:
            result[rel] = {"kind": "other", "mode": mode}
    return result


def load_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContinuationError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContinuationError(f"JSON object required: {path}")
    return value


def bind_delta_review(path: Optional[Path]) -> Optional[Dict[str, Any]]:
    if path is None:
        return None
    path = path.resolve()
    packet = load_json(path)
    packet_id = str(packet.get("packet_id", ""))
    material = dict(packet)
    material.pop("packet_id", None)
    encoded = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    expected = "sha256:" + hashlib.sha256(encoded).hexdigest()
    if packet_id != expected:
        raise ContinuationError("delta review packet hash is invalid")
    if packet.get("mode") != "revision":
        raise ContinuationError("continuation requires a revision delta review packet")
    items = packet.get("acceptance_items")
    if not isinstance(items, list):
        raise ContinuationError("delta review packet acceptance_items must be an array")
    acceptance_ids = sorted({
        str(item.get("id")) for item in items
        if isinstance(item, dict) and item.get("id")
    })
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "packet_id": packet_id,
        "state_id": packet.get("state_id"),
        "graph_id": packet.get("graph_id"),
        "acceptance_ids": acceptance_ids,
        "new_diff_refs": packet.get("new_diff_refs", []),
        "new_test_refs": packet.get("new_test_refs", []),
    }


def optional_artifact_ref(value: object) -> Optional[Dict[str, Any]]:
    path = Path(str(value or "")).resolve()
    if not str(value or "").strip() or not path.is_file() or path.is_symlink():
        return None
    return {
        "path": str(path),
        "sha256": "sha256:" + sha256_file(path),
        "bytes": path.stat().st_size,
    }


def bounded_findings(values: Iterable[str]) -> List[str]:
    findings = sorted({str(value).strip() for value in values if str(value).strip()})
    if len(findings) > 20 or any(len(value) > 240 for value in findings):
        raise ContinuationError("unresolved findings exceed the bounded continuation summary")
    return findings


def evidence_refs(values: Iterable[str]) -> List[str]:
    refs = sorted({str(value).strip() for value in values if str(value).strip()})
    if any(not re.fullmatch(r"sha256:[0-9a-f]{64}", value) for value in refs):
        raise ContinuationError("new validation evidence must use immutable sha256: refs")
    return refs


def live_pid_file(path: Path) -> bool:
    try:
        value = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(value, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def process_identity_state(
    path: Path, task_id: str, role: str,
) -> str:
    """Classify a recorded writer without treating PID reuse as liveness."""
    identity = load_json(path)
    module_path = SCRIPT_DIR / "process-identity.py"
    spec = importlib.util.spec_from_file_location(
        "_aiwf_process_identity", module_path,
    )
    if spec is None or spec.loader is None:
        raise ContinuationError("process identity checker is unavailable")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        state, _ = module.check(
            identity, expected_task_id=task_id, expected_role=role,
        )
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise ContinuationError(f"process identity check failed: {path}: {exc}") from exc
    return str(state)


def validate_recorded_writers(runtime: Dict[str, Any], task_id: str) -> None:
    pid_files = runtime.get("pid_files") or {}
    identity_files = runtime.get("process_identity_files") or {}
    if not isinstance(pid_files, dict) or not isinstance(identity_files, dict):
        raise ContinuationError("runtime process receipts must be objects")

    # ``pid`` is the historical alias for the Claude role.  Once the named
    # Claude receipt exists, checking the alias with bare kill(0) would undo
    # the identity protection and recreate the PID-reuse false positive.
    roles = [role for role in ("dispatcher", "claude", "checker") if role in pid_files]
    if pid_files.get("pid") and not pid_files.get("claude"):
        roles.append("pid")

    for role in roles:
        raw_pid = pid_files.get(role)
        if not raw_pid:
            continue
        identity_role = "claude" if role == "pid" else role
        raw_identity = identity_files.get(identity_role)
        identity_path = Path(str(raw_identity)) if raw_identity else None
        if identity_path is not None and identity_path.is_file():
            state = process_identity_state(identity_path, task_id, identity_role)
            if state == "running-same-process":
                raise ContinuationError(
                    f"recorded process is still live: {raw_pid}"
                )
            if state in {"not-running", "pid-reused-or-foreign"}:
                continue
            raise ContinuationError(
                f"recorded process identity is not trustworthy: "
                f"{identity_path} ({state})"
            )
        if live_pid_file(Path(str(raw_pid))):
            raise ContinuationError(f"recorded process is still live: {raw_pid}")


def normalize_task_role(value: object) -> Optional[str]:
    value = str(value or "").strip().lower()
    if value in {"checker", "test", "checker/test", "checker-test"}:
        return "checker-test"
    return "builder" if value in {"builder", "revision"} else None


def normalize_builder_mode(value: object) -> Optional[str]:
    value = str(value or "").strip().lower()
    if value in {"", "auto"}:
        return None
    return value if value in ALLOWED_BUILDER_MODES else None


def task_contract(card: Path) -> Dict[str, Optional[str]]:
    """Read task and Builder mode from Task JSON or rendered metadata.

    Legacy Markdown tables remain a compatibility fallback, but JSON-backed
    continuations no longer depend on their presence.
    """
    text = card.read_text(encoding="utf-8", errors="replace")
    declared_mode: object = ""
    builder_mode: object = ""
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = None
    if isinstance(value, dict):
        declared_mode = value.get("mode", "")
        extensions = value.get("extensions")
        routing = extensions.get("routing_hints", {}) if isinstance(extensions, dict) else {}
        execution = extensions.get("execution", {}) if isinstance(extensions, dict) else {}
        if isinstance(execution, dict):
            builder_mode = execution.get("builder_mode", "")
        if not builder_mode and isinstance(routing, dict):
            builder_mode = routing.get("builder_mode", "")
            role = str(routing.get("claude_role", "")).strip().lower()
            builder_mode = builder_mode or ROLE_BUILDER_MODES.get(role, "")
    else:
        metadata = re.search(
            r"(?im)<!--\s*aiwf-execution-card-v1;\s*"
            r"task-mode=([^;\s>]+);\s*builder-mode=([^;\s>]+)\s*-->",
            text,
        )
        if metadata:
            declared_mode, builder_mode = metadata.group(1), metadata.group(2)
        else:
            mode_match = re.search(r"(?im)^\|\s*Mode\s*\|\s*([^|]+)", text)
            builder_match = re.search(
                r"(?im)^\|\s*Builder mode\s*\|\s*([^|]+)", text
            )
            declared_mode = mode_match.group(1) if mode_match else ""
            builder_mode = builder_match.group(1) if builder_match else ""
    declared = str(declared_mode or "").strip().lower()
    role = normalize_task_role(declared)
    if declared in ROLE_BUILDER_MODES:
        role = "builder"
        builder_mode = builder_mode or ROLE_BUILDER_MODES[declared]
    elif declared == "checker":
        role = "checker-test"
    return {
        "declared_mode": declared or None,
        "role": role,
        "builder_mode": normalize_builder_mode(builder_mode),
    }


def task_role(card: Path) -> Optional[str]:
    return task_contract(card)["role"]


def repository_root() -> Path:
    return Path(git(Path.cwd(), "rev-parse", "--show-toplevel").strip()).resolve()


def runtime_repository_root(source: Path) -> Path:
    common = Path(git(source, "rev-parse", "--git-common-dir").strip())
    if not common.is_absolute():
        common = source / common
    common = common.resolve()
    if common.name == ".git":
        return common.parent
    primary = git(source, "worktree", "list", "--porcelain")
    for line in primary.splitlines():
        if line.startswith("worktree "):
            return Path(line[len("worktree "):]).resolve()
    raise ContinuationError("cannot resolve Git common runtime repository root")


def validate_runtime(root: Path, task_id: str) -> tuple[Dict[str, Any], Path, Path]:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", task_id):
        raise ContinuationError("unsafe prior task id")
    runtime_root = runtime_repository_root(root)
    runtime_path = runtime_root / ".worktrees" / f"{task_id}.runtime.json"
    runtime = load_json(runtime_path)
    if runtime.get("task_id") != task_id:
        raise ContinuationError("prior runtime task id mismatch")
    strategy = str(runtime.get("strategy", ""))
    if strategy not in ALLOWED_PRIOR_STRATEGIES:
        raise ContinuationError(f"prior strategy is not reviewable: {strategy or 'missing'}")
    if strategy == "reviewed-continuation" and runtime.get("provenance_root_strategy") != "fresh":
        raise ContinuationError("reviewed continuation lacks fresh-root provenance")
    if runtime.get("dag_group") or runtime.get("parallel"):
        raise ContinuationError("parallel/DAG worktrees cannot be continued")
    source = Path(str(runtime.get("source_repository", ""))).resolve()
    worktree = Path(str(runtime.get("worktree", ""))).resolve()
    if source != root or not is_within(worktree, runtime_root / ".worktrees"):
        raise ContinuationError("runtime repository/worktree boundary mismatch")
    for takeover_marker in (runtime_root / ".worktrees").glob("*.codex-write-owner.json"):
        marker = load_json(takeover_marker)
        marker_worktree = Path(str(marker.get("worktree", ""))).resolve()
        if marker_worktree == worktree:
            raise ContinuationError("worktree ownership was transferred to Codex")
    if not worktree.is_dir() or git(worktree, "rev-parse", "--is-inside-work-tree").strip() != "true":
        raise ContinuationError("recorded worktree is unavailable")
    validate_recorded_writers(runtime, task_id)
    source_head = git(root, "rev-parse", "HEAD").strip()
    worktree_head = git(worktree, "rev-parse", "HEAD").strip()
    source_base = str(runtime.get("source_base_commit") or runtime.get("base_commit") or "")
    execution_base = str(
        runtime.get("execution_base_commit")
        or runtime.get("worktree_start_commit")
        or source_base
    )
    if not source_base or not execution_base:
        raise ContinuationError("runtime baseline identity is missing")
    if source_head != source_base:
        raise ContinuationError("source HEAD does not match recorded source base")
    if worktree_head != execution_base:
        raise ContinuationError("worktree HEAD does not match recorded execution base")
    snapshot = str(runtime.get("dirty_snapshot_commit") or "")
    if snapshot and snapshot != execution_base:
        raise ContinuationError("dirty snapshot provenance does not match execution base")
    return runtime, runtime_path, worktree


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def prepare(args: argparse.Namespace) -> Dict[str, Any]:
    if args.decision != "accepted-direction":
        raise ContinuationError("--decision accepted-direction is required")
    root = repository_root()
    runtime, runtime_path, worktree = validate_runtime(root, args.prior_task_id)
    card = args.next_task_card.resolve()
    if not card.is_file():
        raise ContinuationError("next task card not found")
    next_contract = task_contract(card)
    next_role = args.next_role or next_contract["role"]
    if next_role not in ALLOWED_ROLES or next_contract["role"] != next_role:
        raise ContinuationError("next role does not match task card Mode")
    prior_declared_mode = str(runtime.get("task_mode") or "").strip().lower()
    prior_role = normalize_task_role(prior_declared_mode)
    if prior_role is None:
        prior_role = task_role(worktree / "TASK_CARD_FULL.md") or ""
    prior_session_id = str(runtime.get("claude_session_id") or "").strip()
    if prior_role == "checker-test":
        if next_role != "checker-test":
            raise ContinuationError(
                "Checker worktrees may only start checker-test reviewed continuation"
            )
        try:
            uuid.UUID(prior_session_id)
        except (ValueError, AttributeError):
            raise ContinuationError(
                "Checker reviewed continuation requires a valid recorded Claude session"
            )
    elif prior_role != "builder":
        raise ContinuationError("prior worktree role is not reviewable")
    prior_builder_mode = normalize_builder_mode(runtime.get("builder_mode"))
    next_builder_mode = next_contract["builder_mode"]
    if next_role == "builder":
        inherited_builder_mode = prior_builder_mode or next_builder_mode or "standard"
        if next_builder_mode and next_builder_mode != inherited_builder_mode:
            raise ContinuationError(
                "next task card Builder mode conflicts with the reviewed continuation"
            )
    else:
        inherited_builder_mode = "standard"
    prior_tool_profile = str(runtime.get("tool_profile") or "").strip()
    inherited_tool_profile = prior_tool_profile or None
    actual = changed_paths(worktree)
    accepted = normalize_paths(args.accepted_existing_path, worktree)
    allowed = normalize_paths(args.allow_new_write_path, worktree)
    if not actual or actual != accepted:
        raise ContinuationError(
            f"accepted existing paths must exactly match current changes: actual={actual}"
        )
    accepted_state = path_state(worktree, accepted)
    if not any(
        value.get("kind") in {"missing", "symlink"}
        or (value.get("kind") == "file" and int(value.get("size", 0)) > 0)
        for value in accepted_state.values()
    ):
        raise ContinuationError("current changes contain no material implementation evidence")
    if not allowed:
        raise ContinuationError("at least one --allow-new-write-path is required")
    approval_id = uuid.uuid4().hex
    runtime_root = runtime_repository_root(root)
    output = (
        args.output.resolve() if args.output else
        runtime_root / ".worktrees" / "continuations" /
        f"{args.prior_task_id}-{approval_id}.json"
    )
    if is_within(output, worktree):
        raise ContinuationError(
            "continuation authorization must be stored outside the product worktree"
        )
    source_head = git(root, "rev-parse", "HEAD").strip()
    delta_review = bind_delta_review(args.delta_review_packet)
    unresolved_findings = bounded_findings(args.unresolved_finding)
    new_validation_refs = evidence_refs(args.new_validation_ref)
    value: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "approval_id": approval_id,
        "request_id": approval_id,
        "status": "available",
        "decision": args.decision,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "prior_task_id": args.prior_task_id,
        "prior_declared_mode": prior_declared_mode or None,
        "prior_role": prior_role,
        "prior_claude_session_id": prior_session_id or None,
        "next_role": next_role,
        "next_declared_mode": next_contract["declared_mode"],
        "next_builder_mode": next_builder_mode,
        "inherited_builder_mode": inherited_builder_mode,
        "prior_tool_profile": prior_tool_profile or None,
        "inherited_tool_profile": inherited_tool_profile,
        "prior_context_lease_id": runtime.get("context_lease_id"),
        "prior_context_lease_continuation_kind": runtime.get(
            "context_lease_continuation_kind"
        ),
        "prior_strategy": runtime["strategy"],
        "provenance_root_strategy": runtime.get("provenance_root_strategy", "fresh"),
        "runtime_path": str(runtime_path.resolve()),
        "source_repository": str(root),
        "worktree": str(worktree),
        "base_commit": runtime["base_commit"],
        "source_base_commit": runtime.get("source_base_commit", runtime["base_commit"]),
        "execution_base_commit": runtime.get(
            "execution_base_commit", runtime.get("worktree_start_commit", runtime["base_commit"])
        ),
        "source_head": source_head,
        "worktree_head": git(worktree, "rev-parse", "HEAD").strip(),
        "next_task_card": str(card),
        "next_task_card_sha256": sha256_file(card),
        "worktree_state_hash": compute_worktree_state_hash(worktree),
        "actual_changed_paths": actual,
        "accepted_existing_paths": accepted,
        "allow_new_write_paths": allowed,
        "accepted_path_state": accepted_state,
        "delta_continuation": {
            "baseline_worktree_state_hash": compute_worktree_state_hash(worktree),
            "delta_review_packet": delta_review,
            "unresolved_findings": unresolved_findings,
            "new_validation_refs": new_validation_refs,
            "full_prior_task_card_repeated": False,
        },
        "context_reuse": {
            "strategy": "same-session-plus-delta-capsule",
            "prior_skill_context_compilation": optional_artifact_ref(
                runtime.get("skill_context_compilation")
            ),
            "prior_execution_capsule_receipt": optional_artifact_ref(
                runtime.get("execution_capsule_receipt")
            ),
            "accepted_path_summaries_reused": len(accepted_state),
            "full_prior_task_card_repeated": False,
        },
    }
    dispatch_argv = [
        "bash", "ai/dispatch-to-claude.sh", str(card),
        "--reviewed-continuation", str(output),
    ]
    value["authorization_path"] = str(output)
    value["dispatch_argv"] = dispatch_argv
    value["dispatch_command"] = shlex.join(dispatch_argv)
    atomic_json(output, value)
    return value


def validate_common(approval_path: Path, card: Path) -> tuple[Dict[str, Any], Path, Path]:
    approval = load_json(approval_path.resolve())
    if approval.get("schema_version") != SCHEMA_VERSION:
        raise ContinuationError("unsupported approval schema")
    if approval.get("decision") != "accepted-direction" or approval.get("status") != "available":
        raise ContinuationError("approval is not available/accepted")
    root = repository_root()
    runtime, runtime_path, worktree = validate_runtime(root, str(approval.get("prior_task_id", "")))
    card = card.resolve()
    exact = {
        "runtime_path": str(runtime_path.resolve()),
        "source_repository": str(root),
        "worktree": str(worktree),
        "base_commit": runtime.get("base_commit"),
        "source_base_commit": runtime.get("source_base_commit", runtime.get("base_commit")),
        "execution_base_commit": runtime.get(
            "execution_base_commit", runtime.get("worktree_start_commit", runtime.get("base_commit"))
        ),
        "source_head": git(root, "rev-parse", "HEAD").strip(),
        "worktree_head": git(worktree, "rev-parse", "HEAD").strip(),
        "next_task_card": str(card),
        "next_task_card_sha256": sha256_file(card),
    }
    prior_declared_mode = str(runtime.get("task_mode") or "").strip().lower()
    prior_role = normalize_task_role(prior_declared_mode)
    if prior_role is None:
        prior_role = task_role(worktree / "TASK_CARD_FULL.md") or ""
    exact["prior_role"] = prior_role
    if prior_role == "checker-test":
        exact["prior_claude_session_id"] = str(
            runtime.get("claude_session_id") or ""
        ).strip()
    for key, expected in exact.items():
        if approval.get(key) != expected:
            raise ContinuationError(f"approval binding mismatch: {key}")
    contract = task_contract(card)
    if contract["role"] != approval.get("next_role"):
        raise ContinuationError("next role/task card mismatch")
    if approval.get("next_role") == "builder":
        inherited = normalize_builder_mode(approval.get("inherited_builder_mode"))
        declared_builder = contract["builder_mode"]
        if not inherited or (declared_builder and declared_builder != inherited):
            raise ContinuationError("next Builder mode/approval mismatch")
    actual = changed_paths(worktree)
    if actual != approval.get("accepted_existing_paths"):
        raise ContinuationError("changed path set drifted after approval")
    if compute_worktree_state_hash(worktree) != approval.get("worktree_state_hash"):
        raise ContinuationError("worktree state drifted after approval")
    if path_state(worktree, actual) != approval.get("accepted_path_state"):
        raise ContinuationError("path content/mode drifted after approval")
    return approval, root, worktree


def validate(args: argparse.Namespace) -> Dict[str, Any]:
    approval, _, _ = validate_common(args.approval, args.next_task_card)
    return approval


def post_run(args: argparse.Namespace) -> Dict[str, Any]:
    approval = load_json(args.approval.resolve())
    worktree = Path(str(approval.get("worktree", ""))).resolve()
    if not worktree.is_dir():
        raise ContinuationError("approval worktree is unavailable")
    actual = changed_paths(worktree)
    accepted = set(approval.get("accepted_existing_paths") or [])
    allowed = set(approval.get("allow_new_write_paths") or [])
    outside = sorted(set(actual) - accepted - allowed)
    if outside:
        raise ContinuationError(f"post-run paths outside approval: {outside}")
    protected = sorted(accepted - allowed)
    current = path_state(worktree, protected)
    baseline = {key: value for key, value in (approval.get("accepted_path_state") or {}).items() if key in protected}
    if current != baseline:
        raise ContinuationError("accepted existing paths were modified outside new-write scope")
    return {"approval_id": approval.get("approval_id"), "changed_paths": actual,
            "outside_paths": [], "protected_existing_unchanged": True}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--prior-task-id", required=True)
    prepare_parser.add_argument("--next-task-card", type=Path, required=True)
    prepare_parser.add_argument("--next-role", choices=sorted(ALLOWED_ROLES))
    prepare_parser.add_argument("--decision", required=True)
    prepare_parser.add_argument("--accepted-existing-path", action="append", default=[], required=True)
    prepare_parser.add_argument("--allow-new-write-path", action="append", default=[], required=True)
    prepare_parser.add_argument("--delta-review-packet", type=Path)
    prepare_parser.add_argument("--unresolved-finding", action="append", default=[])
    prepare_parser.add_argument("--new-validation-ref", action="append", default=[])
    prepare_parser.add_argument(
        "--output", type=Path,
        help="authorization path; defaults to the common .worktrees control directory",
    )
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--approval", type=Path, required=True)
    validate_parser.add_argument("--next-task-card", type=Path, required=True)
    post_parser = sub.add_parser("post-run")
    post_parser.add_argument("--approval", type=Path, required=True)
    return result


def main(argv: Optional[List[str]] = None) -> int:
    args = parser().parse_args(argv)
    try:
        value = prepare(args) if args.command == "prepare" else (
            validate(args) if args.command == "validate" else post_run(args)
        )
    except (ContinuationError, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"reviewed-continuation: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
