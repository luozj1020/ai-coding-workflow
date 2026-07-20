#!/usr/bin/env python3
"""Normalize and aggregate per-call model usage without estimating missing data."""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, List, Optional, Union

SCHEMA_VERSION = 1
NUMERIC_FIELDS = (
    "wall_time_ms", "api_time_ms", "first_progress_ms", "input_tokens",
    "cached_input_tokens", "cache_creation_input_tokens", "output_tokens",
    "reasoning_tokens", "total_tokens", "cost_usd",
)
SUM_FIELDS = NUMERIC_FIELDS
TOKEN_FIELDS = ("input_tokens", "output_tokens")


def load_pricing(path: Path) -> dict[str, Any]:
    """Load a versioned, user-supplied API pricing catalog.

    Pricing is deliberately external to usage normalization because model rates
    change independently of workflow code. Entries are matched in order.
    """
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("pricing catalog schema_version must be 1")
    entries = value.get("models")
    if not isinstance(entries, list) or not entries:
        raise ValueError("pricing catalog models must be a non-empty list")
    required = ("pattern", "input_per_million", "cached_input_per_million", "output_per_million")
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or any(key not in entry for key in required):
            raise ValueError("invalid pricing entry {}".format(index))
        if not isinstance(entry["pattern"], str) or not entry["pattern"]:
            raise ValueError("invalid pricing pattern {}".format(index))
        for key in required[1:]:
            if not isinstance(entry[key], (int, float)) or isinstance(entry[key], bool) or entry[key] < 0:
                raise ValueError("invalid {} in pricing entry {}".format(key, index))
        if not isinstance(entry.get("input_includes_cached", True), bool):
            raise ValueError("input_includes_cached must be boolean in pricing entry {}".format(index))
        if not isinstance(entry.get("billable", True), bool):
            raise ValueError("billable must be boolean in pricing entry {}".format(index))
    return value


def calculate_cost(record: dict[str, Any], pricing: dict[str, Any]) -> Optional[float]:
    """Calculate one call's API cost, or return None when evidence is incomplete."""
    model = record.get("model")
    input_tokens = record.get("input_tokens")
    output_tokens = record.get("output_tokens")
    cached_tokens = record.get("cached_input_tokens")
    if not isinstance(model, str) or not isinstance(input_tokens, (int, float)) or not isinstance(output_tokens, (int, float)):
        return None
    for entry in pricing.get("models", []):
        if not fnmatch.fnmatchcase(model.lower(), str(entry["pattern"]).lower()):
            continue
        if cached_tokens is None:
            cached_tokens = 0
        if not isinstance(cached_tokens, (int, float)) or isinstance(cached_tokens, bool):
            return None
        uncached = input_tokens
        if entry.get("input_includes_cached", True):
            if cached_tokens > input_tokens:
                return None
            uncached = input_tokens - cached_tokens
        if entry.get("billable", True) is False:
            return 0.0
        return round((
            uncached * entry["input_per_million"]
            + cached_tokens * entry["cached_input_per_million"]
            + output_tokens * entry["output_per_million"]
        ) / 1_000_000, 9)
    return None


def _number(value: Any, *, integer: bool = True) -> Optional[Union[int, float]]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value) if integer else float(value)
    except (TypeError, ValueError):
        return None


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _record(**values: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": None,
        "task_id": None,
        "call_id": None,
        "experiment_arm": None,
        "role": None,
        "stage": None,
        "model": None,
        "started_at": None,
        "finished_at": None,
        "wall_time_ms": None,
        "api_time_ms": None,
        "first_progress_ms": None,
        "input_tokens": None,
        "cached_input_tokens": None,
        "cache_creation_input_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
        "total_tokens": None,
        "cost_usd": None,
        "usage_source": None,
        "usage_complete": False,
        "result": None,
    }
    record.update(values)
    record["usage_complete"] = all(record.get(key) is not None for key in TOKEN_FIELDS)
    return record


