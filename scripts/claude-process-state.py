#!/usr/bin/env python3
"""Classify a Claude PID without confusing sandbox invisibility with exit."""
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
from typing import Optional

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


def _identity_state(identity_file: Optional[Path]) -> Optional[str]:
    if identity_file is None or not identity_file.is_file():
        return None
    helper = Path(__file__).resolve().with_name("process-identity.py")
    if not helper.is_file():
        return "visibility-unknown"
    spec = importlib.util.spec_from_file_location("process_identity", helper)
    if spec is None or spec.loader is None:
        return "visibility-unknown"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        import json
        identity = json.loads(identity_file.read_text(encoding="utf-8"))
        status, _ = module.check(identity)
    except (OSError, ValueError, TypeError):
        return "visibility-unknown"
    return {
        "running-same-process": "running",
        "not-running": "not-running",
        "pid-reused-or-foreign": "not-running",
    }.get(status, "visibility-unknown")


def classify(
    pid_file: Path, progress_file: Path, mode: str = "auto",
    identity_file: Optional[Path] = None,
) -> str:
    if not pid_file.is_file():
        return "missing"
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return "missing"
    identity = _identity_state(identity_file)
    if identity is not None:
        return identity
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
    parser.add_argument("--identity-file", type=Path)
    parser.add_argument("--visibility-mode", choices=("auto", "normal", "restricted"),
                        default=os.environ.get("CLAUDE_CODE_PROCESS_VISIBILITY", "auto"))
    args = parser.parse_args()
    print(classify(args.pid_file, args.progress_file, args.visibility_mode, args.identity_file))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
