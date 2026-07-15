#!/usr/bin/env python3
"""run-workflow.py — Quota-efficient aiwf run lifecycle.

Implements `aiwf run task.json` as the primary optimized lifecycle:
  1. lint Task
  2. compose profiles
  3. validate composed Task
  4. collect repository facts
  5. route
  6. build/cache context
  7. create/preview execution plan
  8. explicit --execute dispatch through broker-mediated Claude path
  9. automatic Evidence Builder
  10. deterministic acceptance
  11. Review Ladder/recovery
  12. remote handoff requirement or final structured decision
  13. ledger/benchmark metrics

Default is preview: no model call, worktree, remote action, push, merge,
or destructive mutation.  `--execute` is explicit.

Each phase writes a stable artifact under one run directory plus append-only
phase events and an artifact manifest.  On failure, stop with exact
phase/status and preserve prior artifacts.

Python 3.9+ compatible. No third-party dependencies.

Usage:
    python scripts/run-workflow.py task.json
    python scripts/run-workflow.py task.json --execute
    python scripts/run-workflow.py task.json --execute --dispatcher /path/to/dispatch
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from evidence_hash import content_hash, evidence_hash
from event_writer import EventWriter, build_event

# ---------------------------------------------------------------------------
# Module loading
# ---------------------------------------------------------------------------

def _load_module(name: str, filename: str):
    """Load a sibling script as a module."""
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


task_schema = _load_module("task_schema", "task_schema.py")
route_task = _load_module("route_task", "route-task.py")
workflow_economics = _load_module("workflow_economics", "workflow_economics.py")

# ---------------------------------------------------------------------------
# Phase definitions
# ---------------------------------------------------------------------------

PHASES = [
    "lint",
    "compose",
    "validate",
    "facts",
    "route",
    "context",
    "plan",
    "dispatch",
    "evidence",
    "acceptance",
    "review-ladder",
    "handoff",
    "ledger",
]


# ---------------------------------------------------------------------------
# Run directory and artifact management
# ---------------------------------------------------------------------------

def create_run_dir(base: Path, task_id: str) -> Path:
    """Create a timestamped run directory."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_id = f"run-{task_id}-{ts}"
    run_dir = base / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def write_artifact(path: Path, data: Any, is_json: bool = True) -> Dict[str, Any]:
    """Write an artifact file and return manifest entry metadata."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if is_json:
        content = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        path.write_text(content, encoding="utf-8")
    else:
        if isinstance(data, bytes):
            path.write_bytes(data)
            return {
                "path": str(path),
                "size": len(data),
                "sha256": content_hash(data),
            }
        content = str(data)
        path.write_text(content, encoding="utf-8")
    raw = path.read_bytes()
    return {
        "path": str(path),
        "size": len(raw),
        "sha256": content_hash(raw),
    }


def update_manifest(manifest_path: Path, run_id: str, entries: List[Dict[str, Any]]) -> None:
    """Write or update the artifact manifest."""
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "entries": entries,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Phase result tracking
# ---------------------------------------------------------------------------

class PhaseError(Exception):
    """Raised when a phase fails. Carries the phase name and status."""
    def __init__(self, phase: str, status: str, message: str):
        self.phase = phase
        self.status = status
        super().__init__(f"Phase '{phase}' {status}: {message}")


class RunContext:
    """Holds state for a single run lifecycle."""

    def __init__(
        self,
        task_path: Path,
        run_dir: Path,
        execute: bool = False,
        profiles_dir: Optional[Path] = None,
        repo: Optional[Path] = None,
        dispatcher: Optional[str] = None,
    ):
        self.task_path = task_path
        self.run_dir = run_dir
        self.execute = execute
        self.profiles_dir = profiles_dir
        self.repo = repo or _find_repo_root(task_path)
        self.dispatcher = dispatcher

        self.run_id = run_dir.name
        self.manifest_entries: List[Dict[str, Any]] = []
        self.artifact_manifest_path = run_dir / "artifact-manifest.json"
        self.events_path = run_dir / "run-events.jsonl"
        self.result_path = run_dir / "result.json"
        self.event_writer = EventWriter(self.events_path)

        self.task_data: Optional[Dict[str, Any]] = None
        self.composed: Optional[Dict[str, Any]] = None
        self.facts: Optional[Dict[str, Any]] = None
        self.routing: Optional[Dict[str, Any]] = None
        self.execution_plan: Optional[Dict[str, Any]] = None
        self.evidence: Optional[Dict[str, Any]] = None
        self.acceptance: Optional[Dict[str, Any]] = None
        self.ladder: Optional[Dict[str, Any]] = None
        self.handoff: Optional[Dict[str, Any]] = None

        self.phase_timings: Dict[str, float] = {}
        self.model_calls: List[Dict[str, Any]] = []
        self.phase_order: List[str] = []
        self.stop_after_dispatch = False

    def emit_event(self, event_name: str, phase: str, detail: Optional[Dict[str, Any]] = None) -> None:
        """Emit a phase event to the append-only event log."""
        ev = build_event(
            run_id=self.run_id,
            task_id=self.task_data.get("id", "") if self.task_data else "",
            event=event_name,
            phase=phase,
            role="system",
            detail=detail or {},
        )
        self.event_writer.append(ev)

    def record_artifact(self, path: Path, is_json: bool = True) -> None:
        """Record an artifact in the manifest."""
        if path.exists():
            raw = path.read_bytes()
            try:
                manifest_path = str(path.resolve().relative_to(self.run_dir.resolve()))
            except ValueError:
                manifest_path = path.name
            entry = {
                "path": manifest_path,
                "size": len(raw),
                "sha256": content_hash(raw),
                "content_type": "application/json" if is_json else "text/plain",
                "producer": "run-workflow",
                "phase": self.phase_order[-1] if self.phase_order else "setup",
                "required": False,
            }
            self.manifest_entries.append(entry)
            update_manifest(self.artifact_manifest_path, self.run_id, self.manifest_entries)

    def write_result(self, status: str, failed_phase: Optional[str] = None, error: Optional[str] = None) -> None:
        """Write the final result.json."""
        result = {
            "schema_version": 1,
            "run_id": self.run_id,
            "task_id": self.task_data.get("id", "") if self.task_data else "",
            "goal": self.task_data.get("goal", "") if self.task_data else "",
            "status": status,
            "lane": self.routing.get("lane") if self.routing else None,
            "execution_plan": _safe_path(self.run_dir / "execution-plan.json"),
            "model_calls": self.model_calls,
            "acceptance_status": self.acceptance.get("status") if self.acceptance else None,
            "review_tier": self.ladder.get("tier") if self.ladder else None,
            "review_owner": self.ladder.get("action") if self.ladder else None,
            "remote_required": self.handoff.get("remote_required") if self.handoff else False,
            "final_decision": self._final_decision(status),
            "phase_timings": self.phase_timings,
            "phases_completed": list(self.phase_order),
            "failed_phase": failed_phase,
            "error": error,
            "artifact_manifest": str(self.artifact_manifest_path),
            "events": str(self.events_path),
            "run_dir": str(self.run_dir),
        }
        self.result_path.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _final_decision(self, status: str) -> str:
        """Determine the final decision string."""
        if status == "completed":
            if self.ladder and self.ladder.get("tier") == "L0-local":
                if self.acceptance and self.acceptance.get("status") == "passed":
                    return "accept"
                return "human-review"
            return "escalate"
        if status == "routed":
            return "codex-fast-path"
        return "failed"


def _safe_path(p: Path) -> Optional[str]:
    """Return path string if file exists, else None."""
    return str(p) if p.exists() else None


def _find_repo_root(task_path: Path) -> Path:
    """Find git repo root from task path or cwd."""
    for candidate in [task_path.resolve().parent, Path.cwd()]:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=str(candidate),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode == 0:
                return Path(result.stdout.strip())
        except (FileNotFoundError, OSError):
            pass
    return task_path.resolve().parent


# ---------------------------------------------------------------------------
# Phase implementations
# ---------------------------------------------------------------------------

def phase_lint(ctx: RunContext) -> None:
    """Phase 1: Lint task JSON."""
    ctx.phase_order.append("lint")
    start = time.monotonic()

    try:
        ctx.task_data = task_schema.load_task_json(str(ctx.task_path))
    except task_schema.ValidationError as exc:
        raise PhaseError("lint", "failed", str(exc))

    errors = task_schema.validate_task(ctx.task_data)
    if errors:
        raise PhaseError("lint", "failed", "; ".join(errors))

    lint_result = {"valid": True, "task_id": ctx.task_data.get("id", ""), "issues": []}
    out = ctx.run_dir / "lint-result.json"
    write_artifact(out, lint_result)
    ctx.record_artifact(out)
    ctx.phase_timings["lint"] = time.monotonic() - start
    ctx.emit_event("lint_complete", "setup", {"task_id": ctx.task_data.get("id", "")})


def phase_compose(ctx: RunContext) -> None:
    """Phase 2: Compose profiles."""
    ctx.phase_order.append("compose")
    start = time.monotonic()

    profiles_dir = ctx.profiles_dir or task_schema.find_default_profiles_dir()
    try:
        ctx.composed = task_schema.compose_profiles(
            ctx.task_data.get("profiles", []), profiles_dir, ctx.task_data
        )
    except (task_schema.ProfileLoadError, task_schema.ProfileConflictError) as exc:
        raise PhaseError("compose", "failed", str(exc))

    out = ctx.run_dir / "composed-task.json"
    write_artifact(out, ctx.composed)
    ctx.record_artifact(out)
    ctx.phase_timings["compose"] = time.monotonic() - start
    ctx.emit_event("compose_complete", "setup", {
        "profiles": ctx.task_data.get("profiles", []),
    })


def phase_validate(ctx: RunContext) -> None:
    """Phase 3: Validate composed task."""
    ctx.phase_order.append("validate")
    start = time.monotonic()

    errors = task_schema.validate_task(ctx.composed)
    if errors:
        raise PhaseError("validate", "failed", "; ".join(errors))

    out = ctx.run_dir / "validation-result.json"
    write_artifact(out, {"valid": True, "errors": []})
    ctx.record_artifact(out)
    ctx.phase_timings["validate"] = time.monotonic() - start
    ctx.emit_event("validate_complete", "setup")


def phase_facts(ctx: RunContext) -> None:
    """Phase 4: Collect repository facts."""
    ctx.phase_order.append("facts")
    start = time.monotonic()

    # Write composed task to temp for collect_facts input
    composed_path = ctx.run_dir / "composed-task-input.json"
    composed_path.write_text(json.dumps(ctx.composed, sort_keys=True), encoding="utf-8")

    try:
        collect_facts = _load_module("collect_task_facts", "collect-task-facts.py")
        extensions = ctx.composed.get("extensions", {})
        routing_hints = extensions.get("routing_hints", {}) if isinstance(extensions, dict) else {}
        hints_path = ctx.run_dir / "routing-hints.json"
        hints_path.write_text(
            json.dumps(routing_hints if isinstance(routing_hints, dict) else {}, sort_keys=True),
            encoding="utf-8",
        )
        ctx.facts = collect_facts.collect_facts(
            composed_path,
            hints_path=hints_path,
            profiles_dir=ctx.profiles_dir,
            repo=ctx.repo,
        )
    except Exception as exc:
        raise PhaseError("facts", "failed", str(exc))

    out = ctx.run_dir / "routing-facts.json"
    write_artifact(out, ctx.facts)
    ctx.record_artifact(out)
    ctx.phase_timings["facts"] = time.monotonic() - start
    ctx.emit_event("facts_complete", "setup", {
        "target_files_count": ctx.facts.get("target_files_count", 0),
        "repository_size": ctx.facts.get("repository_size", "unknown"),
    })


def phase_route(ctx: RunContext) -> None:
    """Phase 5: Route task."""
    ctx.phase_order.append("route")
    start = time.monotonic()

    ctx.routing = route_task.route(ctx.facts)

    out = ctx.run_dir / "routing-decision.json"
    write_artifact(out, ctx.routing)
    ctx.record_artifact(out)
    ctx.phase_timings["route"] = time.monotonic() - start
    ctx.emit_event("route_complete", "setup", {
        "lane": ctx.routing.get("lane"),
        "single_pass_allowed": ctx.routing.get("execution", {}).get("single_pass_allowed", False),
    })


def phase_context(ctx: RunContext) -> None:
    """Phase 6: Build/cache context."""
    ctx.phase_order.append("context")
    start = time.monotonic()

    # Build a context packet from facts
    context_packet = {
        "schema_version": 1,
        "task_id": ctx.facts.get("task_id", ""),
        "goal": ctx.facts.get("goal", ""),
        "L0": {
            "files": ctx.facts.get("target_files", []),
            "symbols": [],
            "targets": [],
        },
        "L1": {
            "snippets": [],
            "call_paths": [],
            "constraints": [],
        },
        "L2": {"enabled": False, "full_files": []},
        "forbidden_paths": ctx.composed.get("scope", {}).get("forbidden_paths", []),
        "validation": [v.get("command", "") for v in ctx.composed.get("validation", [])],
        "acceptance": [
            {"id": a.get("id", ""), "description": a.get("description", "")}
            for a in ctx.composed.get("acceptance", [])
        ],
    }

    out = ctx.run_dir / "context-packet.json"
    write_artifact(out, context_packet)
    ctx.record_artifact(out)

    # Also materialize CLAUDE_CONTEXT_PACKET.md
    dispatch_efficient = _load_module("dispatch_efficient", "dispatch-efficient.py")
    md_content = dispatch_efficient._render_context_packet_md(context_packet)
    md_path = ctx.run_dir / "CLAUDE_CONTEXT_PACKET.md"
    md_path.write_text(md_content, encoding="utf-8")

    ctx.phase_timings["context"] = time.monotonic() - start
    ctx.emit_event("context_complete", "setup")


def phase_plan(ctx: RunContext) -> None:
    """Phase 7: Create/preview execution plan."""
    ctx.phase_order.append("plan")
    start = time.monotonic()

    lane = ctx.routing.get("lane", "standard")
    budget = ctx.routing.get("budget", {})
    execution = ctx.routing.get("execution", {})

    ctx.execution_plan = {
        "schema_version": 1,
        "task_id": ctx.facts.get("task_id", ""),
        "lane": lane,
        "budget": {
            "codex_calls": budget.get("codex_calls", 0),
            "claude_calls": budget.get("claude_calls", 1),
            "spark_calls": budget.get("spark_calls", 0),
        },
        "review": {
            "reserved_for": budget.get("codex_reserved_for", []),
            "milestones": [],
        },
        "execution": {
            "owner": execution.get("owner", "claude-builder"),
            "owner_source": execution.get("owner_source", "compatibility-default"),
            "builder_checker_split": execution.get("builder_checker_split", False),
            "checker_model_dispatch": execution.get("checker_model_dispatch", False),
            "checker_value_reasons": execution.get("checker_value_reasons", []),
            "checker_skip_reason": execution.get("checker_skip_reason"),
            "single_pass_allowed": execution.get("single_pass_allowed", False),
            "single_pass_reason": execution.get("single_pass_reason", ""),
            "remote_rounds": execution.get("remote_rounds", 1),
        },
        "context_packet": str(ctx.run_dir / "context-packet.json"),
        "composed_task": str(ctx.run_dir / "composed-task.json"),
    }

    out = ctx.run_dir / "execution-plan.json"
    write_artifact(out, ctx.execution_plan)
    ctx.record_artifact(out)
    ctx.phase_timings["plan"] = time.monotonic() - start
    ctx.emit_event("plan_complete", "setup", {
        "lane": lane,
        "single_pass_allowed": execution.get("single_pass_allowed", False),
    })


def phase_dispatch(ctx: RunContext) -> None:
    """Phase 8: Dispatch to Claude (--execute only)."""
    ctx.phase_order.append("dispatch")

    execution = ctx.execution_plan.get("execution", {})
    if execution.get("owner") == "codex-fast-path":
        decision = {
            "schema_version": 1,
            "task_id": ctx.facts.get("task_id", ""),
            "action": "codex-fast-path",
            "claude_dispatched": False,
            "reason": "shared economic router selected Codex before Claude start",
            "owner_source": execution.get("owner_source"),
        }
        out = ctx.run_dir / "dispatch-decision.json"
        write_artifact(out, decision)
        ctx.record_artifact(out)
        ctx.phase_timings["dispatch"] = 0.0
        ctx.stop_after_dispatch = True
        ctx.emit_event("dispatch_routed_to_codex", "dispatch", decision)
        return

    if not ctx.execute:
        # Preview mode: write dispatch preview, skip execution
        preview = {
            "mode": "preview",
            "task_id": ctx.facts.get("task_id", ""),
            "lane": ctx.routing.get("lane", "standard"),
            "dispatch_card": str(ctx.task_path),
            "single_pass": ctx.execution_plan.get("execution", {}).get("single_pass_allowed", False),
            "execute": False,
            "message": "Preview mode. Use --execute to dispatch.",
        }
        out = ctx.run_dir / "dispatch-preview.json"
        write_artifact(out, preview)
        ctx.record_artifact(out)
        ctx.phase_timings["dispatch"] = 0.0
        ctx.emit_event("dispatch_preview", "dispatch", {"mode": "preview"})
        return

    start = time.monotonic()

    # Prepare dispatch card (may be modified for single-pass)
    dispatch_card = ctx.task_path
    if ctx.execution_plan.get("execution", {}).get("single_pass_allowed"):
        text = ctx.task_path.read_text(encoding="utf-8")
        import re
        text = re.sub(
            r"(?im)^\|\s*Mode\s*\|\s*builder\s*\|",
            "| Mode | mixed-exception |",
            text,
            count=1,
        )
        text += "\n## Mixed Exception\nExpress Lane authorizes implementation plus exact narrow validation only.\n"
        dispatch_card = ctx.run_dir / "single-pass-task-card.md"
        dispatch_card.write_text(text, encoding="utf-8")

    # Write dispatch preview for audit
    preview = {
        "mode": "execute",
        "task_id": ctx.facts.get("task_id", ""),
        "lane": ctx.routing.get("lane", "standard"),
        "dispatch_card": str(dispatch_card),
        "single_pass": ctx.execution_plan.get("execution", {}).get("single_pass_allowed", False),
        "execute": True,
    }
    preview_path = ctx.run_dir / "dispatch-preview.json"
    write_artifact(preview_path, preview)
    ctx.record_artifact(preview_path)

    # Execute through broker-mediated path
    dispatcher = ctx.dispatcher or str(HERE / "dispatch-to-claude.sh")
    cmd = ["bash", dispatcher, str(dispatch_card)]

    stdout_path = ctx.run_dir / "dispatch.stdout"
    stderr_path = ctx.run_dir / "dispatch.stderr"

    try:
        with open(stdout_path, "wb") as out_f, open(stderr_path, "wb") as err_f:
            proc = subprocess.run(
                cmd,
                stdout=out_f,
                stderr=err_f,
                cwd=str(ctx.repo),
            )
        exit_code = proc.returncode
    except Exception as exc:
        raise PhaseError("dispatch", "failed", str(exc))

    ctx.model_calls.append({
        "role": "claude",
        "stage": "builder",
        "exit_code": exit_code,
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
    })

    ctx.record_artifact(stdout_path, is_json=False)
    ctx.record_artifact(stderr_path, is_json=False)

    ctx.phase_timings["dispatch"] = time.monotonic() - start
    ctx.emit_event("dispatch_complete", "dispatch", {"exit_code": exit_code})

    if exit_code != 0:
        raise PhaseError("dispatch", "failed", f"exit code {exit_code}")


def phase_evidence(ctx: RunContext) -> None:
    """Phase 9: Build evidence."""
    ctx.phase_order.append("evidence")
    start = time.monotonic()

    evidence_builder = _load_module("evidence_builder", "evidence-builder.py")
    ctx.evidence = evidence_builder.build_evidence(
        ctx.task_path,
        ctx.run_dir,
        extra={"run_id": ctx.run_id, "lane": ctx.routing.get("lane", "standard")},
    )

    out = ctx.run_dir / "evidence.json"
    write_artifact(out, ctx.evidence)
    ctx.record_artifact(out)
    ctx.phase_timings["evidence"] = time.monotonic() - start
    ctx.emit_event("evidence_complete", "review")


def phase_acceptance(ctx: RunContext) -> None:
    """Phase 10: Deterministic acceptance."""
    ctx.phase_order.append("acceptance")
    start = time.monotonic()

    evaluate_acceptance = _load_module("evaluate_acceptance", "evaluate-acceptance.py")

    # Build validation results from evidence
    validation_results = {}
    if ctx.evidence and ctx.evidence.get("validation_results"):
        vr = ctx.evidence["validation_results"]
        if isinstance(vr, dict) and vr.get("json_parsed"):
            validation_results = vr.get("content", {})

    # Build diff evidence
    diff_evidence = {}
    if ctx.evidence:
        diff_evidence = {
            "changed_files": ctx.evidence.get("changed_files", "").splitlines() if isinstance(ctx.evidence.get("changed_files"), str) else [],
        }

    ctx.acceptance = evaluate_acceptance.evaluate_task(
        task=ctx.composed,
        validation_results=validation_results,
        artifact_manifest=ctx.evidence.get("artifact_manifest") if ctx.evidence else None,
        diff_evidence=diff_evidence,
    )

    out = ctx.run_dir / "acceptance-result.json"
    write_artifact(out, ctx.acceptance)
    ctx.record_artifact(out)
    ctx.phase_timings["acceptance"] = time.monotonic() - start
    ctx.emit_event("acceptance_complete", "review", {
        "status": ctx.acceptance.get("status"),
        "mechanical_failures": ctx.acceptance.get("mechanical_failures", []),
    })


def phase_review_ladder(ctx: RunContext) -> None:
    """Phase 11: Review Ladder/recovery."""
    ctx.phase_order.append("review-ladder")
    start = time.monotonic()

    review_ladder = _load_module("review_ladder", "review-ladder.py")

    assured = ctx.routing.get("lane") == "assured"
    high_risk = any(
        ctx.facts.get("effective_risks", {}).get(k) == "yes"
        for k in ("security", "public_api")
    ) if ctx.facts else False

    ctx.ladder = review_ladder.evaluate_ladder(
        task=ctx.composed,
        validation_results=None,
        artifact_manifest=ctx.evidence.get("artifact_manifest") if ctx.evidence else None,
        diff_evidence={},
        assured=assured,
        high_risk=high_risk,
    )

    out = ctx.run_dir / "review-ladder-result.json"
    write_artifact(out, ctx.ladder)
    ctx.record_artifact(out)
    ctx.phase_timings["review-ladder"] = time.monotonic() - start
    ctx.emit_event("review_ladder_complete", "review", {
        "tier": ctx.ladder.get("tier"),
        "action": ctx.ladder.get("action"),
    })


def phase_handoff(ctx: RunContext) -> None:
    """Phase 12: Remote handoff or final decision."""
    ctx.phase_order.append("handoff")
    start = time.monotonic()

    remote_required = False
    ext = ctx.composed.get("extensions", {})
    if isinstance(ext, dict):
        remote_ext = ext.get("remote_validation", {})
        if isinstance(remote_ext, dict) and remote_ext.get("automation") == "preview":
            remote_required = True
        cpp_ext = ext.get("cpp_bazel", {})
        if isinstance(cpp_ext, dict) and cpp_ext.get("allow_remote_required"):
            remote_required = True

    ctx.handoff = {
        "schema_version": 1,
        "remote_required": remote_required,
        "review_tier": ctx.ladder.get("tier") if ctx.ladder else None,
        "review_action": ctx.ladder.get("action") if ctx.ladder else None,
        "acceptance_status": ctx.acceptance.get("status") if ctx.acceptance else None,
        "decision": ctx._final_decision("completed"),
    }

    out = ctx.run_dir / "handoff-decision.json"
    write_artifact(out, ctx.handoff)
    ctx.record_artifact(out)
    ctx.phase_timings["handoff"] = time.monotonic() - start
    ctx.emit_event("handoff_complete", "decision", {
        "remote_required": remote_required,
        "decision": ctx.handoff["decision"],
    })


def phase_ledger(ctx: RunContext) -> None:
    """Phase 13: Ledger/benchmark metrics."""
    ctx.phase_order.append("ledger")
    start = time.monotonic()

    total_time = sum(ctx.phase_timings.values())

    metrics = {
        "schema_version": 1,
        "run_id": ctx.run_id,
        "task_id": ctx.facts.get("task_id", "") if ctx.facts else "",
        "lane": ctx.routing.get("lane") if ctx.routing else None,
        "total_elapsed_seconds": round(total_time, 3),
        "phase_timings": {k: round(v, 3) for k, v in ctx.phase_timings.items()},
        "model_calls_count": len(ctx.model_calls),
        "model_calls": ctx.model_calls,
        "model_calls_by_role": {
            role: sum(1 for call in ctx.model_calls if call.get("role") == role)
            for role in ("codex", "claude", "spark")
        },
        "execution_owner": ctx.routing.get("execution", {}).get("owner") if ctx.routing else None,
        "execution_owner_source": ctx.routing.get("execution", {}).get("owner_source") if ctx.routing else None,
        "checker_model_dispatched": ctx.routing.get("execution", {}).get("checker_model_dispatch", False) if ctx.routing else False,
        "checker_skip_reason": ctx.routing.get("execution", {}).get("checker_skip_reason") if ctx.routing else None,
        "task_card_bytes": ctx.task_path.stat().st_size if ctx.task_path.is_file() else None,
        "control_plane_seconds": round(sum(
            ctx.phase_timings.get(name, 0.0)
            for name in ("lint", "compose", "validate", "facts", "route", "context", "plan")
        ), 3),
        "acceptance_status": ctx.acceptance.get("status") if ctx.acceptance else None,
        "review_tier": ctx.ladder.get("tier") if ctx.ladder else None,
        "remote_required": ctx.handoff.get("remote_required") if ctx.handoff else False,
        "artifact_count": len(ctx.manifest_entries),
        "express_zero_codex": (
            ctx.routing.get("lane") == "express"
            and all(mc.get("role") != "codex" for mc in ctx.model_calls)
        ),
    }

    out = ctx.run_dir / "run-metrics.json"
    write_artifact(out, metrics)
    ctx.record_artifact(out)

    accepted = bool(
        ctx.execute
        and ctx.acceptance
        and ctx.acceptance.get("status") == "passed"
        and ctx._final_decision("completed") == "accept"
    )
    repository = ctx.facts.get("repository", {}) if ctx.facts else {}
    calls_by_role = metrics["model_calls_by_role"]
    economics = {
        "schema_version": 1,
        "run_id": ctx.run_id,
        "task_id": metrics["task_id"],
        "task_type": ctx.facts.get("task_type", "unknown") if ctx.facts else "unknown",
        "repository_scale": (
            repository.get("routing_scale", "unknown")
            if isinstance(repository, dict) else "unknown"
        ),
        "owner": metrics["execution_owner"],
        "accepted": accepted,
        "first_pass": accepted and calls_by_role.get("claude", 0) <= 1,
        "codex_takeover": False,
        "claude_reuse_ratio": None,
        "diff_reuse": {},
        "reuse_evidence_available": False,
        "reuse_unavailable_reason": "claude-and-final-diff-not-both-bound",
        "model_calls": calls_by_role,
        "task_card_bytes": metrics["task_card_bytes"],
        "review_packet_bytes": None,
        "worktree_setup_seconds": metrics["phase_timings"].get("dispatch"),
        "total_elapsed_seconds": metrics["total_elapsed_seconds"],
        "control_plane_seconds": metrics["control_plane_seconds"],
        "checker_model_dispatched": metrics["checker_model_dispatched"],
    }
    economics_path = ctx.run_dir / "workflow-economics.json"
    history_path = ctx.repo / ".ai-workflow" / "economics-history.jsonl"
    economics["history_appended"] = workflow_economics.append_history_once(
        history_path, economics
    )
    write_artifact(economics_path, economics)
    ctx.record_artifact(economics_path)
    ctx.phase_timings["ledger"] = time.monotonic() - start
    ctx.emit_event("run_complete", "finalization", {
        "total_elapsed_seconds": round(total_time, 3),
        "model_calls_count": len(ctx.model_calls),
    })


# ---------------------------------------------------------------------------
# Main lifecycle
# ---------------------------------------------------------------------------

def run_lifecycle(
    task_path: Path,
    execute: bool = False,
    profiles_dir: Optional[Path] = None,
    repo: Optional[Path] = None,
    run_dir_base: Optional[Path] = None,
    dispatcher: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the full 13-phase lifecycle.

    Returns the result dict.
    """
    # Resolve paths
    task_path = task_path.resolve()
    if not task_path.is_file():
        return {"status": "failed", "error": f"Task file not found: {task_path}"}

    repo_root = repo or _find_repo_root(task_path)
    base = run_dir_base or repo_root / ".worktrees"

    # Safely derive a non-empty task id from Task JSON before emitting run_start.
    # Valid tasks use their id; missing/malformed tasks use a stable provisional id
    # and then fail normally in lint, preserving events/result/manifest.
    task_id_from_json = None
    try:
        raw = json.loads(task_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and raw.get("id"):
            task_id_from_json = raw["id"]
        task_id = task_id_from_json or task_path.stem
    except (json.JSONDecodeError, OSError):
        task_id = task_path.stem

    run_dir = create_run_dir(base, task_id)
    ctx = RunContext(
        task_path=task_path,
        run_dir=run_dir,
        execute=execute,
        profiles_dir=profiles_dir,
        repo=repo_root,
        dispatcher=dispatcher,
    )

    # Set provisional task_data so emit_event can derive a non-empty task_id
    # before phase_lint loads the full task.  lint will overwrite this.
    if task_id_from_json:
        ctx.task_data = {"id": task_id_from_json}
    else:
        ctx.task_data = {"id": "provisional-" + task_path.stem}

    # Write initial manifest
    update_manifest(ctx.artifact_manifest_path, ctx.run_id, [])
    ctx.emit_event("run_start", "setup", {
        "task_path": str(task_path),
        "execute": execute,
    })

    # Execute phases in order; stop on first failure
    phase_funcs = [
        phase_lint,
        phase_compose,
        phase_validate,
        phase_facts,
        phase_route,
        phase_context,
        phase_plan,
        phase_dispatch,
        phase_evidence,
        phase_acceptance,
        phase_review_ladder,
        phase_handoff,
        phase_ledger,
    ]

    failed_phase = None
    error_msg = None
    status = "completed"

    for phase_func in phase_funcs:
        try:
            phase_func(ctx)
        except PhaseError as exc:
            failed_phase = exc.phase
            error_msg = str(exc)
            status = "failed"
            ctx.emit_event(f"{exc.phase}_failed", _phase_to_event_phase(exc.phase), {
                "status": exc.status,
                "error": error_msg,
            })
            break
        except Exception as exc:
            failed_phase = phase_func.__name__.replace("phase_", "").replace("_", "-")
            error_msg = str(exc)
            status = "failed"
            ctx.emit_event(f"{failed_phase}_error", "setup", {
                "error": error_msg,
            })
            break
        if ctx.stop_after_dispatch:
            phase_ledger(ctx)
            status = "routed"
            break

    # Always write result and final manifest, even on failure
    ctx.write_result(status, failed_phase=failed_phase, error=error_msg)
    update_manifest(ctx.artifact_manifest_path, ctx.run_id, ctx.manifest_entries)

    # Load and return result
    result = json.loads(ctx.result_path.read_text(encoding="utf-8"))
    return result


def _phase_to_event_phase(phase_name: str) -> str:
    """Map phase name to event_writer VALID_PHASES."""
    mapping = {
        "lint": "setup",
        "compose": "setup",
        "validate": "setup",
        "facts": "setup",
        "route": "setup",
        "context": "setup",
        "plan": "setup",
        "dispatch": "dispatch",
        "evidence": "review",
        "acceptance": "review",
        "review-ladder": "review",
        "handoff": "decision",
        "ledger": "finalization",
    }
    return mapping.get(phase_name, "setup")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aiwf run",
        description="Quota-efficient aiwf run lifecycle.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Default is preview mode: no model calls, no worktree, no destructive mutations.\n"
            "Use --execute to dispatch through the broker-mediated Claude path.\n"
            "Exit codes: 0=success, 1=phase failure, 2=task error."
        ),
    )
    parser.add_argument(
        "task",
        help="Path to the task JSON file.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute dispatch (default is preview only).",
    )
    parser.add_argument(
        "--profiles-dir",
        default=None,
        help="Directory containing profile JSON files.",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="Repository root. Defaults to Git discovery.",
    )
    parser.add_argument(
        "--run-dir-base",
        default=None,
        help="Base directory for run output. Default: <repo>/.worktrees/",
    )
    parser.add_argument(
        "--dispatcher",
        default=None,
        help="Path to dispatch script. Default: scripts/dispatch-to-claude.sh",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output result as JSON.",
    )

    args = parser.parse_args(argv)

    result = run_lifecycle(
        task_path=Path(args.task),
        execute=args.execute,
        profiles_dir=Path(args.profiles_dir) if args.profiles_dir else None,
        repo=Path(args.repo) if args.repo else None,
        run_dir_base=Path(args.run_dir_base) if args.run_dir_base else None,
        dispatcher=args.dispatcher,
    )

    if args.json_output:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        status = result.get("status", "unknown")
        lane = result.get("lane", "unknown")
        run_dir = result.get("run_dir", "")
        failed = result.get("failed_phase")
        error = result.get("error")

        print(f"Status: {status}")
        print(f"Lane: {lane}")
        print(f"Run directory: {run_dir}")

        if result.get("acceptance_status"):
            print(f"Acceptance: {result['acceptance_status']}")
        if result.get("review_tier"):
            print(f"Review tier: {result['review_tier']}")
        if result.get("final_decision"):
            print(f"Decision: {result['final_decision']}")
        if failed:
            print(f"Failed phase: {failed}")
        if error:
            print(f"Error: {error}")

        phases = result.get("phases_completed", [])
        if phases:
            print(f"Phases completed: {', '.join(phases)}")

    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
