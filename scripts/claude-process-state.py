#!/usr/bin/env python3
"""Classify a Claude PID without confusing sandbox invisibility with exit."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

TERMINAL_MARKERS = ("Final dispatch outcome:", "Dispatch Complete")


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def restricted_environment(mode: str) -> bool:
    if mode == "restricted":
        return True
    if mode == "normal":
        return False
    return os.environ.get("CODEX_SANDBOX_NETWORK_DISABLED", "").lower() in {"1", "true", "yes"}


def classify(pid_file: Path, progress_file: Path, mode: str = "auto") -> str:
    if not pid_file.is_file():
        return "missing"
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return "missing"
    if pid_alive(pid):
        return "running"
    if not restricted_environment(mode):
        return "not-running"
    try:
        progress = progress_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        progress = ""
    if "Claude process started:" in progress and not any(marker in progress for marker in TERMINAL_MARKERS):
        return "visibility-unknown"
    return "not-running"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid-file", type=Path, required=True)
    parser.add_argument("--progress-file", type=Path, required=True)
    parser.add_argument("--visibility-mode", choices=("auto", "normal", "restricted"),
                        default=os.environ.get("CLAUDE_CODE_PROCESS_VISIBILITY", "auto"))
    args = parser.parse_args()
    print(classify(args.pid_file, args.progress_file, args.visibility_mode))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
