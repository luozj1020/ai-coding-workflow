#!/usr/bin/env python3
"""Emit the explicit no-delegation decision for a bounded Codex change."""
from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reason", required=True, help="Why direct Codex ownership is appropriate")
    parser.add_argument(
        "--kind",
        choices=("small-local-change", "workflow-maintenance", "deterministic-correction"),
        default="small-local-change",
        help="Direct path classification (default: small-local-change)",
    )
    parser.add_argument("--path", action="append", default=[], help="Exact repository-relative target")
    parser.add_argument("--check", action="append", default=[], help="Exact deterministic validation")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not args.reason.strip():
        parser.error("--reason must not be empty")
    if not args.path or any(
        not value
        or value in {".", ".."}
        or value.startswith(("/", "./"))
        or value.endswith("/")
        or "\\" in value
        or ".." in value.split("/")
        for value in args.path
    ):
        parser.error("at least one safe repository-relative --path is required")
    if len(set(args.path)) != len(args.path):
        parser.error("--path values must be unique")
    value = {
        "schema_version": 1,
        "workflow_bypassed": args.kind,
        "owner": "codex",
        "task_card_required": False,
        "spark_required": False,
        "claude_required": False,
        "reason": args.reason,
        "write_paths": args.path,
        "required_checks": args.check,
        "merge_authorized": False,
    }
    if args.json:
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    else:
        for key, item in value.items():
            print(f"{key}={json.dumps(item, ensure_ascii=False) if isinstance(item, (list, bool)) else item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
