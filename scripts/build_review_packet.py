"""build_review_packet — Build a bounded review packet from run artifacts.

Replaces full evidence concatenation with a bounded JSON packet.
Defaults: max_prompt_bytes=200000, max_diff_hunks=40, max_log_tail_lines=120,
          max_artifact_summary_bytes=20000.

Python 3.9+ compatible. No third-party dependencies.

Importable module — the CLI entry point lives in build-review-packet.py.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1

DEFAULT_MAX_PROMPT_BYTES = 32_768
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


def parse_acceptance_matrix(path: Path) -> List[Dict[str, Any]]:
    """Extract acceptance criteria from a JSON or Markdown task card."""
    text = read_text(path)
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
            return [{"id": str(item.get("id", f"AC-{i + 1}")),
                     "description": str(item.get("description", "")),
                     "status": "not-evaluated", "evidence": []}
                    for i, item in enumerate(data.get("acceptance", []))
                    if isinstance(item, dict)]
        except (json.JSONDecodeError, AttributeError):
            return []
    section = re.search(r"(?ims)^##\s+Acceptance(?: Criteria)?\s*$\n(.*?)(?=^##\s|\Z)", text)
    if not section:
        return []
    result: List[Dict[str, Any]] = []
    for line in section.group(1).splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if line.lstrip().startswith("|") and len(cells) >= 2 and cells[0].lower() not in {"id", "---", "----"} and not set(cells[0]) <= {"-", ":"}:
            result.append({"id": cells[0], "description": cells[1], "status": "not-evaluated", "evidence": []})
        elif re.match(r"^\s*[-*]\s+", line):
            desc = re.sub(r"^\s*[-*]\s+", "", line).strip()
            result.append({"id": f"AC-{len(result) + 1}", "description": desc, "status": "not-evaluated", "evidence": []})
    return result


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
    hunk_started = False

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
            hunk_started = False
        elif line.startswith("@@"):
            # New hunk header
            # File headers belong to the first hunk. Only flush when a prior
            # @@ hunk has already started.
            if hunk_started and current_hunk_lines and current_file:
                hunks.append({
                    "file": current_file,
                    "hunk": "\n".join(current_hunk_lines),
                })
                if len(hunks) >= max_hunks:
                    break
                current_hunk_lines = []
            current_hunk_lines.append(line)
            hunk_started = True
        else:
            current_hunk_lines.append(line)

    # Don't forget the last hunk
    if current_hunk_lines and current_file and len(hunks) < max_hunks:
        hunks.append({
            "file": current_file,
            "hunk": "\n".join(current_hunk_lines),
        })

    return hunks[:max_hunks]


def build_diff_focus(diff_text: str, hunks: List[Dict[str, str]]) -> Dict[str, Any]:
    """Build a compact semantic index before exposing bounded raw hunks."""
    per_file: Dict[str, Dict[str, int]] = {}
    symbols: List[str] = []
    current = "unknown"
    risk_patterns = {
        "public-contract": re.compile(r"\b(public|export|api|schema|migration)\b", re.I),
        "concurrency": re.compile(r"\b(thread|lock|mutex|atomic|async|concurr)\w*\b", re.I),
        "security-permission": re.compile(r"\b(auth|permission|credential|secret|security)\w*\b", re.I),
        "process-shell": re.compile(r"\b(subprocess|process|shell|timeout|signal|pid)\b", re.I),
    }
    risk_hits: Dict[str, List[str]] = defaultdict(list)
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split(" b/", 1)
            current = parts[1] if len(parts) == 2 else "unknown"
            per_file.setdefault(current, {"added": 0, "deleted": 0})
        elif line.startswith("@@"):
            tail = line.rsplit("@@", 1)[-1].strip()
            if tail and tail not in symbols and len(symbols) < 50:
                symbols.append(tail[:160])
        elif line.startswith("+") and not line.startswith("+++"):
            per_file.setdefault(current, {"added": 0, "deleted": 0})["added"] += 1
            for label, pattern in risk_patterns.items():
                if pattern.search(line) and current not in risk_hits[label]:
                    risk_hits[label].append(current)
        elif line.startswith("-") and not line.startswith("---"):
            per_file.setdefault(current, {"added": 0, "deleted": 0})["deleted"] += 1

    ranked = []
    for index, hunk in enumerate(hunks):
        text = hunk.get("hunk", "")
        score = sum(3 for pattern in risk_patterns.values() if pattern.search(text))
        score += min(5, text.count("+") + text.count("-"))
        ranked.append((score, index, hunk))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    hunks[:] = [item[2] for item in ranked]
    return {
        "files": per_file,
        "symbols": symbols,
        "risk_hits": dict(sorted(risk_hits.items())),
        "review_order": "risk-and-change-density",
    }


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
    task_card: Optional[Path] = None,
    diff_file: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build a bounded review packet from run directory artifacts."""

    # Find key artifacts
    task_cards = [task_card.resolve()] if task_card else sorted(run_dir.glob("task-card-*.md")) + sorted(run_dir.glob("task-card-*.json"))
    diff_files = [diff_file.resolve()] if diff_file else sorted(run_dir.glob("*.diff"))
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
        diff_focus = build_diff_focus(diff_text, diff_hunks)
    else:
        diff_focus = {"files": {}, "symbols": [], "risk_hits": {}, "review_order": "none"}

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
        "acceptance_matrix": parse_acceptance_matrix(task_cards[-1]) if task_cards else [],
        "changed_files": changed_files,
        "diff_hunks": diff_hunks,
        "diff_focus": diff_focus,
        "checker_summary": checker_summary[:max_artifact_summary_bytes],
        "failures": failures[:50],  # Cap failures
        "artifact_manifest": manifest_entries[:200],  # Cap entries
        "omitted_evidence": omitted,
        "prompt_bytes": 0,  # Will be set after serialization
    }

    # prompt_bytes describes the actual bounded model input, not packet storage.
    packet["prompt_bytes"] = len(render_review_prompt(packet, max_prompt_bytes).encode("utf-8"))

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
        "## Required Review Decision JSON",
        "Return exactly one JSON object (raw or in one fenced json block) with: "
        "schema_version=1; decision=accept|revise|split|reject; scope=phase|whole-task; "
        "non-empty reasoning; direction.status=accepted|rejected|needs-revision|not-applicable; "
        "acceptance=[{id,status,evidence}]; validation={status,failed_checks}; next_task; lessons. "
        "For revise/split next_task must contain mode, goal, acceptance. Human prose cannot override JSON.",
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
    focus = packet.get("diff_focus", {})
    if focus.get("files"):
        lines.append("## Diff Focus")
        lines.append("")
        lines.append("Review order: " + str(focus.get("review_order", "unknown")))
        for path, counts in focus.get("files", {}).items():
            lines.append(f"- {path}: +{counts.get('added', 0)} / -{counts.get('deleted', 0)}")
        if focus.get("symbols"):
            lines.append("Symbols/hunk contexts: " + "; ".join(focus["symbols"][:20]))
        for label, paths in focus.get("risk_hits", {}).items():
            lines.append(f"Risk signal {label}: {', '.join(paths)}")
        lines.append("")

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
        suffix = "\n\n... [prompt truncated to fit byte limit]"
        budget = max(0, max_bytes - len(suffix.encode("utf-8")))
        truncated = encoded[:budget]
        prompt = truncated.decode("utf-8", errors="ignore") + suffix

    return prompt
