#!/usr/bin/env python3
"""Check mechanical Claude report claims against an isolated worktree diff.

This helper deliberately does not judge semantic correctness.  It verifies
machine-readable file/symbol/count/cleanliness claims before Codex spends
tokens on semantic review and writes a compact schema-v1 JSON artifact.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

CONTROL_FILES = {
    "TASK_CARD.md", "TASK_CARD_FULL.md", "CLAUDE_TASK_CARD.md",
    "CLAUDE_PROMPT.md", "CLAUDE_PROGRESS.md", "CLAUDE_REPORT.md",
    "ADVISOR_REQUEST.json", "advisor-packet.json", "advisor-packet.md",
    "advisor-decision.json", "advisor-response-validated.json",
    "advisor-call-result.json", "truncation-manifest.json",
}
PATH_LINE = re.compile(r"^claimed_file=(.+)$", re.M)
SYMBOL_LINE = re.compile(r"^claimed_symbol=(.+)$", re.M)
COUNT_LINE = re.compile(r"^claimed_changed_file_count=(\d+)$", re.M)
CLEAN_LINE = re.compile(r"^claimed_no_unexpected_files=(yes|no)$", re.M)


def _git(worktree: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(worktree), *args], text=True, encoding="utf-8",
        errors="replace", capture_output=True, check=False,
    )
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or "git command failed")
    return proc.stdout


def _changed_paths(worktree: Path, base: str) -> Tuple[List[str], List[str]]:
    tracked = set(
        p for p in _git(worktree, "diff", "--name-only", base, "--").splitlines()
        if p and Path(p).name not in CONTROL_FILES
    )
    untracked = set(
        p for p in _git(worktree, "ls-files", "--others", "--exclude-standard").splitlines()
        if p and Path(p).name not in CONTROL_FILES
    )
    return sorted(tracked | untracked), sorted(untracked)


def _diff_text(worktree: Path, base: str, untracked: Iterable[str]) -> str:
    chunks = [_git(worktree, "diff", "--no-ext-diff", base, "--")]
    for path in untracked:
        candidate = worktree / path
        if candidate.is_file():
            chunks.append(candidate.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def verify(report: Path, worktree: Path, base: str = "HEAD") -> Dict[str, object]:
    report_text = report.read_text(encoding="utf-8", errors="replace")
    actual_files, untracked = _changed_paths(worktree, base)
    diff_text = _diff_text(worktree, base, untracked)
    claimed_files = sorted({v.strip().strip("`") for v in PATH_LINE.findall(report_text) if v.strip()})
    claimed_symbols = sorted({v.strip().strip("`") for v in SYMBOL_LINE.findall(report_text) if v.strip()})
    count_match = COUNT_LINE.search(report_text)
    clean_match = CLEAN_LINE.search(report_text)

    checks: List[Dict[str, object]] = []
    conflicts: List[str] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"check": name, "status": status, "detail": detail})
        if status == "conflict":
            conflicts.append(detail)

    if claimed_files:
        missing = sorted(set(claimed_files) - set(actual_files))
        unclaimed = sorted(set(actual_files) - set(claimed_files))
        add("claimed_files", "conflict" if missing or unclaimed else "matched",
            f"missing_from_diff={missing}; unclaimed_actual={unclaimed}")
    else:
        add("claimed_files", "not-claimed", "add one claimed_file=<repo-relative-path> line per implementation file")

    if count_match:
        claimed_count = int(count_match.group(1))
        add("claimed_changed_file_count", "matched" if claimed_count == len(actual_files) else "conflict",
            f"claimed={claimed_count}; actual={len(actual_files)}")
    else:
        add("claimed_changed_file_count", "not-claimed", f"actual={len(actual_files)}")

    for symbol in claimed_symbols:
        add(f"symbol:{symbol}", "matched" if symbol in diff_text else "conflict",
            f"claimed_symbol={symbol}; present_in_diff={'yes' if symbol in diff_text else 'no'}")
    if not claimed_symbols:
        add("claimed_symbols", "not-claimed", "optional; use claimed_symbol=<name> for important wiring claims")

    unexpected_untracked = sorted(set(untracked) - set(claimed_files))
    if clean_match and clean_match.group(1) == "yes":
        add("no_unexpected_files", "matched" if not unexpected_untracked else "conflict",
            f"unexpected_untracked={unexpected_untracked}")
    elif clean_match:
        add("no_unexpected_files", "declared-no", f"unexpected_untracked={unexpected_untracked}")
    else:
        add("no_unexpected_files", "not-claimed", f"unexpected_untracked={unexpected_untracked}")

    claimed = sum(1 for row in checks if row["status"] not in {"not-claimed"})
    status = "conflict" if conflicts else ("matched" if claimed else "insufficient-claims")
    return {
        "schema_version": 1,
        "status": status,
        "semantic_review_required": True,
        "acceptance_satisfied": False,
        "base": base,
        "actual_changed_files": actual_files,
        "checks": checks,
        "conflicts": conflicts,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--base", default="HEAD")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fail-on-conflict", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = verify(args.report.resolve(), args.worktree.resolve(), args.base)
    except (OSError, RuntimeError) as exc:
        result = {"schema_version": 1, "status": "error", "error": str(exc),
                  "semantic_review_required": True, "acceptance_satisfied": False}
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    if result.get("status") == "error":
        return 2
    if args.fail_on_conflict and result.get("status") == "conflict":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
