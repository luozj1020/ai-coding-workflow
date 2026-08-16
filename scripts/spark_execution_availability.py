#!/usr/bin/env python3
"""Persist the execution environment required for reliable Spark calls."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


SCHEMA_VERSION = 1
DEFAULT_TTL_SECONDS = 86400
DEFAULT_CIRCUIT_TTL_SECONDS = 300
DEFAULT_AUDIT_TIMEOUT_SECONDS = 30
HOST_STATES = {
    "host-required",
    "host-available",
    "host-suspected-unavailable",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def state_path(repository: Path) -> Path:
    override = os.environ.get("CODEX_SPARK_EXECUTION_STATE_FILE", "").strip()
    if override:
        return Path(override)
    return repository.resolve() / ".ai-workflow" / "spark-execution-availability.json"


def circuit_path(repository: Path) -> Path:
    override = os.environ.get("CODEX_SPARK_CIRCUIT_STATE_FILE", "").strip()
    if override:
        return Path(override)
    return repository.resolve() / ".ai-workflow" / "spark-circuit-breaker.json"


def _context_hash(repository: Path) -> str:
    payload = {
        "codex_binary": os.environ.get("CODEX_SPARK_CODEX_BIN", "codex"),
        "model": os.environ.get("CODEX_SPARK_MODEL", "gpt-5.3-codex-spark"),
        "repository": str(repository.resolve()),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _read(path: Path) -> Optional[Dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _atomic_write(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def ttl_seconds() -> int:
    raw = os.environ.get(
        "CODEX_SPARK_EXECUTION_STATE_TTL_SECONDS", str(DEFAULT_TTL_SECONDS)
    )
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_TTL_SECONDS
    return value if value > 0 else DEFAULT_TTL_SECONDS


def _positive_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def circuit_ttl_seconds() -> int:
    return _positive_env(
        "CODEX_SPARK_CIRCUIT_TTL_SECONDS", DEFAULT_CIRCUIT_TTL_SECONDS
    )


def audit_timeout_seconds(requested: int, mode: str) -> int:
    """Cap advisory task-card audits without changing other Spark modes."""
    if mode != "task-card-audit":
        return requested
    return min(
        requested,
        _positive_env(
            "CODEX_SPARK_AUDIT_TIMEOUT_SECONDS", DEFAULT_AUDIT_TIMEOUT_SECONDS
        ),
    )


def circuit(
    repository: Path, mode: str, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Return whether a recent equivalent Spark failure should be skipped."""
    path = circuit_path(repository)
    record = _read(path)
    result: Dict[str, Any] = {
        "open": False,
        "status": "missing",
        "state_file": str(path),
        "mode": mode,
    }
    if record is None:
        return result
    result["status"] = record.get("status", "invalid")
    result["recorded_at"] = record.get("recorded_at")
    if (
        record.get("schema_version") != SCHEMA_VERSION
        or record.get("context_hash") != _context_hash(repository)
        or record.get("mode") != mode
        or record.get("status") not in {"open", "closed"}
    ):
        result["status"] = "context-mismatch"
        return result
    try:
        recorded = datetime.fromisoformat(
            str(record["recorded_at"]).replace("Z", "+00:00")
        )
    except (KeyError, TypeError, ValueError):
        result["status"] = "invalid-timestamp"
        return result
    age = max(0, int(((now or _now()) - recorded).total_seconds()))
    result["age_seconds"] = age
    if age > circuit_ttl_seconds():
        result["status"] = "expired"
        return result
    result["open"] = record.get("status") == "open"
    result["source"] = record.get("source", "unknown")
    result["detail"] = record.get("detail")
    return result


def _record_circuit(
    repository: Path,
    mode: str,
    status: str,
    source: str,
    detail: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    path = circuit_path(repository)
    value: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "mode": mode,
        "context_hash": _context_hash(repository),
        "recorded_at": _timestamp(_now()),
        "source": source,
    }
    if detail:
        value["detail"] = detail
    _atomic_write(path, value)
    return {**value, "open": status == "open", "state_file": str(path)}


def record_circuit_failure(
    repository: Path,
    mode: str,
    source: str,
    detail: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return _record_circuit(repository, mode, "open", source, detail)


def record_circuit_success(
    repository: Path,
    mode: str,
    source: str,
    detail: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return _record_circuit(repository, mode, "closed", source, detail)


def preference(repository: Path, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Return a bounded host preference without claiming API availability."""
    path = state_path(repository)
    record = _read(path)
    result: Dict[str, Any] = {
        "cache_valid": False,
        "preferred_execution_env": "auto",
        "state_file": str(path),
        "status": "missing",
    }
    if record is None:
        return result
    result["status"] = record.get("status", "invalid")
    result["recorded_at"] = record.get("recorded_at")
    if (
        record.get("schema_version") != SCHEMA_VERSION
        or record.get("context_hash") != _context_hash(repository)
        or record.get("status") not in HOST_STATES
    ):
        result["status"] = "context-mismatch"
        return result
    try:
        recorded = datetime.fromisoformat(
            str(record["recorded_at"]).replace("Z", "+00:00")
        )
    except (KeyError, TypeError, ValueError):
        result["status"] = "invalid-timestamp"
        return result
    age = max(0, int(((now or _now()) - recorded).total_seconds()))
    result["age_seconds"] = age
    if age > ttl_seconds():
        result["status"] = "expired"
        return result
    result.update(
        cache_valid=True,
        preferred_execution_env="host",
        source=record.get("source", "unknown"),
    )
    return result


def record(
    repository: Path,
    status: str,
    source: str,
    detail: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if status not in HOST_STATES:
        raise ValueError("invalid Spark execution availability status")
    path = state_path(repository)
    value: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "preferred_execution_env": "host",
        "context_hash": _context_hash(repository),
        "recorded_at": _timestamp(_now()),
        "source": source,
    }
    if detail:
        value["detail"] = detail
    _atomic_write(path, value)
    return {**value, "state_file": str(path)}
