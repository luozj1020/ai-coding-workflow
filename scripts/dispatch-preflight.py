#!/usr/bin/env python3
"""Validate that task-relevant dirty source is visible in the execution worktree."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List


def _safe_relative(value: str) -> bool:
    path = value.replace("\\", "/")
    return bool(path) and not path.startswith("/") and not (
        len(path) > 2 and path[1] == ":" and path[2] == "/"
    ) and ".." not in path.split("/")


def _mentioned(path: str, card: str) -> bool:
    normalized = path.replace("\\", "/")
    if normalized in card:
        return True
    parts = normalized.split("/")
    return any("/".join(parts[:index]) + "/" in card for index in range(1, len(parts)))


def _digest(path: Path) -> str:
    if path.is_symlink():
        material = ("symlink:" + os.readlink(path)).encode("utf-8", errors="surrogateescape")
    elif path.is_file():
        material = path.read_bytes()
    else:
        return "missing"
    return "sha256:" + hashlib.sha256(material).hexdigest()


def _atomic_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def assess(source: Path, worktree: Path, task_card: Path, dirty_paths: Iterable[str]) -> Dict[str, Any]:
    card = task_card.read_text(encoding="utf-8", errors="replace")
    considered = sorted({item.strip().replace("\\", "/") for item in dirty_paths if item.strip()})
    unsafe = [item for item in considered if not _safe_relative(item)]
    relevant = [item for item in considered if _safe_relative(item) and _mentioned(item, card)]
    entries: List[Dict[str, Any]] = []
    blocked: List[str] = []
    for item in relevant:
        source_path = source / item
        target_path = worktree / item
        source_hash = _digest(source_path)
        target_hash = _digest(target_path)
        if target_hash == "missing":
            state = "missing-in-execution-worktree"
        elif source_hash != target_hash:
            state = "stale-in-execution-worktree"
        else:
            state = "visible-and-equal"
        if state != "visible-and-equal":
            blocked.append(item)
        entries.append({
            "path": item,
            "state": state,
            "source_object": source_hash,
            "execution_object": target_hash,
        })
    status = "blocked" if unsafe or blocked else "passed"
    return {
        "schema_version": 1,
        "status": status,
        "source_repository": str(source.resolve()),
        "execution_worktree": str(worktree.resolve()),
        "dirty_paths_considered": len(considered),
        "task_relevant_dirty_paths": relevant,
        "unsafe_dirty_paths": unsafe,
        "path_evidence": entries,
        "blocked_paths": blocked,
        "reason": (
            "task-relevant dirty source is absent or stale in the execution worktree"
            if blocked else ("unsafe dirty path" if unsafe else "execution worktree matches relevant source")
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--task-card", type=Path, required=True)
    parser.add_argument("--dirty-paths", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        dirty = args.dirty_paths.read_text(encoding="utf-8", errors="replace").splitlines()
        result = assess(args.source, args.worktree, args.task_card, dirty)
        _atomic_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] == "passed" else 2
    except OSError as exc:
        print("Error: {}".format(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
