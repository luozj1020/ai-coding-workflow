#!/usr/bin/env python3
"""Prepare exact writable bind targets for a read-only-root Claude sandbox."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional


CONTROL_WRITES = (
    "CLAUDE_PROGRESS.md",
    "CLAUDE_REPORT.md",
    "CLAUDE_TASK_CARD.md",
)
UNSAFE_PATTERN = re.compile(r"[*?\[\]{}:]")
CARD_FIELD_PATTERN = r"(?i)^(?:-[ \t]*)?(?:\*\*)?{label}:(?:\*\*)?[ \t]*(.*)$"


class SandboxError(RuntimeError):
    pass


def _path_item(raw: str) -> str:
    value = raw.strip()
    if value.startswith("`") or value.endswith("`"):
        if len(value) < 2 or not (value.startswith("`") and value.endswith("`")):
            raise SandboxError(f"Write path has text outside its code span: {raw!r}")
        value = value[1:-1]
    elif any(char.isspace() for char in value):
        raise SandboxError(
            f"Write path contains prose or whitespace; use an exact backtick-quoted path: {raw!r}"
        )
    if "`" in value:
        raise SandboxError(f"Write path contains an invalid code-span delimiter: {raw!r}")
    return value


def _card_paths(text: str) -> List[str]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(
            CARD_FIELD_PATTERN.format(label=r"Write[ \t]+paths"), line
        )
        if not match:
            continue
        inline = match.group(1).strip()
        if inline:
            if inline.lower() in {"none", "not assigned", "n/a"}:
                return []
            return [
                _path_item(item)
                for item in inline.split(",")
                if item.strip()
            ]

        values: List[str] = []
        for nested in lines[index + 1:]:
            if re.match(
                r"(?i)^(?:-[ \t]*)?(?:\*\*)?[^:#]+:(?:\*\*)?[ \t]*$",
                nested,
            ) or nested.startswith("#"):
                break
            if not nested.strip():
                continue
            item = re.match(r"^[ \t]*-[ \t]+(.+?)\s*$", nested)
            if item is None:
                raise SandboxError(
                    f"invalid multi-line Write paths entry: {nested!r}"
                )
            values.append(_path_item(item.group(1)))
        return values
    raise SandboxError("task card has no Write paths")


def _full_replacement_paths(text: str) -> List[str]:
    match = re.search(
        r"(?im)^(?:-[ \t]*)?(?:\*\*)?Full[ \t]+file[ \t]+replacement[ \t]+paths:(?:\*\*)?[ \t]*(.*)$",
        text,
    )
    if not match:
        return []
    value = match.group(1).strip()
    if value.lower() in {"", "none", "not assigned", "n/a"}:
        return []
    return [_path_item(item) for item in value.split(",") if item.strip()]


def normalize(raw: str, worktree: Path) -> str:
    value = raw.replace("\\", "/").strip()
    if not value or any(ord(char) < 32 for char in value) or UNSAFE_PATTERN.search(value):
        raise SandboxError(f"Write path must be exact and glob-free: {raw!r}")
    pure = PurePosixPath(value.rstrip("/"))
    if pure.is_absolute() or ".." in pure.parts or value in {".", "./"}:
        raise SandboxError(f"Write path must be repository-relative: {raw!r}")
    if not pure.parts or pure.parts[0] in {
        ".git", ".worktrees", ".aiwf-write-staging", ".aiwf-runtime"
    }:
        raise SandboxError(f"workflow metadata path is forbidden: {raw!r}")
    target = worktree.joinpath(*pure.parts)
    try:
        resolved_relative = target.resolve(strict=False).relative_to(worktree.resolve())
    except ValueError as exc:
        raise SandboxError(f"Write path escapes worktree: {raw!r}") from exc
    if resolved_relative.parts and resolved_relative.parts[0] in {
        ".git", ".worktrees", ".aiwf-write-staging", ".aiwf-runtime"
    }:
        raise SandboxError(f"Write path resolves into workflow metadata: {raw!r}")
    return pure.as_posix() + ("/" if value.endswith("/") else "")


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, value: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def prepare(
    card: Path, worktree: Path, output: Path,
    allowed_paths: Optional[List[str]] = None,
    staging_root: Optional[Path] = None,
) -> Dict[str, object]:
    worktree = worktree.resolve()
    if not worktree.is_dir():
        raise SandboxError("worktree is unavailable")
    text = card.read_text(encoding="utf-8", errors="replace")
    raw_paths = allowed_paths if allowed_paths else _card_paths(text)
    declared = [normalize(value, worktree) for value in raw_paths]
    explicit_full_replacements = {
        normalize(value, worktree).rstrip("/") for value in _full_replacement_paths(text)
    }
    undeclared_full = explicit_full_replacements.difference(
        value.rstrip("/") for value in declared
    )
    if undeclared_full:
        raise SandboxError(
            "Full file replacement path is not an exact declared Write path: "
            + ", ".join(sorted(undeclared_full))
        )
    if not declared:
        raise SandboxError("a writing task requires at least one exact Write path")
    values = sorted(set(declared + list(CONTROL_WRITES)))
    bind_targets: List[str] = []
    bindings: List[Dict[str, object]] = []
    staging_root = (staging_root or output.parent / f".{output.stem}.staging").resolve()
    if staging_root == worktree or worktree in staging_root.parents:
        raise SandboxError("write staging root must be outside the worktree")
    staging_root.mkdir(parents=True, exist_ok=True)
    for value in values:
        directory = value.endswith("/")
        relative = value.rstrip("/")
        target = worktree / relative
        cursor = worktree
        for part in PurePosixPath(relative).parts:
            cursor /= part
            if cursor.is_symlink():
                raise SandboxError(f"symlink component in Write path is forbidden: {relative}")
            if not cursor.exists():
                break
        target_preexisted = target.exists()
        stage = staging_root / relative
        stage.parent.mkdir(parents=True, exist_ok=True)
        if directory:
            if target.exists():
                shutil.copytree(target, stage, symlinks=True, dirs_exist_ok=True)
            else:
                stage.mkdir(parents=True, exist_ok=True)
            # bubblewrap requires an existing mount destination.
            target.mkdir(parents=True, exist_ok=True)
        else:
            if target.exists():
                shutil.copy2(target, stage)
            else:
                stage.touch()
                # The destination placeholder is not evidence and is ignored
                # by retry-in-place until staged content is synchronized.
                target.parent.mkdir(parents=True, exist_ok=True)
                target.touch()
        if directory != target.is_dir():
            expected = "directory" if directory else "file"
            raise SandboxError(f"Write path is not the declared {expected}: {relative}")
        if target.is_file() and target.stat().st_nlink != 1:
            raise SandboxError(f"hard-linked Write path is forbidden: {relative}")
        bind_targets.append(str(target.resolve()))
        bindings.append({
            "relative_path": relative,
            "kind": "directory" if directory else "file",
            "source": str(stage.resolve()),
            "target": str(target.resolve()),
            "target_preexisted": target_preexisted,
            "staged_initial_sha256": None if directory else _sha256(stage),
            "candidate_validation_required": (
                not directory and PurePosixPath(relative).suffix.lower()
                in {".py", ".pyi", ".json", ".toml"}
            ),
            "complete_file_write_allowed": (
                not target_preexisted or relative in explicit_full_replacements
                or relative in CONTROL_WRITES
            ),
        })
    value: Dict[str, object] = {
        "schema_version": 1,
        "status": "ready",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "enforcement": "bubblewrap-read-only-root-exact-writable-binds",
        "task_card": str(card.resolve()),
        "task_card_object": _sha256(card),
        "worktree": str(worktree),
        "declared_write_paths": declared,
        "control_write_paths": list(CONTROL_WRITES),
        "bind_targets": bind_targets,
        "staging_root": str(staging_root),
        "bindings": bindings,
        "complete_file_write_policy": "new-files-or-explicit-paths-only",
        "candidate_checkpoint_policy": "validate-before-same-inode-write-and-rollback-on-io-failure",
        "candidate_validation_extensions": [".json", ".py", ".pyi", ".toml"],
        "large_fragment_policy": {
            "minimum_existing_file_bytes": 4096,
            "maximum_fraction_without_full_replacement_authority": 0.75,
        },
        "bash_cannot_bypass_scope": True,
    }
    atomic_json(output, value)
    return value


def sync_receipt(receipt: Path) -> Dict[str, object]:
    value = json.loads(receipt.read_text(encoding="utf-8"))
    if value.get("status") != "ready":
        raise SandboxError("write sandbox receipt is not ready")
    synced: List[str] = []
    for binding in value.get("bindings", []):
        if not isinstance(binding, dict):
            raise SandboxError("write sandbox receipt has an invalid binding")
        source = Path(str(binding.get("source", "")))
        target = Path(str(binding.get("target", "")))
        kind = binding.get("kind")
        if kind == "file":
            if not source.is_file() or source.is_symlink():
                raise SandboxError(f"staged file is unavailable: {source}")
            target.parent.mkdir(parents=True, exist_ok=True)
            if not binding.get("target_preexisted") and source.stat().st_size == 0:
                # Bubblewrap needs a mount destination, but an interrupted run
                # must not leave an evidence-free zero-byte product file.
                if target.exists() and target.is_file() and target.stat().st_size == 0:
                    target.unlink()
                synced.append(str(binding.get("relative_path", "")))
                continue
            fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.aiwf-sync-", dir=str(target.parent))
            os.close(fd)
            try:
                shutil.copy2(source, temporary)
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        elif kind == "directory":
            if not source.is_dir() or source.is_symlink():
                raise SandboxError(f"staged directory is unavailable: {source}")
            target.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target, symlinks=True, dirs_exist_ok=True)
        else:
            raise SandboxError("write sandbox receipt has an invalid binding kind")
        synced.append(str(binding.get("relative_path", "")))
    return {"status": "synced", "synced_paths": synced}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-card", type=Path)
    parser.add_argument("--worktree", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--staging-root", type=Path)
    parser.add_argument("--sync-receipt", type=Path)
    parser.add_argument("--allow-path", action="append", default=[])
    parser.add_argument("--print-paths", action="store_true")
    parser.add_argument("--print-bindings", action="store_true")
    args = parser.parse_args()
    try:
        if args.sync_receipt:
            print(json.dumps(sync_receipt(args.sync_receipt.resolve()), sort_keys=True))
            return 0
        if not args.task_card or not args.worktree or not args.output:
            parser.error("--task-card, --worktree, and --output are required for preparation")
        value = prepare(
            args.task_card.resolve(), args.worktree, args.output.resolve(),
            args.allow_path or None,
            args.staging_root.resolve() if args.staging_root else None,
        )
    except (OSError, ValueError, SandboxError) as exc:
        print(f"write sandbox: {exc}", file=os.sys.stderr)
        return 2
    if args.print_bindings:
        for binding in value["bindings"]:
            print(f"{binding['source']}\t{binding['target']}")
    elif args.print_paths:
        for path in value["bind_targets"]:
            print(path)
    else:
        print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
