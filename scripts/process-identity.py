#!/usr/bin/env python3
"""Capture and verify a process identity without trusting PID alone."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def _process_linux(pid: int) -> Optional[Dict[str, Any]]:
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


def _process_windows(pid: int) -> Optional[Dict[str, Any]]:
    """Use stable native process metadata without optional dependencies."""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        )
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.QueryFullProcessImageNameW.argtypes = (
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        )
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.ProcessIdToSessionId.argtypes = (wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
        kernel32.ProcessIdToSessionId.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        process = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not process:
            return None
        try:
            creation = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            if not kernel32.GetProcessTimes(
                process,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return None
            size = wintypes.DWORD(32768)
            executable = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(
                process, 0, executable, ctypes.byref(size)
            ):
                return None
            session = wintypes.DWORD()
            if not kernel32.ProcessIdToSessionId(pid, ctypes.byref(session)):
                return None
            start = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
            command_identity = executable.value.casefold().encode("utf-8", errors="replace")
            return {
                "pid": pid,
                "start_time_ticks": start,
                "pid_namespace_inode": int(session.value),
                "cmdline_sha256": "sha256:" + hashlib.sha256(command_identity).hexdigest(),
            }
        finally:
            kernel32.CloseHandle(process)
    except (AttributeError, OSError, ValueError):
        return None


def _process(pid: int) -> Optional[Dict[str, Any]]:
    if sys.platform == "win32":
        return _process_windows(pid)
    return _process_linux(pid)


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
