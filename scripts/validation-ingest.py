#!/usr/bin/env python3
"""Compress remote validation logs into machine-readable evidence.

Classification precedence (PR6):
  1. exit 0 → passed
  2. SHA mismatch → invalid-environment
  3. permission denied → permission
  4. network/connection/proxy/timeout → network
  5. not found/no such package/download failed → dependency
  6. timeout/timed out → timeout
  7. error/compilation failed → compile
  8. FAILED/AssertionError/test.*fail → test
  9. anything else → unknown

Passed results never route to Spark.

Python 3.9+ compatible. No third-party dependencies.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Classification rules (ordered by precedence)
# ---------------------------------------------------------------------------

CLASSIFICATION_RULES = [
    # (name, pattern) — first match wins after exit-code and SHA checks
    ("permission", re.compile(r"permission denied|unauthorized", re.IGNORECASE)),
    ("network", re.compile(r"network|connection|proxy|timed out", re.IGNORECASE)),
    ("dependency", re.compile(r"not found|no such package|download failed", re.IGNORECASE)),
    ("timeout", re.compile(r"timeout|timed out", re.IGNORECASE)),
    ("compile", re.compile(r"error:|compilation failed", re.IGNORECASE)),
    ("test", re.compile(r"FAILED|AssertionError|test.*fail", re.IGNORECASE)),
]


def classify_log(text: str, exit_code: Optional[int], expected_sha: Optional[str]) -> str:
    """Classify a validation log with precedence.

    Returns one of: passed, invalid-environment, permission, network,
    dependency, timeout, compile, test, unknown.
    """
    # 1. Exit 0 → passed (never routes to Spark)
    if exit_code == 0:
        return "passed"

    # 2. SHA mismatch → invalid-environment
    sha_match = re.search(r"\b[0-9a-f]{40,64}\b", text, re.IGNORECASE)
    if expected_sha and sha_match:
        if not sha_match.group(0).startswith(expected_sha):
            return "invalid-environment"

    # 3-8. Classification rules (first match wins)
    for name, pattern in CLASSIFICATION_RULES:
        if pattern.search(text):
            return name

    # 9. Unknown
    return "unknown"


def extract_failed_targets(text: str) -> List[str]:
    """Extract failed Bazel targets from log text."""
    return sorted(set(
        re.findall(r"(?m)^(?://[^\s:]+:[^\s]+).*?(?:FAILED|FAIL)", text)
    ))


def extract_locations(text: str) -> List[str]:
    """Extract file:line locations from log text."""
    return re.findall(r"(?:[A-Za-z]:)?[^\s:]+:\d+(?::\d+)?", text)[:20]


def extract_key_lines(text: str) -> List[str]:
    """Extract lines with error/fail/timeout/denied keywords."""
    return [
        line for line in text.splitlines()
        if re.search(r"error|fail|timeout|denied", line, re.IGNORECASE)
    ][:30]


def main() -> int:
    p = argparse.ArgumentParser(
        description="Compress remote validation logs into machine-readable evidence."
    )
    p.add_argument("log", help="Path to validation log file.")
    p.add_argument("--expected-sha", help="Expected commit SHA for comparison.")
    p.add_argument("--exit-code", type=int, help="Exit code from validation command.")
    a = p.parse_args()

    log_path = Path(a.log)
    if not log_path.exists():
        print(f"Error: Log file not found: {a.log}", file=sys.stderr)
        return 1

    text = log_path.read_text(encoding="utf-8", errors="replace")

    # Extract SHA from log
    sha_match = re.search(r"\b[0-9a-f]{40,64}\b", text, re.IGNORECASE)
    commit_sha = sha_match.group(0) if sha_match else None

    # SHA match check
    sha_matches = True
    if a.expected_sha and sha_match:
        sha_matches = sha_match.group(0).startswith(a.expected_sha)

    # Classify with precedence
    classification = classify_log(text, a.exit_code, a.expected_sha)

    out: Dict[str, Any] = {
        "schema_version": 1,
        "commit_sha": commit_sha,
        "sha_matches": sha_matches,
        "exit_code": a.exit_code,
        "classification": classification,
        "failed_targets": extract_failed_targets(text),
        "locations": extract_locations(text),
        "key_lines": extract_key_lines(text),
    }

    print(json.dumps(out, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
