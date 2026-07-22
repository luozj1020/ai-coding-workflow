#!/usr/bin/env python3
"""Snapshot recognized untracked workflow controls outside the source root."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List


CONTROL_FILES = {
    "TASK_CARD.md", "TASK_CARD_FULL.md", "CLAUDE_TASK_CARD.md",
    "CLAUDE_PROMPT.md", "CLAUDE_PROGRESS.md", "CLAUDE_REPORT.md",
}


def _hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def archive(repo: Path, destination: Path) -> Dict[str, object]:
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "--others", "--exclude-standard"],
        text=True, encoding="utf-8", errors="replace", capture_output=True, check=False,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or "cannot enumerate untracked files")
    names = sorted({line.strip().replace("\\", "/") for line in result.stdout.splitlines()})
    selected = [name for name in names if "/" not in name and name in CONTROL_FILES]
    entries: List[Dict[str, str]] = []
    destination.mkdir(parents=True, exist_ok=True)
    for name in selected:
        source = repo / name
        target = destination / name
        shutil.copy2(source, target)
        entries.append({"path": name, "source_object": _hash(source), "archive_object": _hash(target)})
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_repository": str(repo.resolve()),
        "archive_directory": str(destination.resolve()),
        "archived_paths": selected,
        "path_evidence": entries,
        "source_files_retained": True,
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
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--archive-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        value = archive(args.repo, args.archive_dir)
        atomic_write(args.output, value)
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
