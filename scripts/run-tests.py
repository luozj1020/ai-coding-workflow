#!/usr/bin/env python3
"""Run the repository's quick, integration, or full unittest tier."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import unittest
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


def parse_shard(value: str) -> tuple[int, int]:
    """Parse a human-facing one-based shard such as ``2/4``."""
    try:
        index_text, count_text = value.split("/", 1)
        index = int(index_text)
        count = int(count_text)
    except (AttributeError, TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("shard must use INDEX/COUNT, for example 1/4") from exc
    if count < 1 or index < 1 or index > count:
        raise argparse.ArgumentTypeError("shard INDEX must be between 1 and COUNT")
    return index, count


def flatten_test_ids(suite: unittest.TestSuite) -> list[str]:
    """Return stable unittest IDs from an arbitrarily nested suite."""
    ids: list[str] = []
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            ids.extend(flatten_test_ids(test))
        else:
            ids.append(test.id())
    return ids


def discover_test_ids(modules: list[str]) -> list[str]:
    root_text = str(ROOT)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromNames(modules)
    if loader.errors:
        raise ValueError("failed to load selected tests: " + "; ".join(loader.errors))
    return sorted(flatten_test_ids(suite))


def select_shard(items: list[str], shard: tuple[int, int]) -> list[str]:
    """Round-robin sorted test IDs so large modules cannot dominate one shard."""
    index, count = shard
    return [item for position, item in enumerate(sorted(items)) if position % count == index - 1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tier", nargs="?", default="quick", choices=("quick", "integration", "full"))
    parser.add_argument("--list", action="store_true", dest="list_only")
    parser.add_argument(
        "--shard",
        type=parse_shard,
        metavar="INDEX/COUNT",
        help="run a deterministic one-based shard of individual unittest cases",
    )
    parser.add_argument("--manifest", type=Path, default=ROOT / "tests" / "test-tiers.json")
    parser.add_argument("--tests-dir", type=Path, default=ROOT / "tests")
    args = parser.parse_args(argv)
    try:
        files = select_tests(args.tier, load_manifest(args.manifest), discover_tests(args.tests_dir))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    modules = [f"tests.{Path(name).stem}" for name in files]
    targets = modules
    description = f"{len(modules)} files"
    if args.shard:
        try:
            all_ids = discover_test_ids(modules)
        except ValueError as exc:
            parser.error(str(exc))
        targets = select_shard(all_ids, args.shard)
        index, count = args.shard
        description = f"shard {index}/{count}, {len(targets)}/{len(all_ids)} cases"
    if args.list_only:
        print("\n".join(targets if args.shard else files))
        return 0
    print(f"Running {args.tier} tier ({description})", flush=True)
    return subprocess.run([sys.executable, "-m", "unittest", *targets], cwd=ROOT).returncode


if __name__ == "__main__":
    raise SystemExit(main())
