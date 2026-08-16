#!/usr/bin/env python3
"""Validate tracked and untracked worktree changes without mutating Git state."""

from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
from typing import Any, Iterable, Optional

try:
    import tomllib
except ImportError:  # Python 3.9/3.10 compatibility.
    tomllib = None


CONTROL_NAMES = {
    "TASK_CARD.md", "TASK_CARD_FULL.md", "CLAUDE_TASK_CARD.md", "CLAUDE_PROMPT.md",
    "CLAUDE_PROGRESS.md", "CLAUDE_REPORT.md", "ADVISOR_REQUEST.json",
}
MAX_FILE_BYTES = 16 * 1024 * 1024


class ValidationError(RuntimeError):
    pass


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    if check and result.returncode:
        raise ValidationError(
            f"git {' '.join(args)} failed: {(result.stderr or result.stdout).strip()}"
        )
    return result


def normalize_path(value: str) -> str:
    value = value.replace("\\", "/").strip()
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise ValidationError(f"unsafe changed path: {value!r}")
    return path.as_posix()


def paths_from(root: Path, *args: str) -> list[str]:
    return [
        normalize_path(value) for value in git(root, *args).stdout.splitlines()
        if value.strip() and PurePosixPath(value.strip()).name not in CONTROL_NAMES
    ]


def changed_paths(root: Path) -> tuple[list[str], list[str]]:
    tracked = sorted(set(
        paths_from(root, "diff", "--name-only")
        + paths_from(root, "diff", "--cached", "--name-only")
    ))
    untracked = sorted(set(paths_from(root, "ls-files", "--others", "--exclude-standard")))
    return tracked, untracked


def diff_check(root: Path, args: Iterable[str], label: str) -> list[dict[str, Any]]:
    result = git(root, *args, check=False)
    message = (result.stdout + result.stderr).strip()
    if result.returncode not in {0, 1} or message:
        return [{"kind": "diff-check", "path": None, "label": label,
                 "message": message or f"git exited {result.returncode}"}]
    return []


def top_level_duplicates(tree: ast.Module) -> dict[str, list[str]]:
    definitions = Counter(
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    )
    imports: Counter[tuple[Any, ...]] = Counter()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports[("import", alias.name, alias.asname)] += 1
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imports[("from", node.level, node.module, alias.name, alias.asname)] += 1
    return {
        "definitions": sorted(name for name, count in definitions.items() if count > 1),
        "imports": sorted(repr(value) for value, count in imports.items() if count > 1),
    }


def main_guard_count(tree: ast.Module) -> int:
    result = 0
    for node in tree.body:
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        test = node.test
        if (
            isinstance(test.left, ast.Name) and test.left.id == "__name__"
            and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)
            and len(test.comparators) == 1
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value == "__main__"
        ):
            result += 1
    return result


def validate_python(path: str, raw: bytes, previous: Optional[bytes]) -> tuple[list[str], list[dict[str, Any]]]:
    checks = ["python-utf8", "python-ast", "python-compile", "python-module-boundary"]
    errors: list[dict[str, Any]] = []
    try:
        source = raw.decode("utf-8")
        tree = ast.parse(source, filename=path)
        compile(tree, path, "exec", dont_inherit=True)
    except (UnicodeDecodeError, SyntaxError) as exc:
        return checks, [{"kind": "syntax", "path": path, "message": str(exc)}]
    duplicates = top_level_duplicates(tree)
    for label, values in duplicates.items():
        if values:
            errors.append({
                "kind": f"duplicate-{label}", "path": path,
                "message": ", ".join(values[:12]),
            })
    if main_guard_count(tree) > 1:
        errors.append({"kind": "duplicate-entry-point", "path": path,
                       "message": "multiple __main__ guards"})
    secondary_strings = [
        node.lineno for index, node in enumerate(tree.body) if index > 0
        and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]
    if secondary_strings:
        errors.append({"kind": "secondary-module-string", "path": path,
                       "message": f"module-like strings at lines {secondary_strings[:8]}"})
    internal_headers = [
        index for index, line in enumerate(source.splitlines(), 1)
        if (line.startswith("#!") and index != 1)
        or (
            re.search(r"^[ \t]*#.*coding[:=][ \t]*[-\w.]+", line)
            and index > 2
        )
    ]
    if internal_headers:
        errors.append({"kind": "internal-module-header", "path": path,
                       "message": f"headers at lines {internal_headers[:8]}"})
    if previous:
        try:
            previous_lines = previous.decode("utf-8").splitlines()
        except UnicodeDecodeError:
            previous_lines = []
        candidate_lines = source.splitlines()
        if len(previous_lines) >= 20:
            maximum = max(len(previous_lines) + 400, len(previous_lines) * 3)
            if len(candidate_lines) > maximum:
                errors.append({"kind": "abnormal-line-growth", "path": path,
                               "message": f"{len(previous_lines)} -> {len(candidate_lines)}; maximum {maximum}"})
    return checks, errors


