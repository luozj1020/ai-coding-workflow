#!/usr/bin/env python3
"""Build a bounded, read-only Bazel context packet for selected source files."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


RULE_RE = re.compile(r"(?m)^\s*([A-Za-z_][\w.]*)\s*\(")
ATTR_RE = re.compile(r"(?ms)\b(name|srcs|hdrs|deps)\s*=\s*(\[[^]]*\]|\"[^\"]*\"|'[^']*')")
STRING_RE = re.compile(r"['\"]([^'\"]+)['\"]")


def _rule_blocks(text: str) -> list[tuple[str, str]]:
    """Return top-level rule calls using a small string-aware parenthesis scan."""
    blocks: list[tuple[str, str]] = []
    for match in RULE_RE.finditer(text):
        depth = 0
        quote: str | None = None
        escaped = False
        for index in range(match.end() - 1, len(text)):
            char = text[index]
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
                continue
            if char in "'\"":
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    blocks.append((match.group(1), text[match.start() : index + 1]))
                    break
    return blocks


def _attributes(block: str) -> dict[str, list[str]]:
    attrs: dict[str, list[str]] = {}
    for match in ATTR_RE.finditer(block):
        attrs[match.group(1)] = STRING_RE.findall(match.group(2))
    return attrs


def _nearest_build(root: Path, source: Path) -> Path | None:
    directory = source.parent
    while directory == root or root in directory.parents:
        for name in ("BUILD.bazel", "BUILD"):
            candidate = directory / name
            if candidate.is_file():
                return candidate
        if directory == root:
            break
        directory = directory.parent
    return None


def build_context(root: Path, file_names: list[str]) -> dict:
    files = []
    targets: dict[str, dict] = {}
    build_files: set[str] = set()
    for supplied in file_names:
        source = (root / supplied).resolve()
        try:
            relative = source.relative_to(root)
        except ValueError:
            files.append({"file": supplied, "status": "outside-repository"})
            continue
        build = _nearest_build(root, source)
        if not build:
            files.append({"file": relative.as_posix(), "status": "no-build-file"})
            continue
        build_relative = build.relative_to(root).as_posix()
        build_files.add(build_relative)
        package = build.parent.relative_to(root).as_posix()
        package = "" if package == "." else package
        source_from_package = source.relative_to(build.parent).as_posix()
        owners = []
        for kind, block in _rule_blocks(build.read_text(encoding="utf-8", errors="replace")):
            attrs = _attributes(block)
            names = attrs.get("name", [])
            if not names:
                continue
            sources = attrs.get("srcs", []) + attrs.get("hdrs", [])
            if source_from_package not in sources and relative.name not in sources:
                continue
            label = f"//{package}:{names[0]}" if package else f"//:{names[0]}"
            targets[label] = {
                "label": label,
                "kind": kind,
                "build_file": build_relative,
                "srcs": attrs.get("srcs", []),
                "hdrs": attrs.get("hdrs", []),
                "deps": attrs.get("deps", []),
            }
            owners.append(label)
        files.append({
            "file": relative.as_posix(),
            "status": "matched" if owners else "build-file-only",
            "build_file": build_relative,
            "candidate_targets": owners,
        })
    target_list = sorted(targets.values(), key=lambda item: item["label"])
    test_targets = [item["label"] for item in target_list if "test" in item["kind"].lower() or item["label"].lower().endswith(("_test", "_tests"))]
    return {
        "schema_version": 1,
        "repository": str(root),
        "files": files,
        "build_files": sorted(build_files),
        "candidate_targets": target_list,
        "candidate_test_targets": test_targets,
        "validation_commands": [f"bazel test {label} --test_output=errors" for label in test_targets],
        "limitations": [
            "Static bounded parsing only; macros, select(), glob(), aliases, and generated sources may require manual review.",
            "No bazel query/build/test command was executed.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--file", action="append", required=True, dest="files")
    parser.add_argument("--output")
    args = parser.parse_args()
    root = Path(args.repo).resolve()
    result = build_context(root, args.files)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
