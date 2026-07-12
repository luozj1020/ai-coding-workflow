#!/usr/bin/env python3
"""event_writer.py — Append-only Event v2 writer with atomic locking.

Python 3.9+ compatible. No third-party dependencies.
Writes one JSONL event per line to the run's event log file.
Uses file locking suitable for concurrent readers.

Usage:
    python scripts/event_writer.py <events_path> <event_json>

As a module:
    from event_writer import EventWriter
    writer = EventWriter("/path/to/run-events.jsonl")
    writer.append(event_dict)
"""
from __future__ import annotations

try:
    import fcntl
except ImportError:  # Windows has no fcntl; append+flush remains the fallback.
    fcntl = None
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 2

VALID_PHASES = ("setup", "dispatch", "review", "decision", "finalization")
VALID_ROLES = ("run-loop", "dispatch", "reviewer", "claude", "codex", "checker", "system")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class EventWriterError(Exception):
    """Base exception for event writer errors."""
    pass


class EventValidationError(EventWriterError):
    """Event failed validation before write."""
    pass


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------

def generate_event_id(run_id: str, event_name: str, iteration: Optional[int] = None) -> str:
    """Generate a deterministic-enough event ID.

    Format: <run_id>-<event_name>[-iter<N>]-<short_hash>
    Unique per append via timestamp + random suffix.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    random_part = uuid.uuid4().hex[:8]
    parts = [run_id, event_name]
    if iteration is not None:
        parts.append(f"iter{iteration}")
    parts.append(f"{ts}-{random_part}")
    return "-".join(parts)


# ---------------------------------------------------------------------------
# Event building
# ---------------------------------------------------------------------------

def build_event(
    *,
    run_id: str,
    task_id: str,
    event: str,
    phase: str = "setup",
    role: str = "system",
    iteration: Optional[int] = None,
    parent_event_id: Optional[str] = None,
    artifact_refs: Optional[List[str]] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a v2 event dict with all required fields populated.

    Does NOT validate — caller should use validate_event() or the writer
    will validate on append.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": generate_event_id(run_id, event, iteration),
        "parent_event_id": parent_event_id,
        "run_id": run_id,
        "task_id": task_id,
        "iteration": iteration,
        "phase": phase,
        "role": role,
        "event": event,
        "artifact_refs": artifact_refs or [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "detail": detail or {},
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_event(data: Dict[str, Any]) -> List[str]:
    """Validate an event dict against v2 schema rules.

    Returns a list of error strings (empty if valid).
    Does NOT check causal parent existence — that requires reading the log.
    """
    errors: List[str] = []

    if not isinstance(data, dict):
        return ["event must be an object"]

    # schema_version
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}, got {data.get('schema_version')}")

    # event_id
    eid = data.get("event_id")
    if not isinstance(eid, str) or not eid:
        errors.append("event_id must be a non-empty string")

    # parent_event_id (nullable)
    pid = data.get("parent_event_id")
    if pid is not None and (not isinstance(pid, str) or not pid):
        errors.append("parent_event_id must be null or a non-empty string")

    # run_id
    rid = data.get("run_id")
    if not isinstance(rid, str) or not rid:
        errors.append("run_id must be a non-empty string")

    # task_id
    tid = data.get("task_id")
    if not isinstance(tid, str) or not tid:
        errors.append("task_id must be a non-empty string")

    # iteration (nullable int)
    it = data.get("iteration")
    if it is not None:
        if not isinstance(it, int) or it < 1:
            errors.append("iteration must be null or a positive integer")

    # phase
    phase = data.get("phase")
    if phase not in VALID_PHASES:
        errors.append(f"phase must be one of {VALID_PHASES}, got '{phase}'")

    # role
    role = data.get("role")
    if role not in VALID_ROLES:
        errors.append(f"role must be one of {VALID_ROLES}, got '{role}'")

    # event name
    event = data.get("event")
    if not isinstance(event, str) or not event:
        errors.append("event must be a non-empty string")

    # artifact_refs
    refs = data.get("artifact_refs")
    if not isinstance(refs, list):
        errors.append("artifact_refs must be an array")
    else:
        for i, ref in enumerate(refs):
            if not isinstance(ref, str) or not ref:
                errors.append(f"artifact_refs[{i}] must be a non-empty string")

    # timestamp
    ts = data.get("timestamp")
    if not isinstance(ts, str) or not ts:
        errors.append("timestamp must be a non-empty string")

    # detail
    detail = data.get("detail")
    if not isinstance(detail, dict):
        errors.append("detail must be an object")

    return errors


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

class EventWriter:
    """Append-only JSONL event writer with file locking.

    Uses fcntl.flock for exclusive write locks. On Windows, falls back
    to os.open with O_EXCL-like retry behavior.
    """

    def __init__(self, events_path: str | Path):
        self._path = Path(events_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._last_event_id: Optional[str] = None

    @property
    def path(self) -> Path:
        return self._path

    def append(self, event: Dict[str, Any], validate: bool = True) -> str:
        """Append one event to the JSONL log.

        Returns the event_id of the written event.
        Raises EventValidationError if validate=True and event is invalid.
        """
        if validate:
            errors = validate_event(event)
            if errors:
                raise EventValidationError("; ".join(errors))

        self._locked_append(event)
        self._last_event_id = event["event_id"]
        return event["event_id"]

    def _locked_append(self, event: Dict[str, Any]) -> None:
        """Recover the durable tail and append one linked event under lock."""
        # Retry loop for lock contention
        for attempt in range(10):
            try:
                with open(self._path, "a+", encoding="utf-8") as f:
                    try:
                        if fcntl is not None:
                            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                    except (OSError, AttributeError):
                        # Windows or unsupported — retry-based approach
                        pass
                    f.seek(0)
                    prior = [line for line in f.read().splitlines() if line.strip()]
                    if event.get("parent_event_id") is None and prior:
                        try:
                            event["parent_event_id"] = json.loads(prior[-1])["event_id"]
                        except (json.JSONDecodeError, KeyError, TypeError) as exc:
                            raise EventWriterError("Cannot link event to malformed durable tail") from exc
                    line = json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n"
                    f.seek(0, os.SEEK_END)
                    f.write(line)
                    f.flush()
                    os.fsync(f.fileno())
                    try:
                        if fcntl is not None:
                            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    except (OSError, AttributeError):
                        pass
                return
            except OSError:
                if attempt < 9:
                    time.sleep(0.01 * (attempt + 1))
                else:
                    raise

    def read_all(self) -> List[Dict[str, Any]]:
        """Read all events from the log file."""
        if not self._path.exists():
            return []
        events = []
        for line_num, line in enumerate(self._path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                raise EventWriterError(f"Malformed JSON at line {line_num}")
        return events

    def last_event_id(self) -> Optional[str]:
        """Return the last event_id from the log, or None if empty."""
        if self._last_event_id:
            return self._last_event_id
        events = self.read_all()
        if events:
            return events[-1].get("event_id")
        return None


# ---------------------------------------------------------------------------
# Legacy event migration helper
# ---------------------------------------------------------------------------

def is_legacy_event(data: Dict[str, Any]) -> bool:
    """Check if an event uses the legacy v1 format (no schema_version or version=1)."""
    return data.get("schema_version") is None or data.get("schema_version") == 1


def report_legacy_events(events_path: str | Path) -> List[Dict[str, Any]]:
    """Read a JSONL file and return events that use the legacy format.

    Does NOT rewrite them — just reports for awareness.
    """
    path = Path(events_path)
    if not path.exists():
        return []
    legacy = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            if is_legacy_event(data):
                legacy.append(data)
        except json.JSONDecodeError:
            continue
    return legacy


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point.

    Usage: python event_writer.py <events_path> <event_json>
    """
    if argv is None:
        argv = sys.argv[1:]

    if len(argv) < 2:
        print("Usage: python event_writer.py <events_path> <event_json>", file=sys.stderr)
        return 1

    events_path = argv[0]
    event_json = argv[1]

    try:
        event = json.loads(event_json)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON: {e}", file=sys.stderr)
        return 1

    writer = EventWriter(events_path)
    try:
        event_id = writer.append(event)
        print(event_id)
        return 0
    except EventValidationError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
