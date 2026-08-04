#!/usr/bin/env python3
"""Compact one accepted task's runtime artifacts into a deterministic archive."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


KEEP_SUFFIXES = (
    ".task-card.md",
    ".diff",
    ".outcome.json",
    ".validation-capability.json",
    ".checker-contract.json",
    ".recovered-completion.json",
    ".activity-observation.json",
    ".codex-write-owner.json",
    ".final-index.json",
)


class ArchiveError(RuntimeError):
    pass


def runtime_repo_root(source: Path) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=str(source), capture_output=True, text=True, check=False,
    )
    if result.returncode == 0:
        common = Path(result.stdout.strip())
        if not common.is_absolute():
            common = source / common
        common = common.resolve()
        if common.name == ".git":
            return common.parent
    return source.resolve()


def _hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def plan(repo: Path, task_id: str) -> Dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", task_id):
        raise ArchiveError("unsafe task id")
    root = runtime_repo_root(repo) / ".worktrees"
    runtime_path = root / f"{task_id}.runtime.json"
    if not runtime_path.is_file():
        raise ArchiveError("runtime receipt is missing")
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    if runtime.get("task_id") != task_id:
        raise ArchiveError("runtime task identity mismatch")
    worktree = Path(str(runtime.get("worktree") or "")).resolve()
    if not worktree.is_dir():
        raise ArchiveError("execution worktree is unavailable")
    card_source = worktree / "TASK_CARD_FULL.md"
    if not card_source.is_file():
        raise ArchiveError("final task card is missing")
    artifacts = sorted(
        path for path in root.glob(f"{task_id}.*")
        if path.is_file() and not any(path.name.endswith(suffix) for suffix in KEEP_SUFFIXES)
    )
    archive = root / "archive" / task_id
    head = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    return {
        "schema_version": 1,
        "task_id": task_id,
        "status": "preview",
        "archive_directory": str(archive),
        "worktree": str(worktree),
        "commit_reference": head,
        "task_card_source": str(card_source),
        "task_card_destination": str(root / f"{task_id}.task-card.md"),
        "archive_candidates": [str(path) for path in artifacts],
        "retained_suffixes": list(KEEP_SUFFIXES),
    }


def apply(value: Dict[str, Any]) -> Dict[str, Any]:
    archive = Path(value["archive_directory"])
    archive.mkdir(parents=True, exist_ok=True)
    card_source = Path(value["task_card_source"])
    card_destination = Path(value["task_card_destination"])
    shutil.copy2(card_source, card_destination)
    moved: List[Dict[str, str]] = []
    for raw in value["archive_candidates"]:
        source = Path(raw)
        if not source.is_file():
            continue
        target = archive / source.name
        shutil.move(str(source), str(target))
        moved.append({"path": source.name, "object": _hash(target)})
    index = {
        **value,
        "status": "archived",
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "task_card_object": _hash(card_destination),
        "moved": moved,
    }
    index_path = card_destination.with_name(f"{value['task_id']}.final-index.json")
    atomic_json(index_path, index)
    return index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path, nargs="?", default=Path.cwd())
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--final-status", choices=("accepted",), required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        value = plan(args.repo, args.task_id)
        if args.apply:
            value = apply(value)
    except (OSError, ValueError, json.JSONDecodeError, ArchiveError) as exc:
        print(f"runtime archive: {exc}", file=os.sys.stderr)
        return 2
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
