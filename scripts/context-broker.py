#!/usr/bin/env python3
"""Pull bounded repository context into content-addressed Evidence Objects."""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evidence_store import (  # noqa: E402
    EvidenceStoreError, build_object, iter_object_ids, load_object, reference_for,
    read_json_bounded, store_object,
)
from workflow_state import (  # noqa: E402
    WorkflowStateError, atomic_write_json, canonical_json, validate_state,
)


QUERY_TYPES = ("definition", "callers", "callees", "tests", "build-rules")
KIND_FOR_QUERY = {
    "definition": "symbol-slice",
    "callers": "callers",
    "callees": "callees",
    "tests": "test-definition",
    "build-rules": "build-rule",
}
QUALITY_FOR_QUERY = {
    "definition": "exact-text-candidate",
    "tests": "exact-text-candidate",
    "build-rules": "exact-text-candidate",
    "callers": "bounded-lexical-candidate",
    "callees": "bounded-lexical-candidate",
}
SOURCE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx",
    ".py", ".go", ".rs", ".java", ".kt", ".kts", ".js", ".jsx",
    ".ts", ".tsx", ".cs", ".rb", ".php", ".swift", ".scala",
}
BUILD_NAMES = {
    "BUILD", "BUILD.bazel", "WORKSPACE", "WORKSPACE.bazel", "CMakeLists.txt",
    "Makefile", "meson.build", "build.gradle", "build.gradle.kts",
}
SKIP_DIRECTORIES = {
    ".git", ".ai-workflow", ".worktrees", ".codegraph", "node_modules",
    "vendor", "dist", "build", "target", "__pycache__", ".venv", "venv",
}
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_STATE_DOCUMENT_BYTES = 4 * 1024 * 1024
MAX_QUERY_DOCUMENT_BYTES = 1024 * 1024
MAX_QUERY_SYMBOLS = 16
MAX_QUERY_PATHS = 64
MAX_QUERY_SLOTS = 64
MAX_SCANNED_FILES = 20000
MAX_CACHE_INDEX_OBJECTS = 100000
MAX_MATCHES_PER_SLOT = 8
MAX_SLICE_LINES = 32
BROKER_VERSION = "1"
CONTROL_CALLS = {
    "if", "for", "while", "switch", "catch", "return", "sizeof", "alignof",
    "decltype", "typeid", "assert", "defined", "with", "match",
}


class ContextBrokerError(EvidenceStoreError):
    """A Pull Context request cannot be satisfied safely."""


