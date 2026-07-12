#!/usr/bin/env python3
"""build-review-packet.py — Build a bounded review packet from run artifacts.

Replaces full evidence concatenation with a bounded JSON packet.
Defaults: max_prompt_bytes=200000, max_diff_hunks=40, max_log_tail_lines=120,
          max_artifact_summary_bytes=20000.

Python 3.9+ compatible. No third-party dependencies.

Usage:
    python scripts/build-review-packet.py <run_dir> [--output FILE] [--supplemental FILE ...]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1

DEFAULT_MAX_PROMPT_BYTES = 200_000
DEFAULT_MAX_DIFF_HUNKS = 40
DEFAULT_MAX_LOG_TAIL_LINES = 120
DEFAULT_MAX_ARTIFACT_SUMMARY_BYTES = 20_000

# Binary file detection extensions
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp",
    ".pdf", ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".bin",
    ".woff", ".woff2", ".ttf", ".eot",
    ".pyc", ".pyo", ".class", ".o", ".obj",
}

# Secret/redaction patterns
SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password|credential)\s*[:=]\s*\S+"),
    re.compile(r"(?i)-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----"),
    re.compile(r"(?i)ghp_[A-Za-z0-9]{36}"),  # GitHub personal access token
    re.compile(r"(?i)sk-[A-Za-z0-9]{32,}"),   # OpenAI-style API key
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_text(path: Path, limit: int | None = None) -> str:
    """Read text file with optional byte limit."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if limit is not None and len(text.encode("utf-8")) > limit:
        encoded = text.encode("utf-8")[:limit]
        return encoded.decode("utf-8", errors="replace") + "\n... [truncated]"
    return text


