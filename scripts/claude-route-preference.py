#!/usr/bin/env python3
"""
claude-route-preference.py - Persist and retrieve the learned Claude dispatch route.

Commands:
    resolve [--fallback ROUTE]  Print the preferred route (or fallback if unavailable).
    record  --route ROUTE [--source LABEL]  Atomically persist a successful route.
    show                        Display the current record for diagnostics.

State file precedence:
    1. AIWF_CLAUDE_ROUTE_STATE env var (explicit test/operator override)
    2. Windows LOCALAPPDATA/ai-coding-workflow/claude-route.json
    3. Unix XDG_STATE_HOME/ai-coding-workflow/claude-route.json
    4. ~/.local/state/ai-coding-workflow/claude-route.json

The record contains only: schema_version, route, recorded_at (UTC ISO-8601),
and source label.  No URLs, proxy values, tokens, env dumps, prompts, or
repository paths are ever stored.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

SCHEMA_VERSION = 1
VALID_ROUTES = {"direct", "inherit"}
VALID_SOURCE_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
DEFAULT_ROUTE = "direct"
STATE_DIR_NAME = "ai-coding-workflow"
STATE_FILE_NAME = "claude-route.json"


def _state_path() -> Path:
    """Resolve the state file path using the precedence rules."""
    explicit = os.environ.get("AIWF_CLAUDE_ROUTE_STATE")
    if explicit:
        return Path(explicit)

    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / STATE_DIR_NAME / STATE_FILE_NAME

    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg) / STATE_DIR_NAME / STATE_FILE_NAME

    return Path.home() / ".local" / "state" / STATE_DIR_NAME / STATE_FILE_NAME


def _read_record(path: Path) -> Optional[Dict]:
    """Read and validate the state file. Returns None on any failure."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    route = data.get("route")
    if route not in VALID_ROUTES:
        return None
    return data


def _atomic_write(path: Path, content: str) -> None:
    """Write content atomically via temp + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), suffix=".tmp", prefix=".claude-route-"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        # Atomic replace (same filesystem guaranteed by using same directory)
        os.replace(tmp, str(path))
    except BaseException:
        # Clean up temp file on failure
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def cmd_resolve(args: argparse.Namespace) -> int:
    """Print the preferred route, or the fallback if unavailable."""
    try:
        path = _state_path()
        record = _read_record(path)
    except (OSError, RuntimeError):
        print(args.fallback)
        return 0
    if record is not None:
        print(record["route"])
    else:
        print(args.fallback)
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    """Atomically record a successful route."""
    route = args.route
    if route not in VALID_ROUTES:
        print(f"Error: route must be one of {sorted(VALID_ROUTES)}", file=sys.stderr)
        return 1

    source = args.source if args.source is not None else "dispatch"
    if not VALID_SOURCE_RE.match(source):
        print(f"Error: source must match {VALID_SOURCE_RE.pattern}", file=sys.stderr)
        return 1

    try:
        path = _state_path()
    except (OSError, RuntimeError) as exc:
        print(f"Warning: could not resolve state path: {exc}", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc).replace(microsecond=0)
    record = {
        "schema_version": SCHEMA_VERSION,
        "route": route,
        "recorded_at": now.isoformat().replace("+00:00", "Z"),
        "source": source,
    }
    content = json.dumps(record, indent=2, sort_keys=True) + "\n"
    try:
        _atomic_write(path, content)
    except OSError as exc:
        # Persistence failure is advisory; warn but do not change dispatch outcome.
        print(f"Warning: could not write route preference: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """Display the current record for diagnostics."""
    try:
        path = _state_path()
        record = _read_record(path)
    except (OSError, RuntimeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1
    if record is None:
        print(json.dumps({"state_file": str(path), "status": "no_valid_record"}))
    else:
        record["state_file"] = str(path)
        record["status"] = "ok"
        print(json.dumps(record, indent=2, sort_keys=True))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Persist and retrieve the learned Claude dispatch route."
    )
    sub = parser.add_subparsers(dest="command")

    p_resolve = sub.add_parser("resolve", help="Print the preferred route or fallback.")
    p_resolve.add_argument(
        "--fallback",
        default=DEFAULT_ROUTE,
        help=f"Fallback route when no valid record exists (default: {DEFAULT_ROUTE}).",
    )

    p_record = sub.add_parser("record", help="Atomically persist a successful route.")
    p_record.add_argument(
        "--route",
        required=True,
        choices=sorted(VALID_ROUTES),
        help="The route that succeeded.",
    )
    p_record.add_argument(
        "--source",
        default="dispatch",
        help="Label recording why this route was persisted (default: dispatch).",
    )

    sub.add_parser("show", help="Display the current record for diagnostics.")

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 1

    handlers = {"resolve": cmd_resolve, "record": cmd_record, "show": cmd_show}
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
