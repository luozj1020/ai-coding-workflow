#!/usr/bin/env python3
"""Compile bounded, provenance-bound execution guidance from a frozen task card.

The compiler intentionally does not summarize or reinterpret the task contract.
It selects only safe procedural cues from a local registry, while the task card
remains authoritative for write scope, acceptance, validation, authority, and
stop conditions.  The output is deterministic: identical card, facts, and
registry content produce identical packet content and receipt fields (apart
from caller-selected output paths).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


SCHEMA = "aiwf-context-compilation-v1"
PACKET_SCHEMA = "aiwf-compiled-context-packet-v1"
REGISTRY_SCHEMA = "aiwf-skill-context-rules-v1"
SAFE_CUE_KINDS = frozenset(("procedure", "retrieval", "validation", "output-contract"))
RULE_POLARITIES = frozenset(("positive", "negative"))
COMPILATION_STRATEGIES = frozenset(("coverage", "anchors-only"))
DEFAULT_MAX_CUES = 8
DEFAULT_MAX_RESCUED = 3
DEFAULT_MAX_BYTES = 12 * 1024

# The first group reflects the explicit route chosen by the Task Card.  The
# second is evidence discovered from bounded task facts.  Keeping the two
# sources distinct makes the SkillRAE-inspired two-route retrieval observable
# without making an embedding service or an LLM part of the safety boundary.
TOP_DOWN_CONDITIONS = frozenset((
    "phases", "continuation_kinds", "roles", "presets", "builder_modes", "gates_any",
))
BOTTOM_UP_CONDITIONS = frozenset((
    "task_types", "repository_scales", "languages_any", "codegraph_statuses", "requires_sections",
))


class ContextCompileError(RuntimeError):
    """Raised when the deterministic compiler cannot produce safe output."""


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _atomic_write(path: Path, content: str) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _load_json(path: Path, label: str) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContextCompileError("cannot read {}: {}".format(label, exc)) from exc
    if not isinstance(value, dict):
        raise ContextCompileError("{} must contain a JSON object".format(label))
    return value


def _default_registry_path() -> Path:
    here = Path(__file__).resolve().parent
    candidates = (
        here.parent / "assets" / "skill-context" / "rules-v1.json",
        here / "skill-context" / "rules-v1.json",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ContextCompileError("skill-context rule registry is unavailable")


def _repository_root(path: Path) -> Path:
    try:
        result = subprocess.run(
            ["git", "-C", str(path.resolve().parent), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        result = None
    if result is not None and result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip()).resolve()
    return path.resolve().parent


def _sections(text: str) -> List[str]:
    return [match.group(1).strip() for match in re.finditer(r"(?m)^##[ \t]+([^\n]+?)\s*$", text)]


def _metadata(text: str, name: str) -> Dict[str, str]:
    pattern = r"(?is)<!--\s*{}\s*:\s*(.*?)\s*-->".format(re.escape(name))
    match = re.search(pattern, text)
    if not match:
        return {}
    result: Dict[str, str] = {}
    for part in match.group(1).split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip().lower()
        value = value.strip()
        if key:
            result[key] = value
    return result


def _table_value(text: str, field: str) -> str:
    expression = r"(?im)^\|\s*{}\s*\|\s*([^|]+?)\s*\|\s*$".format(re.escape(field))
    match = re.search(expression, text)
    return match.group(1).strip() if match else ""


def _normal_token(value: object) -> str:
    return re.sub(r"[^a-z0-9_.+-]+", "-", str(value).strip().lower()).strip("-")


def _token_list(value: object) -> List[str]:
    if isinstance(value, str):
        raw: Sequence[object] = re.split(r"[,\s]+", value)
    elif isinstance(value, (list, tuple, set)):
        raw = value
    else:
        raw = ()
    result: List[str] = []
    for item in raw:
        token = _normal_token(item)
        if token and token not in result:
            result.append(token)
    return result


def _facts(text: str, supplied: Optional[Dict[str, Any]], phase: str, continuation_kind: str) -> Dict[str, Any]:
    component = _metadata(text, "task-card-components")
    embedded = _metadata(text, "task-context-facts")
    mode = _normal_token(_table_value(text, "Mode") or component.get("task-mode") or "builder")
    preset = _normal_token(component.get("preset") or mode or "builder")
    builder_mode = _normal_token(component.get("builder-mode") or "auto")
    gates = _token_list(component.get("gates", ""))
    source = supplied or {}
    repository = source.get("repository") if isinstance(source.get("repository"), dict) else {}

    task_type = _normal_token(
        source.get("task_type") or embedded.get("task-type") or "unknown"
    )
    scale = _normal_token(
        source.get("repository_size")
        or repository.get("routing_scale")
        or embedded.get("repository-scale")
        or "unknown"
    )
    languages = _token_list(
        source.get("languages") or source.get("language") or embedded.get("languages", "")
    )
    codegraph_status = _normal_token(
        source.get("codegraph_status") or embedded.get("codegraph-status")
        or _table_value(text, "CodeGraph status") or "unknown"
    )
    return {
        "phase": _normal_token(phase),
        "continuation_kind": _normal_token(continuation_kind),
        "role": mode,
        "preset": preset,
        "builder_mode": builder_mode,
        "gates": gates,
        "task_type": task_type,
        "repository_scale": scale,
        "languages": languages,
        "codegraph_status": codegraph_status,
        "sections": sorted(_sections(text)),
    }


def _contract_anchors(sections: Sequence[str]) -> Dict[str, Any]:
    observed = set(sections)
    anchors = {
        "write_boundary": (
            "Scope", "Handoff Contract", "Revision Delta", "Required Changes", "Required Revisions"
        ),
        "acceptance": ("Acceptance Criteria",),
        "validation": ("Validation Contract",),
        "stop_conditions": ("Stop Conditions",),
        "report": ("Required Report",),
    }
    rows: List[Dict[str, Any]] = []
    for anchor, alternatives in anchors.items():
        present = [name for name in alternatives if name in observed]
        rows.append({
            "id": anchor,
            "status": "present" if present else "missing",
            "sections": present,
            "acceptable_sections": list(alternatives),
        })
    return {
        "authoritative": True,
        "rows": rows,
        "complete": all(row["status"] == "present" for row in rows),
    }


def _coverage_tags(rule: Dict[str, Any]) -> List[str]:
    """Return deterministic capability labels, preserving legacy registries.

    New registry entries declare ``coverage`` explicitly.  An untagged custom
    rule remains usable, but is marked legacy and cannot silently satisfy a
    named coverage requirement.  This makes migration safe while ensuring the
    built-in registry is selected by marginal coverage rather than raw rank.
    """
    coverage = rule.get("coverage")
    if coverage is None:
        return ["legacy-" + str(rule["id"])]
    return sorted(_token_list(coverage))


def _has_explicit_coverage(rule: Dict[str, Any]) -> bool:
    return "coverage" in rule


def _coverage_requirements(facts: Dict[str, Any], anchors: Dict[str, Any]) -> List[Dict[str, str]]:
    """Derive the small, auditable cue set this card actually needs.

    These labels do not replace task-card contracts.  They only state which
    *procedural* concerns need a local cue when one is available.  Missing
    labels remain visible in the receipt rather than being guessed.
    """
    requirements: List[Dict[str, str]] = []

    def require(identifier: str, reason: str) -> None:
        if not any(row["id"] == identifier for row in requirements):
            requirements.append({"id": identifier, "reason": reason})

    preset = facts.get("preset")
    if preset in {"builder", "batch-builder"}:
        require("implementation-scope", "builder preset")
    if preset == "checker":
        require("file-validation", "checker preset")
    if facts.get("continuation_kind") != "initial":
        require("continuation-scope", "continuation kind")
    gates = set(facts.get("gates", ()))
    if "large-repo" in gates:
        require("bounded-discovery", "large-repo gate")
    if "root-cause" in gates:
        require("root-cause-evidence", "root-cause gate")
    if "tdd" in gates:
        require("test-order", "tdd gate")
    sections = set(facts.get("sections", ()))
    if "Required Report" in sections:
        require("output-contract", "required report section")
    if (
        "Validation Contract" in sections
        and facts.get("task_type") in {"bugfix", "regression"}
    ):
        require("narrow-validation", "bugfix/regression validation contract")
    textual_languages = {"shell", "bash", "config", "bazel", "yaml", "toml"}
    if textual_languages.intersection(facts.get("languages", ())):
        require("textual-retrieval", "text/configuration language")
        require("textual-retrieval-boundary", "text/configuration language")
    if facts.get("codegraph_status") in {"ready", "used-for-concrete-symbol", "used"}:
        require("worktree-bound-graph", "ready CodeGraph evidence")
    return requirements


def _retrieval_routes(rule: Dict[str, Any]) -> List[str]:
    """Expose whether a candidate came from route topology, task facts, or both."""
    conditions = rule.get("conditions", {})
    routes: List[str] = []
    if any(conditions.get(key) for key in TOP_DOWN_CONDITIONS):
        routes.append("top-down")
    if any(conditions.get(key) for key in BOTTOM_UP_CONDITIONS):
        routes.append("bottom-up")
    # An unconditional compatibility cue has no semantic evidence; label it
    # conservatively as top-down rather than inventing a bottom-up match.
    return routes or ["top-down"]


def _validate_rule(rule: Dict[str, Any], index: int) -> None:
    identifier = rule.get("id")
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", identifier):
        raise ContextCompileError("registry rule[{}] has an invalid id".format(index))
    if rule.get("kind") not in SAFE_CUE_KINDS:
        raise ContextCompileError(
            "registry rule {} has a non-procedural or unsafe kind".format(identifier)
        )
    if rule.get("selection") not in {"anchor", "rescue"}:
        raise ContextCompileError("registry rule {} has an invalid selection".format(identifier))
    polarity = rule.get("polarity", "positive")
    if polarity not in RULE_POLARITIES:
        raise ContextCompileError("registry rule {} has an invalid polarity".format(identifier))
    conflict_group = rule.get("conflict_group")
    if conflict_group is not None and (
        not isinstance(conflict_group, str)
        or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", conflict_group)
    ):
        raise ContextCompileError("registry rule {} has an invalid conflict group".format(identifier))
    conflicts_with = rule.get("conflicts_with", [])
    if not isinstance(conflicts_with, list) or any(
        not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", value)
        for value in conflicts_with
    ):
        raise ContextCompileError("registry rule {} has invalid explicit conflicts".format(identifier))
    review_version = rule.get("review_version")
    if review_version is not None and (
        not isinstance(review_version, int) or review_version < 1
    ):
        raise ContextCompileError("registry rule {} has an invalid review version".format(identifier))
    if not isinstance(rule.get("text"), str) or not rule["text"].strip():
        raise ContextCompileError("registry rule {} has no cue text".format(identifier))
    if not isinstance(rule.get("priority"), int):
        raise ContextCompileError("registry rule {} has a non-integer priority".format(identifier))
    if not isinstance(rule.get("conditions", {}), dict):
        raise ContextCompileError("registry rule {} has invalid conditions".format(identifier))
    if "coverage" in rule:
        coverage = rule["coverage"]
        if (
            not isinstance(coverage, list)
            or not coverage
            or len(coverage) > 8
            or len(set(coverage)) != len(coverage)
            or any(
                not isinstance(value, str)
                or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", value)
                for value in coverage
            )
        ):
            raise ContextCompileError("registry rule {} has invalid coverage labels".format(identifier))
    source = rule.get("source")
    if not isinstance(source, dict) or not isinstance(source.get("path"), str):
        raise ContextCompileError("registry rule {} lacks source provenance".format(identifier))


def _matches(rule: Dict[str, Any], facts: Dict[str, Any]) -> bool:
    conditions = rule.get("conditions", {})
    scalar_conditions = {
        "phases": "phase",
        "continuation_kinds": "continuation_kind",
        "roles": "role",
        "presets": "preset",
        "builder_modes": "builder_mode",
        "task_types": "task_type",
        "repository_scales": "repository_scale",
        "codegraph_statuses": "codegraph_status",
    }
    for condition, fact_key in scalar_conditions.items():
        expected = _token_list(conditions.get(condition, ()))
        if expected and facts.get(fact_key) not in expected:
            return False
    gates_any = _token_list(conditions.get("gates_any", ()))
    if gates_any and not set(gates_any).intersection(facts.get("gates", ())):
        return False
    languages_any = _token_list(conditions.get("languages_any", ()))
    if languages_any and not set(languages_any).intersection(facts.get("languages", ())):
        return False
    sections = set(facts.get("sections", ()))
    required_sections = conditions.get("requires_sections", ())
    if required_sections and not set(required_sections).issubset(sections):
        return False
    return True


def _match_reason(rule: Dict[str, Any], facts: Dict[str, Any]) -> List[str]:
    """Return a deterministic, non-sensitive explanation for rule selection."""
    conditions = rule.get("conditions", {})
    scalar_conditions = {
        "phases": "phase",
        "continuation_kinds": "continuation_kind",
        "roles": "role",
        "presets": "preset",
        "builder_modes": "builder_mode",
        "task_types": "task_type",
        "repository_scales": "repository_scale",
        "codegraph_statuses": "codegraph_status",
    }
    reasons: List[str] = []
    for condition, fact_key in scalar_conditions.items():
        expected = _token_list(conditions.get(condition, ()))
        if expected:
            reasons.append("{}={}".format(fact_key, facts.get(fact_key, "unknown")))
    gates_any = _token_list(conditions.get("gates_any", ()))
    if gates_any:
        matched = sorted(set(gates_any).intersection(facts.get("gates", ())))
        reasons.append("gates={}".format(",".join(matched)))
    languages_any = _token_list(conditions.get("languages_any", ()))
    if languages_any:
        matched = sorted(set(languages_any).intersection(facts.get("languages", ())))
        reasons.append("languages={}".format(",".join(matched)))
    required_sections = conditions.get("requires_sections", ())
    if required_sections:
        reasons.append("sections=" + ",".join(sorted(str(value) for value in required_sections)))
    return reasons or ["unconditional"]


def _source_span(source: bytes, anchor: str) -> Dict[str, Any]:
    """Bind provenance to an exact Markdown heading from already-read source bytes."""
    unavailable = {
        "status": "unavailable",
        "start_line": None,
        "end_line": None,
        "sha256": None,
    }
    if not anchor:
        return {**unavailable, "status": "unanchored"}
    lines = source.decode("utf-8", errors="replace").splitlines()
    heading = re.compile(r"^(#{{1,6}})[ \t]+{}[ \t]*$".format(re.escape(anchor)))
    start = None
    level = None
    for index, line in enumerate(lines):
        match = heading.match(line)
        if match:
            start = index
            level = len(match.group(1))
            break
    if start is None or level is None:
        return {**unavailable, "status": "anchor-not-found"}
    end = len(lines)
    for index in range(start + 1, len(lines)):
        match = re.match(r"^(#{1,6})[ \\t]+", lines[index])
        if match and len(match.group(1)) <= level:
            end = index
            break
    body = "\n".join(lines[start:end]) + "\n"
    return {
        "status": "bound",
        "start_line": start + 1,
        "end_line": end,
        "sha256": _sha256_bytes(body.encode("utf-8")),
    }


def _source_provenance(rule: Dict[str, Any], source_root: Path) -> Dict[str, Any]:
    source = rule["source"]
    relative = Path(source["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ContextCompileError("registry rule {} has unsafe source path".format(rule["id"]))
    candidate = source_root / relative
    value: Dict[str, Any] = {
        "path": relative.as_posix(),
        "anchor": str(source.get("anchor") or ""),
        "status": "unavailable",
        "sha256": None,
    }
    try:
        raw = candidate.read_bytes()
    except OSError:
        value["span"] = _source_span(b"", value["anchor"])
    else:
        value["status"] = "bound"
        value["sha256"] = _sha256_bytes(raw)
        value["span"] = _source_span(raw, value["anchor"])
    return value


def _render_packet(
    task_card_sha256: str,
    facts: Dict[str, Any],
    anchors: Dict[str, Any],
    active: Sequence[Tuple[Dict[str, Any], Dict[str, Any]]],
    rescued: Sequence[Tuple[Dict[str, Any], Dict[str, Any]]],
) -> str:
    lines = [
        "<!-- {} -->".format(PACKET_SCHEMA),
        "## Compiled Execution Guidance",
        "",
        "This deterministic packet adds bounded procedural cues only. The exact Task Card remains authoritative for write scope, acceptance, validation, authority, and stop conditions.",
        "",
        "### Contract Anchors",
        "",
    ]
    for row in anchors["rows"]:
        if row["status"] == "present":
            lines.append("- `{}`: {}".format(row["id"], ", ".join("`{}`".format(value) for value in row["sections"])))
        else:
            lines.append("- `{}`: missing from this card; do not infer or replace it from this packet.".format(row["id"]))
    report_row = next(row for row in anchors["rows"] if row["id"] == "report")
    if report_row["status"] == "present":
        lines.extend([
            "",
            "### Output Contract Binding",
            "",
            "- The Task Card's `Required Report` section is authoritative. These cues cannot add report fields, replace declared checks, or turn progress/control files into completion evidence.",
        ])
    positive_active = [(rule, provenance) for rule, provenance in active if rule.get("polarity", "positive") == "positive"]
    negative_active = [(rule, provenance) for rule, provenance in active if rule.get("polarity", "positive") == "negative"]
    positive_rescued = [(rule, provenance) for rule, provenance in rescued if rule.get("polarity", "positive") == "positive"]
    negative_rescued = [(rule, provenance) for rule, provenance in rescued if rule.get("polarity", "positive") == "negative"]
    lines.extend(["", "### Active Procedure Cues", ""])
    if positive_active:
        for rule, _ in positive_active:
            lines.append("- `{}`: {}".format(rule["id"], rule["text"].strip()))
    else:
        lines.append("- No additional component-local procedure cue matched this task card.")
    if positive_rescued:
        lines.extend(["", "### Rescued Local Cues", ""])
        for rule, _ in positive_rescued:
            lines.append("- `{}`: {}".format(rule["id"], rule["text"].strip()))
    if negative_active or negative_rescued:
        lines.extend(["", "### Boundaries / Avoid", ""])
        for rule, _ in negative_active + negative_rescued:
            lines.append("- `{}`: {}".format(rule["id"], rule["text"].strip()))
    lines.extend(["", "### Provenance", ""])
    for rule, provenance in list(active) + list(rescued):
        suffix = provenance["sha256"] if provenance["sha256"] else "source-unavailable"
        anchor = "#" + provenance["anchor"] if provenance["anchor"] else ""
        lines.append("- `{}` ← `{}`{} ({})".format(rule["id"], provenance["path"], anchor, suffix))
    lines.extend([
        "",
        "- Task-card digest: `{}`".format(task_card_sha256),
        "- Compilation phase: `{}`; continuation: `{}`; preset: `{}`.".format(
            facts["phase"], facts["continuation_kind"], facts["preset"]
        ),
        "",
    ])
    return "\n".join(lines)


def compile_context(
    task_card: Path,
    registry_path: Optional[Path] = None,
    facts_path: Optional[Path] = None,
    source_root: Optional[Path] = None,
    phase: str = "bootstrap",
    continuation_kind: str = "initial",
    max_optional_rules: int = DEFAULT_MAX_CUES,
    max_rescued_rules: int = DEFAULT_MAX_RESCUED,
    max_bytes: int = DEFAULT_MAX_BYTES,
    require_complete: bool = False,
    strategy: str = "coverage",
) -> Tuple[str, Dict[str, Any]]:
    if max_optional_rules < 0 or max_rescued_rules < 0 or max_bytes < 512:
        raise ContextCompileError("context compilation budgets are invalid")
    if strategy not in COMPILATION_STRATEGIES:
        raise ContextCompileError("unsupported context compilation strategy: {}".format(strategy))
    task_card = task_card.resolve()
    if not task_card.is_file():
        raise ContextCompileError("task card not found: {}".format(task_card))
    card_text = task_card.read_text(encoding="utf-8", errors="replace")
    supplied = _load_json(facts_path.resolve(), "task facts") if facts_path else None
    facts = _facts(card_text, supplied, phase, continuation_kind)
    anchors = _contract_anchors(facts["sections"])
    if require_complete and not anchors["complete"]:
        missing = [row["id"] for row in anchors["rows"] if row["status"] == "missing"]
        raise ContextCompileError("task card lacks required contract anchors: {}".format(", ".join(missing)))

    registry_path = (registry_path or _default_registry_path()).resolve()
    registry = _load_json(registry_path, "skill-context registry")
    if registry.get("schema") != REGISTRY_SCHEMA or registry.get("schema_version") != 1:
        raise ContextCompileError("unsupported skill-context registry schema")
    rules = registry.get("rules")
    if not isinstance(rules, list):
        raise ContextCompileError("skill-context registry rules must be an array")
    identifiers = set()
    for index, item in enumerate(rules):
        if not isinstance(item, dict):
            raise ContextCompileError("registry rule[{}] must be an object".format(index))
        _validate_rule(item, index)
        if item["id"] in identifiers:
            raise ContextCompileError("skill-context registry has duplicate rule id: {}".format(item["id"]))
        identifiers.add(item["id"])
    for rule in rules:
        for conflict in rule.get("conflicts_with", ()):
            if conflict not in identifiers:
                raise ContextCompileError(
                    "registry rule {} conflicts with unknown rule {}".format(rule["id"], conflict)
                )

    root = (source_root or _repository_root(task_card)).resolve()
    candidates = [rule for rule in rules if _matches(rule, facts)]
    active_rules = sorted(
        (rule for rule in candidates if rule["selection"] == "anchor"),
        key=lambda item: (-item["priority"], item["id"]),
    )
    rescued_rules = sorted(
        (rule for rule in candidates if rule["selection"] == "rescue"),
        key=lambda item: (-item["priority"], item["id"]),
    )
    conflicts = set()
    selected_ids = {rule["id"] for rule in active_rules + rescued_rules}
    for rule in active_rules + rescued_rules:
        for conflict in rule.get("conflicts_with", ()):
            if conflict in selected_ids:
                conflicts.add(tuple(sorted((rule["id"], str(conflict)))))
    if conflicts:
        raise ContextCompileError("conflicting skill-context rules: {}".format(", ".join(
            "/".join(pair) for pair in sorted(conflicts))))
    conflict_groups: Dict[str, List[str]] = {}
    for rule in active_rules + rescued_rules:
        group = rule.get("conflict_group")
        if group:
            conflict_groups.setdefault(group, []).append(rule["id"])
    occupied_conflict_groups = {
        group: sorted(values) for group, values in conflict_groups.items()
    }
    conflicting_groups = {
        group: values for group, values in occupied_conflict_groups.items() if len(values) > 1
    }
    if conflicting_groups:
        detail = ", ".join(
            "{}={}".format(group, "/".join(values))
            for group, values in sorted(conflicting_groups.items())
        )
        raise ContextCompileError("conflicting skill-context rule groups: {}".format(detail))

    included_active: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    included_rescued: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    omitted: List[Dict[str, Any]] = []
    cue_count = 0
    rescue_count = 0
    task_card_sha256 = _sha256_file(task_card)
    required_coverage = _coverage_requirements(facts, anchors)
    required_ids = {row["id"] for row in required_coverage}
    covered: set[str] = set()
    candidate_records: Dict[str, Dict[str, Any]] = {}
    candidate_by_id = {rule["id"]: rule for rule in candidates}
    candidate_routes = {"top_down": [], "bottom_up": [], "both": []}
    for rule in candidates:
        routes = _retrieval_routes(rule)
        if "top-down" in routes:
            candidate_routes["top_down"].append(rule["id"])
        if "bottom-up" in routes:
            candidate_routes["bottom_up"].append(rule["id"])
        if len(routes) > 1:
            candidate_routes["both"].append(rule["id"])
        candidate_records[rule["id"]] = {
            "id": rule["id"],
            "selection": rule["selection"],
            "priority": rule["priority"],
            "polarity": rule.get("polarity", "positive"),
            "coverage": _coverage_tags(rule),
            "retrieval_routes": routes,
            "match_reason": _match_reason(rule, facts),
            "decision": "pending",
            "reason": "not-yet-ranked",
            "marginal_coverage": [],
        }

    def omit(rule: Dict[str, Any], reason: str, marginal: Sequence[str] = ()) -> None:
        record = candidate_records[rule["id"]]
        if record["decision"] != "pending":
            return
        record["decision"] = "omitted"
        record["reason"] = reason
        record["marginal_coverage"] = list(marginal)
        omitted.append({
            "id": rule["id"],
            "reason": reason,
            "coverage": _coverage_tags(rule),
            "marginal_coverage": list(marginal),
        })

    def include(
        rule: Dict[str, Any], rescued: bool, marginal: Sequence[str], reason: str,
    ) -> bool:
        nonlocal cue_count, rescue_count
        if cue_count >= max_optional_rules:
            omit(rule, "optional-cue-limit", marginal)
            return False
        if rescued and rescue_count >= max_rescued_rules:
            omit(rule, "rescued-cue-limit", marginal)
            return False
        provenance = _source_provenance(rule, root)
        candidate_active = included_active + ([] if rescued else [(rule, provenance)])
        candidate_rescued = included_rescued + ([(rule, provenance)] if rescued else [])
        rendered = _render_packet(
            task_card_sha256, facts, anchors, candidate_active, candidate_rescued
        )
        if len(rendered.encode("utf-8")) > max_bytes:
            omit(rule, "optional-byte-budget", marginal)
            return False
        if rescued:
            included_rescued.append((rule, provenance))
            rescue_count += 1
        else:
            included_active.append((rule, provenance))
        cue_count += 1
        record = candidate_records[rule["id"]]
        record["decision"] = "included"
        record["reason"] = reason
        record["marginal_coverage"] = list(marginal)
        return True

    for rule in active_rules:
        offered = set(_coverage_tags(rule))
        marginal = sorted(offered - covered)
        if include(rule, False, marginal, "active-anchor"):
            covered.update(offered)

    remaining_rescued = list(rescued_rules)
    if strategy == "anchors-only":
        for rule in remaining_rescued:
            omit(rule, "ablation-anchors-only")
    else:
        # Greedy set cover gives every rescue cue an observable marginal reason.
        # It prefers broad coverage, then the frozen registry priority and ID.
        while remaining_rescued:
            scored: List[Tuple[int, int, str, Dict[str, Any], List[str]]] = []
            for rule in remaining_rescued:
                gain = sorted(set(_coverage_tags(rule)).intersection(required_ids - covered))
                if gain:
                    scored.append((-len(gain), -rule["priority"], rule["id"], rule, gain))
            if not scored:
                break
            _, _, _, rule, gain = sorted(scored, key=lambda value: value[:3])[0]
            remaining_rescued.remove(rule)
            if include(rule, True, gain, "coverage-rescue"):
                covered.update(gain)

        # Existing third-party registries may not have coverage labels yet.
        # Preserve their prior bounded behavior, but make the compatibility
        # path explicit so they cannot satisfy named requirements by accident.
        for rule in list(remaining_rescued):
            if not _has_explicit_coverage(rule):
                remaining_rescued.remove(rule)
                include(rule, True, (), "legacy-untagged-compatibility")

        for rule in remaining_rescued:
            remaining = sorted(set(_coverage_tags(rule)).intersection(required_ids - covered))
            if remaining:
                omit(rule, "coverage-unmet-after-budget", remaining)
            else:
                omit(rule, "zero-marginal-coverage")

    covered_by: Dict[str, List[str]] = {}
    for rule, _ in included_active + included_rescued:
        for tag in _coverage_tags(rule):
            covered_by.setdefault(tag, []).append(rule["id"])
    covered_by = {key: sorted(value) for key, value in sorted(covered_by.items())}
    uncovered = [row for row in required_coverage if row["id"] not in covered_by]

    def receipt_entry(rule: Dict[str, Any], provenance: Dict[str, Any]) -> Dict[str, Any]:
        record = candidate_records[rule["id"]]
        return {
            "id": rule["id"],
            "kind": rule["kind"],
            "selection": rule["selection"],
            "polarity": rule.get("polarity", "positive"),
            "coverage": _coverage_tags(rule),
            "conflict_group": rule.get("conflict_group"),
            "review_version": rule.get("review_version"),
            "match_reason": _match_reason(rule, facts),
            "retrieval_routes": record["retrieval_routes"],
            "marginal_coverage": record["marginal_coverage"],
            "selection_reason": record["reason"],
            "source": provenance,
        }

    # A receipt is still valuable when no local cue applies, but injecting the
    # anchor-only packet would merely repeat sections already present in the
    # execution card. Keep that no-op packet empty so dispatch can omit it.
    packet = (
        _render_packet(task_card_sha256, facts, anchors, included_active, included_rescued)
        if included_active or included_rescued
        else ""
    )
    if len(packet.encode("utf-8")) > max_bytes:
        raise ContextCompileError("contract anchors exceed compiled-context byte budget")
    receipt: Dict[str, Any] = {
        "schema": SCHEMA,
        "schema_version": 1,
        "status": "compiled",
        "task_card": str(task_card),
        "task_card_sha256": task_card_sha256,
        "registry": {
            "path": str(registry_path),
            "sha256": _sha256_file(registry_path),
        },
        "task_facts": facts,
        "contract_anchors": anchors,
        "strategy": strategy,
        "coverage": {
            "required": required_coverage,
            "covered_by": covered_by,
            "uncovered": uncovered,
            "minimum_sufficient": (
                strategy == "coverage"
                and not any(
                    record["decision"] == "included"
                    and record["selection"] == "rescue"
                    and _has_explicit_coverage(candidate_by_id[record["id"]])
                    and not record["marginal_coverage"]
                    for record in candidate_records.values()
                )
            ),
        },
        "candidate_routes": {
            key: sorted(value) for key, value in sorted(candidate_routes.items())
        },
        "candidates": [candidate_records[key] for key in sorted(candidate_records)],
        "output_contract": {
            "authoritative_source": "task-card",
            "sections": next(
                row["sections"] for row in anchors["rows"] if row["id"] == "report"
            ),
            "compiler_may_extend": False,
        },
        "selected": [receipt_entry(rule, provenance) for rule, provenance in included_active],
        "rescued": [receipt_entry(rule, provenance) for rule, provenance in included_rescued],
        "omitted": omitted,
        "conflict_groups_checked": occupied_conflict_groups,
        "conflict_free": True,
        "negative_selected_count": sum(
            1 for rule, _ in included_active + included_rescued
            if rule.get("polarity", "positive") == "negative"
        ),
        "packet_sha256": _sha256_bytes(packet.encode("utf-8")),
        "packet_bytes": len(packet.encode("utf-8")),
        "hard_contracts_trimmed": False,
        "model_generated": False,
    }
    return packet, receipt


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--task-card", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--receipt", type=Path)
    result.add_argument("--registry", type=Path)
    result.add_argument("--facts", type=Path)
    result.add_argument("--source-root", type=Path)
    result.add_argument("--phase", choices=("bootstrap", "delta"), default="bootstrap")
    result.add_argument(
        "--continuation-kind",
        choices=("initial", "next-slice", "revision", "checker-followup"),
        default="initial",
    )
    result.add_argument("--max-optional-rules", type=int, default=DEFAULT_MAX_CUES)
    result.add_argument("--max-rescued-rules", type=int, default=DEFAULT_MAX_RESCUED)
    result.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    result.add_argument(
        "--strategy", choices=tuple(sorted(COMPILATION_STRATEGIES)), default="coverage",
        help="coverage selects a minimum coverage rescue set; anchors-only is an ablation arm.",
    )
    result.add_argument("--require-complete", action="store_true")
    return result


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parser().parse_args(argv)
    try:
        packet, receipt = compile_context(
            task_card=args.task_card,
            registry_path=args.registry,
            facts_path=args.facts,
            source_root=args.source_root,
            phase=args.phase,
            continuation_kind=args.continuation_kind,
            max_optional_rules=args.max_optional_rules,
            max_rescued_rules=args.max_rescued_rules,
            max_bytes=args.max_bytes,
            require_complete=args.require_complete,
            strategy=args.strategy,
        )
        _atomic_write(args.output, packet)
        receipt["output"] = str(args.output.resolve())
        if args.receipt:
            _atomic_write(args.receipt, json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
        return 0
    except (ContextCompileError, OSError, UnicodeError, ValueError) as exc:
        print("skill-context: {}".format(exc), file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
