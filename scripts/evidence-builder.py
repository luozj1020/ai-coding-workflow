#!/usr/bin/env python3
"""Automatic Evidence Builder for ai-coding-workflow.

Discovers dispatch artifacts, computes canonical hashes, and produces a
machine-readable evidence.json consumed by the Review Ladder.

No models are invoked. Python 3.9+ compatible. No third-party dependencies.

Usage:
    python scripts/evidence-builder.py build --task task.json --dispatch-dir run/dispatch --output evidence.json
    aiwf evidence build --task task.json --dispatch-dir run/dispatch --output evidence.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Allow running from repo root or scripts dir
sys.path.insert(0, str(Path(__file__).resolve().parent))
from evidence_hash import content_hash, evidence_hash

# ---------------------------------------------------------------------------
# Artifact discovery
# ---------------------------------------------------------------------------

# Maps artifact key -> (filename, required, is_json)
_ARTIFACT_MANIFEST = [
    ("execution_plan",          "execution-plan.json",       False, True),
    ("context_packet",          "context-packet.json",       False, True),
    ("result_json",             "result.json",               False, True),
    ("diff_output",             "dispatch.stdout",           False, False),
    ("diff_error",              "dispatch.stderr",           False, False),
    ("progress_log",            "dispatch-progress.log",     False, False),
    ("changed_files",           "changed-files.txt",         False, False),
    ("diffstat",                "diffstat.txt",              False, False),
    ("checker_report",          "checker-report.json",       False, True),
    ("checker_log",             "checker-log.txt",           False, False),
    ("runtime_json",            "runtime.json",              False, True),
    ("validation_log",          "validation-log.txt",        False, False),
    ("validation_results",      "validation-results.json",   False, True),
    ("artifact_manifest",       "artifact-manifest.json",    False, True),
    ("remote_ingest",           "remote-ingest.json",        False, True),
    ("quota_ledger",            "quota-ledger.json",         False, True),
    ("model_ledger",            "model-ledger.jsonl",        False, False),
    ("progress_status",         "progress-status.json",      False, True),
    ("dispatch_preview",        "dispatch-preview.json",     False, True),
    ("retry_state",             "retry-state.json",          False, True),
]

# Special discovery: Claude report may live in the worktree, not dispatch dir
_CLAUDE_REPORT_NAMES = ["CLAUDE_REPORT.md"]


def _read_artifact(path: Path, is_json: bool) -> Dict[str, Any]:
    """Read an artifact and return its metadata.

    Returns dict with keys: present, hash, content (if present), error (if absent).
    """
    if not path.exists():
        return {"present": False, "error": f"not found: {path.name}"}
    if not path.is_file():
        return {"present": False, "error": f"not a file: {path.name}"}

    try:
        raw = path.read_bytes()
    except OSError as exc:
        return {"present": False, "error": f"read error: {exc}"}

    result: Dict[str, Any] = {
        "present": True,
        "path": str(path),
        "size_bytes": len(raw),
        "hash": content_hash(raw),
    }

    if is_json:
        try:
            data = json.loads(raw)
            result["json_parsed"] = True
            result["content"] = data
        except (json.JSONDecodeError, UnicodeDecodeError):
            result["json_parsed"] = False
            result["content"] = raw.decode("utf-8", errors="replace")
    else:
        result["content"] = raw.decode("utf-8", errors="replace")

    return result


def discover_artifacts(dispatch_dir: Path) -> Dict[str, Any]:
    """Discover all known artifacts in the dispatch directory.

    Returns a dict mapping artifact key to its read metadata.
    Missing optional artifacts have present=False with an explicit error.
    """
    artifacts: Dict[str, Any] = {}
    for key, filename, required, is_json in _ARTIFACT_MANIFEST:
        path = dispatch_dir / filename
        artifacts[key] = _read_artifact(path, is_json)

    # Also try to find Claude report in the dispatch dir (worktree)
    for name in _CLAUDE_REPORT_NAMES:
        path = dispatch_dir / name
        if path.exists():
            artifacts["claude_report"] = _read_artifact(path, False)
            break

    return artifacts


# ---------------------------------------------------------------------------
# Evidence assembly
# ---------------------------------------------------------------------------

def build_evidence(
    task_path: Path,
    dispatch_dir: Path,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a complete evidence document.

    Reads task metadata, discovers dispatch artifacts, computes canonical
    hashes, and returns the evidence structure.
    """
    task_raw = task_path.read_bytes()
    try:
        task_data = json.loads(task_raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        task_data = {}

    artifacts = discover_artifacts(dispatch_dir)

    # Build per-category hashes from discovered content
    evidence_categories: Dict[str, Any] = {}

    # Task evidence
    if task_data:
        evidence_categories["task_hash"] = evidence_hash(task_data)
    else:
        evidence_categories["task_hash"] = content_hash(task_raw)

    # Context evidence
    ctx = artifacts.get("context_packet", {})
    if ctx.get("present") and ctx.get("json_parsed"):
        evidence_categories["context_hash"] = evidence_hash(ctx["content"])
    elif ctx.get("present"):
        evidence_categories["context_hash"] = ctx["hash"]

    # Failure evidence (stderr)
    stderr_art = artifacts.get("diff_error", {})
    if stderr_art.get("present"):
        evidence_categories["failure_hash"] = stderr_art["hash"]

    # Environment evidence (retry_state contains environment hash)
    retry = artifacts.get("retry_state", {})
    if retry.get("present") and retry.get("json_parsed"):
        evidence_categories["environment_hash"] = evidence_hash(retry["content"])

    # Diff evidence (stdout = dispatch output)
    stdout_art = artifacts.get("diff_output", {})
    if stdout_art.get("present"):
        evidence_categories["diff_hash"] = stdout_art["hash"]

    # Acceptance evidence (validation results)
    validation = artifacts.get("validation_results", {})
    if validation.get("present") and validation.get("json_parsed"):
        evidence_categories["acceptance_hash"] = evidence_hash(validation["content"])
    elif validation.get("present"):
        evidence_categories["acceptance_hash"] = validation["hash"]

    # Review evidence (checker report)
    checker = artifacts.get("checker_report", {})
    if checker.get("present") and checker.get("json_parsed"):
        evidence_categories["review_hash"] = evidence_hash(checker["content"])
    elif checker.get("present"):
        evidence_categories["review_hash"] = checker["hash"]

    # Ledger evidence (model ledger)
    ledger = artifacts.get("model_ledger", {})
    if ledger.get("present"):
        evidence_categories["ledger_hash"] = ledger["hash"]

    # Remote evidence
    remote = artifacts.get("remote_ingest", {})
    if remote.get("present") and remote.get("json_parsed"):
        evidence_categories["remote_hash"] = evidence_hash(remote["content"])

    # Build the full evidence document
    evidence: Dict[str, Any] = {
        "schema_version": 1,
        "task": task_data if task_data else {"raw_hash": content_hash(task_raw)},
        "dispatch_dir": str(dispatch_dir),
        "artifacts": {},
        "evidence_hashes": evidence_categories,
    }

    # Artifact summary (present/missing, hash when present)
    for key, art in artifacts.items():
        summary: Dict[str, Any] = {"present": art.get("present", False)}
        if art.get("present"):
            summary["hash"] = art["hash"]
            summary["size_bytes"] = art.get("size_bytes", 0)
        else:
            summary["error"] = art.get("error", "unknown")
        evidence["artifacts"][key] = summary

    # Include discovered content for key structured artifacts
    for key in ("result_json", "validation_results", "artifact_manifest",
                "remote_ingest", "quota_ledger", "runtime_json",
                "checker_report", "progress_status"):
        art = artifacts.get(key, {})
        if art.get("present") and art.get("json_parsed"):
            evidence[key] = art["content"]

    # Claude report (as string, not JSON)
    claude_rpt = artifacts.get("claude_report", {})
    if claude_rpt.get("present"):
        evidence["claude_report"] = claude_rpt.get("content", "")

    # Include changed files and diffstat if present
    for key in ("changed_files", "diffstat"):
        art = artifacts.get(key, {})
        if art.get("present"):
            evidence[key] = art.get("content", "")

    # Merge extra data (e.g., failure_count, codex_available)
    if extra:
        evidence.update(extra)

    # Deterministic top-level evidence_hash computed over the payload
    # excluding the hash field itself.  Artifact paths are excluded so
    # that identical content at different paths yields the same hash
    # where path identity is not evidence.
    hashable = {
        "schema_version": evidence.get("schema_version"),
        "task": evidence.get("task"),
        "dispatch_dir": evidence.get("dispatch_dir"),
        "artifacts": {
            k: {ik: iv for ik, iv in v.items() if ik != "path"}
            for k, v in evidence.get("artifacts", {}).items()
        },
        "evidence_hashes": evidence.get("evidence_hashes"),
    }
    for key in ("result_json", "validation_results", "artifact_manifest",
                "remote_ingest", "quota_ledger", "runtime_json",
                "checker_report", "progress_status", "claude_report",
                "changed_files", "diffstat"):
        if key in evidence:
            hashable[key] = evidence[key]
    evidence["evidence_hash"] = evidence_hash(hashable)

    return evidence


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_cmd(args: argparse.Namespace) -> int:
    """Execute the 'build' subcommand."""
    task_path = Path(args.task) if args.task else None
    dispatch_dir = Path(args.dispatch_dir)
    output_path = Path(args.output)

    if task_path and not task_path.is_file():
        print(f"Error: task file not found: {task_path}", file=sys.stderr)
        return 1

    if not dispatch_dir.is_dir():
        print(f"Error: dispatch directory not found: {dispatch_dir}", file=sys.stderr)
        return 1

    evidence = build_evidence(task_path, dispatch_dir)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": "built",
        "output": str(output_path),
        "artifacts_found": sum(1 for v in evidence["artifacts"].values() if v.get("present")),
        "artifacts_missing": sum(1 for v in evidence["artifacts"].values() if not v.get("present")),
    }, sort_keys=True))
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="evidence-builder",
        description="Automatic Evidence Builder for ai-coding-workflow.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build_p = sub.add_parser("build", help="Build evidence from dispatch artifacts.")
    build_p.add_argument("--task", default=None, help="Path to task JSON (optional).")
    build_p.add_argument("--dispatch-dir", required=True, help="Dispatch output directory.")
    build_p.add_argument("--output", required=True, help="Output evidence JSON path.")

    args = parser.parse_args(argv)
    if args.command == "build":
        return build_cmd(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
