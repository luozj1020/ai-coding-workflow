#!/usr/bin/env python3
"""Normalize and validate bounded Spark control-plane decisions.

Spark remains advisory. This module converts legacy key=value output into a
small versioned object so downstream helpers do not need to reread model prose.
Invalid output fails closed to Codex review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

FIELD = re.compile(r"^([a-z][a-z0-9_]*)=(.*)$")
KINDS = {"route", "monitor", "failure", "parallel"}
CONFIDENCE = {"high", "medium", "low"}
ROUTE_DECISIONS = {"codex-fast-path", "claude-builder", "spec-first", "human-clarification"}
MONITOR_DECISIONS = {"continue", "inspect", "interrupt-candidate", "terminal", "visibility-unknown", "uncertain"}
FAILURE_DECISIONS = {"wait", "retry", "continue-same-worktree", "narrow", "split", "codex-takeover", "human-review"}
PARALLEL_DECISIONS = {"serial", "canary", "parallel-candidate", "split-required"}
MAX_INPUT_BYTES = 32 * 1024
MAX_REASONS = 8


def parse_fields(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        match = FIELD.match(raw.strip())
        if match:
            values[match.group(1)] = match.group(2).strip()
    return values


def evidence_hash(kind: str, evidence: Mapping[str, Any]) -> str:
    canonical = json.dumps({"kind": kind, "evidence": evidence}, sort_keys=True,
                           separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "yes", "true", "on"} if value is not None else default


def _bounded(value: Any, default: str = "unknown", limit: int = 160) -> str:
    text = " ".join(str(value if value not in (None, "") else default).split())
    return text[:limit]


def _reasons(value: Any) -> list[str]:
    items = value if isinstance(value, list) else re.split(r"[,;]", str(value or "unspecified"))
    result = []
    for item in items:
        reason = re.sub(r"[^a-z0-9_.-]+", "-", str(item).strip().lower()).strip("-")
        if reason and reason not in result:
            result.append(reason[:64])
        if len(result) == MAX_REASONS:
            break
    return result or ["unspecified"]


def normalize(kind: str, source: Mapping[str, Any], *, evidence: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    if kind not in KINDS:
        raise ValueError("unsupported-kind")
    values = dict(source)
    confidence = _bounded(values.get("confidence", values.get("cost_confidence", "low"))).lower()
    if confidence not in CONFIDENCE:
        confidence = "low"
    reason_source = values.get("reason_codes", values.get("reason_code", values.get("risk_flags", "unspecified")))

    if kind == "route":
        decision = _bounded(values.get("deterministic_owner", values.get("recommended_owner",
                            values.get("economic_recommendation", "human-clarification"))))
        if decision not in ROUTE_DECISIONS:
            decision = "human-clarification"
        claude_role = _bounded(values.get("claude_role", "execution-builder" if decision == "claude-builder" else "none"))
        if claude_role not in {"solution-planner", "execution-builder", "batch-builder", "none"}:
            claude_role = "execution-builder" if decision == "claude-builder" else "none"
        if decision != "claude-builder":
            claude_role = "none"
        detail: dict[str, Any] = {
            "task_card_required": decision == "claude-builder",
            "claude_role": claude_role,
            "durable_output_required": _bool(values.get("durable_output_required")),
            "readonly_delegation_value": _bool(values.get("readonly_delegation_value")),
            "predicted_diff_lines_high": _bounded(values.get("calibrated_diff_lines_high",
                                                 values.get("predicted_diff_lines_high"))),
            "predicted_files": _bounded(values.get("predicted_files")),
        }
    elif kind == "monitor":
        decision = _bounded(values.get("decision", "uncertain"))
        if decision not in MONITOR_DECISIONS:
            decision = "uncertain"
        detail = {
            "next_check_seconds": _bounded(values.get("next_check_seconds")),
            "execution_phase": _bounded(values.get("execution_phase")),
            "implementation_complete": _bool(values.get("implementation_complete")),
            "completion_ready": _bool(values.get("completion_ready")),
            "finish_recommended": _bool(values.get("finish_recommended")),
            "interrupt_authorized": False,
        }
    elif kind == "failure":
        decision = _bounded(values.get("decision", values.get("recommended_action", "human-review")))
        if decision not in FAILURE_DECISIONS:
            decision = "human-review"
        detail = {"failure_class": _bounded(values.get("failure_class", values.get("spark_failure_class"))),
                  "counts_toward_takeover": _bool(values.get("counts_toward_takeover")),
                  "takeover_authorized": False}
    else:
        decision = _bounded(values.get("decision", values.get("parallel_decision", "serial")))
        if decision not in PARALLEL_DECISIONS:
            decision = "serial"
        try:
            concurrency = max(1, min(2, int(values.get("max_concurrency", 1))))
        except (TypeError, ValueError):
            concurrency = 1
        if decision not in {"canary", "parallel-candidate"}:
            concurrency = 1
        detail = {"max_concurrency": concurrency, "dispatch_authorized": False,
                  "serial_reconciliation_required": True}

    result: dict[str, Any] = {
        "schema_version": 1,
        "protocol": f"spark-{kind}-decision-v1",
        "decision": decision,
        "confidence": confidence,
        "reason_codes": _reasons(reason_source),
        "summary": _bounded(values.get("reason", values.get("summary", values.get("answer")))),
        "requires_codex_review": _bool(values.get("codex_review_required"), confidence != "high"),
        "advisory_only": True,
        "evidence_hash": evidence_hash(kind, evidence or values),
    }
    result.update(detail)
    return result


def validate(kind: str, value: Mapping[str, Any]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if value.get("schema_version") != 1:
        errors.append("schema-version")
    if value.get("protocol") != f"spark-{kind}-decision-v1":
        errors.append("protocol")
    if value.get("confidence") not in CONFIDENCE:
        errors.append("confidence")
    if value.get("advisory_only") is not True:
        errors.append("advisory-only")
    if not isinstance(value.get("requires_codex_review"), bool):
        errors.append("codex-review-type")
    reasons = value.get("reason_codes")
    if not isinstance(reasons, list) or not reasons or len(reasons) > MAX_REASONS:
        errors.append("reason-codes")
    allowed = {"route": ROUTE_DECISIONS, "monitor": MONITOR_DECISIONS,
               "failure": FAILURE_DECISIONS, "parallel": PARALLEL_DECISIONS}[kind]
    if value.get("decision") not in allowed:
        errors.append("decision")
    if kind == "monitor" and value.get("interrupt_authorized") is not False:
        errors.append("interrupt-authority")
    if kind == "failure" and value.get("takeover_authorized") is not False:
        errors.append("takeover-authority")
    if kind == "parallel" and value.get("dispatch_authorized") is not False:
        errors.append("dispatch-authority")
    return not errors, errors


def parse_and_normalize(kind: str, text: str, *, evidence: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    if len(text.encode("utf-8", errors="replace")) > MAX_INPUT_BYTES:
        raise ValueError("input-too-large")
    stripped = text.strip()
    if stripped.startswith("{"):
        source = json.loads(stripped)
        if not isinstance(source, dict):
            raise ValueError("not-an-object")
        if source.get("protocol") == f"spark-{kind}-decision-v1":
            ok, errors = validate(kind, source)
            if not ok:
                raise ValueError("invalid:" + ",".join(errors))
            return dict(source)
    else:
        source = parse_fields(text)
    result = normalize(kind, source, evidence=evidence)
    ok, errors = validate(kind, result)
    if not ok:
        raise ValueError("invalid:" + ",".join(errors))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=sorted(KINDS))
    parser.add_argument("path", nargs="?", type=Path, help="input file; default stdin")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="trusted local field override; may be repeated")
    args = parser.parse_args()
    text = args.path.read_text(encoding="utf-8", errors="replace") if args.path else sys.stdin.read()
    try:
        if args.set:
            source = parse_fields(text)
            for item in args.set:
                if "=" not in item:
                    raise ValueError("invalid-override")
                key, raw = item.split("=", 1)
                if not FIELD.match(f"{key}={raw}"):
                    raise ValueError("invalid-override")
                source[key] = raw
            text = "\n".join(f"{key}={raw}" for key, raw in source.items())
        value = parse_and_normalize(args.kind, text)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "reason": _bounded(exc, "invalid-output")}, sort_keys=True))
        return 2
    print(json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":") if args.compact else None,
                     indent=None if args.compact else 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