def _metadata(record: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    for key in (
        "run_id", "task_id", "call_id", "experiment_arm", "role", "stage",
        "model", "started_at", "finished_at", "first_progress_ms", "result",
        "wall_time_ms", "api_time_ms",
    ):
        if metadata.get(key) is not None:
            record[key] = metadata[key]
    record["usage_complete"] = all(record.get(key) is not None for key in TOKEN_FIELDS)
    return record


def parse_claude(value: dict[str, Any], **metadata: Any) -> dict[str, Any]:
    """Normalize one Claude ``--output-format json`` terminal result."""
    usage = value.get("usage") if isinstance(value.get("usage"), dict) else {}
    per_model = value.get("modelUsage") if isinstance(value.get("modelUsage"), dict) else {}
    model = metadata.get("model")
    if model is None and per_model:
        model = ",".join(sorted(str(name) for name in per_model))
    record = _record(
        role="claude",
        model=model,
        wall_time_ms=_number(value.get("duration_ms")),
        api_time_ms=_number(value.get("duration_api_ms")),
        input_tokens=_number(_first(usage, "input_tokens")),
        cached_input_tokens=_number(_first(usage, "cache_read_input_tokens", "cached_input_tokens")),
        cache_creation_input_tokens=_number(usage.get("cache_creation_input_tokens")),
        output_tokens=_number(usage.get("output_tokens")),
        reasoning_tokens=_number(usage.get("reasoning_tokens")),
        total_tokens=_number(usage.get("total_tokens")),
        cost_usd=_number(value.get("total_cost_usd"), integer=False),
        usage_source="claude-json",
        result="success" if value.get("is_error") is False else value.get("result", "unknown"),
    )
    return _metadata(record, metadata)


def _walk_usage(value: Any, found: dict[str, Any]) -> None:
    aliases = {
        "input_tokens": "input_tokens",
        "output_tokens": "output_tokens",
        "total_tokens": "total_tokens",
        "cached_input_tokens": "cached_input_tokens",
        "cache_read_input_tokens": "cached_input_tokens",
        "cache_creation_input_tokens": "cache_creation_input_tokens",
        "reasoning_tokens": "reasoning_tokens",
        "duration_ms": "wall_time_ms",
        "duration_api_ms": "api_time_ms",
        "cost_usd": "cost_usd",
        "total_cost_usd": "cost_usd",
    }
    if isinstance(value, dict):
        for key, item in value.items():
            if key in aliases and isinstance(item, (int, float)) and not isinstance(item, bool):
                found[aliases[key]] = item
            _walk_usage(item, found)
    elif isinstance(value, list):
        for item in value:
            _walk_usage(item, found)


def parse_codex_events(lines: Iterable[str], **metadata: Any) -> dict[str, Any]:
    """Normalize one Codex ``exec --json`` call.

    Codex usage events are cumulative snapshots.  The last usage-bearing event
    wins so retries/events are not accidentally summed twice.
    """
    selected: dict[str, Any] = {}
    selected_event: dict[str, Any] = {}
    for raw in lines:
        try:
            event = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        found: dict[str, Any] = {}
        _walk_usage(event, found)
        if found:
            selected = found
            selected_event = event
    record = _record(
        role="codex",
        model=selected_event.get("model"),
        started_at=_first(selected_event, "started_at", "created_at", "timestamp"),
        finished_at=_first(selected_event, "finished_at", "completed_at", "timestamp"),
        usage_source="codex-jsonl",
        result=_first(selected_event, "result", "status", "type") or "unknown",
        **{key: _number(value, integer=key != "cost_usd") for key, value in selected.items()},
    )
    return _metadata(record, metadata)


def parse_file(source: str, path: Path, **metadata: Any) -> dict[str, Any]:
    if source == "claude":
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Claude result must be a JSON object")
        return parse_claude(value, **metadata)
    return parse_codex_events(path.read_text(encoding="utf-8", errors="replace").splitlines(), **metadata)


@contextmanager
def _portable_lock(path: Path, timeout: float = 5.0):
    lock = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(fd, (str(os.getpid()) + "\n").encode("ascii"))
            os.close(fd)
            break
        except (FileExistsError, PermissionError):
            # Windows may report sharing/access denial instead of EEXIST while
            # another process creates or removes an O_EXCL lock file. Treat it
            # as transient contention, still bounded by the same deadline.
            try:
                if time.time() - lock.stat().st_mtime > 60:
                    lock.unlink()
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError("usage ledger lock timed out: {}".format(lock))
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def load_records(path: Path, *, strict: bool = False) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            if strict:
                raise ValueError("malformed usage ledger line {}".format(number)) from exc
            continue
        if isinstance(value, dict):
            records.append(value)
        elif strict:
            raise ValueError("usage ledger line {} is not an object".format(number))
    return records


def append_once(path: Path, record: dict[str, Any]) -> bool:
    call_id = record.get("call_id")
    if not call_id:
        raise ValueError("call_id is required for idempotent append")
    path.parent.mkdir(parents=True, exist_ok=True)
    with _portable_lock(path):
        existing = load_records(path, strict=True)
        if any(row.get("call_id") == call_id for row in existing):
            return False
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    return True


def _group(records: list[dict[str, Any]], pricing: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "calls": len(records),
        "complete_calls": sum(row.get("usage_complete") is True for row in records),
    }
    result["usage_complete"] = bool(records) and result["complete_calls"] == len(records)
    for field in SUM_FIELDS:
        values = [row[field] for row in records if isinstance(row.get(field), (int, float)) and not isinstance(row.get(field), bool)]
        result[field] = sum(values) if values else None
    result["provider_cost_complete"] = bool(records) and all(
        isinstance(row.get("cost_usd"), (int, float)) and not isinstance(row.get("cost_usd"), bool)
        for row in records
    )
    if pricing is not None:
        calculated = [calculate_cost(row, pricing) for row in records]
        result["calculated_cost_complete"] = bool(records) and all(value is not None for value in calculated)
        result["calculated_cost_usd"] = (
            round(sum(value for value in calculated if value is not None), 9)
            if result["calculated_cost_complete"] else None
        )
    return result


def aggregate(records: list[dict[str, Any]], pricing: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    roles: dict[str, list[dict[str, Any]]] = defaultdict(list)
    stages: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        roles[str(row.get("role") or "unknown")].append(row)
        stages[str(row.get("stage") or "unknown")].append(row)
    return {
        "schema_version": SCHEMA_VERSION,
        "totals": _group(records, pricing),
        "by_role": {key: _group(value, pricing) for key, value in sorted(roles.items())},
        "by_stage": {key: _group(value, pricing) for key, value in sorted(stages.items())},
    }


def _metadata_args(parser: argparse.ArgumentParser) -> None:
    for name in ("run-id", "task-id", "call-id", "experiment-arm", "role", "stage", "model", "started-at", "finished-at", "result"):
        parser.add_argument("--" + name)
    parser.add_argument("--first-progress-ms", type=int)
    parser.add_argument("--wall-time-ms", type=int)
    parser.add_argument("--api-time-ms", type=int)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("capture", help="Normalize one terminal call and optionally append it")
    capture.add_argument("--source", choices=("claude", "codex"), required=True)
    capture.add_argument("--input", type=Path, required=True)
    capture.add_argument("--ledger", type=Path)
    capture.add_argument("--output", type=Path)
    _metadata_args(capture)
    append = sub.add_parser("append", help="Append one already-normalized JSON record")
    append.add_argument("ledger", type=Path)
    append.add_argument("record", type=Path)
    summary = sub.add_parser("aggregate", help="Aggregate a canonical JSONL ledger")
    summary.add_argument("ledger", type=Path)
    summary.add_argument("--output", type=Path)
    summary.add_argument("--pricing", type=Path,
                         help="Versioned API price catalog used for a separate calculated cost")
    args = parser.parse_args(argv)
    if args.command == "capture":
        metadata = {key: value for key, value in vars(args).items() if key in {
            "run_id", "task_id", "call_id", "experiment_arm", "role", "stage", "model",
            "started_at", "finished_at", "first_progress_ms", "wall_time_ms",
            "api_time_ms", "result",
        } and value is not None}
        record = parse_file(args.source, args.input, **metadata)
        appended = append_once(args.ledger, record) if args.ledger else None
        payload: dict[str, Any] = {"record": record}
        if appended is not None:
            payload["appended"] = appended
    elif args.command == "append":
        record = json.loads(args.record.read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            raise ValueError("record must be a JSON object")
        payload = {"appended": append_once(args.ledger, record)}
    else:
        payload = aggregate(load_records(args.ledger), load_pricing(args.pricing) if args.pricing else None)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if getattr(args, "output", None):
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