def sha256_id(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def validate_query(value: Any) -> List[str]:
    if not isinstance(value, dict) or set(value) != {"state_id", "requester", "query"}:
        return ["query document must contain exactly state_id, requester, and query"]
    errors: List[str] = []
    state_id = value.get("state_id")
    if not isinstance(state_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", state_id):
        errors.append("state_id must be a sha256: digest")
    requester = value.get("requester")
    if not isinstance(requester, str) or not requester or len(requester) > 256:
        errors.append("requester must be a non-empty string of at most 256 characters")
    query = value.get("query")
    required = {"intent", "symbols", "include", "max_bytes"}
    optional = {"max_objects", "role", "paths"}
    if not isinstance(query, dict) or not required <= set(query) or set(query) - required - optional:
        errors.append("query has missing or unknown fields")
        return errors
    intent = query.get("intent")
    if not isinstance(intent, str) or not intent or len(intent) > 256:
        errors.append("query.intent must be a non-empty string of at most 256 characters")
    for field in ("symbols", "include"):
        items = query.get(field)
        if not isinstance(items, list) or not items or not all(isinstance(item, str) and item for item in items):
            errors.append(f"query.{field} must be a non-empty string array")
        elif len(items) != len(set(items)):
            errors.append(f"query.{field} must not contain duplicates")
    if isinstance(query.get("symbols"), list) and any(
        isinstance(item, str) and len(item) > 512 for item in query["symbols"]
    ):
        errors.append("query.symbols entries must not exceed 512 characters")
    if isinstance(query.get("symbols"), list) and len(query["symbols"]) > MAX_QUERY_SYMBOLS:
        errors.append(f"query.symbols exceeds {MAX_QUERY_SYMBOLS} item limit")
    if isinstance(query.get("include"), list) and any(
        item not in QUERY_TYPES for item in query["include"]
    ):
        errors.append("query.include contains an unsupported query type")
    for field, maximum in (("max_bytes", 1024 * 1024), ("max_objects", 256)):
        value_at_field = query.get(field, 32)
        if (
            not isinstance(value_at_field, int) or isinstance(value_at_field, bool)
            or value_at_field < 1 or value_at_field > maximum
        ):
            errors.append(f"query.{field} must be an integer from 1 to {maximum}")
    if query.get("role") not in (None, "planner", "builder", "checker", "reviewer"):
        errors.append("query.role is unsupported")
    paths = query.get("paths", [])
    if not isinstance(paths, list) or not all(isinstance(path, str) and path for path in paths):
        errors.append("query.paths must be a string array")
    elif len(paths) != len(set(paths)):
        errors.append("query.paths must not contain duplicates")
    elif any(len(path) > 1024 for path in paths):
        errors.append("query.paths entries must not exceed 1024 characters")
    elif len(paths) > MAX_QUERY_PATHS:
        errors.append(f"query.paths exceeds {MAX_QUERY_PATHS} item limit")
    elif any(normalize_repo_path(path) is None for path in paths):
        errors.append("query.paths must be repository-relative")
    symbols = query.get("symbols")
    include = query.get("include")
    if isinstance(symbols, list) and isinstance(include, list) and len(symbols) * len(include) > MAX_QUERY_SLOTS:
        errors.append(f"query expands beyond {MAX_QUERY_SLOTS} request slots")
    return errors


def normalize_repo_path(value: str) -> Optional[str]:
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:/", normalized):
        return None
    if ".." in normalized.split("/"):
        return None
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized or "."


def path_allowed(path: str, allowed_paths: Sequence[str]) -> bool:
    if not allowed_paths:
        return True
    normalized = normalize_repo_path(path)
    if normalized is None:
        return False
    for allowed in allowed_paths:
        pattern = normalize_repo_path(allowed)
        if pattern is None:
            continue
        pattern = pattern.rstrip("/")
        if (
            normalized == pattern or normalized.startswith(pattern + "/")
            or fnmatch.fnmatch(normalized, pattern)
        ):
            return True
    return False


def requested_path_allowed(path: str, allowed_paths: Sequence[str]) -> bool:
    normalized = normalize_repo_path(path)
    return normalized is not None and path_allowed(normalized, allowed_paths)


def readable_regular_file(path: Path) -> bool:
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    return stat.S_ISREG(mode) and bool(mode & (stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH))


def iter_repository_files(repo: Path, *, build_only: bool = False) -> Iterable[Path]:
    yielded = 0
    for directory, names, filenames in os.walk(repo, followlinks=False):
        directory_path = Path(directory)
        names[:] = sorted(
            name for name in names
            if name not in SKIP_DIRECTORIES and not (directory_path / name).is_symlink()
        )
        for filename in sorted(filenames):
            path = directory_path / filename
            if path.is_symlink() or not path.is_file():
                continue
            if build_only:
                if path.name not in BUILD_NAMES:
                    continue
            elif path.suffix.lower() not in SOURCE_SUFFIXES:
                continue
            try:
                if path.stat().st_size > MAX_SOURCE_BYTES:
                    continue
            except OSError:
                continue
            yield path
            yielded += 1
            if yielded >= MAX_SCANNED_FILES:
                return


def read_lines(path: Path) -> Optional[List[str]]:
    if not readable_regular_file(path):
        return None
    try:
        if path.stat().st_size > MAX_SOURCE_BYTES:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\0" in raw:
        return None
    return raw.decode("utf-8", errors="replace").splitlines()


def symbol_terms(symbol: str) -> Tuple[str, str]:
    terminal = re.split(r"::|\.|#", symbol)[-1]
    return symbol, terminal


def find_occurrences(
    repo: Path, symbol: str, allowed_paths: Sequence[str], requested_paths: Sequence[str],
    *, tests_only: bool = False,
) -> List[Tuple[Path, int, List[str]]]:
    exact, terminal = symbol_terms(symbol)
    results: List[Tuple[Path, int, List[str]]] = []
    for path in iter_repository_files(repo):
        relative = path.relative_to(repo).as_posix()
        if not path_allowed(relative, allowed_paths):
            continue
        if requested_paths and not any(
            relative == item or relative.startswith(item.rstrip("/") + "/")
            or fnmatch.fnmatch(relative, item)
            for item in requested_paths
        ):
            continue
        lower_parts = [part.lower() for part in path.relative_to(repo).parts]
        is_test = any("test" in part or "spec" in part for part in lower_parts)
        if tests_only != is_test:
            continue
        lines = read_lines(path)
        if lines is None:
            continue
        for index, line in enumerate(lines, start=1):
            if exact in line or (terminal != exact and re.search(rf"\b{re.escape(terminal)}\b", line)):
                results.append((path, index, lines))
                if len(results) >= 128:
                    return results
    return results


def definition_score(line: str, symbol: str) -> int:
    _, terminal = symbol_terms(symbol)
    score = 0
    if symbol in line:
        score += 5
    if re.search(rf"\b(def|class|struct|enum|interface|trait|func|fn)\s+{re.escape(terminal)}\b", line):
        score += 8
    if re.search(rf"\b{re.escape(terminal)}\s*\(", line):
        score += 3
    if "{" in line or line.rstrip().endswith(":"):
        score += 2
    if line.lstrip().startswith(("//", "#", "/*", "*")):
        score -= 6
    return score


def choose_definition(
    occurrences: Sequence[Tuple[Path, int, List[str]]], symbol: str,
) -> Optional[Tuple[Path, int, List[str]]]:
    if not occurrences:
        return None
    return max(occurrences, key=lambda item: (definition_score(item[2][item[1] - 1], symbol), -item[1], str(item[0])))


def line_slice(lines: Sequence[str], center: int, *, radius: int = 8) -> Tuple[int, int, str]:
    start = max(1, center - radius)
    end = min(len(lines), center + radius)
    if end - start + 1 > MAX_SLICE_LINES:
        end = start + MAX_SLICE_LINES - 1
    text = "\n".join(f"{index}: {lines[index - 1]}" for index in range(start, end + 1))
    return start, end, text


def git_commit(repo: Path) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
            text=True, encoding="utf-8", errors="replace", check=False,
        )
    except OSError:
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def make_object(
    store: Path, repo: Path, state: Dict[str, Any], *, kind: str, symbol: str,
    path: Path, start_line: int, end_line: int, payload: Dict[str, Any], commit: Optional[str],
) -> Tuple[Dict[str, Any], bool]:
    relative = path.relative_to(repo).as_posix()
    metadata = {
        "schema_version": 1,
        "kind": kind,
        "repository": {"commit": commit, "path": relative},
        "selector": {
            "symbol": symbol, "start_line": start_line, "end_line": end_line,
        },
        "producer": {"tool": "context-broker", "version": BROKER_VERSION},
        "dependency_hashes": {
            "file_hash": file_hash(path),
            "symbol_hash": None,
            "build_configuration_hash": None,
            "validation_command_hash": None,
            "worktree_state_hash": state["repository_state_hash"],
        },
    }
    obj = build_object(metadata, canonical_json(payload).encode("utf-8"), "json")
    _, reused = store_object(store, obj)
    # A reused immutable object may have acquired a stale/unknown validity
    # sidecar since it was first stored. Never return it as newly generated.
    return load_object(store, obj["object_id"]), reused


def build_cache_index(store: Path) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    kind_to_query = {kind: query_type for query_type, kind in KIND_FOR_QUERY.items()}
    result: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for index, object_id in enumerate(iter_object_ids(store)):
        if index >= MAX_CACHE_INDEX_OBJECTS:
            break
        try:
            raw = load_object(store, object_id, check_validity=False)
        except EvidenceStoreError:
            continue
        query_type = kind_to_query.get(raw.get("kind"))
        symbol = raw.get("selector", {}).get("symbol")
        if query_type is not None and isinstance(symbol, str):
            result.setdefault((symbol, query_type), []).append(raw)
    return result


def current_object_status(
    obj: Dict[str, Any], repo: Path, state: Dict[str, Any], commit: Optional[str],
) -> str:
    relative = obj.get("repository", {}).get("path")
    normalized = normalize_repo_path(relative) if isinstance(relative, str) else None
    if normalized is None:
        return "permission-denied"
    path = repo / normalized
    if path.is_symlink() or not readable_regular_file(path):
        return "permission-denied" if path.exists() else "stale"
    try:
        path.resolve().relative_to(repo.resolve())
    except (OSError, ValueError):
        return "permission-denied"
    dependencies = obj.get("dependency_hashes", {})
    expected_file_hash = dependencies.get("file_hash")
    if not isinstance(expected_file_hash, str) or file_hash(path) != expected_file_hash:
        return "stale"
    expected_commit = obj.get("repository", {}).get("commit")
    if expected_commit is not None and expected_commit != commit:
        return "stale"
    expected_worktree = dependencies.get("worktree_state_hash")
    if expected_worktree is not None and expected_worktree != state["repository_state_hash"]:
        return "stale"
    return "valid"


def cached_for_slot(
    cache_index: Dict[Tuple[str, str], List[Dict[str, Any]]], store: Path,
    repo: Path, state: Dict[str, Any], commit: Optional[str], symbol: str,
    query_type: str, allowed_paths: Sequence[str], requested_paths: Sequence[str],
) -> Tuple[List[Dict[str, Any]], bool, bool]:
    matches: List[Dict[str, Any]] = []
    stale = False
    denied = False
    for raw in cache_index.get((symbol, query_type), []):
        path = raw.get("repository", {}).get("path")
        if isinstance(path, str) and not path_allowed(path, allowed_paths):
            continue
        if requested_paths and isinstance(path, str) and not any(
            path == item or path.startswith(item.rstrip("/") + "/")
            or fnmatch.fnmatch(path, item) for item in requested_paths
        ):
            continue
        current_status = current_object_status(raw, repo, state, commit)
        if current_status == "permission-denied":
            denied = True
            continue
        if current_status == "stale":
            stale = True
            continue
        try:
            matches.append(load_object(store, raw["object_id"]))
        except EvidenceStoreError:
            stale = True
    matches.sort(key=lambda obj: (
        obj["repository"]["path"] or "",
        obj["selector"]["start_line"] or 0,
        obj["selector"]["end_line"] or 0,
        obj["object_id"],
    ))
    return matches, stale, denied


def generate_for_slot(
    store: Path, repo: Path, state: Dict[str, Any], symbol: str, query_type: str,
    allowed_paths: Sequence[str], requested_paths: Sequence[str], commit: Optional[str],
) -> List[Dict[str, Any]]:
    normal = find_occurrences(repo, symbol, allowed_paths, requested_paths)
    definition = choose_definition(normal, symbol)
    generated: List[Dict[str, Any]] = []

    def add(path: Path, center: int, lines: Sequence[str], payload_extra: Dict[str, Any]) -> None:
        start, end, text = line_slice(lines, center)
        payload = {
            "query_type": query_type,
            "analysis_method": QUALITY_FOR_QUERY[query_type],
            "semantic_guarantee": False,
            "path": path.relative_to(repo).as_posix(),
            "symbol": symbol,
            "start_line": start,
            "end_line": end,
            "text": text,
        }
        payload.update(payload_extra)
        obj, _ = make_object(
            store, repo, state, kind=KIND_FOR_QUERY[query_type], symbol=symbol,
            path=path, start_line=start, end_line=end, payload=payload, commit=commit,
        )
        generated.append(obj)

    if query_type == "definition":
        if definition is not None:
            add(*definition, {"match": "definition"})
    elif query_type == "callers":
        for occurrence in normal:
            if definition is not None and occurrence[0] == definition[0] and occurrence[1] == definition[1]:
                continue
            line = occurrence[2][occurrence[1] - 1]
            _, terminal = symbol_terms(symbol)
            if re.search(rf"\b{re.escape(terminal)}\s*\(", line):
                add(*occurrence, {"match": "caller"})
            if len(generated) >= MAX_MATCHES_PER_SLOT:
                break
    elif query_type == "callees" and definition is not None:
        path, center, lines = definition
        start = max(0, center - 1)
        region = lines[start:min(len(lines), start + MAX_SLICE_LINES)]
        _, terminal = symbol_terms(symbol)
        calls = []
        for line in region:
            for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_:]*)\s*\(", line):
                name = match.group(1)
                if name not in CONTROL_CALLS and name.split("::")[-1] != terminal:
                    calls.append(name)
        calls = sorted(set(calls))[:64]
        if calls:
            add(path, center, lines, {"match": "callee-set", "callees": calls})
    elif query_type == "tests":
        for occurrence in find_occurrences(
            repo, symbol, allowed_paths, requested_paths, tests_only=True,
        )[:MAX_MATCHES_PER_SLOT]:
            add(*occurrence, {"match": "related-test"})
    elif query_type == "build-rules":
        source_names = set()
        if definition is not None:
            source_names.add(definition[0].name)
        _, terminal = symbol_terms(symbol)
        needles = source_names | {terminal}
        for path in iter_repository_files(repo, build_only=True):
            relative = path.relative_to(repo).as_posix()
            if not path_allowed(relative, allowed_paths):
                continue
            if requested_paths and not any(
                relative == item or relative.startswith(item.rstrip("/") + "/")
                or fnmatch.fnmatch(relative, item) for item in requested_paths
            ):
                continue
            lines = read_lines(path)
            if lines is None:
                continue
            for index, line in enumerate(lines, start=1):
                if any(needle in line for needle in needles):
                    add(path, index, lines, {"match": "build-rule"})
                    break
            if len(generated) >= MAX_MATCHES_PER_SLOT:
                break
    generated.sort(key=lambda obj: (
        obj["repository"]["path"] or "",
        obj["selector"]["start_line"] or 0,
        obj["selector"]["end_line"] or 0,
        obj["object_id"],
    ))
    return generated


def priority(query_type: str, role: Optional[str], phase: str) -> int:
    role_orders = {
        "planner": ("definition", "build-rules", "tests", "callers", "callees"),
        "builder": ("definition", "callers", "callees", "build-rules", "tests"),
        "checker": ("tests", "build-rules", "definition", "callers", "callees"),
        "reviewer": ("tests", "callers", "callees", "definition", "build-rules"),
    }
    inferred = "checker" if phase == "verification" else "reviewer" if phase == "review" else "builder"
    order = role_orders[role or inferred]
    return order.index(query_type)


def build_response(
    *, state: Dict[str, Any], request: Dict[str, Any], store: Path, repo: Path,
) -> Dict[str, Any]:
    query = request["query"]
    allowed_paths = state["next_action"]["allowed_paths"]
    requested_paths = query.get("paths", [])
    denied_paths = [
        path for path in requested_paths if not requested_path_allowed(path, allowed_paths)
    ]
    slots = [
        (symbol, query_type) for symbol in query["symbols"] for query_type in query["include"]
    ]
    slots.sort(key=lambda item: (priority(item[1], query.get("role"), state["phase"]), item[0], item[1]))
    candidates: List[Tuple[Dict[str, Any], str, str]] = []
    unresolved: List[Dict[str, str]] = []
    hits = 0
    generated_count = 0
    commit = git_commit(repo)
    cache_index = build_cache_index(store)

    for symbol, query_type in slots:
        if denied_paths:
            unresolved.append({
                "symbol": symbol,
                "query_type": query_type,
                "status": "permission-denied",
                "detail": "requested path is outside state.next_action.allowed_paths: " + ", ".join(denied_paths),
            })
            continue
        cached, stale, denied = cached_for_slot(
            cache_index, store, repo, state, commit, symbol, query_type,
            allowed_paths, requested_paths,
        )
        if cached:
            hits += 1
            candidates.extend((obj, query_type, "hit") for obj in cached)
            continue
        try:
            generated = generate_for_slot(
                store, repo, state, symbol, query_type, allowed_paths, requested_paths, commit,
            )
        except EvidenceStoreError:
            if not stale:
                raise
            generated = []
        if generated:
            generated_count += 1
            candidates.extend((obj, query_type, "generated") for obj in generated)
        else:
            unresolved.append({
                "symbol": symbol,
                "query_type": query_type,
                "status": "permission-denied" if denied else "stale" if stale else "not-found",
                "detail": (
                    "matching cached objects are unreadable under repository permissions" if denied
                    else "matching cached objects are stale" if stale
                    else "no bounded repository evidence matched"
                ),
            })

    max_bytes = query["max_bytes"]
    max_objects = query.get("max_objects", 32)
    used_bytes = 0
    objects: List[Dict[str, Any]] = []
    seen = set()
    for obj, query_type, outcome in candidates:
        if obj["object_id"] in seen:
            continue
        if len(objects) >= max_objects or used_bytes + obj["content_bytes"] > max_bytes:
            unresolved.append({
                "symbol": obj["selector"]["symbol"],
                "query_type": query_type,
                "status": "budget-exceeded",
                "detail": f"object {obj['object_id']} exceeds the remaining response budget",
            })
            continue
        ref = reference_for(obj)
        ref["query_type"] = query_type
        ref["cache_outcome"] = outcome
        ref["evidence_quality"] = QUALITY_FOR_QUERY[query_type]
        objects.append(ref)
        seen.add(obj["object_id"])
        used_bytes += obj["content_bytes"]

    response: Dict[str, Any] = {
        "schema_version": 1,
        "context_id": "",
        "state_id": state["state_id"],
        "requester": request["requester"],
        "intent": query["intent"],
        "objects": objects,
        "unresolved": unresolved,
        "cache": {"requested": len(slots), "hits": hits, "generated": generated_count},
        "budget": {
            "max_bytes": max_bytes, "used_bytes": used_bytes,
            "max_objects": max_objects, "used_objects": len(objects),
        },
    }
    identity = deepcopy(response)
    identity.pop("context_id")
    identity.pop("cache")
    for item in identity["objects"]:
        item.pop("cache_outcome")
    response["context_id"] = sha256_id(identity)
    return response


def request_command(args: argparse.Namespace) -> int:
    state = read_json_bounded(args.state, MAX_STATE_DOCUMENT_BYTES, "Workflow State")
    state_errors = validate_state(state)
    if state_errors:
        raise ContextBrokerError("invalid Workflow State: " + "; ".join(state_errors))
    request = read_json_bounded(args.query, MAX_QUERY_DOCUMENT_BYTES, "Context Query")
    query_errors = validate_query(request)
    if query_errors:
        raise ContextBrokerError("invalid Context Query: " + "; ".join(query_errors))
    if request["state_id"] != state["state_id"]:
        raise ContextBrokerError("Context Query state_id does not match Workflow State")
    response = build_response(state=state, request=request, store=args.store, repo=args.repo.resolve())
    atomic_write_json(args.output, response)
    print(json.dumps({
        "status": "built", "context_id": response["context_id"],
        "output": str(args.output), "objects": len(response["objects"]),
        "unresolved": len(response["unresolved"]), "cache": response["cache"],
        "budget": response["budget"],
    }, sort_keys=True))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    request = sub.add_parser("request")
    request.add_argument("--state", type=Path, required=True)
    request.add_argument("--query", type=Path, required=True)
    request.add_argument("--output", type=Path, required=True)
    request.add_argument("--repo", type=Path, default=Path("."))
    request.add_argument("--store", type=Path, default=Path(".ai-workflow/objects"))
    request.set_defaults(handler=request_command)
    return root


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(argv)
    try:
        if not args.repo.is_dir():
            raise ContextBrokerError(f"repository directory is unreadable: {args.repo}")
        return args.handler(args)
    except (OSError, json.JSONDecodeError, WorkflowStateError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
