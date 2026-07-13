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


def _get_codex_binary() -> str:
    """Return the Codex CLI binary path.

    Respects CODEX_BINARY env var for deterministic testing without
    shell interpolation.  Falls back to 'codex' on PATH.
    """
    return os.environ.get("CODEX_BINARY", "codex")


def _parse_single_json_response(text: str) -> Optional[dict]:
    """Parse model output as exactly one JSON object.

    Returns the parsed dict on success, or None on any ambiguity:
    - JSONL event stream (multiple JSON objects / lines)
    - Markdown fences wrapping JSON
    - Leading/trailing prose around a JSON object
    - Non-dict top-level value

    Tolerates a ``{"result": "<json>"}`` wrapper (some model CLIs wrap output).
    """
    stripped = text.strip()
    if not stripped:
        return None

    # Parse the entire output. Do not recover JSON from fences or prose: the
    # brokered advisor contract requires one unambiguous JSON object.
    try:
        candidate = json.loads(stripped)
        if isinstance(candidate, dict) and "schema_version" in candidate:
            return candidate
        if isinstance(candidate, dict) and "result" in candidate:
            result_str = candidate["result"]
            if isinstance(result_str, str):
                try:
                    inner = json.loads(result_str)
                    if isinstance(inner, dict) and "schema_version" in inner:
                        return inner
                except (json.JSONDecodeError, TypeError):
                    pass
    except json.JSONDecodeError:
        return None

    return None


def _build_model_command(advisor: str) -> list:
    """Build the non-interactive model invocation command for the given advisor.

    Uses ``codex exec`` (non-interactive stdin mode) with ``-`` to read the
    prompt from stdin.  ``--json`` is NOT passed because the broker captures
    stdout directly and expects a single JSON response object.

    Spark: ``codex exec --model gpt-5.3-codex-spark --sandbox workspace-write -``
    Codex: ``codex exec --sandbox read-only -``

    No fallback between advisors — Spark failure never falls back to Codex.
    """
    codex_bin = _get_codex_binary()
    if advisor == "spark":
        return [codex_bin, "exec", "--model", "gpt-5.3-codex-spark",
                "--sandbox", "workspace-write", "-"]
    elif advisor == "codex":
        return [codex_bin, "exec", "--sandbox", "read-only", "-"]
    else:
        return []