def previous_bytes(root: Path, path: str) -> Optional[bytes]:
    result = subprocess.run(
        ["git", "-C", str(root), "show", f"HEAD:{path}"], capture_output=True,
        timeout=60,
    )
    return result.stdout if result.returncode == 0 else None


def validate_file(root: Path, path: str, max_bytes: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    full = root / path
    errors: list[dict[str, Any]] = []
    if not full.exists() and previous_bytes(root, path) is not None:
        return {
            "path": path, "checks": ["tracked-deletion"], "status": "passed",
            "change": "deleted",
        }, []
    if not full.is_file() or full.is_symlink():
        return {"path": path, "checks": ["regular-file"], "status": "failed"}, [
            {"kind": "non-regular-file", "path": path, "message": "changed path is not a regular file"}
        ]
    raw = full.read_bytes()
    checks = ["size-bound"]
    if len(raw) > max_bytes:
        errors.append({"kind": "file-too-large", "path": path,
                       "message": f"{len(raw)} bytes exceeds {max_bytes}"})
    suffix = full.suffix.lower()
    if suffix in {".py", ".pyi"} and not errors:
        extra, found = validate_python(path, raw, previous_bytes(root, path))
        checks.extend(extra)
        errors.extend(found)
    elif suffix == ".json" and not errors:
        checks.append("json-parse")
        try:
            json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors.append({"kind": "json-parse", "path": path, "message": str(exc)})
    elif suffix == ".toml" and not errors:
        checks.append("toml-parse")
        if tomllib is None:
            errors.append({"kind": "toml-parse", "path": path,
                           "message": "TOML parser unavailable; use Python 3.11+"})
        else:
            try:
                tomllib.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
                errors.append({"kind": "toml-parse", "path": path, "message": str(exc)})
    return {
        "path": path, "bytes": len(raw),
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "checks": checks, "status": "failed" if errors else "passed",
    }, errors


def embedded_content_errors(root: Path, paths: list[str], max_bytes: int) -> list[dict[str, Any]]:
    material: dict[str, bytes] = {}
    for path in paths:
        full = root / path
        if full.is_file() and not full.is_symlink() and 80 <= full.stat().st_size <= max_bytes:
            material[path] = full.read_bytes()
    errors: list[dict[str, Any]] = []
    for inner_path, inner in material.items():
        for outer_path, outer in material.items():
            if inner_path == outer_path or len(inner) >= len(outer):
                continue
            if inner in outer:
                errors.append({
                    "kind": "embedded-file-content", "path": outer_path,
                    "message": f"contains complete changed file {inner_path}",
                })
    return errors


def atomic_json(path: Path, value: dict[str, Any]) -> None:
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


def validate(root: Path, output: Optional[Path], max_bytes: int) -> dict[str, Any]:
    root = Path(git(root, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    tracked, untracked = changed_paths(root)
    errors = diff_check(root, ("diff", "--check"), "tracked-unstaged")
    errors.extend(diff_check(root, ("diff", "--cached", "--check"), "tracked-staged"))
    for path in untracked:
        errors.extend(diff_check(
            root, ("diff", "--no-index", "--check", "--", "/dev/null", path),
            f"untracked:{path}",
        ))
    file_checks: list[dict[str, Any]] = []
    all_paths = sorted(set(tracked + untracked))
    for path in all_paths:
        result, found = validate_file(root, path, max_bytes)
        file_checks.append(result)
        errors.extend(found)
    errors.extend(embedded_content_errors(root, all_paths, max_bytes))
    value = {
        "schema_version": 1,
        "status": "failed" if errors else "passed",
        "worktree": str(root),
        "tracked_paths": tracked,
        "untracked_paths": untracked,
        "untracked_diff_check_complete": True,
        "file_checks": file_checks,
        "errors": errors,
    }
    if output:
        atomic_json(output.resolve(), value)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-file-bytes", type=int, default=MAX_FILE_BYTES)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.max_file_bytes <= 0:
        parser.error("--max-file-bytes must be positive")
    try:
        value = validate(args.worktree.resolve(), args.output, args.max_file_bytes)
    except (OSError, ValidationError, subprocess.SubprocessError) as exc:
        print(f"worktree validation: {exc}", file=os.sys.stderr)
        return 2
    if args.json or not args.output:
        print(json.dumps(value, sort_keys=True))
    return 0 if value["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
