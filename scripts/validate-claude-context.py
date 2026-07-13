#!/usr/bin/env python3
"""Validate that a Claude Context Packet is dense enough for bounded execution."""
import argparse, json, re
from pathlib import Path

REQUIRED = (
    "target files/modules", "relevant symbols/functions", "reference examples / source of truth",
    "do not read / do not modify", "known constraints", "narrow validation commands",
    "context is sufficient for execution?",
)

def parse(text):
    fields = {}
    active = False
    for line in text.splitlines():
        if line.strip() == "## Claude Context Packet": active = True; continue
        if active and line.startswith("## "): break
        if active and line.strip().startswith("|"):
            cells = [v.strip() for v in line.strip().strip("|").split("|")]
            if len(cells) >= 2 and cells[0].lower() not in {"field", "-------"}:
                fields[cells[0].lower()] = cells[1]
    return fields

def validate(path):
    fields = parse(path.read_text(encoding="utf-8", errors="replace"))
    missing = [name for name in REQUIRED if not fields.get(name) or fields[name].lower() in {"tbd", "unknown"}]
    targets = [v for v in re.split(r"[,;\n]", fields.get("target files/modules", "")) if v.strip()]
    sufficient = fields.get("context is sufficient for execution?", "").lower() == "yes"
    explicit_eligible = fields.get("execution-only eligible?", "").lower() == "yes"
    return {"schema_version": 1, "complete": not missing, "missing_fields": missing,
            "target_file_count": len(targets), "target_count_recommended": 1 <= len(targets) <= 5,
            "execution_only_eligible": not missing and sufficient and explicit_eligible}

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("task_card", type=Path); p.add_argument("--require-complete", action="store_true"); a=p.parse_args()
    result=validate(a.task_card); print(json.dumps(result, sort_keys=True)); return 1 if a.require_complete and not result["complete"] else 0
if __name__ == "__main__": raise SystemExit(main())