def _build_binding_suffix(
    *,
    request_id: str,
    evidence_hash: str,
    reservation_id: str,
    advisor: str,
) -> str:
    """Build the binding suffix appended to the advisor prompt.

    Tells the model the exact request_id, evidence_hash, reservation_id,
    advisor enum, and strict response schema requirements.
    """
    return (
        "\n\n--- BINDING CONTEXT (do not modify) ---\n"
        f"request_id: {request_id}\n"
        f"evidence_hash: {evidence_hash}\n"
        f"reservation_id: {reservation_id}\n"
        f"advisor: {advisor}\n"
        "schema_version: 1\n"
        "\n"
        "You MUST include these exact values in your JSON response.\n"
        "Your response MUST be a single JSON object with exactly these fields:\n"
        "schema_version, request_id, advisor, reservation_id, evidence_hash,\n"
        "decision, answer, allowed_changes, forbidden_changes, new_validation,\n"
        "risk_changed, resume_allowed.\n"
        "--- END BINDING CONTEXT ---\n"
    )


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
    reservation_id: Optional[str] = None,
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
    if reservation_id:
        cmd += ["--reservation-id", reservation_id]

    model_cmd = _build_model_command(role)
    if not model_cmd:
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

    # Generate stable unique reservation_id before broker invocation
    reservation_id = f"advisor-{uuid.uuid4().hex[:16]}"
    run_id = f"advisor-{uuid.uuid4().hex[:12]}"

    # Compute input hash
    input_hash = _content_hash(args.prompt.read_bytes())

    # Read original prompt and append binding suffix
    original_prompt_bytes = args.prompt.read_bytes()
    binding_suffix = _build_binding_suffix(
        request_id=request_id,
        evidence_hash=evidence_hash,
        reservation_id=reservation_id,
        advisor=args.advisor,
    )

    # Build effective prompt: original + binding suffix
    effective_prompt_bytes = original_prompt_bytes + binding_suffix.encode("utf-8")

    # Role-specific prompt caps after binding suffix is appended.
    # Spark 16 KiB, Codex 32 KiB.  UTF-8 truncation must not leave invalid bytes.
    _ROLE_PROMPT_CAPS = {"spark": 16 * 1024, "codex": 32 * 1024}
    prompt_cap = _ROLE_PROMPT_CAPS.get(args.advisor, 32 * 1024)
    if len(effective_prompt_bytes) > prompt_cap:
        suffix_len = len(binding_suffix.encode("utf-8"))
        max_original = prompt_cap - suffix_len
        if max_original < 512:
            _write_json(output_dir / "advisor-call-result.json", {
                "ok": False,
                "reason": "binding-suffix-too-large",
                "task_id": task_id,
            })
            return 2
        # Truncate at a valid UTF-8 boundary to avoid leaving partial sequences
        truncated = original_prompt_bytes[:max_original]
        # Walk back up to 3 bytes to find a valid UTF-8 boundary
        for _back in range(4):
            try:
                truncated.decode("utf-8")
                break
            except UnicodeDecodeError:
                truncated = original_prompt_bytes[:max_original - _back - 1]
        effective_prompt_bytes = truncated + binding_suffix.encode("utf-8")

    # Record truncation evidence for the report
    truncation_applied = len(effective_prompt_bytes) < len(
        original_prompt_bytes + binding_suffix.encode("utf-8")
    )

    # Write effective prompt to a temp file for broker invocation
    effective_prompt_path = output_dir / "advisor-effective-prompt.md"
    effective_prompt_path.write_bytes(effective_prompt_bytes)

    # Write the evidence file (the packet JSON)
    evidence_path = output_dir / "advisor-evidence.json"
    evidence_path.write_bytes(args.packet.read_bytes())

    # Output paths
    model_output = output_dir / "advisor-model-output.json"
    model_stderr = output_dir / "advisor-model-stderr.txt"

    # --- Transient writable CODEX_HOME for Spark ---
    # The Codex CLI initializes local app-server state before contacting Spark.
    # Advisory calls need a transient writable home while linking only the
    # existing read-only identity/config inputs.  Mirrors run-codex-spark.sh.
    transient_codex_home = None
    original_codex_home = os.environ.get("CODEX_HOME", "")
    if args.advisor == "spark":
        import shutil as _shutil
        transient_codex_home = output_dir / ".codex-spark-runtime"
        transient_codex_home.mkdir(parents=True, exist_ok=True)
        _codex_home_src = Path(original_codex_home) if original_codex_home else Path.home() / ".codex"
        for _input in ("auth.json", "config.toml", "installation_id",
                       "models_cache.json", "version.json"):
            _src = _codex_home_src / _input
            if _src.is_file():
                _shutil.copy2(str(_src), str(transient_codex_home / _input))
        os.environ["CODEX_HOME"] = str(transient_codex_home)

    # Execute via broker (reserves exactly one call with pre-generated reservation)
    exit_code = _run_model_call_broker(
        role=args.advisor,
        stage="advisor-call",
        task_id=task_id,
        input_path=effective_prompt_path,
        evidence_path=evidence_path,
        output_path=model_output,
        stderr_path=model_stderr,
        ledger_path=args.ledger,
        plan_path=args.plan,
        run_id=run_id,
        reservation_id=reservation_id,
    )

    # Clean up transient CODEX_HOME (retain broker/result diagnostics)
    if transient_codex_home is not None and transient_codex_home.is_dir():
        import shutil as _shutil
        # Remove only the copied identity files, keep the directory for diagnostics
        for _input in ("auth.json", "config.toml", "installation_id",
                       "models_cache.json", "version.json"):
            _p = transient_codex_home / _input
            if _p.is_file():
                _p.unlink(missing_ok=True)
        # Restore original CODEX_HOME
        if original_codex_home:
            os.environ["CODEX_HOME"] = original_codex_home
        elif "CODEX_HOME" in os.environ:
            del os.environ["CODEX_HOME"]

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

    # The model output must contain exactly one JSON object (the advisor
    # response).  A JSONL event stream, Markdown fence, leading/trailing
    # prose, or multiple objects must fail closed with a bounded diagnostic.
    model_output_text = model_output.read_text(encoding="utf-8", errors="replace")

    response_data = _parse_single_json_response(model_output_text)

    if response_data is None:
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
        expected_reservation_id=reservation_id,
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

    # Success — write the validated result with actual reservation_id
    _write_json(output_dir / "advisor-call-result.json", {
        "ok": True,
        "task_id": task_id,
        "request_id": request_id,
        "advisor": args.advisor,
        "reservation_id": reservation_id,
        "evidence_hash": evidence_hash,
        "decision": normalized["decision"],
        "resume_eligible": normalized.get("resume_eligible", False),
        "truncation_applied": truncation_applied,
        "prompt_cap": prompt_cap,
        "response": normalized,
    })

    # Also write the validated response separately
    _write_json(output_dir / "advisor-response-validated.json", normalized)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
