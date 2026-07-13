#!/usr/bin/env python3
"""Unified brokered advisor call command.

Reserves exactly one model call via model-call-broker, executes once,
validates the structured response, and produces a stable machine-readable
result.  Never marks acceptance or merge authorization.

Python 3.9-compatible, no external dependencies.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from evidence_hash import content_hash as _content_hash


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run_model_call_broker(
    *,
    role: str,
    stage: str,
    task_id: str,
    input_path: Path,
    evidence_path: Path,
    output_path: Path,
    stderr_path: Path,
    ledger_path: Path,
    plan_path: Optional[Path] = None,
    run_id: Optional[str] = None,
) -> int:
    """Run the model-call-broker with the given arguments. Returns exit code."""
    cmd = [
        sys.executable, str(SCRIPT_DIR / "model-call-broker.py"),
        "--role", role,
        "--stage", stage,
        "--task-id", task_id,
        "--input", str(input_path),
        "--evidence", str(evidence_path),
        "--output", str(output_path),
        "--stderr", str(stderr_path),
        "--ledger", str(ledger_path),
    ]
    if plan_path and plan_path.is_file():
        cmd += ["--plan", str(plan_path)]
    if run_id:
        cmd += ["--run-id", run_id]

    # The actual model command depends on the role
    if role == "spark":
        model_cmd = ["spark", "--json"]
    elif role == "codex":
        model_cmd = ["codex", "exec", "--json"]
    elif role == "human":
        # Human mode: no model call, just validate an explicitly supplied response
        return 0
    else:
        return 3  # Unknown role

    cmd += ["--"] + model_cmd

    result = subprocess.run(cmd, capture_output=False)
    return result.returncode


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--packet", type=Path, required=True,
                   help="Path to advisor-packet.json from prepare-advisor-continuation")
    p.add_argument("--prompt", type=Path, required=True,
                   help="Path to bounded advisor prompt file")
    p.add_argument("--advisor", required=True, choices=["spark", "codex", "human"],
                   help="Advisor type")
    p.add_argument("--output-dir", type=Path, required=True,
                   help="Output directory for results")
    p.add_argument("--ledger", type=Path, default=Path(".ai-workflow/model-calls.jsonl"),
                   help="Ledger JSONL path")
    p.add_argument("--plan", type=Path, default=None,
                   help="Optional execution plan JSON")
    p.add_argument("--response-file", type=Path, default=None,
                   help="For human advisor: path to explicitly supplied response JSON")
    p.add_argument("--task-id", default=None,
                   help="Task ID override (defaults to packet task_id)")
    args = p.parse_args(argv)

    # Load packet
    packet = json.loads(args.packet.read_text(encoding="utf-8"))
    task_id = args.task_id or packet["task_id"]
    request_id = packet.get("request_id", "")
    evidence_hash = packet.get("evidence_hash", "")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Validate prompt exists and is non-empty
    if not args.prompt.is_file() or args.prompt.stat().st_size == 0:
        _write_json(output_dir / "advisor-call-result.json", {
            "ok": False,
            "reason": "missing-or-empty-prompt",
            "task_id": task_id,
        })
        return 2

    # For human advisor: validate the explicitly supplied response
    if args.advisor == "human":
        if args.response_file is None or not args.response_file.is_file():
            _write_json(output_dir / "advisor-call-result.json", {
                "ok": False,
                "reason": "human-advisor-requires-response-file",
                "task_id": task_id,
            })
            return 2

        # Validate the response through the response validator
        import importlib.util
        _vr_path = Path(__file__).resolve().parent / "validate-advisor-response.py"
        _vr_spec = importlib.util.spec_from_file_location("validate_advisor_response", _vr_path)
        _vr_mod = importlib.util.module_from_spec(_vr_spec)
        _vr_spec.loader.exec_module(_vr_mod)
        validate_resp = _vr_mod.validate_response

        original_allowed = packet.get("allowed_changes", [])
        original_forbidden = packet.get("forbidden_paths", [])

        ok, normalized, diagnostic = validate_resp(
            str(args.response_file),
            expected_request_id=request_id,
            expected_evidence_hash=evidence_hash,
            original_allowed_changes=original_allowed,
            original_forbidden_changes=original_forbidden,
        )

        if not ok:
            _write_json(output_dir / "advisor-call-result.json", {
                "ok": False,
                "reason": f"invalid-human-response: {diagnostic.get('reason', 'unknown')}",
                "task_id": task_id,
                "diagnostic": diagnostic,
            })
            return 2

        # Human mode: zero model calls, just validate
        _write_json(output_dir / "advisor-call-result.json", {
            "ok": True,
            "task_id": task_id,
            "request_id": request_id,
            "advisor": "human",
            "reservation_id": "human-validated",
            "evidence_hash": evidence_hash,
            "decision": normalized["decision"],
            "resume_eligible": normalized.get("resume_eligible", False),
            "response": normalized,
        })
        return 0

    # For model advisors (spark/codex): broker-mediated execution
    # Compute input hash
    input_hash = _content_hash(args.prompt.read_bytes())

    # Write the evidence file (the packet JSON)
    evidence_path = output_dir / "advisor-evidence.json"
    evidence_path.write_bytes(args.packet.read_bytes())

    # Output paths
    model_output = output_dir / "advisor-model-output.json"
    model_stderr = output_dir / "advisor-model-stderr.txt"

    run_id = f"advisor-{uuid.uuid4().hex[:12]}"

    # Execute via broker (reserves exactly one call)
    exit_code = _run_model_call_broker(
        role=args.advisor,
        stage="advisor-call",
        task_id=task_id,
        input_path=args.prompt,
        evidence_path=evidence_path,
        output_path=model_output,
        stderr_path=model_stderr,
        ledger_path=args.ledger,
        plan_path=args.plan,
        run_id=run_id,
    )

    if exit_code == 2:
        # Broker denied (budget exhausted or duplicate)
        _write_json(output_dir / "advisor-call-result.json", {
            "ok": False,
            "reason": "broker-denied",
            "task_id": task_id,
            "exit_code": exit_code,
            "stderr": model_stderr.read_text(encoding="utf-8", errors="replace")[:2048]
            if model_stderr.is_file() else "",
        })
        return 2

    if exit_code != 0:
        _write_json(output_dir / "advisor-call-result.json", {
            "ok": False,
            "reason": "model-call-failed",
            "task_id": task_id,
            "exit_code": exit_code,
        })
        return 1

    # Parse model output and validate the structured response
    if not model_output.is_file():
        _write_json(output_dir / "advisor-call-result.json", {
            "ok": False,
            "reason": "no-model-output",
            "task_id": task_id,
        })
        return 1

    # The model output should contain the advisor response JSON.
    # Try to extract it from the model output (which may be wrapped in
    # model-specific JSON format).
    model_output_text = model_output.read_text(encoding="utf-8", errors="replace")

    # Try to find a JSON object in the output that looks like an advisor response
    response_data = None
    try:
        # First try direct parse
        candidate = json.loads(model_output_text)
        if isinstance(candidate, dict) and "schema_version" in candidate:
            response_data = candidate
        elif isinstance(candidate, dict) and "result" in candidate:
            # Some models wrap the output
            result_str = candidate["result"]
            if isinstance(result_str, str):
                response_data = json.loads(result_str)
    except (json.JSONDecodeError, TypeError):
        pass

    if response_data is None:
        # Write the raw output for inspection
        _write_json(output_dir / "advisor-call-result.json", {
            "ok": False,
            "reason": "unparseable-model-output",
            "task_id": task_id,
            "raw_output_preview": model_output_text[:2048],
        })
        return 1

    # Write the raw response for validation
    raw_response_path = output_dir / "advisor-response-raw.json"
    _write_json(raw_response_path, response_data)

    # Validate the response (import from hyphenated filename)
    import importlib.util
    _vr_path = Path(__file__).resolve().parent / "validate-advisor-response.py"
    _vr_spec = importlib.util.spec_from_file_location("validate_advisor_response", _vr_path)
    _vr_mod = importlib.util.module_from_spec(_vr_spec)
    _vr_spec.loader.exec_module(_vr_mod)
    validate_resp = _vr_mod.validate_response

    original_allowed = packet.get("allowed_changes", [])
    original_forbidden = packet.get("forbidden_paths", [])

    ok, normalized, diagnostic = validate_resp(
        str(raw_response_path),
        expected_request_id=request_id,
        expected_evidence_hash=evidence_hash,
        original_allowed_changes=original_allowed,
        original_forbidden_changes=original_forbidden,
    )

    if not ok:
        _write_json(output_dir / "advisor-call-result.json", {
            "ok": False,
            "reason": f"invalid-model-response: {diagnostic.get('reason', 'unknown')}",
            "task_id": task_id,
            "diagnostic": diagnostic,
        })
        return 1

    # Success — write the validated result
    _write_json(output_dir / "advisor-call-result.json", {
        "ok": True,
        "task_id": task_id,
        "request_id": request_id,
        "advisor": args.advisor,
        "reservation_id": run_id,
        "evidence_hash": evidence_hash,
        "decision": normalized["decision"],
        "resume_eligible": normalized.get("resume_eligible", False),
        "response": normalized,
    })

    # Also write the validated response separately
    _write_json(output_dir / "advisor-response-validated.json", normalized)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
