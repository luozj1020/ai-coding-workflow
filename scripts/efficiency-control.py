#!/usr/bin/env python3
"""Deterministic Optimization control plane; it never invokes models."""
import argparse, hashlib, importlib.util, json, time
from pathlib import Path

HERE = Path(__file__).resolve().parent

def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

router = load_module("aiwf_route", "route-task.py")
evaluator = load_module("aiwf_accept", "evaluate-acceptance.py")
tiers = load_module("aiwf_tier", "select-review-tier.py")

def digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()

def write_json(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")

def prepare(args):
    hints = json.loads(Path(args.hints).read_text(encoding="utf-8"))
    route = router.route(hints); task_id = hints.get("task_id") or Path(args.task_card).stem
    # Copy single-pass eligibility from Router — never re-derive
    single = route["execution"]["single_pass_allowed"]
    spark_use = (not hints.get("local_tools_sufficient", False) and hints.get("spark_may_change_route", False)
                 and (hints.get("spark_may_avoid_codex", False) or hints.get("spark_may_avoid_retry", False))
                 and route["budget"]["spark_calls"] > 0)
    levels = {
        "L0": {"files": hints.get("target_files", []), "symbols": hints.get("symbols", []), "targets": hints.get("build_targets", [])},
        "L1": {"snippets": hints.get("reference_snippets", []), "call_paths": hints.get("call_paths", []), "constraints": hints.get("constraints", [])},
        "L2": {"full_files": hints.get("full_files", []), "enabled": False},
    }
    cache_identity = {"commit": hints.get("commit"), "files": hints.get("target_files", []), "symbols": hints.get("symbols", []), "profile": hints.get("profile"), "targets": hints.get("build_targets", [])}
    plan = {"schema_version": 1, "generated_at": int(time.time()), "task_id": task_id, "lane": route["lane"], "budget": route["budget"],
            "execution": {"builder_checker_split": route["execution"]["builder_checker_split"], "single_pass_allowed": single, "single_pass_reason": route["execution"]["single_pass_reason"], "max_iterations": 2 if hints.get("latency_mode", "interactive") == "interactive" else 3, "require_new_evidence_for_retry": True},
            "review": {"reserved_for": route["budget"].get("codex_reserved_for", []), "milestones": ["implementation-complete", "validation-complete", "final-candidate"], "incremental": True},
            "spark": {"invoke": bool(spark_use), "reason": "may change route and avoid stronger/retry call" if spark_use else "local tools sufficient or result cannot change route", "max_calls": 1},
            "context": {"cache_key": digest(cache_identity), "levels": levels, "default_level": "L1", "allow_l2_on_gap": True},
            "legacy_loop_compatible": True, "automatic_model_invocation": False, "automatic_merge": False}
    output = Path(args.output_dir)
    context_packet = {"schema_version": 1, "task_id": task_id, "goal": hints.get("goal", ""), "acceptance": hints.get("acceptance", []), "forbidden_paths": hints.get("forbidden_paths", []), "validation": hints.get("validation", []), **levels}
    cache_path = Path(args.cache_dir) / (plan["context"]["cache_key"] + ".json")
    plan["context"]["cache_reused"] = cache_path.exists()
    if cache_path.exists():
        context_packet = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        write_json(cache_path, context_packet)
    write_json(output / "execution-plan.json", plan)
    write_json(output / "context-packet.json", context_packet)
    write_json(output / "retry-state.json", {"task_card": hashlib.sha256(Path(args.task_card).read_bytes()).hexdigest(), "context": digest(levels), "failure_log": None, "environment": digest(hints.get("environment", {}))})
    print(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2))

def review(args):
    plan = json.loads(Path(args.plan).read_text()); evidence = json.loads(Path(args.evidence).read_text())
    result = evaluator.evaluate(evidence); tier = tiers.select_tier({**result, "lane": plan["lane"], "codex_available": evidence.get("codex_available", True)})
    previous = json.loads(Path(args.previous).read_text()) if args.previous else {}
    evidence_hash = digest(evidence)
    ledger_path = Path(args.ledger); ledger = [json.loads(x) for x in ledger_path.read_text(encoding="utf-8").splitlines() if x.strip()] if ledger_path.exists() else []
    codex_rows = [x for x in ledger if x.get("task_id") == plan["task_id"] and x.get("model") == "codex"]
    duplicate = any(x.get("evidence_hash") == evidence_hash for x in codex_rows)
    budget_available = len(codex_rows) < plan["budget"]["codex_calls"]
    output = {"schema_version": 1, "task_id": plan["task_id"], "milestone": args.milestone, "acceptance": result, "review": tier, "evidence_hash": evidence_hash, "incremental_evidence": {k: v for k, v in evidence.items() if previous.get(k) != v}, "codex_call_authorized": tier["tier"] == "L2-codex" and args.milestone in plan["review"]["milestones"] and budget_available and not duplicate, "codex_budget": {"used": len(codex_rows), "max": plan["budget"]["codex_calls"], "duplicate_evidence": duplicate}}
    write_json(args.output, output); print(json.dumps(output, sort_keys=True, indent=2))
    return 0 if result["status"] == "passed" else 2

def main():
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare"); prep.add_argument("--hints", required=True); prep.add_argument("--task-card", required=True); prep.add_argument("--output-dir", required=True); prep.add_argument("--cache-dir", default=".ai-workflow/cache/context")
    rev = sub.add_parser("review"); rev.add_argument("--plan", required=True); rev.add_argument("--evidence", required=True); rev.add_argument("--previous"); rev.add_argument("--ledger", default=".ai-workflow/run-ledger.jsonl"); rev.add_argument("--milestone", choices=["implementation-complete", "validation-complete", "final-candidate"], required=True); rev.add_argument("--output", required=True)
    args = parser.parse_args(); return prepare(args) if args.command == "prepare" else review(args)

if __name__ == "__main__": raise SystemExit(main() or 0)
