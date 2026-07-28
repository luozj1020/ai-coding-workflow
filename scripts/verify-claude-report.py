#!/usr/bin/env python3
"""Check mechanical Claude report claims against an isolated worktree diff.

This helper deliberately does not judge semantic correctness.  It verifies
machine-readable file/symbol/count/cleanliness claims before Codex spends
tokens on semantic review and writes a compact schema-v1 JSON artifact.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

CONTROL_FILES = {
    "TASK_CARD.md", "TASK_CARD_FULL.md", "CLAUDE_TASK_CARD.md",
    "CLAUDE_PROMPT.md", "CLAUDE_PROGRESS.md", "CLAUDE_REPORT.md",
    "ADVISOR_REQUEST.json", "advisor-packet.json", "advisor-packet.md",
    "advisor-decision.json", "advisor-response-validated.json",
    "advisor-call-result.json", "truncation-manifest.json",
}
PATH_LINE = re.compile(r"^claimed_file=(.+)$", re.M)
SYMBOL_LINE = re.compile(r"^claimed_symbol=(.+)$", re.M)
COUNT_LINE = re.compile(r"^claimed_changed_file_count=(\d+)$", re.M)
CLEAN_LINE = re.compile(r"^claimed_no_unexpected_files=(yes|no)$", re.M)
TEST_COUNT_LINE = re.compile(r"^claimed_test_count=(\d+)$", re.M)
VALIDATION_COMMAND_LINE = re.compile(r"^claimed_validation_command=(.+)$", re.M)
VALIDATION_EXIT_LINE = re.compile(r"^claimed_validation_exit_code=(-?\d+)$", re.M)
RESOLVED_FINDING_LINE = re.compile(r"^resolved_finding=(.+)$", re.M)
ACCEPTANCE_TEST_LINE = re.compile(r"^acceptance_test=(.+)$", re.M)
PROSE_TEST_COUNT = re.compile(
    r"(?:added|created|新增|增加)?\s*(\d+)\s*(?:new\s+)?(?:tests?|test cases?|个?测试)", re.I,
)
PROSE_FILE_COUNT = re.compile(
    r"(?:modified|changed|updated|修改(?:了)?|变更(?:了)?)\s*(\d+)\s*(?:个\s*)?(?:files?|文件)", re.I,
)
TEST_DECLARATION = re.compile(
    r"(?:\bdef\s+test_[A-Za-z0-9_]+\s*\(|\b(?:it|test)\s*\(|#\[test\]|\bfunc\s+Test[A-Za-z0-9_]+\s*\()"
)


def _git(worktree: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(worktree), *args], text=True, encoding="utf-8",
        errors="replace", capture_output=True, check=False,
    )
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or "git command failed")
    return proc.stdout


def _changed_paths(worktree: Path, base: str) -> Tuple[List[str], List[str]]:
    tracked = set(
        p for p in _git(worktree, "diff", "--name-only", base, "--").splitlines()
        if p and Path(p).name not in CONTROL_FILES
    )
    untracked = set(
        p for p in _git(worktree, "ls-files", "--others", "--exclude-standard").splitlines()
        if p and Path(p).name not in CONTROL_FILES
    )
    return sorted(tracked | untracked), sorted(untracked)


def _diff_text(worktree: Path, base: str, untracked: Iterable[str]) -> str:
    chunks = [_git(worktree, "diff", "--no-ext-diff", base, "--")]
    for path in untracked:
        candidate = worktree / path
        if candidate.is_file():
            chunks.append(candidate.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def _added_text(worktree: Path, base: str, untracked: Iterable[str]) -> str:
    chunks = [_git(worktree, "diff", "--no-ext-diff", "--unified=0", base, "--")]
    added = [line[1:] for line in chunks[0].splitlines()
             if line.startswith("+") and not line.startswith("+++")]
    for path in untracked:
        candidate = worktree / path
        if candidate.is_file():
            added.extend(candidate.read_text(encoding="utf-8", errors="replace").splitlines())
    return "\n".join(added)


def _added_text_for_paths(
    worktree: Path, base: str, paths: Iterable[str], untracked: Iterable[str],
) -> str:
    selected = list(paths)
    untracked_set = set(untracked)
    tracked = [path for path in selected if path not in untracked_set]
    added: List[str] = []
    if tracked:
        patch = _git(worktree, "diff", "--no-ext-diff", "--unified=0", base, "--", *tracked)
        added.extend(line[1:] for line in patch.splitlines()
                     if line.startswith("+") and not line.startswith("+++"))
    for path in selected:
        if path not in untracked_set:
            continue
        candidate = worktree / path
        if candidate.is_file():
            added.extend(candidate.read_text(encoding="utf-8", errors="replace").splitlines())
    return "\n".join(added)


def _is_test_path(path: str) -> bool:
    normalized = "/" + path.replace("\\", "/").lower().strip("/")
    name = Path(path).name.lower()
    return (
        "/tests/" in normalized or "/test/" in normalized or "/__tests__/" in normalized
        or name.startswith("test_") or name.endswith("_test.py")
        or name.endswith((".test.ts", ".test.tsx", ".test.js", ".test.jsx", ".spec.ts", ".spec.tsx", ".spec.js", ".spec.jsx"))
    )


def _task_field(text: str, name: str) -> str:
    table = re.search(
        rf"^\|\s*{re.escape(name)}\s*\|\s*(.*?)\s*\|\s*$",
        text, re.I | re.M,
    )
    if table:
        return table.group(1).strip()
    bullet = re.search(
        rf"^-\s*{re.escape(name)}\s*:\s*(.*?)\s*$",
        text, re.I | re.M,
    )
    return bullet.group(1).strip() if bullet else ""


def _task_requirements(task_card: Optional[Path]) -> Dict[str, object]:
    if not task_card or not task_card.is_file():
        return {
            "tests_required": False, "validation_required": False,
            "acceptance_to_test_ids": [], "exact_validation_command": "",
        }
    text = task_card.read_text(encoding="utf-8", errors="replace")
    tests_required = bool(re.search(r"(?im)^\s*-?\s*Tests required:\s*yes\s*$", text))
    owner = re.search(r"(?im)^\|\s*Test writing\s*\|\s*([^|]+)\|", text)
    if owner and re.search(r"\b(?:claude|builder|checker|model|executor)\b", owner.group(1), re.I):
        tests_required = True
    validation_required = bool(re.search(r"(?i)Validation required:\s*yes(?:\b|\s)", text))
    validation_owner = re.search(r"(?im)^\|\s*Narrow validation\s*\|\s*([^|]+)\|", text)
    if validation_owner and re.search(r"\b(?:claude|builder|checker|model|executor)\b", validation_owner.group(1), re.I):
        validation_required = True
    acceptance_value = _task_field(text, "Acceptance-to-test IDs")
    exact_validation_command = _task_field(text, "Exact narrow command").strip().strip("`")
    acceptance_ids = [] if acceptance_value.lower() in {
        "", "none", "n/a", "not-required",
    } else sorted({
        item.strip().strip("`")
        for item in re.split(r"[,;]", acceptance_value)
        if item.strip()
    })
    return {
        "tests_required": tests_required,
        "validation_required": validation_required,
        "acceptance_to_test_ids": acceptance_ids,
        "exact_validation_command": exact_validation_command,
    }


def _parse_resolved_finding(value: str) -> Dict[str, str]:
    parts = [part.strip() for part in value.split("|") if part.strip()]
    result = {"finding_id": parts[0] if parts else ""}
    for part in parts[1:]:
        if "=" in part:
            key, item = part.split("=", 1)
            result[key.strip().lower()] = item.strip().strip("`")
    return result


def _parse_acceptance_test(value: str) -> Dict[str, str]:
    parts = [part.strip() for part in value.split("|") if part.strip()]
    result: Dict[str, str] = {}
    if parts and "=" not in parts[0]:
        result["acceptance_id"] = parts.pop(0)
    for part in parts:
        if "=" in part:
            key, item = part.split("=", 1)
            result[key.strip().lower()] = item.strip().strip("`")
    if "id" in result and "acceptance_id" not in result:
        result["acceptance_id"] = result["id"]
    return result


def _prose_changed_files(report_text: str) -> List[str]:
    section = re.search(r"(?ims)^## Files Changed\s*$\n(.*?)(?=^##\s|\Z)", report_text)
    if not section:
        return []
    paths = set()
    for line in section.group(1).splitlines():
        if not re.match(r"^\s*[-*]", line):
            continue
        backticked = re.findall(r"`([^`]+)`", line)
        candidates = backticked or re.findall(
            r"^\s*[-*]\s+([^\s:—–]+\.[A-Za-z0-9]+)(?:\s|:|—|–|$)", line,
        )
        for candidate in candidates:
            value = candidate.strip().replace("\\", "/")
            if "/" in value or "." in Path(value).name:
                paths.add(value)
    return sorted(paths)


def _report_section(report_text: str, heading: str) -> str:
    section = re.search(
        rf"(?ims)^##\s+{re.escape(heading)}\s*$\n(.*?)(?=^##\s|\Z)",
        report_text,
    )
    return section.group(1).strip() if section else ""


def _standard_no_unexpected_files(report_text: str) -> bool:
    section = _report_section(report_text, "Out-of-Scope Confirmation")
    if not section:
        return False
    compact = " ".join(section.split()).strip(" .:-").lower()
    return compact in {"none", "no", "n/a", "not applicable", "无", "没有"} or bool(
        re.search(
            r"\b(?:no|without)\s+(?:unexpected|unlisted|out-of-scope|extra)\s+"
            r"(?:files?|changes?|writes?)\b",
            compact,
        )
    )


def _standard_validation_exit(report_text: str, command: str) -> Optional[int]:
    if not command:
        return None
    checks = _report_section(report_text, "Checks Run")
    for line in checks.splitlines():
        cleaned = line.strip().strip("-* ").replace("`", "")
        if not cleaned.startswith(command):
            continue
        if re.search(r"\b(?:failed|failure|error|blocked|denied)\b", cleaned, re.I):
            return 1
        if re.search(r"\b(?:passed|pass|success|succeeded|green|ok)\b", cleaned, re.I):
            return 0
    return None


def _repo_contains(worktree: Path, needle: str) -> bool:
    if not needle:
        return False
    try:
        if _git(worktree, "grep", "-F", "-e", needle, "--").strip():
            return True
    except RuntimeError:
        pass
    for path in _changed_paths(worktree, "HEAD")[1]:
        candidate = worktree / path
        if candidate.is_file() and needle in candidate.read_text(encoding="utf-8", errors="replace"):
            return True
    return False


def verify(
    report: Path, worktree: Path, base: str = "HEAD", task_card: Optional[Path] = None,
) -> Dict[str, object]:
    report_text = report.read_text(encoding="utf-8", errors="replace")
    actual_files, untracked = _changed_paths(worktree, base)
    diff_text = _diff_text(worktree, base, untracked)
    added_text = _added_text(worktree, base, untracked)
    claimed_files = sorted({v.strip().strip("`") for v in PATH_LINE.findall(report_text) if v.strip()})
    claimed_symbols = sorted({v.strip().strip("`") for v in SYMBOL_LINE.findall(report_text) if v.strip()})
    count_match = COUNT_LINE.search(report_text)
    clean_match = CLEAN_LINE.search(report_text)
    test_count_match = TEST_COUNT_LINE.search(report_text)
    validation_command_match = VALIDATION_COMMAND_LINE.search(report_text)
    validation_exit_match = VALIDATION_EXIT_LINE.search(report_text)
    requirements = _task_requirements(task_card)
    actual_test_files = sorted(path for path in actual_files if _is_test_path(path))
    added_test_text = _added_text_for_paths(worktree, base, actual_test_files, untracked)
    actual_test_count = len(TEST_DECLARATION.findall(added_test_text))
    prose_test_counts = sorted({int(value) for value in PROSE_TEST_COUNT.findall(report_text)})
    prose_file_counts = sorted({int(value) for value in PROSE_FILE_COUNT.findall(report_text)})
    prose_files = _prose_changed_files(report_text)

    checks: List[Dict[str, object]] = []
    conflicts: List[str] = []
    missing_claims: List[str] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"check": name, "status": status, "detail": detail})
        if status == "conflict":
            conflicts.append(detail)

    effective_claimed_files = claimed_files or prose_files
    file_claim_source = "machine" if claimed_files else ("standard-report" if prose_files else "none")
    if effective_claimed_files:
        missing = sorted(set(effective_claimed_files) - set(actual_files))
        unclaimed = sorted(set(actual_files) - set(effective_claimed_files))
        add("claimed_files", "conflict" if missing or unclaimed else "matched",
            f"source={file_claim_source}; missing_from_diff={missing}; unclaimed_actual={unclaimed}")
    else:
        add(
            "claimed_files", "not-claimed",
            "add claimed_file=<path> lines or a complete standard ## Files Changed list",
        )
        missing_claims.append("claimed_files")

    if prose_files:
        missing = sorted(set(prose_files) - set(actual_files))
        omitted = sorted(set(actual_files) - set(prose_files))
        add("prose_files_changed", "conflict" if missing or omitted else "matched",
            f"missing_from_diff={missing}; omitted_actual={omitted}")
    for prose_count in prose_file_counts:
        add("prose_changed_file_count", "matched" if prose_count == len(actual_files) else "conflict",
            f"claimed={prose_count}; actual={len(actual_files)}")

    if count_match:
        claimed_count = int(count_match.group(1))
        add("claimed_changed_file_count", "matched" if claimed_count == len(actual_files) else "conflict",
            f"claimed={claimed_count}; actual={len(actual_files)}")
    elif effective_claimed_files:
        add(
            "claimed_changed_file_count",
            "matched" if len(effective_claimed_files) == len(actual_files) else "conflict",
            f"source={file_claim_source}; claimed={len(effective_claimed_files)}; actual={len(actual_files)}",
        )
    else:
        add("claimed_changed_file_count", "not-claimed", f"actual={len(actual_files)}")
        missing_claims.append("claimed_changed_file_count")

    for symbol in claimed_symbols:
        add(f"symbol:{symbol}", "matched" if symbol in diff_text else "conflict",
            f"claimed_symbol={symbol}; present_in_diff={'yes' if symbol in diff_text else 'no'}")
    if not claimed_symbols:
        add("claimed_symbols", "not-claimed", "optional; use claimed_symbol=<name> for important wiring claims")

    unexpected_untracked = sorted(set(untracked) - set(effective_claimed_files))
    if clean_match and clean_match.group(1) == "yes":
        add("no_unexpected_files", "matched" if not unexpected_untracked else "conflict",
            f"unexpected_untracked={unexpected_untracked}")
    elif clean_match:
        add("no_unexpected_files", "declared-no", f"unexpected_untracked={unexpected_untracked}")
    elif _standard_no_unexpected_files(report_text) and effective_claimed_files:
        add(
            "no_unexpected_files",
            "matched" if not unexpected_untracked else "conflict",
            f"source=standard-report; unexpected_untracked={unexpected_untracked}",
        )
    else:
        add(
            "no_unexpected_files", "not-claimed",
            f"unexpected_untracked={unexpected_untracked}; add the machine claim or an explicit standard Out-of-Scope Confirmation",
        )
        missing_claims.append("claimed_no_unexpected_files")

    declared_test_counts = list(prose_test_counts)
    if test_count_match:
        declared_test_counts.append(int(test_count_match.group(1)))
    for declared in sorted(set(declared_test_counts)):
        add(
            f"claimed_test_count:{declared}",
            "matched" if declared == actual_test_count else "conflict",
            f"claimed={declared}; detected_added_test_declarations={actual_test_count}; test_files={actual_test_files}",
        )
    if any(value > 0 for value in declared_test_counts) and not actual_test_files:
        add("claimed_test_files", "conflict", "report claims added tests but diff contains no test file")
    if requirements["tests_required"] and not actual_test_files:
        add("required_test_diff", "conflict", "task assigns test writing but diff contains no test file")
    elif requirements["tests_required"] and not declared_test_counts:
        add("claimed_test_count", "not-claimed", "task assigns test writing; add claimed_test_count=<n>")
        missing_claims.append("claimed_test_count")

    validation_status = "not-required"
    if requirements["validation_required"]:
        standard_validation_exit = _standard_validation_exit(
            report_text, str(requirements.get("exact_validation_command") or ""),
        )
        if (
            not validation_command_match
            and standard_validation_exit is not None
        ):
            add(
                "validation_command",
                "matched",
                f"source=standard-report; command={requirements['exact_validation_command']}",
            )
        if not validation_command_match and standard_validation_exit is None:
            validation_status = "missing-evidence"
            missing_claims.append("claimed_validation_command")
        elif not validation_exit_match and standard_validation_exit is None:
            validation_status = "missing-evidence"
            missing_claims.append("claimed_validation_exit_code")
        elif standard_validation_exit == 0 and not validation_exit_match:
            validation_status = "claimed-unverified"
            add("validation_exit_code", "matched", "source=standard-report; claimed exit=0")
        elif standard_validation_exit not in (None, 0) and not validation_exit_match:
            validation_status = "failed"
            add("validation_exit_code", "conflict", "standard report records validation failure")
        elif int(validation_exit_match.group(1)) == 0:
            validation_status = "claimed-unverified"
        else:
            validation_status = "failed"
            add("validation_exit_code", "conflict", f"claimed exit={validation_exit_match.group(1)}")

    resolved_findings: List[Dict[str, str]] = []
    for raw in RESOLVED_FINDING_LINE.findall(report_text):
        finding = _parse_resolved_finding(raw)
        resolved_findings.append(finding)
        finding_id = finding.get("finding_id", "unknown")
        path = finding.get("file", "")
        symbol = finding.get("symbol", "")
        test = finding.get("test", "")
        if not path or path not in actual_files:
            add(f"resolved_finding:{finding_id}:file", "conflict", f"file={path or 'missing'} is not in actual diff")
        if not symbol or symbol not in diff_text:
            add(f"resolved_finding:{finding_id}:symbol", "conflict", f"symbol={symbol or 'missing'} is not in diff")
        if not test:
            add(f"resolved_finding:{finding_id}:test", "conflict", "test evidence is missing")
        elif test.lower() != "not-required" and test not in added_text and not _repo_contains(worktree, test):
            add(f"resolved_finding:{finding_id}:test", "conflict", f"test={test} is not evidenced")

    required_acceptance_ids = set(
        requirements.get("acceptance_to_test_ids") or []
    )
    acceptance_test_claims: List[Dict[str, str]] = []
    supported_acceptance_ids: Set[str] = set()
    claimed_acceptance_ids: Set[str] = set()
    for raw in ACCEPTANCE_TEST_LINE.findall(report_text):
        claim = _parse_acceptance_test(raw)
        acceptance_test_claims.append(claim)
        acceptance_id = claim.get("acceptance_id", "")
        path = claim.get("file", "").replace("\\", "/")
        test = claim.get("test", "")
        assertion = claim.get("assertion", "")
        if acceptance_id:
            claimed_acceptance_ids.add(acceptance_id)
        missing = [
            name for name, value in (
                ("acceptance_id", acceptance_id), ("file", path),
                ("test", test), ("assertion", assertion),
            ) if not value
        ]
        if missing:
            add(
                f"acceptance_test:{acceptance_id or 'missing-id'}",
                "conflict",
                f"acceptance_test claim missing fields={missing}",
            )
            continue
        if acceptance_id not in required_acceptance_ids:
            add(
                f"acceptance_test:{acceptance_id}",
                "conflict",
                f"acceptance_id={acceptance_id} is not required by task card",
            )
            continue
        if path not in actual_test_files:
            add(
                f"acceptance_test:{acceptance_id}:file",
                "conflict",
                f"file={path} is not a changed test file",
            )
            continue
        candidate = worktree / path
        content = candidate.read_text(encoding="utf-8", errors="replace")
        if test not in content:
            add(
                f"acceptance_test:{acceptance_id}:test",
                "conflict",
                f"test={test} is not present in {path}",
            )
            continue
        if assertion not in content:
            add(
                f"acceptance_test:{acceptance_id}:assertion",
                "conflict",
                f"assertion={assertion} is not present in {path}",
            )
            continue
        supported_acceptance_ids.add(acceptance_id)
        add(
            f"acceptance_test:{acceptance_id}",
            "matched",
            f"file={path}; test={test}; assertion={assertion}",
        )
    for acceptance_id in sorted(required_acceptance_ids - supported_acceptance_ids):
        if acceptance_id in claimed_acceptance_ids:
            continue
        add(
            f"acceptance_test:{acceptance_id}",
            "unverified",
            f"acceptance_id={acceptance_id} has no acceptance_test claim",
        )
        missing_claims.append(f"acceptance_test:{acceptance_id}")

    status = "conflict" if conflicts else ("insufficient-claims" if missing_claims else "matched")
    return {
        "schema_version": 1,
        "status": status,
        "semantic_review_required": True,
        "acceptance_satisfied": False,
        "base": base,
        "actual_changed_files": actual_files,
        "actual_test_files": actual_test_files,
        "detected_added_test_declarations": actual_test_count,
        "task_requirements": requirements,
        "validation_status": validation_status,
        "artifact_valid": status == "matched",
        "completion_state": "semantic-review-required" if status == "matched" else "needs-review",
        "missing_claims": sorted(set(missing_claims)),
        "resolved_findings": resolved_findings,
        "acceptance_test_claims": acceptance_test_claims,
        "acceptance_test_coverage": {
            "required": sorted(required_acceptance_ids),
            "supported": sorted(supported_acceptance_ids),
            "unverified": sorted(required_acceptance_ids - supported_acceptance_ids),
        },
        "checks": checks,
        "conflicts": conflicts,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--base", default="HEAD")
    parser.add_argument("--task-card", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fail-on-conflict", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = verify(
            args.report.resolve(), args.worktree.resolve(), args.base,
            args.task_card.resolve() if args.task_card else None,
        )
    except (OSError, RuntimeError) as exc:
        result = {"schema_version": 1, "status": "error", "error": str(exc),
                  "semantic_review_required": True, "acceptance_satisfied": False}
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    if result.get("status") == "error":
        return 2
    if args.fail_on_conflict and result.get("status") == "conflict":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
