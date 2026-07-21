#!/usr/bin/env python3
"""Immutable content-addressed Evidence Object Store primitives."""
from __future__ import annotations

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None
try:
    import msvcrt
except ImportError:  # pragma: no cover - Unix
    msvcrt = None

import base64
import binascii
import hashlib
import json
import re
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from workflow_state import WorkflowStateError, atomic_write_json, canonical_json


SCHEMA_VERSION = 1
OBJECT_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
KINDS = {
    "symbol-slice", "file-slice", "call-path", "callers", "callees",
    "test-definition", "test-result", "build-rule", "compiler-error",
    "runtime-error", "diff-hunk", "repository-fact", "decision-record",
    "acceptance-record",
}
DEPENDENCY_KEYS = {
    "file_hash", "symbol_hash", "build_configuration_hash",
    "validation_command_hash", "worktree_state_hash",
}
METRICS_FILE = "evidence-cache-metrics.json"
MAX_STORED_OBJECT_BYTES = 8 * 1024 * 1024
MAX_VALIDITY_BYTES = 256 * 1024
MAX_METRICS_BYTES = 16 * 1024 * 1024


class EvidenceStoreError(WorkflowStateError):
    """An Evidence Object, reference, or store operation is invalid."""


def sha256_id(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def object_id_for(obj: Dict[str, Any]) -> str:
    material = deepcopy(obj)
    material.pop("object_id", None)
    return sha256_id(canonical_json(material).encode("utf-8"))


def object_path(store: Path, object_id: str) -> Path:
    if not isinstance(object_id, str) or not OBJECT_ID_RE.fullmatch(object_id):
        raise EvidenceStoreError("object_id must be a sha256: digest")
    digest = object_id.split(":", 1)[1]
    return store / digest[:2] / (digest[2:] + ".json")


def read_json_bounded(path: Path, max_bytes: int, label: str) -> Any:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise EvidenceStoreError(f"cannot inspect {label}: {exc}") from exc
    if size > max_bytes:
        raise EvidenceStoreError(f"{label} exceeds {max_bytes} byte limit")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceStoreError(f"cannot read {label}: {exc}") from exc


def validate_store_path(store: Path, path: Path) -> None:
    if store.exists() and store.is_symlink():
        raise EvidenceStoreError("Evidence Object store root must not be a symlink")
    if path.parent.exists() and path.parent.is_symlink():
        raise EvidenceStoreError("Evidence Object shard must not be a symlink")
    if path.exists() and path.is_symlink():
        raise EvidenceStoreError("Evidence Object path must not be a symlink")


def validity_path(path: Path) -> Path:
    return path.with_suffix(".validity.json")


def _nullable_string(value: Any, path: str, errors: List[str]) -> None:
    if value is not None and (not isinstance(value, str) or not value):
        errors.append(f"{path} must be null or a non-empty string")


def validate_metadata(metadata: Any) -> List[str]:
    if not isinstance(metadata, dict):
        return ["metadata must be an object"]
    required = {
        "schema_version", "kind", "repository", "selector", "producer",
        "dependency_hashes",
    }
    errors = []
    if set(metadata) != required:
        errors.append("metadata fields do not match schema")
        return errors
    if metadata.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if metadata.get("kind") not in KINDS:
        errors.append("kind is unsupported")
    repository = metadata.get("repository")
    if not isinstance(repository, dict) or set(repository) != {"commit", "path"}:
        errors.append("repository must contain exactly commit and path")
        repository = {}
    else:
        _nullable_string(repository.get("commit"), "repository.commit", errors)
        _nullable_string(repository.get("path"), "repository.path", errors)
        path = repository.get("path")
        if isinstance(path, str):
            normalized = path.replace("\\", "/")
            if (
                normalized.startswith("/") or re.match(r"^[A-Za-z]:/", normalized)
                or ".." in normalized.split("/")
            ):
                errors.append("repository.path must be repository-relative")
    selector = metadata.get("selector")
    if not isinstance(selector, dict) or set(selector) != {"symbol", "start_line", "end_line"}:
        errors.append("selector must contain exactly symbol, start_line, and end_line")
        selector = {}
    else:
        _nullable_string(selector.get("symbol"), "selector.symbol", errors)
        for field in ("start_line", "end_line"):
            value = selector.get(field)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 1
            ):
                errors.append(f"selector.{field} must be null or a positive integer")
        if (
            isinstance(selector.get("start_line"), int)
            and isinstance(selector.get("end_line"), int)
            and selector["end_line"] < selector["start_line"]
        ):
            errors.append("selector.end_line must not precede start_line")
    producer = metadata.get("producer")
    if not isinstance(producer, dict) or set(producer) != {"tool", "version"}:
        errors.append("producer must contain exactly tool and version")
    else:
        for field in ("tool", "version"):
            if not isinstance(producer.get(field), str) or not producer[field]:
                errors.append(f"producer.{field} must be a non-empty string")
    dependencies = metadata.get("dependency_hashes")
    if not isinstance(dependencies, dict) or set(dependencies) != DEPENDENCY_KEYS:
        errors.append("dependency_hashes fields do not match schema")
    else:
        for field in DEPENDENCY_KEYS:
            _nullable_string(dependencies.get(field), f"dependency_hashes.{field}", errors)
        if dependencies.get("symbol_hash") and not selector.get("symbol"):
            errors.append("symbol_hash requires selector.symbol")
        if dependencies.get("file_hash") and not repository.get("path"):
            errors.append("file_hash requires repository.path")
    return errors


