#!/usr/bin/env python3
"""Persist a bounded, context-bound Claude API availability observation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
VALID_ROUTES = {"direct", "inherit"}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def context_hash(
    repository: Path, route: str, environment: str, claude_command: str,
    tool_profile: str = "",
) -> str:
    # Connectivity is independent of the requested tool profile.  Keeping the
    # profile in this identity caused a new network round-trip whenever a card
    # changed from locator-builder to minimal-builder.  Tool inventory remains
    # separately bound below and is never reused across profiles.
    payload = {
        "claude_command": str(Path(claude_command).resolve()) if claude_command else "unavailable",
        "environment": environment,
        "repository": str(repository.resolve()),
        "route": route,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def read(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def common(args: argparse.Namespace) -> tuple[str, str]:
    if args.route not in VALID_ROUTES:
        raise ValueError("route must be direct or inherit")
    return args.route, context_hash(
        args.repository, args.route, args.environment, args.claude_command,
        getattr(args, "tool_profile", ""),
    )


def check(args: argparse.Namespace) -> int:
    route, expected_context = common(args)
    record = read(args.state)
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "missing",
        "interaction_conclusion": "unknown",
        "route": route,
        "probe_environment": args.environment,
        "live_probe": False,
        "cache_valid": False,
        "ttl_seconds": args.ttl,
    }
    if record is None:
        print(json.dumps(result, sort_keys=True))
        return 1
    result["recorded_at"] = record.get("recorded_at")
    if record.get("status") != "available":
        result["status"] = "invalidated"
    elif record.get("route") != route or record.get("context_hash") != expected_context:
        result["status"] = "context-mismatch"
    else:
        try:
            recorded = datetime.fromisoformat(str(record["recorded_at"]).replace("Z", "+00:00"))
            age = max(0, int((now_utc() - recorded).total_seconds()))
        except (KeyError, TypeError, ValueError):
            result["status"] = "invalid-timestamp"
        else:
            result["age_seconds"] = age
            if age <= args.ttl:
                requested_profile = args.tool_profile or "default"
                recorded_profile = record.get("tool_profile", "default")
                inventory_matches = recorded_profile == requested_profile
                result.update(
                    status="available-cached",
                    interaction_conclusion="available",
                    cache_valid=True,
                    source=record.get("source", "unknown"),
                    tool_profile_recorded=recorded_profile,
                    tool_profile_matches=inventory_matches,
                    # The observed init inventory describes the executable and
                    # route, not merely the card that requested it.  Consumers
                    # may reuse it when it satisfies a new profile; a missing
                    # capability still triggers their normal fail-closed path.
                    tool_inventory=record.get("tool_inventory", []),
                    tool_inventory_verified=bool(record.get("tool_inventory_verified")),
                )
                print(json.dumps(result, sort_keys=True))
                return 0
            result["status"] = "expired"
    print(json.dumps(result, sort_keys=True))
    return 1


def record(args: argparse.Namespace) -> int:
    route, identity = common(args)
    value = {
        "schema_version": SCHEMA_VERSION,
        "status": "available",
        "route": route,
        "probe_environment": args.environment,
        "context_hash": identity,
        "tool_profile": args.tool_profile or "default",
        "recorded_at": timestamp(now_utc()),
        "source": args.source,
        "tool_inventory": sorted(set(args.tool_inventory or [])),
        "tool_inventory_verified": bool(args.tool_inventory_verified),
    }
    atomic_write(args.state, value)
    print(json.dumps({**value, "state_file": str(args.state)}, sort_keys=True))
    return 0


def invalidate(args: argparse.Namespace) -> int:
    previous = read(args.state) or {}
    value = {
        "schema_version": SCHEMA_VERSION,
        "status": "suspected-unavailable",
        "route": previous.get("route"),
        "probe_environment": previous.get("probe_environment"),
        "context_hash": previous.get("context_hash"),
        "recorded_at": previous.get("recorded_at"),
        "invalidated_at": timestamp(now_utc()),
        "reason": args.reason,
    }
    atomic_write(args.state, value)
    print(json.dumps({**value, "state_file": str(args.state)}, sort_keys=True))
    return 0


def add_context(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--route", choices=sorted(VALID_ROUTES), required=True)
    parser.add_argument("--environment", default="auto")
    parser.add_argument("--claude-command", default="")
    parser.add_argument("--tool-profile", default="")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check_parser = commands.add_parser("check")
    add_context(check_parser)
    check_parser.add_argument("--ttl", type=int, required=True)
    record_parser = commands.add_parser("record")
    add_context(record_parser)
    record_parser.add_argument("--source", required=True)
    record_parser.add_argument("--tool-inventory", action="append", default=[])
    record_parser.add_argument("--tool-inventory-verified", action="store_true")
    invalidate_parser = commands.add_parser("invalidate")
    invalidate_parser.add_argument("--state", type=Path, required=True)
    invalidate_parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    if getattr(args, "ttl", 1) <= 0:
        parser.error("--ttl must be positive")
    try:
        return {"check": check, "record": record, "invalidate": invalidate}[args.command](args)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
