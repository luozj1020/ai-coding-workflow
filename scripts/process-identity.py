#!/usr/bin/env python3
"""Capture and verify a Linux process identity without trusting PID alone."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def _process(pid: int) -> Optional[Dict[str, Any]]:
    root = Path("/proc") / str(pid)
    try:
        stat = (root / "stat").read_text(encoding="utf-8", errors="replace")
        tail = stat[stat.rfind(")") + 2 :].split()
        cmdline = (root / "cmdline").read_bytes()
        namespace_inode = (root / "ns" / "pid").stat().st_ino
        return {
            "pid": pid,
            "start_time_ticks": int(tail[19]),
            "pid_namespace_inode": namespace_inode,
            "cmdline_sha256": "sha256:" + hashlib.sha256(cmdline).hexdigest(),
        }
    except (OSError, ValueError, IndexError):
        return None


def capture(pid: int, task_id: str, role: str) -> Dict[str, Any]:
    current = _process(pid)
    if current is None:
        raise ValueError("process identity is unavailable")
    current.update({
        "schema_version": 1,
        "task_id": task_id,
        "role": role,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    })
    return current


def check(
    identity: Dict[str, Any], expected_task_id: Optional[str] = None,
    expected_role: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    metadata_mismatches = []
    if expected_task_id is not None and identity.get("task_id") != expected_task_id:
        metadata_mismatches.append("task_id")
    if expected_role is not None and identity.get("role") != expected_role:
        metadata_mismatches.append("role")
    if metadata_mismatches:
        return "invalid-identity", {"mismatched_fields": metadata_mismatches}
    try:
        pid = int(identity["pid"])
    except (KeyError, TypeError, ValueError):
        return "invalid-identity", {}
    current = _process(pid)
    if current is None:
        try:
            os.kill(pid, 0)
        except (OSError, ValueError):
            return "not-running", {}
        return "visibility-unknown", {}
    fields = ("start_time_ticks", "pid_namespace_inode", "cmdline_sha256")
    mismatches = [field for field in fields if identity.get(field) != current.get(field)]
    if mismatches:
        return "pid-reused-or-foreign", {"mismatched_fields": mismatches, "current": current}
    return "running-same-process", {"current": current}


def _write(path: Path, value: Dict[str, Any]) -> None:
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    capture_parser = sub.add_parser("capture")
    capture_parser.add_argument("--pid", type=int, required=True)
    capture_parser.add_argument("--task-id", required=True)
    capture_parser.add_argument("--role", choices=("dispatcher", "claude", "checker"), required=True)
    capture_parser.add_argument("--output", type=Path, required=True)
    check_parser = sub.add_parser("check")
    check_parser.add_argument("--identity", type=Path, required=True)
    check_parser.add_argument("--task-id")
    check_parser.add_argument("--role", choices=("dispatcher", "claude", "checker"))
    args = parser.parse_args()
    try:
        if args.command == "capture":
            value = capture(args.pid, args.task_id, args.role)
            _write(args.output, value)
            print(json.dumps(value, sort_keys=True))
            return 0
        identity = json.loads(args.identity.read_text(encoding="utf-8"))
        status, detail = check(identity, args.task_id, args.role)
        print(json.dumps({"status": status, **detail}, sort_keys=True))
        if status == "running-same-process":
            return 0
        if status in {"not-running", "pid-reused-or-foreign"}:
            return 1
        return 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("Error: {}".format(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
