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
import json
import os
import re
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


def task_role(card: Path) -> Optional[str]:
    text = card.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"(?im)^\|\s*Mode\s*\|\s*([^|]+)", text)
    if not match:
        return None
    value = match.group(1).strip().lower()
    if value in {"checker", "test", "checker/test", "checker-test"}:
        return "checker-test"
    return "builder" if value == "builder" else None


def repository_root() -> Path:
    return Path(git(Path.cwd(), "rev-parse", "--show-toplevel").strip()).resolve()


def validate_runtime(root: Path, task_id: str) -> tuple[Dict[str, Any], Path, Path]:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", task_id):
        raise ContinuationError("unsafe prior task id")
    runtime_path = root / ".worktrees" / f"{task_id}.runtime.json"
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
    if source != root or not is_within(worktree, root / ".worktrees"):
        raise ContinuationError("runtime repository/worktree boundary mismatch")
    if not worktree.is_dir() or git(worktree, "rev-parse", "--is-inside-work-tree").strip() != "true":
        raise ContinuationError("recorded worktree is unavailable")
    for raw in (runtime.get("pid_files") or {}).values():
        if raw and live_pid_file(Path(str(raw))):
            raise ContinuationError(f"recorded process is still live: {raw}")
    source_head = git(root, "rev-parse", "HEAD").strip()
    worktree_head = git(worktree, "rev-parse", "HEAD").strip()
    base = str(runtime.get("base_commit", ""))
    if not base or source_head != base or worktree_head != base:
        raise ContinuationError("source HEAD, worktree HEAD, and recorded base must match")
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
    if args.next_role not in ALLOWED_ROLES or task_role(card) != args.next_role:
        raise ContinuationError("next role does not match task card Mode")
    prior_role = str(runtime.get("task_mode") or "").lower()
    if prior_role not in ALLOWED_ROLES:
        prior_role = task_role(worktree / "TASK_CARD_FULL.md") or ""
    if prior_role != "builder":
        raise ContinuationError("only Builder worktrees may start reviewed continuation")
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
    source_head = git(root, "rev-parse", "HEAD").strip()
    value: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "approval_id": approval_id,
        "request_id": approval_id,
        "status": "available",
        "decision": args.decision,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "prior_task_id": args.prior_task_id,
        "prior_role": prior_role,
        "next_role": args.next_role,
        "prior_strategy": runtime["strategy"],
        "provenance_root_strategy": runtime.get("provenance_root_strategy", "fresh"),
        "runtime_path": str(runtime_path.resolve()),
        "source_repository": str(root),
        "worktree": str(worktree),
        "base_commit": runtime["base_commit"],
        "source_head": source_head,
        "worktree_head": git(worktree, "rev-parse", "HEAD").strip(),
        "next_task_card": str(card),
        "next_task_card_sha256": sha256_file(card),
        "worktree_state_hash": compute_worktree_state_hash(worktree),
        "actual_changed_paths": actual,
        "accepted_existing_paths": accepted,
        "allow_new_write_paths": allowed,
        "accepted_path_state": accepted_state,
    }
    atomic_json(args.output, value)
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
        "source_head": git(root, "rev-parse", "HEAD").strip(),
        "worktree_head": git(worktree, "rev-parse", "HEAD").strip(),
        "next_task_card": str(card),
        "next_task_card_sha256": sha256_file(card),
    }
    for key, expected in exact.items():
        if approval.get(key) != expected:
            raise ContinuationError(f"approval binding mismatch: {key}")
    if task_role(card) != approval.get("next_role"):
        raise ContinuationError("next role/task card mismatch")
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
    prepare_parser.add_argument("--next-role", choices=sorted(ALLOWED_ROLES), required=True)
    prepare_parser.add_argument("--decision", required=True)
    prepare_parser.add_argument("--accepted-existing-path", action="append", default=[], required=True)
    prepare_parser.add_argument("--allow-new-write-path", action="append", default=[], required=True)
    prepare_parser.add_argument("--output", type=Path, required=True)
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
