#!/usr/bin/env python3
"""Build a human-reviewable patch containing only approved product paths."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


DEFAULT_CONTROL_PATHS = {
    "ADVISOR_REQUEST.json",
    "CLAUDE_PROMPT.md",
    "CLAUDE_PROGRESS.md",
    "CLAUDE_REPORT.md",
    "CLAUDE_TASK_CARD.md",
    "TASK_CARD.md",
    "TASK_CARD_FULL.md",
    "advisor-decision.json",
    "advisor-packet.json",
    "advisor-packet.md",
    "solution-contract.draft.json",
    "truncation-manifest.json",
}


class HandoffError(RuntimeError):
    pass


def safe_relative(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    path = PurePosixPath(normalized.rstrip("/"))
    if (
        not normalized
        or path.is_absolute()
        or normalized.startswith("/")
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise HandoffError("unsafe relative path: {}".format(value))
    return path.as_posix()


def run_git(
    worktree: Path,
    args: Sequence[str],
    allowed: Iterable[int] = (0,),
) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(worktree), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode not in set(allowed):
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise HandoffError("git {} failed: {}".format(" ".join(args), detail))
    return result.stdout


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    atomic_bytes(path, payload)


def load_scope(path: Optional[Path]) -> Dict[str, Any]:
    if path is None:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HandoffError("invalid write-scope receipt: {}".format(exc))
    if not isinstance(value, dict):
        raise HandoffError("write-scope receipt must be a JSON object")
    return value


def allowed_contract(
    worktree: Path, receipt: Dict[str, Any], explicit: Sequence[str]
) -> Tuple[List[str], Set[str], Set[str]]:
    declared = list(explicit)
    declared.extend(str(value) for value in receipt.get("declared_write_paths", []))
    controls = set(DEFAULT_CONTROL_PATHS)
    controls.update(
        safe_relative(str(value)) for value in receipt.get("control_write_paths", [])
    )
    directory_paths: Set[str] = set()
    for binding in receipt.get("bindings", []):
        if isinstance(binding, dict) and binding.get("kind") == "directory":
            directory_paths.add(safe_relative(str(binding.get("relative_path", ""))))
    normalized: List[str] = []
    for raw in declared:
        is_directory = str(raw).replace("\\", "/").strip().endswith("/")
        path = safe_relative(str(raw))
        if is_directory or (worktree / path).is_dir():
            directory_paths.add(path)
        if path not in normalized:
            normalized.append(path)
    if not normalized:
        raise HandoffError("no approved product paths were provided")
    return normalized, directory_paths, controls


def matches(path: str, allowed: Sequence[str], directories: Set[str]) -> bool:
    return any(
        path == candidate
        or (candidate in directories and path.startswith(candidate + "/"))
        for candidate in allowed
    )


def is_control_path(path: str, controls: Set[str]) -> bool:
    """Return whether *path* is workflow metadata, never product output."""
    if path in controls:
        return True
    # Runtime-generated advisor artifacts have bounded root-level names. Keep
    # the rule here aligned with the dispatcher without treating product
    # subdirectories as workflow metadata.
    name = PurePosixPath(path)
    return (
        len(name.parts) == 1
        and name.name.startswith("advisor-response-")
        and name.suffix == ".json"
    )


def name_status(worktree: Path, baseline: str) -> List[Dict[str, str]]:
    raw = run_git(worktree, ["diff", "--name-status", "-z", baseline, "--"])
    fields = raw.split(b"\0")
    result: List[Dict[str, str]] = []
    index = 0
    while index < len(fields) and fields[index]:
        first = fields[index].decode("utf-8", errors="surrogateescape")
        index += 1
        if "\t" in first:
            status, path = first.split("\t", 1)
        else:
            status = first
            if index >= len(fields):
                raise HandoffError("malformed git name-status output")
            path = fields[index].decode("utf-8", errors="surrogateescape")
            index += 1
        entry = {"status": status, "path": safe_relative(path)}
        if status[:1] in {"R", "C"}:
            if index >= len(fields):
                raise HandoffError("malformed rename/copy status")
            target = fields[index].decode("utf-8", errors="surrogateescape")
            index += 1
            entry["source_path"] = entry["path"]
            entry["path"] = safe_relative(target)
        result.append(entry)
    return result


def untracked_paths(worktree: Path) -> List[str]:
    raw = run_git(worktree, ["ls-files", "--others", "--exclude-standard", "-z"])
    return sorted(
        safe_relative(value.decode("utf-8", errors="surrogateescape"))
        for value in raw.split(b"\0") if value
    )


def empty_file_patch(path: str, executable: bool) -> bytes:
    mode = "100755" if executable else "100644"
    return (
        "diff --git a/{0} b/{0}\n"
        "new file mode {1}\n"
        "index 0000000..e69de29\n".format(path, mode)
    ).encode("utf-8")


def build_patch(
    worktree: Path, baseline: str, tracked: Sequence[str], untracked: Sequence[str]
) -> bytes:
    chunks: List[bytes] = []
    if tracked:
        chunks.append(
            run_git(
                worktree,
                ["diff", "--binary", "--full-index", "--no-ext-diff", baseline,
                 "--", *tracked],
            )
        )
    for path in untracked:
        chunk = run_git(
            worktree,
            ["diff", "--no-index", "--binary", "--", "/dev/null", path],
            allowed=(0, 1),
        )
        if not chunk and (worktree / path).is_file() and (worktree / path).stat().st_size == 0:
            chunk = empty_file_patch(path, os.access(worktree / path, os.X_OK))
        chunks.append(chunk)
    return b"".join(chunk for chunk in chunks if chunk)


def build(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    worktree = args.worktree.resolve()
    output_dir = args.output_dir.resolve()
    try:
        output_dir.relative_to(worktree)
    except ValueError:
        pass
    else:
        raise HandoffError("handoff artifacts must be outside the product worktree")

    run_git(worktree, ["cat-file", "-e", args.source_base + "^{commit}"])
    run_git(worktree, ["cat-file", "-e", args.execution_base + "^{commit}"])
    execution_base = run_git(
        worktree, ["rev-parse", args.execution_base + "^{commit}"]
    ).decode().strip()
    source_base = run_git(
        worktree, ["rev-parse", args.source_base + "^{commit}"]
    ).decode().strip()
    execution_tree = run_git(
        worktree, ["rev-parse", execution_base + "^{tree}"]
    ).decode().strip()

    receipt = load_scope(args.write_scope)
    allowed, directories, controls = allowed_contract(
        worktree, receipt, args.allow_path
    )
    tracked_status = name_status(worktree, execution_base)
    untracked = untracked_paths(worktree)
    all_paths = {entry["path"] for entry in tracked_status} | set(untracked)
    all_paths.update(
        entry["source_path"] for entry in tracked_status if entry.get("source_path")
    )
    control_changed = sorted(path for path in all_paths if is_control_path(path, controls))
    unexpected = sorted(
        path for path in all_paths
        if not is_control_path(path, controls) and not matches(path, allowed, directories)
    )
    selected = sorted(
        path for path in all_paths if matches(path, allowed, directories)
    )
    selected_untracked = sorted(set(untracked) & set(selected))
    selected_tracked = sorted(set(selected) - set(selected_untracked))

    patch_path = output_dir / (args.task_id + ".scoped.patch")
    manifest_path = output_dir / (args.task_id + ".scoped-handoff.json")
    if unexpected:
        patch = b""
        status = "blocked"
        exit_code = 2
    else:
        patch = build_patch(
            worktree, execution_base, selected_tracked, selected_untracked
        )
        if selected and not patch:
            # A non-empty product delta paired with an empty handoff is an
            # internal workflow failure, not a valid empty delivery.
            status = "internal-error"
            exit_code = 3
        else:
            status = "ready" if patch else "empty"
            exit_code = 0
    atomic_bytes(patch_path, patch)

    status_by_path = {entry["path"]: entry["status"] for entry in tracked_status}
    changed_files = [
        {
            "path": path,
            "change": "added" if path in selected_untracked else status_by_path.get(path, "unknown"),
        }
        for path in selected
    ]
    dirty_snapshot = bool(args.dirty_snapshot or source_base != execution_base)
    value: Dict[str, Any] = {
        "schema_version": 1,
        "status": status,
        "task_id": args.task_id,
        "authority": "human-review-and-apply-only",
        "merge_authorized": False,
        "worktree": str(worktree),
        "source_base_commit": source_base,
        "execution_base_commit": execution_base,
        "execution_base_tree": execution_tree,
        "dirty_snapshot": dirty_snapshot,
        "whole_worktree_merge_allowed": False,
        "warning": (
            "Dirty snapshot contains pre-existing source changes; do not merge the "
            "worktree. Review and apply only this scoped patch."
            if dirty_snapshot else
            "Models never merge; review and apply only the scoped patch."
        ),
        "declared_product_paths": allowed,
        "product_change_count": len(selected),
        "control_change_count": len(control_changed),
        "changed_files": changed_files,
        "control_changed_paths": control_changed,
        "unexpected_changed_paths": unexpected,
        "out_of_scope_product_paths": unexpected,
        "deliverable": status == "ready",
        "internal_error_reason": (
            "product-changes-with-empty-patch"
            if status == "internal-error" else None
        ),
        "patch": {
            "path": str(patch_path),
            "bytes": len(patch),
            "sha256": "sha256:" + hashlib.sha256(patch).hexdigest(),
        },
        "apply_commands": {
            "check": ["git", "apply", "--check", str(patch_path)],
            "apply": ["git", "apply", str(patch_path)],
        },
        "validation_receipt": (
            str(args.validation_receipt.resolve())
            if args.validation_receipt and args.validation_receipt.is_file()
            else None
        ),
    }
    atomic_json(manifest_path, value)
    value["manifest_path"] = str(manifest_path)
    return value, exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--source-base", required=True)
    parser.add_argument("--execution-base", required=True)
    parser.add_argument("--write-scope", type=Path)
    parser.add_argument("--allow-path", action="append", default=[])
    parser.add_argument("--validation-receipt", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dirty-snapshot", action="store_true")
    args = parser.parse_args()
    if not args.task_id or any(
        character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
        for character in args.task_id
    ):
        parser.error("--task-id contains unsafe characters")
    try:
        value, exit_code = build(args)
    except HandoffError as exc:
        parser.error(str(exc))
    print(json.dumps(value, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