def encode_content(raw: bytes, encoding: str) -> Tuple[Dict[str, Any], bytes]:
    if encoding == "utf-8":
        try:
            value = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise EvidenceStoreError("content is not valid UTF-8; use base64") from exc
        canonical_bytes = raw
    elif encoding == "base64":
        value = base64.b64encode(raw).decode("ascii")
        canonical_bytes = raw
    elif encoding == "json":
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise EvidenceStoreError("content is not valid JSON") from exc
        canonical_bytes = canonical_json(value).encode("utf-8")
    else:
        raise EvidenceStoreError("encoding must be utf-8, base64, or json")
    return {"encoding": encoding, "value": value}, canonical_bytes


def content_bytes(content: Dict[str, Any]) -> bytes:
    encoding = content.get("encoding")
    value = content.get("value")
    if encoding == "utf-8":
        if not isinstance(value, str):
            raise EvidenceStoreError("utf-8 content value must be a string")
        return value.encode("utf-8")
    if encoding == "base64":
        if not isinstance(value, str):
            raise EvidenceStoreError("base64 content value must be a string")
        try:
            return base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise EvidenceStoreError("invalid base64 content") from exc
    if encoding == "json":
        return canonical_json(value).encode("utf-8")
    raise EvidenceStoreError("unsupported content encoding")


def build_object(metadata: Dict[str, Any], raw: bytes, encoding: str) -> Dict[str, Any]:
    errors = validate_metadata(metadata)
    if errors:
        raise EvidenceStoreError("; ".join(errors))
    content, canonical_bytes = encode_content(raw, encoding)
    obj = {
        "schema_version": SCHEMA_VERSION,
        "object_id": "",
        "kind": metadata["kind"],
        "content_hash": sha256_id(canonical_bytes),
        "content_bytes": len(canonical_bytes),
        "repository": deepcopy(metadata["repository"]),
        "selector": deepcopy(metadata["selector"]),
        "producer": deepcopy(metadata["producer"]),
        "dependency_hashes": deepcopy(metadata["dependency_hashes"]),
        "content": content,
    }
    obj["object_id"] = object_id_for(obj)
    return obj


