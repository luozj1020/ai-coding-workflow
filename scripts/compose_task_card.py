#!/usr/bin/env python3
"""Compose a concise task card after Codex selects a preset and optional gates."""

import argparse
import json
from pathlib import Path
import sys


def component_root(script_path=None):
    script = Path(script_path or __file__).resolve()
    repo_root = script.parent.parent
    candidates = [
        repo_root / "assets" / "task-card-components",
        repo_root / "ai" / "task-card-components",
    ]
    for candidate in candidates:
        if (candidate / "catalog.json").is_file():
            return candidate
    raise FileNotFoundError("task-card component catalog not found")


def load_catalog(root):
    with (root / "catalog.json").open(encoding="utf-8") as handle:
        value = json.load(handle)
    if value.get("schema_version") != 1:
        raise ValueError("unsupported task-card component catalog schema")
    return value


def select_components(catalog, preset, gates):
    presets = catalog.get("presets", {})
    known_gates = catalog.get("gates", {})
    if preset not in presets:
        raise ValueError("unknown preset: {}".format(preset))
    unknown = sorted(set(gates) - set(known_gates))
    if unknown:
        raise ValueError("unknown gate(s): {}".format(", ".join(unknown)))
    requested = set(catalog.get("base", []))
    requested.update(presets[preset])
    requested.update(gates)
    order = catalog.get("order", [])
    selected = [name for name in order if name in requested]
    missing_order = sorted(requested - set(selected))
    if missing_order:
        raise ValueError("component(s) missing from order: {}".format(", ".join(missing_order)))
    return selected


def compose(root, catalog, preset, gates):
    selected = select_components(catalog, preset, gates)
    parts = []
    for name in selected:
        path = root / (name + ".md")
        if not path.is_file():
            raise FileNotFoundError("component not found: {}".format(path))
        parts.append(path.read_text(encoding="utf-8").strip())
    metadata = "<!-- task-card-components: preset={}; gates={}; schema=1 -->".format(
        preset, ",".join(gates) if gates else "none"
    )
    task_mode = {
        "builder": "builder",
        "batch-builder": "builder",
        "solution-planner": "builder",
        "exploratory-builder": "builder",
        "checker": "checker-test",
        "revision": "revision",
        "control-plane": "control-plane",
    }[preset]
    body = "\n\n".join(parts).replace("{{TASK_MODE}}", task_mode)
    return metadata + "\n\n" + body + "\n", selected


def recommend_components(facts):
    """Select a minimal preset/gate set from deterministic routing facts."""
    execution = facts.get("execution", {}) if isinstance(facts.get("execution"), dict) else {}
    owner = facts.get("execution_owner") or facts.get("recommended_owner") or execution.get("owner")
    if owner in ("codex", "codex-fast-path") or facts.get("delegation_value") is False:
        return {"skip_card": True, "reason": "codex-fast-path-needs-no-delegation-card"}

    mode = str(facts.get("mode") or facts.get("task_mode") or "builder").lower()
    event = str(facts.get("routing_event") or "initial").lower()
    claude_role = str(facts.get("claude_role") or execution.get("claude_role") or "").lower()
    if claude_role == "solution-planner" or mode == "solution-planner":
        preset = "solution-planner"
    elif claude_role == "batch-builder" or mode == "batch-builder":
        preset = "batch-builder"
    elif claude_role == "exploratory-builder" or mode == "exploratory-builder":
        preset = "exploratory-builder"
    elif mode in ("checker", "checker-test"):
        preset = "checker"
    elif mode == "revision" or event in ("revision", "narrow", "retry"):
        preset = "revision"
    elif mode == "control-plane":
        preset = "control-plane"
    else:
        preset = "builder"

    gates = []
    task_type = str(facts.get("task_type") or "").lower()
    if facts.get("failure_type") or task_type in ("bugfix", "regression"):
        gates.append("root-cause")
    repository = facts.get("repository", {}) if isinstance(facts.get("repository"), dict) else {}
    scale = str(facts.get("repository_size") or repository.get("routing_scale") or "").lower()
    if scale in ("large", "giant", "monorepo"):
        gates.append("large-repo")
    if facts.get("spec_required") is True or facts.get("product_ambiguity") is True:
        gates.append("spec")
    if facts.get("tdd_required") is True:
        gates.append("tdd")
    if facts.get("parallel_candidate") is True:
        gates.append("parallel")
    if facts.get("advisor_required") is True:
        gates.append("advisor")
    if facts.get("persistent_spark_evidence") is True:
        gates.append("spark")
    return {"skip_card": False, "preset": preset, "gates": gates, "reason": "deterministic-minimal-components"}


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", choices=("builder", "batch-builder", "solution-planner", "exploratory-builder", "checker", "revision", "control-plane"))
    parser.add_argument("--gate", action="append", default=[])
    parser.add_argument("--output")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--list", action="store_true", help="Print the compact component catalog as JSON")
    parser.add_argument("--select-from", help="JSON routing facts used to select the minimal preset/gates")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        root = component_root()
        catalog = load_catalog(root)
        if args.list:
            print(json.dumps({"presets": catalog["presets"], "gates": catalog["gates"]}, indent=2, sort_keys=True))
            return 0
        if args.select_from:
            facts = json.loads(Path(args.select_from).read_text(encoding="utf-8"))
            recommendation = recommend_components(facts)
            if recommendation["skip_card"]:
                print(json.dumps(recommendation, sort_keys=True))
                return 0
            if not args.preset:
                args.preset = recommendation["preset"]
            if not args.gate:
                args.gate = recommendation["gates"]
        if not args.preset or not args.output:
            raise ValueError("--preset and --output are required unless --list is used")
        gates = list(dict.fromkeys(args.gate))
        content, selected = compose(root, catalog, args.preset, gates)
        output = Path(args.output)
        if output.exists() and not args.force:
            raise FileExistsError("output exists; use --force: {}".format(output))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8", newline="\n")
        print(json.dumps({"output": str(output), "preset": args.preset, "components": selected}, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("Error: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
