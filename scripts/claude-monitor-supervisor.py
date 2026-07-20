#!/usr/bin/env python3
"""Persist compact Claude monitor events and invoke Spark only on ambiguity.

The supervisor is intentionally model-sparse: the local watcher emits only
material transitions, the deterministic decision helper filters them, and a
Spark monitor-triage call is made only for inspect/interrupt-candidate states.
It never interrupts Claude.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from spark_control_protocol import evidence_hash, parse_and_normalize


def _snapshot(helper: Path, repo: Path, task_id: str) -> dict:
    result = subprocess.run(
        [sys.executable, str(helper), "snapshot", "--repo-root", str(repo),
         "--task-id", task_id, "--format", "json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, check=False,
    )
    if result.returncode:
        return {"decision": "uncertain", "codex_review_required": "yes"}
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"decision": "uncertain", "codex_review_required": "yes"}
    return value if isinstance(value, dict) else {"decision": "uncertain"}


def _append_summary(handle, fields: dict, local: dict) -> None:
    safe = {
        key: " ".join(str(fields.get(key, "unknown")).split())[:160]
        for key in ("decision", "confidence", "evidence_hash")
    }
    safe["reason_codes"] = ",".join(fields.get("reason_codes", ["unspecified"]))[:160]
    safe["codex_review_required"] = "yes" if fields.get("requires_codex_review", True) else "no"
    safe["interrupt_authorized"] = "no"
    safe["execution_phase"] = " ".join(str(local.get("execution_phase", "unknown")).split())[:40]
    safe["implementation_complete"] = "yes" if str(local.get("implementation_complete", "")).lower() == "yes" else "no"
    safe["completion_ready"] = "yes" if str(local.get("completion_ready", "")).lower() == "yes" else "no"
    safe["finish_recommended"] = "yes" if str(local.get("finish_recommended", "")).lower() == "yes" else "no"
    handle.write(
        "spark_monitor_event "
        + " ".join(f"{key}={value}" for key, value in safe.items())
        + "\n"
    )
    handle.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--watch-script", type=Path, required=True)
    parser.add_argument("--monitor-script", type=Path, required=True)
    parser.add_argument("--decision-helper", type=Path, required=True)
    parser.add_argument("--event-log", type=Path, required=True)
    parser.add_argument("--interval", type=int, default=5)
    parser.add_argument("--spark", choices=("auto", "on", "off"), default="auto")
    parser.add_argument("--spark-min-interval", type=int, default=120)
    args = parser.parse_args()

    if args.interval < 1 or args.spark_min_interval < 0:
        parser.error("interval must be positive and spark-min-interval non-negative")

    args.event_log.parent.mkdir(parents=True, exist_ok=True)
    watch = subprocess.Popen(
        ["bash", str(args.watch_script), args.task_id, "--machine",
         "--interval", str(args.interval)],
        cwd=str(args.repo_root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", start_new_session=True,
    )

    stopping = False

    def stop_child(_signum=None, _frame=None) -> None:
        nonlocal stopping
        stopping = True
        if watch.poll() is None:
            try:
                os.killpg(watch.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                watch.terminate()

    signal.signal(signal.SIGTERM, stop_child)
    signal.signal(signal.SIGINT, stop_child)

    last_spark_at = 0.0
    last_evidence_hash = ""
    with args.event_log.open("a", encoding="utf-8") as log:
        assert watch.stdout is not None
        for line in watch.stdout:
            log.write(line)
            log.flush()
            if stopping or not line.startswith("monitor_event "):
                continue
            local = _snapshot(args.decision_helper, args.repo_root, args.task_id)
            decision = str(local.get("decision", "uncertain"))
            if args.spark == "off" or decision not in {"inspect", "interrupt-candidate"}:
                continue
            now = time.monotonic()
            evidence = {key: local.get(key) for key in sorted(local)}
            candidate_hash = evidence_hash("monitor", evidence)
            urgent_new = decision == "interrupt-candidate" and candidate_hash != last_evidence_hash
            if not urgent_new and now - last_spark_at < args.spark_min_interval:
                continue
            try:
                result = subprocess.run(
                    ["bash", str(args.monitor_script), "decision", args.task_id,
                     "--spark", args.spark],
                    cwd=str(args.repo_root), capture_output=True, text=True,
                    encoding="utf-8", errors="replace",
                    timeout=int(os.environ.get("CLAUDE_MONITOR_SPARK_TIMEOUT_SECONDS", "90")) + 15,
                    check=False,
                )
                values = parse_and_normalize("monitor", result.stdout, evidence=evidence)
                if values["evidence_hash"] == last_evidence_hash:
                    continue
            except (OSError, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError):
                values = parse_and_normalize(
                    "monitor",
                    "decision=uncertain\nconfidence=low\nreason_code=spark-monitor-unavailable\n"
                    "codex_review_required=yes\n",
                    evidence=evidence,
                )
            _append_summary(log, values, local)
            last_spark_at = now
            last_evidence_hash = values["evidence_hash"]

    if watch.poll() is None:
        stop_child()
    try:
        return watch.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(watch.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            watch.kill()
        return watch.wait()


if __name__ == "__main__":
    raise SystemExit(main())
