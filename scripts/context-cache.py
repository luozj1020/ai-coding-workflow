#!/usr/bin/env python3
"""Content-addressed cache for bounded, repository-bound context evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional


def key(value: Dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_relative(raw: str) -> str:
    value = raw.replace("\\", "/")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError("cache file paths must be normalized repository-relative paths")
    return path.as_posix()


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _git_head(repo: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ValueError("--repo must identify a Git worktree with a readable HEAD")
    return result.stdout.strip()


def repository_identity(
    repo: Path,
    files: Iterable[str],
    symbols: Iterable[str],
    tool_version: str,
) -> Dict[str, Any]:
    repo = repo.resolve()
    file_hashes: Dict[str, str] = {}
    for raw in sorted(set(files)):
        relative = _safe_relative(raw)
        target = (repo / relative).resolve()
        try:
            target.relative_to(repo)
        except ValueError as exc:
            raise ValueError(f"cache file escapes repository: {relative}") from exc
        if target.is_symlink() or not target.is_file():
            raise ValueError(f"cache file is missing or not a regular file: {relative}")
        file_hashes[relative] = _file_digest(target)
    normalized_symbols = sorted({str(value).strip() for value in symbols if str(value).strip()})
    if not tool_version.strip():
        raise ValueError("--tool-version must be non-empty")
    return {
        "repository_head": _git_head(repo),
        "file_hashes": file_hashes,
        "symbols": normalized_symbols,
        "tool_version": tool_version.strip(),
    }


def _meta_list(meta: Dict[str, Any], field: str) -> List[str]:
    value = meta.get(field, [])
    if not isinstance(value, list):
        raise ValueError(f"meta.{field} must be an array")
    return [str(item) for item in value]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["get", "put"])
    parser.add_argument("--cache", default=".ai-workflow/cache/context")
    parser.add_argument("--meta", required=True)
    parser.add_argument("--content")
    parser.add_argument("--max-bytes", type=int, default=32768)
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--file", action="append", default=[])
    parser.add_argument("--symbol", action="append", default=[])
    parser.add_argument("--tool-version", default="context-cache-v2")
    args = parser.parse_args(argv)
    try:
        meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))
        if not isinstance(meta, dict):
            raise ValueError("--meta must contain a JSON object")
        identity = None
        if args.repo:
            files = args.file or _meta_list(meta, "files")
            symbols = args.symbol or _meta_list(meta, "symbols")
            identity = repository_identity(args.repo, files, symbols, args.tool_version)
        cache_key = key({"meta": meta, "repository_identity": identity})
        root = Path(args.cache)
        destination = root / (cache_key + ".json")
        if args.action == "get":
            if not destination.exists():
                return 2
            value = json.loads(destination.read_text(encoding="utf-8"))
            if value.get("repository_identity") != identity:
                print("context cache identity mismatch", file=sys.stderr)
                return 3
            print(json.dumps(value, ensure_ascii=False, sort_keys=True))
            return 0
        if not args.content:
            parser.error("put requires --content")
        if args.max_bytes <= 0:
            parser.error("--max-bytes must be positive")
        content = Path(args.content).read_text(encoding="utf-8", errors="replace")
        encoded = content.encode("utf-8")[:args.max_bytes]
        content = encoded.decode("utf-8", errors="ignore")
        root.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps({
            "schema_version": 2 if identity else 1,
            "key": cache_key,
            "meta": meta,
            "repository_identity": identity,
            "validation_status": "hash-bound" if identity else "legacy-unverified",
            "created_at": int(time.time()),
            "content": content,
        }, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        print(destination)
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"context cache: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