def validate_object(obj: Any, *, verify_hashes: bool = True) -> List[str]:
    if not isinstance(obj, dict):
        return ["Evidence Object must be an object"]
    required = {
        "schema_version", "object_id", "kind", "content_hash", "content_bytes",
        "repository", "selector", "producer", "dependency_hashes", "content",
    }
    errors = []
    if set(obj) != required:
        errors.append("Evidence Object fields do not match schema")
        return errors
    metadata = {
        key: deepcopy(obj[key])
        for key in ("schema_version", "kind", "repository", "selector", "producer", "dependency_hashes")
    }
    errors.extend(validate_metadata(metadata))
    for field in ("object_id", "content_hash"):
        if not isinstance(obj.get(field), str) or not OBJECT_ID_RE.fullmatch(obj[field]):
            errors.append(f"{field} must be a sha256: digest")
    if not isinstance(obj.get("content_bytes"), int) or isinstance(obj["content_bytes"], bool) or obj["content_bytes"] < 0:
        errors.append("content_bytes must be a non-negative integer")
    if not isinstance(obj.get("content"), dict) or set(obj["content"]) != {"encoding", "value"}:
        errors.append("content must contain exactly encoding and value")
        return errors
    try:
        raw = content_bytes(obj["content"])
    except EvidenceStoreError as exc:
        errors.append(str(exc))
        return errors
    if obj.get("content_bytes") != len(raw):
        errors.append("content_bytes does not match content")
    if verify_hashes:
        if obj.get("content_hash") != sha256_id(raw):
            errors.append("content_hash does not match content")
        if obj.get("object_id") != object_id_for(obj):
            errors.append("object_id does not match canonical object content")
    return errors


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    lock = path.with_suffix(path.suffix + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        elif msvcrt is not None:  # pragma: no cover - Windows
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            handle.seek(0)
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover - Windows
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def store_object(store: Path, obj: Dict[str, Any]) -> Tuple[Path, bool]:
    errors = validate_object(obj)
    if errors:
        raise EvidenceStoreError("; ".join(errors))
    path = object_path(store, obj["object_id"])
    validate_store_path(store, path)
    with file_lock(path):
        if path.exists():
            existing = load_object(store, obj["object_id"], check_validity=False)
            if existing != obj:
                raise EvidenceStoreError("object ID collision or corrupted existing object")
            return path, True
        atomic_write_json(path, obj)
        return path, False


def validate_validity(value: Any) -> List[str]:
    required = {
        "schema_version", "object_id", "status", "checked_at",
        "current_context_hash", "reasons",
    }
    if not isinstance(value, dict) or set(value) != required:
        return ["validity record fields do not match schema"]
    errors = []
    if value.get("schema_version") != 1:
        errors.append("validity record schema_version must be 1")
    for field in ("object_id", "current_context_hash"):
        if not isinstance(value.get(field), str) or not OBJECT_ID_RE.fullmatch(value[field]):
            errors.append(f"validity record {field} must be a sha256: digest")
    if value.get("status") not in {"valid", "stale", "unknown"}:
        errors.append("validity record status is invalid")
    if not isinstance(value.get("checked_at"), str) or not value["checked_at"]:
        errors.append("validity record checked_at must be a non-empty string")
    reasons = value.get("reasons")
    expected_reason = {"dependency", "status", "expected", "actual"}
    if not isinstance(reasons, list):
        errors.append("validity record reasons must be an array")
        return errors
    for index, reason in enumerate(reasons):
        if not isinstance(reason, dict) or set(reason) != expected_reason:
            errors.append(f"validity record reason {index} fields do not match schema")
            continue
        if not isinstance(reason.get("dependency"), str) or not reason["dependency"]:
            errors.append(f"validity record reason {index} dependency is invalid")
        if reason.get("status") not in {"stale", "unknown"}:
            errors.append(f"validity record reason {index} status is invalid")
        for field in ("expected", "actual"):
            _nullable_string(
                reason.get(field), f"validity record reason {index} {field}", errors,
            )
    return errors


def load_validity(path: Path) -> Optional[Dict[str, Any]]:
    sidecar = validity_path(path)
    if not sidecar.exists():
        return None
    if sidecar.is_symlink():
        raise EvidenceStoreError("validity record must not be a symlink")
    value = read_json_bounded(sidecar, MAX_VALIDITY_BYTES, "validity record")
    errors = validate_validity(value)
    if errors:
        raise EvidenceStoreError("invalid validity record: " + "; ".join(errors))
    return value


def load_object(store: Path, object_id: str, *, check_validity: bool = True) -> Dict[str, Any]:
    path = object_path(store, object_id)
    validate_store_path(store, path)
    if not path.is_file():
        raise EvidenceStoreError(f"Evidence Object reference is unreadable: {object_id}")
    obj = read_json_bounded(path, MAX_STORED_OBJECT_BYTES, f"Evidence Object {object_id}")
    errors = validate_object(obj)
    if errors:
        raise EvidenceStoreError("invalid Evidence Object: " + "; ".join(errors))
    if obj["object_id"] != object_id:
        raise EvidenceStoreError("reference object_id does not match stored object")
    if check_validity:
        validity = load_validity(path)
        if validity and validity.get("object_id") != object_id:
            raise EvidenceStoreError("validity record object_id mismatch")
        if validity and validity.get("status") != "valid":
            raise EvidenceStoreError(
                f"Evidence Object {object_id} is {validity.get('status')}: "
                + ", ".join(reason.get("dependency", "unknown") for reason in validity.get("reasons", []))
            )
    return obj


def default_metrics_path(store: Path) -> Path:
    return store.parent / "cache" / "receiver-state" / METRICS_FILE


def empty_metrics() -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "total_reads": 0,
        "total_hits": 0,
        "total_misses": 0,
        "total_unreadable": 0,
        "receivers": {},
    }


