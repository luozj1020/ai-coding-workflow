#!/usr/bin/env python3
"""Parse the bounded Spark stdout envelope without reading advisory prose."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from spark_control_protocol import parse_and_normalize

FIELD = re.compile(r"^([a-z][a-z0-9_]*)=(.*)$")
TERMINAL = {"success", "failed", "unavailable"}


def parse(text: str) -> dict:
    values = {}
    status_history = []
    for raw in text.splitlines():
        match = FIELD.match(raw.strip())
        if not match:
            continue
        key, value = match.groups()
        if key == "spark_status":
            status_history.append(value)
        values[key] = value
    protocol = values.get("spark_protocol")
    terminal = next((value for value in reversed(status_history) if value in TERMINAL), None)
    complete = (
        protocol == "aiwf-spark-stdout-v1"
        and values.get("spark_protocol_end") == protocol
        and terminal is not None
    )
    result = {
        "schema_version": 1,
        "protocol": protocol,
        "complete": complete,
        "terminal_status": terminal,
        "started": "started" in status_history,
        "truncated": values.get("spark_output_truncated") == "yes",
        "auto_disabled": values.get("spark_auto_disabled") == "yes",
        "failure_class": values.get("spark_failure_class"),
        "model_response_received": values.get("spark_model_response_received") == "yes",
        "fields": values,
    }
    mode = values.get("spark_mode", "")
    if mode in {"execution-cost-estimator", "task-size-classifier", "preflight-bundle"}:
        kind = "route"
    elif mode == "monitor-triage":
        kind = "monitor"
    elif mode == "failure-triage":
        kind = "failure"
    elif mode == "parallel-planner":
        kind = "parallel"
    else:
        kind = None
    if kind and terminal == "success":
        try:
            embedded = values.get("spark_decision_json")
            result["structured_decision"] = parse_and_normalize(kind, embedded or text)
            result["decision_valid"] = True
        except (ValueError, json.JSONDecodeError) as exc:
            result["decision_valid"] = False
            result["decision_error"] = str(exc)[:160]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, help="Spark stdout file; default stdin")
    parser.add_argument("--require-terminal", action="store_true")
    args = parser.parse_args()
    text = args.path.read_text(encoding="utf-8", errors="replace") if args.path else sys.stdin.read()
    result = parse(text)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result["complete"] or not args.require_terminal else 2


if __name__ == "__main__":
    raise SystemExit(main())
