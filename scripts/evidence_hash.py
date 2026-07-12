#!/usr/bin/env python3
"""Canonical evidence hashing for the ai-coding-workflow control plane.

Provides stable, content-based SHA-256 hashing for all evidence categories:
Task, Context, Failure, Environment, Diff, Acceptance, and Review Evidence.

Hash canonical JSON for structured data or raw bytes for file content.
Never hashes a path string as the evidence value.

Python 3.9+ compatible. No third-party dependencies.

Usage as module:
    from evidence_hash import canonical_json, content_hash, evidence_hash

Usage as CLI:
    python scripts/evidence_hash.py --category task --file task.json
    python scripts/evidence_hash.py --category diff --stdin
    python scripts/evidence_hash.py --canonical-json '{"key":"value"}'
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Optional, Union

# ---------------------------------------------------------------------------
# Canonical JSON
# ---------------------------------------------------------------------------

def canonical_json(data: Any) -> str:
    """Produce canonical JSON: sorted keys, compact separators, no ASCII escaping.

    This is the single canonical JSON implementation used by all control-plane
    hashing.  Sorting keys ensures stability regardless of dict insertion order.
    """
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# ---------------------------------------------------------------------------
# Hashing primitives
# ---------------------------------------------------------------------------

def content_hash(data: Union[str, bytes]) -> str:
    """SHA-256 of raw content. Accepts str (UTF-8 encoded) or bytes."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def evidence_hash(data: Any) -> str:
    """SHA-256 of canonical JSON representation of *data*.

    Use this for structured evidence (dicts, lists).  For raw file content,
    use ``content_hash`` directly on the bytes.
    """
    return content_hash(canonical_json(data))


# ---------------------------------------------------------------------------
# Category-specific hash functions
# ---------------------------------------------------------------------------

VALID_CATEGORIES = frozenset({
    "task", "context", "failure", "environment", "diff", "acceptance", "review",
})


def hash_task(data: Any) -> str:
    """Hash Task evidence (task JSON, routing facts, task card content)."""
    return evidence_hash(data)


def hash_context(data: Any) -> str:
    """Hash Context evidence (context packet, L0/L1/L2 levels, cache metadata)."""
    return evidence_hash(data)


def hash_failure(data: Any) -> str:
    """Hash Failure evidence (failure logs, error output, diagnostic data)."""
    return evidence_hash(data)


def hash_environment(data: Any) -> str:
    """Hash Environment evidence (env config, tool versions, repo state)."""
    return evidence_hash(data)


def hash_diff(data: Any) -> str:
    """Hash Diff evidence (diff content, changed file lists, diffstat).

    If *data* is a Path or str pointing to an existing file, reads the file
    bytes and hashes them directly (content-based, not path-based).
    """
    if isinstance(data, (str, Path)):
        p = Path(data)
        if p.is_file():
            return content_hash(p.read_bytes())
    return evidence_hash(data)


def hash_acceptance(data: Any) -> str:
    """Hash Acceptance evidence (validation results, acceptance criteria)."""
    return evidence_hash(data)


def hash_review(data: Any) -> str:
    """Hash Review evidence (review decisions, tier selections, ladder results)."""
    return evidence_hash(data)


_CATEGORY_FUNCTIONS = {
    "task": hash_task,
    "context": hash_context,
    "failure": hash_failure,
    "environment": hash_environment,
    "diff": hash_diff,
    "acceptance": hash_acceptance,
    "review": hash_review,
}


def hash_by_category(category: str, data: Any) -> str:
    """Hash evidence data by named category.

    Raises ValueError for unknown categories.
    """
    fn = _CATEGORY_FUNCTIONS.get(category)
    if fn is None:
        raise ValueError(f"Unknown evidence category: {category!r}. Valid: {sorted(VALID_CATEGORIES)}")
    return fn(data)


# ---------------------------------------------------------------------------
# File-based convenience
# ---------------------------------------------------------------------------

def hash_file(path: Union[str, Path], as_json: bool = True) -> str:
    """Hash a file's content.

    If *as_json* is True, parses the file as JSON and hashes canonical form.
    If False, hashes raw file bytes.
    """
    p = Path(path)
    raw = p.read_bytes()
    if as_json:
        try:
            data = json.loads(raw)
            return evidence_hash(data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    return content_hash(raw)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Canonical evidence hashing for ai-coding-workflow.",
    )
    parser.add_argument(
        "--category",
        choices=sorted(VALID_CATEGORIES),
        help="Evidence category. Enables category-specific hashing.",
    )
    parser.add_argument(
        "--file",
        type=Path,
        help="Hash contents of this file.",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read evidence from stdin.",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Hash raw bytes instead of canonical JSON.",
    )
    parser.add_argument(
        "--canonical-json",
        help="Hash this literal JSON string as canonical form.",
    )
    parser.add_argument(
        "--show-canonical",
        action="store_true",
        help="Print the canonical JSON before the hash (for debugging).",
    )

    args = parser.parse_args(argv)

    data: Any = None
    source: str = ""

    if args.canonical_json:
        try:
            data = json.loads(args.canonical_json)
        except json.JSONDecodeError:
            data = args.canonical_json
        source = "cli-json"
    elif args.file:
        raw = args.file.read_bytes()
        if args.raw:
            data = raw
            source = f"file-raw:{args.file}"
        else:
            try:
                data = json.loads(raw)
                source = f"file-json:{args.file}"
            except (json.JSONDecodeError, UnicodeDecodeError):
                data = raw
                source = f"file-raw:{args.file}"
    elif args.stdin:
        raw = sys.stdin.buffer.read()
        if args.raw:
            data = raw
            source = "stdin-raw"
        else:
            try:
                data = json.loads(raw)
                source = "stdin-json"
            except (json.JSONDecodeError, UnicodeDecodeError):
                data = raw
                source = "stdin-raw"
    else:
        parser.error("One of --file, --stdin, or --canonical-json is required.")

    if args.show_canonical and not isinstance(data, bytes):
        print(canonical_json(data), file=sys.stderr)

    if args.category:
        h = hash_by_category(args.category, data)
    elif isinstance(data, bytes):
        h = content_hash(data)
    else:
        h = evidence_hash(data)

    print(h)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