def validate_metrics(metrics: Any) -> List[str]:
    required = {
        "schema_version", "total_reads", "total_hits", "total_misses",
        "total_unreadable", "receivers",
    }
    if not isinstance(metrics, dict) or set(metrics) != required:
        return ["receiver metrics fields do not match schema"]
    errors = []
    if metrics.get("schema_version") != 1:
        errors.append("receiver metrics schema_version must be 1")
    for field in ("total_reads", "total_hits", "total_misses", "total_unreadable"):
        value = metrics.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            errors.append(f"receiver metrics {field} must be non-negative integer")
    receivers = metrics.get("receivers")
    if not isinstance(receivers, dict):
        errors.append("receiver metrics receivers must be an object")
        return errors
    expected_row = {"reads", "hits", "misses", "unreadable", "object_ids"}
    for receiver, row in receivers.items():
        if not isinstance(receiver, str) or not receiver:
            errors.append("receiver metrics key must be non-empty")
            continue
        if not isinstance(row, dict) or set(row) != expected_row:
            errors.append(f"receiver metrics row is invalid: {receiver}")
            continue
        for field in ("reads", "hits", "misses", "unreadable"):
            if not isinstance(row[field], int) or isinstance(row[field], bool) or row[field] < 0:
                errors.append(f"receiver metrics {receiver}.{field} is invalid")
        if not isinstance(row["object_ids"], list) or not all(
            isinstance(item, str) and OBJECT_ID_RE.fullmatch(item) for item in row["object_ids"]
        ) or len(row["object_ids"]) != len(set(row["object_ids"])):
            errors.append(f"receiver metrics {receiver}.object_ids is invalid")
        if isinstance(row.get("reads"), int) and isinstance(row.get("hits"), int) and isinstance(row.get("misses"), int) and row["reads"] != row["hits"] + row["misses"]:
            errors.append(f"receiver metrics {receiver}.reads does not equal hits plus misses")
    numeric_totals = all(
        isinstance(metrics.get(field), int) and not isinstance(metrics.get(field), bool)
        for field in ("total_reads", "total_hits", "total_misses", "total_unreadable")
    )
    valid_rows = all(
        isinstance(row, dict) and all(
            isinstance(row.get(field), int) and not isinstance(row.get(field), bool)
            for field in ("reads", "hits", "misses", "unreadable")
        )
        for row in receivers.values()
    )
    if numeric_totals and metrics["total_reads"] != metrics["total_hits"] + metrics["total_misses"]:
        errors.append("receiver metrics total_reads does not equal hits plus misses")
    if numeric_totals and valid_rows:
        for total_field, row_field in (
            ("total_reads", "reads"), ("total_hits", "hits"),
            ("total_misses", "misses"), ("total_unreadable", "unreadable"),
        ):
            if metrics[total_field] != sum(row[row_field] for row in receivers.values()):
                errors.append(f"receiver metrics {total_field} does not equal receiver rows")
    return errors


