#!/usr/bin/env python3
"""Budgeted dispatch front door. Preview by default; --execute invokes Claude once.

Changes from PR4:
- Context injection: materializes CLAUDE_CONTEXT_PACKET.md from plan/context JSON
  before Claude starts, so the prompt can reference it.
- Real-time tee: --execute streams stdout/stderr to terminal while saving to files,
  replacing the old capture_output=True approach.
"""
import argparse, hashlib, json, os, re, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPARK_HELPER = HERE / "run-codex-spark.sh"
sys.path.insert(0, str(HERE))
from evidence_hash import content_hash as _content_hash, evidence_hash as _evidence_hash


def rows(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []


def _render_context_packet_md(context_packet: dict) -> str:
    """Render a context packet dict as CLAUDE_CONTEXT_PACKET.md.

    Produces a structured markdown with L0/L1/L2 levels for Claude to read
    before exploration.
    """
    lines = [
        "# Claude Context Packet",
        "",
        f"**Task ID:** {context_packet.get('task_id', 'unknown')}",
        f"**Goal:** {context_packet.get('goal', '')}",
        "",
    ]

    # Forbidden paths
    forbidden = context_packet.get("forbidden_paths", [])
    if forbidden:
        lines.append("## Forbidden Paths")
        lines.append("")
        for p in forbidden:
            lines.append(f"- `{p}`")
        lines.append("")

    # Validation
    validation = context_packet.get("validation", [])
    if validation:
        lines.append("## Validation Commands")
        lines.append("")
        for v in validation:
            lines.append(f"- `{v}`")
        lines.append("")

    # Acceptance
    acceptance = context_packet.get("acceptance", [])
    if acceptance:
        lines.append("## Acceptance Criteria")
        lines.append("")
        for a in acceptance:
            if isinstance(a, dict):
                lines.append(f"- {a.get('description', a.get('id', str(a)))}")
            else:
                lines.append(f"- {a}")
        lines.append("")

    # L0: Target files, symbols, build targets
    l0 = context_packet.get("L0", {})
    l0_files = l0.get("files", [])
    l0_symbols = l0.get("symbols", [])
    l0_targets = l0.get("targets", [])
    if l0_files or l0_symbols or l0_targets:
        lines.append("## L0 — Primary Targets")
        lines.append("")
        if l0_files:
            lines.append("### Files")
            lines.append("")
            for f in l0_files:
                lines.append(f"- `{f}`")
            lines.append("")
        if l0_symbols:
            lines.append("### Symbols")
            lines.append("")
            for s in l0_symbols:
                lines.append(f"- `{s}`")
            lines.append("")
        if l0_targets:
            lines.append("### Build Targets")
            lines.append("")
            for t in l0_targets:
                lines.append(f"- `{t}`")
            lines.append("")

    # L1: Snippets, call paths, constraints
    l1 = context_packet.get("L1", {})
    l1_snippets = l1.get("snippets", [])
    l1_call_paths = l1.get("call_paths", [])
    l1_constraints = l1.get("constraints", [])
    if l1_snippets or l1_call_paths or l1_constraints:
        lines.append("## L1 — Context Snippets & Constraints")
        lines.append("")
        if l1_snippets:
            lines.append("### Reference Snippets")
            lines.append("")
            for s in l1_snippets:
                if isinstance(s, dict):
                    lines.append(f"- `{s.get('file', '?')}` lines {s.get('start', '?')}-{s.get('end', '?')}")
                else:
                    lines.append(f"- {s}")
            lines.append("")
        if l1_call_paths:
            lines.append("### Call Paths")
            lines.append("")
            for cp in l1_call_paths:
                lines.append(f"- `{cp}`")
            lines.append("")
        if l1_constraints:
            lines.append("### Constraints")
            lines.append("")
            for c in l1_constraints:
                lines.append(f"- {c}")
            lines.append("")

    interface = context_packet.get("interface_contract", {})
    if isinstance(interface, dict) and any(interface.get(key) for key in ("signatures", "runnable_examples", "async_contract")):
        lines.extend(["## Executable Interface Contract", ""])
        for signature in interface.get("signatures", []):
            lines.append(f"- Signature: `{signature}`")
        for example in interface.get("runnable_examples", []):
            lines.extend(["", "```python", str(example), "```"])
        if interface.get("async_contract"):
            lines.extend(["", f"Async/sync contract: {interface['async_contract']}"])
        material = json.dumps(interface, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        lines.extend(["", f"Evidence hash: `{_content_hash(material)}`", ""])

    # L2: Full files (only when enabled)
    l2 = context_packet.get("L2", {})
    if l2.get("enabled") and l2.get("full_files"):
        lines.append("## L2 — Full File Content")
        lines.append("")
        for entry in l2["full_files"]:
            if isinstance(entry, dict):
                path = entry.get("path", "?")
                content = entry.get("content", "")
                lines.append(f"### `{path}`")
                lines.append("")
                lines.append("```")
                lines.append(content)
                lines.append("```")
                lines.append("")
            else:
                lines.append(f"- `{entry}`")
        lines.append("")

    lines.append("---")
    lines.append("*This packet was auto-generated by the dispatch-efficient control plane.*")
    lines.append("")
    return "\n".join(lines)


def _materialize_context_packet(out: Path, context_packet_path: Path) -> Path:
    """Materialize CLAUDE_CONTEXT_PACKET.md from context-packet.json.

    Returns the path to the generated markdown file.
    """
    packet = json.loads(context_packet_path.read_text(encoding="utf-8"))
    md_content = _render_context_packet_md(packet)
    md_path = out / "CLAUDE_CONTEXT_PACKET.md"
    md_path.write_text(md_content, encoding="utf-8")
    return md_path


def _tee_subprocess(cmd, stdin_data=None, stdout_path=None, stderr_path=None, cwd=None, env=None):
    """Run a subprocess with real-time tee: stream to terminal AND save to files.

    Returns the exit code. Preserves child exit code exactly.
    """
    import threading

    # Open output files
    out_fh = open(stdout_path, "wb") if stdout_path else None
    err_fh = open(stderr_path, "wb") if stderr_path else None
    proc = None

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE if stdin_data is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=env,
        )

        # Feed stdin and close
        if stdin_data is not None:
            proc.stdin.write(stdin_data)
            proc.stdin.close()

        # Thread-safe tee for stdout and stderr
        def tee_stream(src, dst_file, dst_terminal):
            """Read from src, write to dst_file and dst_terminal."""
            while True:
                chunk = src.read(4096)
                if not chunk:
                    break
                if dst_file:
                    dst_file.write(chunk)
                    dst_file.flush()
                dst_terminal.write(chunk)
                dst_terminal.flush()

        stdout_thread = threading.Thread(
            target=tee_stream,
            args=(proc.stdout, out_fh, sys.stdout.buffer),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=tee_stream,
            args=(proc.stderr, err_fh, sys.stderr.buffer),
            daemon=True,
        )

        stdout_thread.start()
        stderr_thread.start()

        proc.wait()
        stdout_thread.join()
        stderr_thread.join()

        return proc.returncode

    finally:
        # Close pipe handles on all paths to avoid ResourceWarning.
        # By the time we reach here either (a) threads joined and pipes
        # hit EOF, or (b) an exception occurred and daemon threads will
        # terminate when the process exits.  Either way, closing is safe.
        if proc is not None:
            try:
                proc.stdout.close()
            except OSError:
                pass
            try:
                proc.stderr.close()
            except OSError:
                pass
        if out_fh:
            out_fh.close()
        if err_fh:
            err_fh.close()


def _spark_auto_disabled(report_path: Path) -> bool:
    if not report_path.exists():
        return False
    text = report_path.read_text(encoding="utf-8", errors="replace").lower()
    return "| spark auto-disabled? | yes |" in text


def _spark_recommended_owner(report_path: Path):
    """Read the deterministic owner from a compact Spark report."""
    if not report_path.exists():
        return None
    text = report_path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"^\| Recommended owner \| ([^|]+) \|$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


def _needs_host_execution(spark_out_dir: Path, spark_stderr_path: Path) -> bool:
    report_path = spark_out_dir / "codex-spark.report.md"
    if report_path.exists():
        text = report_path.read_text(encoding="utf-8", errors="replace").lower()
        if "| host handoff required? | yes |" in text:
            return True
    if spark_stderr_path.exists():
        text = spark_stderr_path.read_text(encoding="utf-8", errors="replace").lower()
        return "host_handoff_required=true" in text or "needs_host_execution=true" in text
    return False


def _attempt_classification_path(explicit, out: Path):
    if explicit:
        path = Path(explicit)
        return path if path.is_file() else None
    stdout_path = out / "dispatch.stdout"
    if not stdout_path.is_file():
        return None
    text = stdout_path.read_text(encoding="utf-8", errors="replace")[-65536:]
    matches = re.findall(r"(?m)^Attempt Class:\s*(.+?)\s*$", text)
    if not matches:
        return None
    path = Path(matches[-1])
    return path if path.is_file() else None


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes"}


def _terminate_process_tree(proc: subprocess.Popen, grace_seconds: float = 5.0) -> None:
    """Terminate and reap the isolated retry process tree."""
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=grace_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            if proc.poll() is None:
                proc.kill()
    else:
        import signal

        try:
            process_group = os.getpgid(proc.pid)
        except OSError:
            process_group = None
        if process_group is not None:
            try:
                os.killpg(process_group, signal.SIGTERM)
            except OSError:
                pass
        try:
            proc.wait(timeout=grace_seconds)
            return
        except subprocess.TimeoutExpired:
            if process_group is not None:
                try:
                    os.killpg(process_group, signal.SIGKILL)
                except OSError:
                    pass
            elif proc.poll() is None:
                proc.kill()
    try:
        proc.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        if proc.poll() is None:
            proc.kill()
        proc.wait()


def _run_host_retry_with_timeout(cmd, timeout, stdout_path: Path, stderr_path: Path):
    """Run one host-authorized retry and return ``(exit_code, timed_out)``."""
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
        kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": stdout_file,
            "stderr": stderr_file,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(cmd, **kwargs)
        try:
            return proc.wait(timeout=timeout), False
        except subprocess.TimeoutExpired:
            _terminate_process_tree(proc)
            return -1, True


def _run_spark_preflight(plan: dict, card: Path, out: Path) -> dict:
    policy = plan.get("spark", {})
    record = {
        "schema_version": 1,
        "attempted": False,
        "invoked": False,
        "stage": policy.get("stage", "preflight"),
        "mode": policy.get("mode", "execution-cost-estimator"),
        "exit_code": None,
        "auto_disabled": False,
        "continued_to_claude": True,
        "skip_reason": policy.get("skip_reason"),
    }
    if not policy.get("invoke", False):
        return record

    record["attempted"] = True
    if not SPARK_HELPER.exists():
        record["exit_code"] = 127
        record["auto_disabled"] = True
        record["skip_reason"] = "skip.helper_missing"
        return record

    spark_out = out / "spark-preflight"
    cmd = [
        "bash", str(SPARK_HELPER), str(card),
        "--mode", record["mode"],
        "--result-mode", "minimal",
        "--output", str(spark_out),
    ]
    exit_code = _tee_subprocess(
        cmd,
        stdout_path=str(out / "spark-preflight.stdout"),
        stderr_path=str(out / "spark-preflight.stderr"),
    )
    record["exit_code"] = exit_code
    report_auto_disabled = _spark_auto_disabled(spark_out / "codex-spark.report.md")
    record["recommended_owner"] = _spark_recommended_owner(
        spark_out / "codex-spark.report.md"
    )
    record["auto_disabled"] = exit_code != 0 or report_auto_disabled
    record["invoked"] = exit_code == 0 and not report_auto_disabled
    if exit_code != 0:
        record["skip_reason"] = "skip.spark_failed"
    return record


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--plan", required=True)
    p.add_argument("--task-card", required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--ledger", default=".ai-workflow/run-ledger.jsonl")
    p.add_argument("--retry-state")
    p.add_argument("--current-context")
    p.add_argument("--failure-log")
    p.add_argument("--attempt-classification")
    p.add_argument("--execute", action="store_true")
    p.add_argument(
        "--host-authority",
        action="store_true",
        default=_truthy(os.environ.get("CODEX_SPARK_HOST_AUTHORITY", "")),
        help="Assert that this dispatcher already runs with host network authority.",
    )
    p.add_argument(
        "--host-retry-timeout",
        type=int,
        default=os.environ.get("CODEX_SPARK_HOST_RETRY_TIMEOUT", "120"),
        help="Positive timeout in seconds for the single host-authorized Spark retry.",
    )
    a = p.parse_args(argv)

    if a.host_retry_timeout < 1:
        p.error("--host-retry-timeout must be a positive integer")

    plan = json.loads(Path(a.plan).read_text())
    card = Path(a.task_card)
    out = Path(a.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    ledger = Path(a.ledger)
    calls = [x for x in rows(ledger) if x.get("task_id") == plan["task_id"] and x.get("model") == "claude"]

    control_plane = plan.get("control_plane", {})
    if control_plane and control_plane.get("within_budget") is False:
        decision = {
            "schema_version": 1,
            "task_id": plan["task_id"],
            "action": "recompose-before-dispatch",
            "claude_dispatched": False,
            "reason": control_plane.get("failures", ["control-plane-budget-exceeded"]),
        }
        (out / "dispatch-decision.json").write_text(
            json.dumps(decision, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(decision, sort_keys=True))
        return 2

    if calls and plan.get("execution", {}).get("delegation_mode") == "canary":
        classification_path = _attempt_classification_path(a.attempt_classification, out)
        if classification_path is None:
            print("canary retry requires --attempt-classification")
            return 2
        classification = json.loads(classification_path.read_text(encoding="utf-8"))
        if classification.get("economic_stop_loss") or classification.get("reroute_required"):
            decision = {
                "schema_version": 1,
                "task_id": plan["task_id"],
                "action": "reroute-before-redispatch",
                "claude_dispatched": False,
                "takeover_authorized": False,
                "reason": classification.get("failure_class", "canary-economic-stop-loss"),
            }
            (out / "dispatch-decision.json").write_text(
                json.dumps(decision, sort_keys=True, indent=2) + "\n", encoding="utf-8"
            )
            print(json.dumps(decision, sort_keys=True))
            return 2
        if not classification.get("same_worktree_retry_eligible"):
            print("canary retry blocked: classification does not authorize transport recovery")
            return 2

    if len(calls) >= plan["budget"]["claude_calls"]:
        print("Claude call budget exhausted")
        return 2

    if calls and not a.retry_state:
        print("Retry requires --retry-state and new evidence")
        return 2

    if calls:
        old = json.loads(Path(a.retry_state).read_text())
        now = {
            "task_card": _content_hash(card.read_bytes()),
            "context": _content_hash(Path(a.current_context).read_bytes()) if a.current_context else None,
            "failure_log": _content_hash(Path(a.failure_log).read_bytes()) if a.failure_log else None,
        }
        if not any(v and v != old.get(k) for k, v in now.items()):
            print("no-new-evidence retry blocked")
            return 2

    dispatch_card = card
    if plan["execution"].get("single_pass_allowed"):
        text = card.read_text(encoding="utf-8")
        text = re.sub(r"(?im)^\|\s*Mode\s*\|\s*builder\s*\|", "| Mode | mixed-exception |", text, count=1)
        text += "\n## Mixed Exception\nExpress Lane authorizes implementation plus exact narrow validation only.\n"
        dispatch_card = out / "single-pass-task-card.md"
        dispatch_card.write_text(text, encoding="utf-8")

    # --- Context injection: materialize CLAUDE_CONTEXT_PACKET.md ---
    context_packet_path = out / "context-packet.json"
    if context_packet_path.exists():
        _materialize_context_packet(out, context_packet_path)

    preview = {
        "task_id": plan["task_id"],
        "lane": plan["lane"],
        "dispatch_card": str(dispatch_card),
        "single_pass": plan["execution"].get("single_pass_allowed", False),
        "call_index": len(calls) + 1,
        "execute": a.execute,
        "owner": plan.get("execution", {}).get("owner", "codex-fast-path"),
        "claude_role": plan.get("execution", {}).get("claude_role", "execution-builder"),
        "builder_mode": plan.get("execution", {}).get("builder_mode", "standard"),
        "checker_model_dispatch": plan.get("execution", {}).get(
            "checker_model_dispatch", False
        ),
        "spark": plan.get("spark", {}),
    }
    (out / "dispatch-preview.json").write_text(json.dumps(preview, sort_keys=True, indent=2) + "\n")
    print(json.dumps(preview, sort_keys=True, indent=2))

    if not a.execute:
        return 0

    if preview["owner"] == "codex-fast-path":
        decision = {
            "schema_version": 1,
            "task_id": plan["task_id"],
            "action": "codex-fast-path",
            "claude_dispatched": False,
            "reason": "pre-card economy route selected Codex; no Claude call made",
        }
        (out / "dispatch-decision.json").write_text(
            json.dumps(decision, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(decision, sort_keys=True))
        return 0

    spark_record = _run_spark_preflight(plan, dispatch_card, out)
    initial_exit_code = spark_record.get("exit_code")
    initial_invoked = bool(spark_record.get("invoked"))
    initial_auto_disabled = bool(spark_record.get("auto_disabled"))
    handoff_needed = _needs_host_execution(
        out / "spark-preflight", out / "spark-preflight.stderr"
    )
    spark_record.update(
        {
            "initial_exit_code": initial_exit_code,
            "initial_invoked": initial_invoked,
            "initial_auto_disabled": initial_auto_disabled,
            "needs_host_execution": handoff_needed,
            "host_authority_present": bool(a.host_authority),
            "host_retry_attempted": False,
            "host_retry_exit_code": None,
            "host_retry_timed_out": False,
            "host_retry_auto_disabled": None,
            "host_handoff_action": (
                "rerun-current-dispatch-from-authorized-host-with---host-authority"
                if handoff_needed and not a.host_authority
                else None
            ),
        }
    )

    if handoff_needed:
        spark_record["invoked"] = False
        spark_record["auto_disabled"] = True
        spark_record["skip_reason"] = "skip.needs_host_execution"
        spark_record["final_state"] = "needs_host_execution"
    elif spark_record["invoked"]:
        spark_record["final_state"] = "invoked"
    else:
        spark_record["final_state"] = "auto_disabled"

    if handoff_needed and a.host_authority:
        host_output = out / "spark-preflight-host"
        host_command = [
            "bash",
            str(SPARK_HELPER),
            str(dispatch_card),
            "--mode",
            spark_record["mode"],
            "--result-mode",
            "minimal",
            "--output",
            str(host_output),
            "--execution-env",
            "host",
        ]
        spark_record["host_retry_attempted"] = True
        host_exit_code, host_timed_out = _run_host_retry_with_timeout(
            host_command,
            a.host_retry_timeout,
            out / "spark-preflight-host.stdout",
            out / "spark-preflight-host.stderr",
        )
        host_auto_disabled = (
            not host_timed_out
            and _spark_auto_disabled(host_output / "codex-spark.report.md")
        )
        host_invoked = host_exit_code == 0 and not host_timed_out and not host_auto_disabled
        host_recommended_owner = _spark_recommended_owner(
            host_output / "codex-spark.report.md"
        )
        spark_record.update(
            {
                "host_retry_exit_code": host_exit_code,
                "host_retry_timed_out": host_timed_out,
                "host_retry_auto_disabled": host_auto_disabled,
                "recommended_owner": host_recommended_owner,
                "invoked": host_invoked,
                "auto_disabled": not host_invoked,
                "host_handoff_action": None,
            }
        )
        if host_timed_out:
            spark_record["skip_reason"] = "skip.spark_host_retry_timeout"
            spark_record["final_state"] = "host_retry_timeout"
        elif host_exit_code != 0:
            spark_record["skip_reason"] = "skip.spark_host_retry_failed"
            spark_record["final_state"] = "host_retry_failed"
        elif host_auto_disabled:
            spark_record["skip_reason"] = "skip.spark_host_retry_auto_disabled"
            spark_record["final_state"] = "host_retry_auto_disabled"
        else:
            spark_record["skip_reason"] = None
            spark_record["final_state"] = "invoked"

    (out / "spark-dispatch.json").write_text(
        json.dumps(spark_record, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    if (
        spark_record.get("invoked")
        and not spark_record.get("auto_disabled")
        and spark_record.get("recommended_owner") == "codex-fast-path"
    ):
        spark_record["continued_to_claude"] = False
        (out / "spark-dispatch.json").write_text(
            json.dumps(spark_record, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        decision = {
            "schema_version": 1,
            "task_id": plan["task_id"],
            "action": "codex-fast-path",
            "claude_dispatched": False,
            "reason": "Spark economy route selected Codex before Claude start",
        }
        (out / "dispatch-decision.json").write_text(
            json.dumps(decision, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(decision, sort_keys=True))
        return 0

    # --- Execute: real-time tee ---
    start = time.time()
    cmd = ["bash", str(HERE / "dispatch-to-claude.sh"), str(dispatch_card)]

    exit_code = _tee_subprocess(
        cmd,
        stdin_data=None,
        stdout_path=str(out / "dispatch.stdout"),
        stderr_path=str(out / "dispatch.stderr"),
        env={
            **os.environ,
            "CLAUDE_CODE_BUILDER_MODE": plan.get("execution", {}).get(
                "builder_mode", "standard"
            ),
            "AI_WORKFLOW_DELEGATION_MODE": plan.get("execution", {}).get(
                "delegation_mode", "unknown"
            ),
        },
    )

    entry = {
        "schema_version": 1,
        "timestamp": int(time.time()),
        "run_id": out.name,
        "task_id": plan["task_id"],
        "stage": (
            "single-pass" if preview["single_pass"]
            else plan.get("execution", {}).get("claude_role", "execution-builder")
        ),
        "model": "claude",
        "call_index": len(calls) + 1,
        "input_hash": _content_hash(dispatch_card.read_bytes()),
        "evidence_hash": _content_hash((a.failure_log or "initial").encode()),
        "elapsed_seconds": round(time.time() - start, 3),
        "result": "dispatched" if exit_code == 0 else "dispatch-failed",
        "next_action": (
            "codex-review-solution-contract"
            if plan.get("execution", {}).get("claude_role") == "solution-planner"
            else "milestone-review"
        ),
    }
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")

    # Write append-only progress log
    progress_path = out / "dispatch-progress.log"
    with progress_path.open("a", encoding="utf-8") as pf:
        pf.write(json.dumps({
            "timestamp": int(time.time()),
            "event": "dispatch-complete",
            "exit_code": exit_code,
            "elapsed_seconds": round(time.time() - start, 3),
        }, sort_keys=True) + "\n")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
