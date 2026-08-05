#!/usr/bin/env python3
"""Validate and freeze a Claude-drafted solution contract.

The helper is deterministic and stdlib-only. It deliberately separates Claude's
convergent planning from Codex's single adversarial review. Only unresolved
blocking findings or an incorporated spec change prevent freezing; recommended
and backlog findings never reopen the accepted implementation contract.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List

REQUIRED = (
    "schema_version", "task_id", "goal", "end_state", "invariants",
    "non_goals", "unknowns", "acceptance", "slices",
)
SEVERITIES = {"blocking", "recommended", "backlog", "spec-change"}
DISPOSITIONS = {"fix-now", "defer", "reject", "incorporate"}


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_contract(data: Any) -> List[str]:
    errors: List[str] = []
    if not isinstance(data, dict):
        return ["contract must be an object"]
    for field in REQUIRED:
        if field not in data:
            errors.append("missing required field: {}".format(field))
    if data.get("schema_version") not in {1, 2}:
        errors.append("schema_version must be 1 or 2")
    for field in ("task_id", "goal", "end_state"):
        if field in data and not _nonempty(data[field]):
            errors.append("{} must be a non-empty string".format(field))
    for field in ("non_goals", "unknowns"):
        value = data.get(field)
        if value is not None and (
            not isinstance(value, list) or any(not _nonempty(item) for item in value)
        ):
            errors.append("{} must be an array of non-empty strings".format(field))
    invariants = data.get("invariants")
    if not isinstance(invariants, list) or not invariants:
        errors.append("invariants must not be empty")
    elif data.get("schema_version") == 1:
        if any(not _nonempty(item) for item in invariants):
            errors.append("schema_version 1 invariants must be non-empty strings")
    else:
        invariant_ids = set()
        expected_invariant = {"id", "description", "acceptance_ids"}
        for index, item in enumerate(invariants):
            if not isinstance(item, dict) or set(item) != expected_invariant:
                errors.append(
                    "schema_version 2 invariants[{}] must contain only id, description, acceptance_ids".format(index)
                )
                continue
            if not _nonempty(item.get("id")) or not _nonempty(item.get("description")):
                errors.append("invariants[{}] id and description must be non-empty".format(index))
            elif item["id"] in invariant_ids:
                errors.append("duplicate invariant id: {}".format(item["id"]))
            else:
                invariant_ids.add(item["id"])
            if not isinstance(item.get("acceptance_ids"), list) or not item["acceptance_ids"] or any(
                not _nonempty(value) for value in item.get("acceptance_ids", [])
            ):
                errors.append("invariants[{}].acceptance_ids must be a non-empty string array".format(index))

    acceptance = data.get("acceptance")
    acceptance_ids = set()
    if not isinstance(acceptance, list) or not acceptance:
        errors.append("acceptance must be a non-empty array")
    else:
        for index, item in enumerate(acceptance):
            if not isinstance(item, dict) or set(item) != {"id", "description"}:
                errors.append("acceptance[{}] must contain only id and description".format(index))
                continue
            if not _nonempty(item["id"]) or not _nonempty(item["description"]):
                errors.append("acceptance[{}] fields must be non-empty strings".format(index))
            elif item["id"] in acceptance_ids:
                errors.append("duplicate acceptance id: {}".format(item["id"]))
            else:
                acceptance_ids.add(item["id"])
    if data.get("schema_version") == 2 and isinstance(invariants, list):
        for index, item in enumerate(invariants):
            if not isinstance(item, dict):
                continue
            for aid in item.get("acceptance_ids", []):
                if aid not in acceptance_ids:
                    errors.append(
                        "invariants[{}] references unknown acceptance id: {}".format(index, aid)
                    )

    slices = data.get("slices")
    slice_ids = set()
    if not isinstance(slices, list) or not slices:
        errors.append("slices must be a non-empty array")
    else:
        expected = {"id", "goal", "write_scope", "depends_on", "acceptance_ids"}
        for index, item in enumerate(slices):
            if not isinstance(item, dict) or set(item) != expected:
                errors.append("slices[{}] has invalid fields".format(index))
                continue
            sid = item.get("id")
            if not _nonempty(sid) or not _nonempty(item.get("goal")):
                errors.append("slices[{}] id and goal must be non-empty".format(index))
            elif sid in slice_ids:
                errors.append("duplicate slice id: {}".format(sid))
            else:
                slice_ids.add(sid)
            for field in ("write_scope", "depends_on", "acceptance_ids"):
                value = item.get(field)
                if not isinstance(value, list) or any(not _nonempty(v) for v in value):
                    errors.append("slices[{}].{} must be a string array".format(index, field))
            if isinstance(item.get("write_scope"), list) and not item["write_scope"]:
                errors.append("slices[{}].write_scope must not be empty".format(index))
            if isinstance(item.get("acceptance_ids"), list) and not item["acceptance_ids"]:
                errors.append("slices[{}].acceptance_ids must not be empty".format(index))
            for aid in item.get("acceptance_ids", []):
                if aid not in acceptance_ids:
                    errors.append("slices[{}] references unknown acceptance id: {}".format(index, aid))
        for index, item in enumerate(slices):
            if isinstance(item, dict):
                for dependency in item.get("depends_on", []):
                    if dependency not in slice_ids:
                        errors.append("slices[{}] references unknown dependency: {}".format(index, dependency))
                    if dependency == item.get("id"):
                        errors.append("slice {} cannot depend on itself".format(dependency))
    return errors


def validate_findings(data: Any) -> List[str]:
    errors: List[str] = []
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        return ["findings document must contain a findings array"]
    for index, finding in enumerate(data["findings"]):
        if not isinstance(finding, dict):
            errors.append("findings[{}] must be an object".format(index))
            continue
        if finding.get("severity") not in SEVERITIES:
            errors.append("findings[{}].severity is invalid".format(index))
        if finding.get("disposition") not in DISPOSITIONS:
            errors.append("findings[{}].disposition is invalid".format(index))
        if not _nonempty(finding.get("summary")):
            errors.append("findings[{}].summary must be non-empty".format(index))
    return errors


def freeze(contract: Dict[str, Any], findings: Dict[str, Any]) -> Dict[str, Any]:
    blockers = [
        row for row in findings["findings"]
        if row["severity"] == "blocking" or (
            row["severity"] == "spec-change" and row["disposition"] == "incorporate"
        )
    ]
    if blockers:
        raise ValueError("contract cannot freeze: {} unresolved blocking finding(s)".format(len(blockers)))
    draft = {key: value for key, value in contract.items() if key not in {"state", "contract_hash", "review"}}
    canonical = json.dumps(draft, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    result = dict(draft)
    result["state"] = "frozen"
    result["contract_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    result["review"] = {
        "adversarial_rounds": 1,
        "stop_rule": "only-blocking-or-incorporated-spec-change-reopens",
        "finding_counts": {
            severity: sum(1 for row in findings["findings"] if row["severity"] == severity)
            for severity in sorted(SEVERITIES)
        },
        "deferred": [
            row["summary"] for row in findings["findings"]
            if row["severity"] in {"recommended", "backlog", "spec-change"}
        ],
    }
    if contract.get("schema_version") == 2:
        matrix_path = Path(__file__).resolve().with_name("build-invariant-acceptance-matrix.py")
        spec = importlib.util.spec_from_file_location("aiwf_invariant_matrix", matrix_path)
        if spec is None or spec.loader is None:
            raise ValueError("invariant acceptance matrix helper is unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        matrix = module.build(result)
        if matrix["errors"] or not matrix["all_invariants_mapped"]:
            raise ValueError("contract cannot freeze: invariant acceptance matrix is incomplete")
        result["acceptance_matrix"] = matrix
    return result


def _write_atomic(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("contract", type=Path)
    review_parser = sub.add_parser(
        "review",
        help="Materialize Codex review findings without hand-editing control JSON.",
    )
    review_parser.add_argument(
        "--finding",
        action="append",
        default=[],
        metavar="SEVERITY:DISPOSITION:SUMMARY",
        help="Repeat for each finding; an empty review is valid.",
    )
    review_parser.add_argument("--output", type=Path, required=True)
    freeze_parser = sub.add_parser("freeze")
    freeze_parser.add_argument("contract", type=Path)
    freeze_parser.add_argument("--findings", type=Path, required=True)
    freeze_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "review":
            findings = {"findings": []}
            for raw in args.finding:
                parts = raw.split(":", 2)
                if len(parts) != 3:
                    raise ValueError(
                        "--finding must be SEVERITY:DISPOSITION:SUMMARY"
                    )
                findings["findings"].append({
                    "severity": parts[0],
                    "disposition": parts[1],
                    "summary": parts[2],
                })
            errors = validate_findings(findings)
            if errors:
                print(json.dumps({"status": "invalid", "errors": errors}, ensure_ascii=False, sort_keys=True))
                return 1
            _write_atomic(args.output, findings)
            print(json.dumps({"status": "written", "output": str(args.output)}, sort_keys=True))
            return 0
        contract = json.loads(args.contract.read_text(encoding="utf-8"))
        errors = validate_contract(contract)
        if args.command == "freeze":
            findings = json.loads(args.findings.read_text(encoding="utf-8"))
            errors.extend(validate_findings(findings))
        if errors:
            print(json.dumps({"status": "invalid", "errors": errors}, ensure_ascii=False, sort_keys=True))
            return 1
        if args.command == "validate":
            print(json.dumps({"status": "valid", "task_id": contract["task_id"]}, sort_keys=True))
            return 0
        frozen = freeze(contract, findings)
        _write_atomic(args.output, frozen)
        print(json.dumps({"status": "frozen", "output": str(args.output), "contract_hash": frozen["contract_hash"]}, sort_keys=True))
        return 0
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
