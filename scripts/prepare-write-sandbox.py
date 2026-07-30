#!/usr/bin/env python3
"""Prepare exact writable bind targets for a read-only-root Claude sandbox."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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


class SandboxError(RuntimeError):
    pass


def _card_paths(text: str) -> List[str]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"(?i)^-[ \t]*Write paths:[ \t]*(.*)$", line)
        if not match:
            continue
        inline = match.group(1).strip()
        if inline:
            if inline.lower() in {"none", "not assigned", "n/a"}:
                return []
            return [
                item.strip().strip("`")
                for item in inline.split(",")
                if item.strip()
            ]

        values: List[str] = []
        for nested in lines[index + 1:]:
            if re.match(r"^-[ \t]*[^:]+:", nested) or nested.startswith("#"):
                break
            if not nested.strip():
                continue
            item = re.match(r"^[ \t]+-[ \t]+(.+?)\s*$", nested)
            if item is None:
                raise SandboxError(
                    f"invalid multi-line Write paths entry: {nested!r}"
                )
            values.append(item.group(1).strip().strip("`"))
        return values
    raise SandboxError("task card has no Write paths")


def normalize(raw: str, worktree: Path) -> str:
    value = raw.replace("\\", "/").strip()
    if not value or UNSAFE_PATTERN.search(value):
        raise SandboxError(f"Write path must be exact and glob-free: {raw!r}")
    pure = PurePosixPath(value.rstrip("/"))
    if pure.is_absolute() or ".." in pure.parts or value in {".", "./"}:
        raise SandboxError(f"Write path must be repository-relative: {raw!r}")
    if not pure.parts or pure.parts[0] in {".git", ".worktrees"}:
        raise SandboxError(f"workflow metadata path is forbidden: {raw!r}")
    target = worktree.joinpath(*pure.parts)
    try:
        resolved_relative = target.resolve(strict=False).relative_to(worktree.resolve())
    except ValueError as exc:
        raise SandboxError(f"Write path escapes worktree: {raw!r}") from exc
    if resolved_relative.parts and resolved_relative.parts[0] in {".git", ".worktrees"}:
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
) -> Dict[str, object]:
    worktree = worktree.resolve()
    if not worktree.is_dir():
        raise SandboxError("worktree is unavailable")
    text = card.read_text(encoding="utf-8", errors="replace")
    raw_paths = allowed_paths if allowed_paths else _card_paths(text)
    declared = [normalize(value, worktree) for value in raw_paths]
    if not declared:
        raise SandboxError("a writing task requires at least one exact Write path")
    values = sorted(set(declared + list(CONTROL_WRITES)))
    bind_targets: List[str] = []
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
        target.parent.mkdir(parents=True, exist_ok=True)
        if directory:
            target.mkdir(parents=True, exist_ok=True)
        elif not target.exists():
            target.touch()
        if directory != target.is_dir():
            expected = "directory" if directory else "file"
            raise SandboxError(f"Write path is not the declared {expected}: {relative}")
        if target.is_file() and target.stat().st_nlink != 1:
            raise SandboxError(f"hard-linked Write path is forbidden: {relative}")
        bind_targets.append(str(target.resolve()))
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
        "bash_cannot_bypass_scope": True,
    }
    atomic_json(output, value)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-card", type=Path, required=True)
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-path", action="append", default=[])
    parser.add_argument("--print-paths", action="store_true")
    args = parser.parse_args()
    try:
        value = prepare(
            args.task_card.resolve(), args.worktree, args.output.resolve(),
            args.allow_path or None,
        )
    except (OSError, ValueError, SandboxError) as exc:
        print(f"write sandbox: {exc}", file=os.sys.stderr)
        return 2
    if args.print_paths:
        for path in value["bind_targets"]:
            print(path)
    else:
        print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
