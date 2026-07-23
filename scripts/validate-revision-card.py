#!/usr/bin/env python3
"""Require exact inline review findings in revision task cards."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict, List, Optional


def revision_section(text: str) -> Optional[str]:
    match = re.search(r"(?ims)^## Revision Delta\s*$\n(.*?)(?=^##\s|\Z)", text)
    return match.group(1) if match else None


def validate(path: Path) -> Dict[str, object]:
    section = revision_section(path.read_text(encoding="utf-8", errors="replace"))
    if section is None:
        return {"schema_version": 1, "status": "not-applicable", "findings": [], "errors": []}
    findings: List[Dict[str, str]] = []
    errors: List[str] = []
    for line in section.splitlines():
        if "finding_id=" not in line:
            continue
        fields: Dict[str, str] = {}
        for part in line.strip().lstrip("- ").strip("`").split("|"):
            if "=" in part:
                key, value = part.split("=", 1)
                fields[key.strip().lower()] = value.strip().strip("`")
        findings.append(fields)
    if not findings:
        errors.append("revision card must inline at least one structured review finding")
    required = ("finding_id", "evidence", "required_change", "acceptance")
    for index, finding in enumerate(findings):
        for field in required:
            value = finding.get(field, "")
            if not value or "<" in value or ">" in value:
                errors.append(f"finding[{index}].{field} must be concrete and placeholder-free")
    return {
        "schema_version": 1,
        "status": "invalid" if errors else "valid",
        "findings": findings,
        "errors": errors,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_card", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = validate(args.task_card.resolve())
    except OSError as exc:
        result = {"schema_version": 1, "status": "error", "findings": [], "errors": [str(exc)]}
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if result["status"] in {"valid", "not-applicable"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