def read_tail(path: Path, max_lines: int) -> str:
    """Read the last N lines of a text file."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = text.splitlines()
    if len(lines) > max_lines:
        return f"... [{len(lines) - max_lines} lines omitted]\n" + "\n".join(lines[-max_lines:])
    return text


def is_binary_path(path: Path) -> bool:
    """Check if a file is likely binary based on extension."""
    return path.suffix.lower() in BINARY_EXTENSIONS


def redact_secrets(text: str) -> str:
    """Replace secret patterns with [REDACTED]."""
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def sha256_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def content_type_for(path: Path) -> str:
    """Guess content type from extension."""
    ext = path.suffix.lower()
    mapping = {
        ".json": "application/json",
        ".md": "text/markdown",
        ".txt": "text/plain",
        ".py": "text/x-python",
        ".sh": "text/x-shellscript",
        ".yaml": "text/yaml",
        ".yml": "text/yaml",
        ".log": "text/plain",
        ".diff": "text/x-diff",
        ".xml": "application/xml",
        ".html": "text/html",
        ".css": "text/css",
        ".js": "text/javascript",
    }
    return mapping.get(ext, "application/octet-stream")


def safe_relative(path: Path, base: Path) -> str:
    """Return a run-relative path string, handling traversal."""
    try:
        rel = path.relative_to(base)
        return str(rel).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


# ---------------------------------------------------------------------------
# Diff parsing
# ---------------------------------------------------------------------------

def parse_diff_hunks(diff_text: str, max_hunks: int) -> List[Dict[str, str]]:
    """Parse a unified diff into bounded hunks.

    Returns list of {file, hunk} dicts, truncated to max_hunks total.
    """
    hunks: List[Dict[str, str]] = []
    current_file = ""
    current_hunk_lines: List[str] = []

    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            # Save previous hunk
            if current_hunk_lines and current_file:
                hunks.append({
                    "file": current_file,
                    "hunk": "\n".join(current_hunk_lines),
                })
                if len(hunks) >= max_hunks:
                    break
            # Extract filename from "diff --git a/path b/path"
            parts = line.split(" b/", 1)
            current_file = parts[1] if len(parts) > 1 else line
            current_hunk_lines = [line]
        elif line.startswith("@@"):
            # New hunk header
            if current_hunk_lines and current_file:
                hunks.append({
                    "file": current_file,
                    "hunk": "\n".join(current_hunk_lines),
                })
                if len(hunks) >= max_hunks:
                    break
            current_hunk_lines = [line]
        else:
            current_hunk_lines.append(line)

    # Don't forget the last hunk
    if current_hunk_lines and current_file and len(hunks) < max_hunks:
        hunks.append({
            "file": current_file,
            "hunk": "\n".join(current_hunk_lines),
        })

    return hunks[:max_hunks]


# ---------------------------------------------------------------------------
# Manifest building
# ---------------------------------------------------------------------------

def build_artifact_manifest(
    run_dir: Path,
    max_summary_bytes: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Build artifact manifest entries and omitted evidence list.

    Returns (manifest_entries, omitted_evidence).
    """
    entries: List[Dict[str, Any]] = []
    omitted: List[Dict[str, str]] = []

    for path in sorted(run_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = safe_relative(path, run_dir)

        # Skip hidden files and directories
        if any(part.startswith(".") for part in path.relative_to(run_dir).parts):
            continue

        # Skip the review-packet itself
        if path.name == "review-packet.json":
            continue

        size = path.stat().st_size
        ct = content_type_for(path)

        if is_binary_path(path):
            entries.append({
                "path": rel,
                "size": size,
                "sha256": sha256_file(path),
                "content_type": ct,
                "producer": "unknown",
                "phase": "unknown",
                "required": False,
            })
            omitted.append({
                "path": rel,
                "reason": "binary file — content not included",
            })
            continue

        entries.append({
            "path": rel,
            "size": size,
            "sha256": sha256_file(path),
            "content_type": ct,
            "producer": "unknown",
            "phase": "unknown",
            "required": False,
        })

    return entries, omitted


# ---------------------------------------------------------------------------
# Packet building
# ---------------------------------------------------------------------------

def build_review_packet(
    run_dir: Path,
    *,
    max_prompt_bytes: int = DEFAULT_MAX_PROMPT_BYTES,
    max_diff_hunks: int = DEFAULT_MAX_DIFF_HUNKS,
    max_log_tail_lines: int = DEFAULT_MAX_LOG_TAIL_LINES,
    max_artifact_summary_bytes: int = DEFAULT_MAX_ARTIFACT_SUMMARY_BYTES,
    supplemental_files: Optional[List[Path]] = None,
) -> Dict[str, Any]:
    """Build a bounded review packet from run directory artifacts."""

    # Find key artifacts
    task_cards = sorted(run_dir.glob("task-card-*.md"))
    diff_files = sorted(run_dir.glob("*.diff"))
    checker_reports = sorted(run_dir.glob("*.checker-report.md"))
    usage_files = sorted(run_dir.glob("*.usage.txt"))
    result_files = sorted(run_dir.glob("*.result.json"))
    report_files = sorted(run_dir.glob("*.report.md"))
    progress_logs = sorted(run_dir.glob("*.progress.log"))

    # Task summary
    task_summary = ""
    if task_cards:
        text = read_text(task_cards[-1], limit=4000)
        task_summary = redact_secrets(text)

    # Active contracts (extract handoff/decision gates from task card)
    active_contracts = ""
    if task_cards:
        text = read_text(task_cards[-1])
        # Extract handoff contract section if present
        for marker in ["## Handoff Contract", "## Decision Gates", "## Execution Phases"]:
            idx = text.find(marker)
            if idx >= 0:
                # Get until next ## or end
                end = text.find("\n## ", idx + len(marker))
                section = text[idx:end if end > 0 else len(text)]
                active_contracts += section + "\n\n"
        if not active_contracts:
            active_contracts = "No explicit contracts found in task card."

    # Changed files from diff
    changed_files: List[Dict[str, str]] = []
    if diff_files:
        diff_text = read_text(diff_files[-1])
        for line in diff_text.splitlines():
            if line.startswith("diff --git"):
                parts = line.split(" b/", 1)
                if len(parts) > 1:
                    changed_files.append({"path": parts[1], "status": "modified"})
            elif line.startswith("new file"):
                pass  # Handled by diff --git line
            elif line.startswith("deleted file"):
                pass

    # Diff hunks
    diff_hunks: List[Dict[str, str]] = []
    if diff_files:
        diff_text = read_text(diff_files[-1])
        diff_hunks = parse_diff_hunks(diff_text, max_diff_hunks)

    # Checker summary
    checker_summary = ""
    if checker_reports:
        checker_summary = redact_secrets(read_tail(checker_reports[-1], max_log_tail_lines))

    # Failures
    failures: List[str] = []
    for report in report_files:
        text = read_text(report, limit=8000)
        if "FAILED" in text or "failed" in text.lower():
            # Extract failure lines
            for line in text.splitlines():
                if "fail" in line.lower() or "error" in line.lower():
                    failures.append(redact_secrets(line.strip()))

    # Artifact manifest
    manifest_entries, omitted = build_artifact_manifest(run_dir, max_artifact_summary_bytes)

    # Add omitted evidence from supplemental files
    if supplemental_files:
        for sf in supplemental_files:
            if sf.exists() and is_binary_path(sf):
                omitted.append({
                    "path": str(sf),
                    "reason": "supplemental binary file",
                })

    # Build packet
    packet = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "task_summary": task_summary[:max_artifact_summary_bytes],
        "active_contracts": active_contracts[:max_artifact_summary_bytes],
        "acceptance_matrix": [],  # Would need to parse from task card
        "changed_files": changed_files,
        "diff_hunks": diff_hunks,
        "checker_summary": checker_summary[:max_artifact_summary_bytes],
        "failures": failures[:50],  # Cap failures
        "artifact_manifest": manifest_entries[:200],  # Cap entries
        "omitted_evidence": omitted,
        "prompt_bytes": 0,  # Will be set after serialization
    }

    # Set prompt_bytes to the size of the serialized packet
    packet_json = json.dumps(packet, indent=2, ensure_ascii=False, sort_keys=True)
    packet["prompt_bytes"] = len(packet_json.encode("utf-8"))

    return packet


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

