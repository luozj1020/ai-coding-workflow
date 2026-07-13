#!/usr/bin/env python3
"""Run the repository's quick, integration, or full unittest tier."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_manifest(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("labels"), dict) or not isinstance(data.get("tiers"), dict):
        raise ValueError("manifest must contain object-valued labels and tiers")
    return data


def discover_tests(tests_dir: Path) -> list[str]:
    return sorted(path.name for path in tests_dir.glob("test_*.py"))


def select_tests(tier: str, manifest: dict, discovered: list[str]) -> list[str]:
    if tier not in manifest["tiers"]:
        raise ValueError(f"unknown tier: {tier}")
    known = set(discovered)
    labels = manifest["labels"]
    for label, files in labels.items():
        missing = set(files) - known
        if missing:
            raise ValueError(f"label {label} references missing tests: {', '.join(sorted(missing))}")
    rule = manifest["tiers"][tier]
    selected = set(known)
    if "require_labels" in rule:
        required = rule["require_labels"]
        if not required:
            selected = set()
        else:
            selected = set(labels.get(required[0], []))
            for label in required[1:]:
                selected &= set(labels.get(label, []))
    for label in rule.get("exclude_labels", []):
        if label not in labels:
            raise ValueError(f"tier {tier} references unknown label: {label}")
        selected -= set(labels[label])
    return sorted(selected)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tier", nargs="?", default="quick", choices=("quick", "integration", "full"))
    parser.add_argument("--list", action="store_true", dest="list_only")
    parser.add_argument("--manifest", type=Path, default=ROOT / "tests" / "test-tiers.json")
    parser.add_argument("--tests-dir", type=Path, default=ROOT / "tests")
    args = parser.parse_args(argv)
    try:
        files = select_tests(args.tier, load_manifest(args.manifest), discover_tests(args.tests_dir))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    if args.list_only:
        print("\n".join(files))
        return 0
    modules = [f"tests.{Path(name).stem}" for name in files]
    print(f"Running {args.tier} tier ({len(modules)} files)", flush=True)
    return subprocess.run([sys.executable, "-m", "unittest", *modules], cwd=ROOT).returncode


if __name__ == "__main__":
    raise SystemExit(main())