def record_receiver_access(
    metrics_path: Path, receiver: str, object_id: str, *, readable: bool,
) -> Dict[str, Any]:
    if not isinstance(receiver, str) or not receiver or len(receiver) > 256:
        raise EvidenceStoreError("receiver must be a non-empty string of at most 256 characters")
    if metrics_path.exists() and metrics_path.is_symlink():
        raise EvidenceStoreError("receiver metrics path must not be a symlink")
    with file_lock(metrics_path):
        if metrics_path.exists():
            metrics = read_json_bounded(
                metrics_path, MAX_METRICS_BYTES, "receiver metrics",
            )
        else:
            metrics = empty_metrics()
        errors = validate_metrics(metrics)
        if errors:
            raise EvidenceStoreError("; ".join(errors))
        receiver_row = metrics["receivers"].setdefault(receiver, {
            "reads": 0, "hits": 0, "misses": 0, "unreadable": 0, "object_ids": [],
        })
        if not readable:
            metrics["total_unreadable"] += 1
            receiver_row["unreadable"] += 1
            outcome = "unreadable"
        else:
            metrics["total_reads"] += 1
            receiver_row["reads"] += 1
            if object_id in receiver_row["object_ids"]:
                metrics["total_hits"] += 1
                receiver_row["hits"] += 1
                outcome = "hit"
            else:
                metrics["total_misses"] += 1
                receiver_row["misses"] += 1
                receiver_row["object_ids"].append(object_id)
                receiver_row["object_ids"].sort()
                outcome = "miss"
        atomic_write_json(metrics_path, metrics)
        return {"outcome": outcome, "metrics": metrics}


def reference_for(obj: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "object_id": obj["object_id"],
        "kind": obj["kind"],
        "content_hash": obj["content_hash"],
        "content_bytes": obj["content_bytes"],
        "repository": deepcopy(obj["repository"]),
        "selector": deepcopy(obj["selector"]),
    }


def build_reference_packet(store: Path, object_ids: Iterable[str]) -> Dict[str, Any]:
    unique_ids = sorted(set(object_ids))
    if not unique_ids:
        raise EvidenceStoreError("reference packet requires at least one object_id")
    references = [reference_for(load_object(store, object_id)) for object_id in unique_ids]
    return {
        "schema_version": 1,
        "packet_type": "evidence-object-references",
        "inline_content": False,
        "object_refs": references,
        "total_content_bytes": sum(item["content_bytes"] for item in references),
    }


def iter_object_ids(store: Path) -> Iterator[str]:
    if not store.exists():
        return
    if store.is_symlink():
        raise EvidenceStoreError("Evidence Object store root must not be a symlink")
    for directory in sorted(store.iterdir()):
        if (
            directory.is_symlink() or not directory.is_dir()
            or not re.fullmatch(r"[0-9a-f]{2}", directory.name)
        ):
            continue
        for path in sorted(directory.glob("*.json")):
            if path.is_symlink():
                continue
            if path.name.endswith(".validity.json"):
                continue
            remainder = path.stem
            if re.fullmatch(r"[0-9a-f]{62}", remainder):
                yield "sha256:" + directory.name + remainder