def render_review_prompt(packet: Dict[str, Any], max_bytes: int = DEFAULT_MAX_PROMPT_BYTES) -> str:
    """Render the review packet into a bounded prompt string.

    Truncates if the rendered prompt exceeds max_bytes.
    """
    lines = [
        "# Review Packet",
        "",
        "## Task Summary",
        packet.get("task_summary", "(none)"),
        "",
        "## Active Contracts",
        packet.get("active_contracts", "(none)"),
        "",
    ]

    # Acceptance matrix
    matrix = packet.get("acceptance_matrix", [])
    if matrix:
        lines.append("## Acceptance Matrix")
        lines.append("")
        lines.append("| ID | Description | Status | Evidence |")
        lines.append("|----|-------------|--------|----------|")
        for item in matrix:
            ev = ", ".join(item.get("evidence", []))
            lines.append(f"| {item.get('id', '?')} | {item.get('description', '')} | {item.get('status', '?')} | {ev} |")
        lines.append("")

    # Changed files
    changed = packet.get("changed_files", [])
    if changed:
        lines.append("## Changed Files")
        lines.append("")
        for f in changed:
            lines.append(f"- [{f.get('status', '?')}] {f.get('path', '?')}")
        lines.append("")

    # Diff hunks
    hunks = packet.get("diff_hunks", [])
    if hunks:
        lines.append("## Diff Hunks (bounded)")
        lines.append("")
        for hunk in hunks:
            lines.append(f"### {hunk.get('file', '?')}")
            lines.append("```diff")
            lines.append(hunk.get("hunk", ""))
            lines.append("```")
        lines.append("")

    # Checker summary
    checker = packet.get("checker_summary", "")
    if checker:
        lines.append("## Checker Summary")
        lines.append("")
        lines.append(checker)
        lines.append("")

    # Failures
    failures = packet.get("failures", [])
    if failures:
        lines.append("## Failures")
        lines.append("")
        for f in failures:
            lines.append(f"- {f}")
        lines.append("")

    # Omitted evidence
    omitted = packet.get("omitted_evidence", [])
    if omitted:
        lines.append("## Omitted Evidence")
        lines.append("")
        for o in omitted:
            lines.append(f"- {o.get('path', '?')}: {o.get('reason', 'unknown')}")
        lines.append("")

    prompt = "\n".join(lines)

    # Truncate if needed
    encoded = prompt.encode("utf-8")
    if len(encoded) > max_bytes:
        truncated = encoded[:max_bytes]
        prompt = truncated.decode("utf-8", errors="replace") + "\n\n... [prompt truncated to fit byte limit]"

    return prompt


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a bounded review packet from run artifacts."
    )
    parser.add_argument("run_dir", help="Run directory containing artifacts.")
    parser.add_argument("--output", help="Output JSON file path. Defaults to <run_dir>/review-packet.json.")
    parser.add_argument("--prompt-output", help="Write rendered prompt to this file.")
    parser.add_argument("--max-prompt-bytes", type=int, default=DEFAULT_MAX_PROMPT_BYTES)
    parser.add_argument("--max-diff-hunks", type=int, default=DEFAULT_MAX_DIFF_HUNKS)
    parser.add_argument("--max-log-tail-lines", type=int, default=DEFAULT_MAX_LOG_TAIL_LINES)
    parser.add_argument("--max-artifact-summary-bytes", type=int, default=DEFAULT_MAX_ARTIFACT_SUMMARY_BYTES)
    parser.add_argument("--supplemental", nargs="*", help="Additional artifact paths to include.")
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir).resolve()
    if not run_dir.is_dir():
        print(f"Error: Run directory not found: {run_dir}", file=sys.stderr)
        return 1

    supplemental = [Path(p) for p in args.supplemental] if args.supplemental else None

    packet = build_review_packet(
        run_dir,
        max_prompt_bytes=args.max_prompt_bytes,
        max_diff_hunks=args.max_diff_hunks,
        max_log_tail_lines=args.max_log_tail_lines,
        max_artifact_summary_bytes=args.max_artifact_summary_bytes,
        supplemental_files=supplemental,
    )

    # Write packet JSON
    output_path = Path(args.output) if args.output else run_dir / "review-packet.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(packet, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # Optionally write rendered prompt
    if args.prompt_output:
        prompt = render_review_prompt(packet, args.max_prompt_bytes)
        prompt_path = Path(args.prompt_output)
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt, encoding="utf-8")
        print(f"Review prompt: {prompt_path}")

    print(f"Review packet: {output_path}")
    print(f"Prompt bytes: {packet['prompt_bytes']}")
    print(f"Diff hunks: {len(packet['diff_hunks'])}")
    print(f"Changed files: {len(packet['changed_files'])}")
    print(f"Omitted evidence: {len(packet['omitted_evidence'])}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