def validate_current_context(value: Any) -> List[str]:
    if not isinstance(value, dict):
        return ["current context must be an object"]
    required = {
        "schema_version", "repository_commit", "worktree_state_hash",
        "build_configuration_hash", "validation_command_hash", "paths",
    }
    errors = []
    if set(value) != required:
        errors.append("current context fields do not match schema")
        return errors
    if value.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    for field in (
        "repository_commit", "worktree_state_hash", "build_configuration_hash",
        "validation_command_hash",
    ):
        _nullable_string(value.get(field), field, errors)
    paths = value.get("paths")
    if not isinstance(paths, dict):
        errors.append("paths must be an object")
        return errors
    for path, row in paths.items():
        if not isinstance(path, str) or not path:
            errors.append("paths keys must be non-empty strings")
            continue
        normalized = path.replace("\\", "/")
        if (
            normalized.startswith("/") or re.match(r"^[A-Za-z]:/", normalized)
            or ".." in normalized.split("/")
        ):
            errors.append(f"paths.{path} must be repository-relative")
        if not isinstance(row, dict) or set(row) != {"file_hash", "symbol_hashes"}:
            errors.append(f"paths.{path} must contain file_hash and symbol_hashes")
            continue
        _nullable_string(row.get("file_hash"), f"paths.{path}.file_hash", errors)
        symbols = row.get("symbol_hashes")
        if not isinstance(symbols, dict) or not all(
            isinstance(symbol, str) and symbol
            and isinstance(symbol_hash, str) and symbol_hash
            for symbol, symbol_hash in symbols.items()
        ):
            errors.append(f"paths.{path}.symbol_hashes must map symbols to hashes")
    return errors


def _comparison(
    reasons: List[Dict[str, Any]], dependency: str, expected: Optional[str], actual: Optional[str],
) -> None:
    if expected is None:
        return
    if actual is None:
        reasons.append({
            "dependency": dependency, "status": "unknown",
            "expected": expected, "actual": None,
        })
    elif actual != expected:
        reasons.append({
            "dependency": dependency, "status": "stale",
            "expected": expected, "actual": actual,
        })


def evaluate_validity(obj: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    object_errors = validate_object(obj)
    current_errors = validate_current_context(current)
    if object_errors or current_errors:
        raise EvidenceStoreError("; ".join(object_errors + current_errors))
    reasons: List[Dict[str, Any]] = []
    dependencies = obj["dependency_hashes"]
    _comparison(reasons, "repository.commit", obj["repository"]["commit"], current["repository_commit"])
    _comparison(
        reasons, "worktree_state_hash", dependencies["worktree_state_hash"],
        current["worktree_state_hash"],
    )
    _comparison(
        reasons, "build_configuration_hash", dependencies["build_configuration_hash"],
        current["build_configuration_hash"],
    )
    _comparison(
        reasons, "validation_command_hash", dependencies["validation_command_hash"],
        current["validation_command_hash"],
    )
    path = obj["repository"]["path"]
    path_row = current["paths"].get(path) if path is not None else None
    _comparison(
        reasons, "file_hash", dependencies["file_hash"],
        path_row.get("file_hash") if path_row else None,
    )
    symbol = obj["selector"]["symbol"]
    symbol_hashes = path_row.get("symbol_hashes", {}) if path_row else {}
    _comparison(
        reasons, "symbol_hash", dependencies["symbol_hash"],
        symbol_hashes.get(symbol) if symbol is not None else None,
    )
    statuses = {reason["status"] for reason in reasons}
    status = "stale" if "stale" in statuses else "unknown" if "unknown" in statuses else "valid"
    return {
        "schema_version": 1,
        "object_id": obj["object_id"],
        "status": status,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "current_context_hash": sha256_id(canonical_json(current).encode("utf-8")),
        "reasons": reasons,
    }
