#!/usr/bin/env python3
"""Deterministic Optimization control plane; it never invokes models."""
import argparse, importlib.util, json, time
from pathlib import Path
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from evidence_hash import evidence_hash as _evidence_hash, content_hash as _content_hash

HERE = Path(__file__).resolve().parent

def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

router = load_module("aiwf_route", "route-task.py")
evaluator = load_module("aiwf_accept", "evaluate-acceptance.py")
tiers = load_module("aiwf_tier", "select-review-tier.py")
economics = load_module("aiwf_economics", "workflow_economics.py")

digest = _evidence_hash

DEFAULT_CONTROL_PLANE_POLICY = {
    "max_prepare_seconds": 45.0,
    "max_task_card_bytes": 24576,
    "max_context_packet_bytes": 65536,
    "max_combined_bytes": 81920,
}

def write_json(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")

def _control_plane_policy(hints: dict) -> dict:
    policy = dict(DEFAULT_CONTROL_PLANE_POLICY)
    override = hints.get("control_plane_policy")
    if isinstance(override, dict):
        for key in policy:
            value = override.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                policy[key] = float(value) if key == "max_prepare_seconds" else int(value)
    return policy

def _json_bytes(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8"))

def _control_plane_state(started: float, policy: dict, card_bytes: int, context_bytes: int) -> dict:
    elapsed = round(time.monotonic() - started, 6)
    failures = []
    if elapsed > policy["max_prepare_seconds"]:
        failures.append("prepare-time-budget-exceeded")
    if card_bytes > policy["max_task_card_bytes"]:
        failures.append("task-card-byte-budget-exceeded")
    if context_bytes > policy["max_context_packet_bytes"]:
        failures.append("context-packet-byte-budget-exceeded")
    if card_bytes + context_bytes > policy["max_combined_bytes"]:
        failures.append("combined-artifact-byte-budget-exceeded")
    return {
        "policy": policy,
        "prepare_elapsed_seconds": elapsed,
        "task_card_bytes": card_bytes,
        "context_packet_bytes": context_bytes,
        "combined_bytes": card_bytes + context_bytes,
        "within_budget": not failures,
        "failures": failures,
    }

def prepare(args):
    started = time.monotonic()
    facts_path = args.facts or args.hints
    hints = json.loads(Path(facts_path).read_text(encoding="utf-8"))
    control_plane_policy = _control_plane_policy(hints)
    route = router.route(hints)
    task_card = Path(args.task_card) if args.task_card else None
    task_id = hints.get("task_id") or (task_card.stem if task_card else "routing")
    # Copy single-pass eligibility from Router — never re-derive
    single = route["execution"]["single_pass_allowed"]
    spark_gate = str(hints.get("spark_gate", "auto")).lower()
    if spark_gate == "off":
        spark_use = False
        spark_skip_reason = "skip.explicit_gate_off"
        spark_reason = "Spark was explicitly disabled for this task"
        spark_trigger_codes = []
    elif route["lane"] == "express":
        spark_use = False
        spark_skip_reason = "skip.sized_tiny_fastpath"
        spark_reason = "deterministic Express task uses the tiny fast path"
        spark_trigger_codes = []
    elif route["budget"]["spark_calls"] <= 0:
        spark_use = False
        spark_skip_reason = "skip.budget_zero"
        spark_reason = "routing budget does not permit a Spark call"
        spark_trigger_codes = []
    elif spark_gate == "on":
        spark_use = True
        spark_skip_reason = None
        spark_reason = "Spark explicitly enabled"
        spark_trigger_codes = []
    else:
        # Auto mode spends the already-budgeted Spark call on the highest-value
        # pre-dispatch role. Unresolved ownership uses the estimator; an
        # already-bound non-Express Claude route uses a task-card audit so the
        # call improves cross-model transfer rather than re-estimating owner.
        precard = route.get("precard_estimator", {})
        if precard.get("spark_action") == "estimate":
            spark_use = True
            spark_skip_reason = None
            spark_reason = "shared route requested one estimate for an explicit Claude candidate"
            spark_trigger_codes = ["route.explicit_claude_candidate_estimate"]
            spark_mode = "execution-cost-estimator"
        else:
            spark_use = True
            spark_skip_reason = None
            spark_reason = "use available Spark budget to audit the frozen delegation card"
            spark_trigger_codes = ["utilize.task_card_audit"]
            spark_mode = "task-card-audit"
    if spark_gate in {"off"} or route["lane"] == "express" or route["budget"]["spark_calls"] <= 0:
        spark_mode = None
    elif spark_gate == "on":
        spark_mode = "task-card-audit"
    output = Path(args.output_dir)
    if route["execution"]["owner"] == "codex-fast-path":
        plan = {
            "schema_version": 1,
            "generated_at": int(time.time()),
            "task_id": task_id,
            "lane": route["lane"],
            "budget": route["budget"],
            "task_card": str(task_card.resolve()) if task_card else "",
            "task_type": hints.get("task_type", "unknown"),
            "repository_scale": hints.get("repository_size", hints.get("repository_scale", "unknown")),
            "execution": {
                "owner": "codex-fast-path",
                "owner_source": route["execution"].get("owner_source"),
                "ownership_profile": route["execution"].get("ownership_profile", "claude-first"),
                "claude_role": "none",
                "builder_mode": "standard",
                "builder_checker_split": False,
                "checker_model_dispatch": False,
                "checker_skip_reason": "checker skipped: deterministic evidence sufficient",
                "single_pass_allowed": route["execution"].get("single_pass_allowed", False),
                "single_pass_reason": route["execution"].get("single_pass_reason", ""),
                "max_iterations": 0,
                "require_new_evidence_for_retry": False,
                "economy_gate": route["execution"].get("economy_gate", {}),
            },
            "spark": {
                "invoke": False,
                "stage": "precard-route",
                "mode": None,
                "reason": "deterministic Codex owner already bound",
                "skip_reason": route["precard_estimator"].get("reason_code"),
                "trigger_codes": [],
                "max_calls": 0,
            },
            "context": {
                "skipped": True,
                "reason": "codex-fast-path-does-not-need-claude-context-packet",
            },
            "control_plane": _control_plane_state(started, control_plane_policy, 0, 0),
            "legacy_loop_compatible": True,
            "automatic_model_invocation": False,
            "automatic_merge": False,
        }
        decision = {
            "schema_version": 1,
            "task_id": task_id,
            "action": "codex-fast-path",
            "claude_dispatched": False,
            "task_card_required": False,
            "context_packet_created": False,
            "reason": "pre-card economy route selected Codex; control plane short-circuited",
        }
        write_json(output / "execution-plan.json", plan)
        write_json(output / "dispatch-decision.json", decision)
        print(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2))
        return

    if task_card is None:
        raise SystemExit("--task-card is required when routing selects Claude")

    levels = {
        "L0": {"files": hints.get("target_files", []), "symbols": hints.get("symbols", []), "targets": hints.get("build_targets", [])},
        "L1": {"snippets": hints.get("reference_snippets", []), "call_paths": hints.get("call_paths", []), "constraints": hints.get("constraints", [])},
        "L2": {"full_files": hints.get("full_files", []), "enabled": False},
    }
    cache_identity = {"commit": hints.get("commit"), "files": hints.get("target_files", []), "symbols": hints.get("symbols", []), "profile": hints.get("profile"), "targets": hints.get("build_targets", [])}
    plan = {"schema_version": 1, "generated_at": int(time.time()), "task_id": task_id, "lane": route["lane"], "budget": route["budget"],
            "task_card": str(task_card.resolve()), "task_type": hints.get("task_type", "unknown"),
            "repository_scale": hints.get("repository_size", hints.get("repository_scale", "unknown")),
            "execution": {"owner": route["execution"]["owner"], "owner_source": route["execution"].get("owner_source"), "ownership_profile": route["execution"].get("ownership_profile", "claude-first"), "claude_role": route["execution"].get("claude_role", "execution-builder"), "builder_mode": route["execution"].get("builder_mode", "standard"), "durable_output_required": route["execution"].get("durable_output_required", False), "delegation_mode": route["execution"].get("delegation_mode", "unproven"), "parallel_release_allowed": False, "portfolio_concurrency_owner": "independent-user-terminals", "builder_checker_split": route["execution"]["builder_checker_split"], "checker_model_dispatch": route["execution"]["checker_model_dispatch"], "checker_value_reasons": route["execution"]["checker_value_reasons"], "checker_skip_reason": route["execution"]["checker_skip_reason"], "single_pass_allowed": single, "single_pass_reason": route["execution"]["single_pass_reason"], "max_iterations": 2, "require_new_evidence_for_retry": True, "economy_gate": route["execution"].get("economy_gate", {})},
            "review": {"reserved_for": route["budget"].get("codex_reserved_for", []), "milestones": ["implementation-complete", "validation-complete", "final-candidate"], "incremental": True},
            "spark": {"invoke": bool(spark_use), "stage": "pre-dispatch", "mode": spark_mode, "reason": spark_reason, "skip_reason": spark_skip_reason, "trigger_codes": spark_trigger_codes, "max_calls": 1},
            "context": {"cache_key": digest(cache_identity), "levels": levels, "default_level": "L1", "allow_l2_on_gap": True},
            "legacy_loop_compatible": True, "automatic_model_invocation": False, "automatic_merge": False}
    context_packet = {"schema_version": 1, "task_id": task_id, "goal": hints.get("goal", ""), "acceptance": hints.get("acceptance", []), "forbidden_paths": hints.get("forbidden_paths", []), "validation": hints.get("validation", []), **levels}
    card_bytes = task_card.stat().st_size
    context_bytes = _json_bytes(context_packet)
    plan["control_plane"] = _control_plane_state(
        started, control_plane_policy, card_bytes, context_bytes
    )
    if not plan["control_plane"]["within_budget"]:
        plan["context"] = {
            "skipped": True,
            "reason": "control-plane-budget-exceeded-before-dispatch",
        }
        decision = {
            "schema_version": 1,
            "task_id": task_id,
            "action": "recompose-before-dispatch",
            "claude_dispatched": False,
            "task_card_required": True,
            "context_packet_created": False,
            "reason": plan["control_plane"]["failures"],
        }
        write_json(output / "execution-plan.json", plan)
        write_json(output / "dispatch-decision.json", decision)
        print(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2))
        return 2
    cache_path = Path(args.cache_dir) / (plan["context"]["cache_key"] + ".json")
    plan["context"]["cache_reused"] = cache_path.exists()
    if cache_path.exists():
        context_packet = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        write_json(cache_path, context_packet)
    write_json(output / "execution-plan.json", plan)
    write_json(output / "context-packet.json", context_packet)
    write_json(output / "retry-state.json", {"task_card": _content_hash(task_card.read_bytes()), "context": digest(levels), "failure_log": None, "environment": digest(hints.get("environment", {}))})
    print(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2))
    return 0

def review(args):
    plan = json.loads(Path(args.plan).read_text()); evidence = json.loads(Path(args.evidence).read_text())

    # Use review-ladder for deterministic evaluation
    ladder = load_module("review_ladder", "review-ladder.py")

    # Build ladder input from evidence
    ladder_input = {
        "task": evidence.get("task", {}),
        "validation_results": evidence.get("validation_results"),
        "artifact_manifest": evidence.get("artifact_manifest"),
        "diff_evidence": evidence.get("diff_evidence", {}),
        "remote_evidence": evidence.get("remote_evidence"),
    }

    # Check if we have a composed task or need legacy mode
    if ladder_input["task"]:
        ladder_result = ladder.evaluate_ladder(
            task=ladder_input["task"],
            validation_results=ladder_input["validation_results"],
            artifact_manifest=ladder_input["artifact_manifest"],
            diff_evidence=ladder_input["diff_evidence"],
            remote_evidence=ladder_input["remote_evidence"],
            failure_count=evidence.get("failure_count", 1),
            assured=plan["lane"] == "assured",
        )
        result = ladder_result["l0_acceptance"]
        tier = {"tier": ladder_result["tier"], "action": ladder_result["action"]}
        model_authorized = ladder_result["model_authorized"]
        model_call_prohibited = ladder_result["model_call_prohibited"]
    else:
        # Legacy mode: use old evaluate + select-tier
        result = evaluator.evaluate(evidence)
        tier = tiers.select_tier({**result, "lane": plan["lane"], "codex_available": evidence.get("codex_available", True)})
        model_authorized = "codex" if tier["tier"] == "L2-codex" else None
        model_call_prohibited = False

    previous = json.loads(Path(args.previous).read_text()) if args.previous else {}
    evidence_hash = digest(evidence)
    ledger_path = Path(args.ledger); ledger = [json.loads(x) for x in ledger_path.read_text(encoding="utf-8").splitlines() if x.strip()] if ledger_path.exists() else []
    codex_rows = [x for x in ledger if x.get("task_id") == plan["task_id"] and x.get("model") == "codex"]
    duplicate = any(x.get("evidence_hash") == evidence_hash for x in codex_rows)
    budget_available = len(codex_rows) < plan["budget"]["codex_calls"]

    # Determine authorization: use ladder result, but still check budget/dedup
    codex_call_authorized = (
        model_authorized == "codex"
        and not model_call_prohibited
        and args.milestone in plan["review"]["milestones"]
        and budget_available
        and not duplicate
    )

    output = {
        "schema_version": 1,
        "task_id": plan["task_id"],
        "milestone": args.milestone,
        "acceptance": result,
        "review": tier,
        "evidence_hash": evidence_hash,
        "incremental_evidence": {k: v for k, v in evidence.items() if previous.get(k) != v},
        "codex_call_authorized": codex_call_authorized,
        "model_call_prohibited": model_call_prohibited,
        "codex_budget": {"used": len(codex_rows), "max": plan["budget"]["codex_calls"], "duplicate_evidence": duplicate},
    }

    # Include ladder-specific fields if available
    if ladder_input["task"]:
        output["mechanical_failures"] = ladder_result.get("mechanical_failures", [])
        output["recovery"] = ladder_result.get("recovery")

    write_json(args.output, output); print(json.dumps(output, sort_keys=True, indent=2))
    if args.milestone == "final-candidate":
        owner = plan.get("execution", {}).get("owner", "codex-fast-path")
        record = {
            "schema_version": 1,
            "run_id": Path(args.output).resolve().parent.name,
            "task_id": plan["task_id"],
            "task_type": plan.get("task_type", "unknown"),
            "repository_scale": plan.get("repository_scale", "unknown"),
            "owner": owner,
            "accepted": result.get("status") == "passed",
            "first_pass": (
                evidence.get("attempt_index") == 1
                if isinstance(evidence.get("attempt_index"), int) else None
            ),
            "codex_takeover": evidence.get("codex_takeover"),
            "claude_reuse_ratio": None,
            "diff_reuse": {},
            "reuse_evidence_available": False,
            "reuse_unavailable_reason": "claude-and-final-diff-not-both-bound",
            "model_calls": {},
            "task_card_bytes": Path(plan["task_card"]).stat().st_size if Path(plan["task_card"]).is_file() else None,
            "review_packet_bytes": Path(args.evidence).stat().st_size,
            "worktree_setup_seconds": None,
            "total_elapsed_seconds": None,
            "checker_model_dispatched": plan.get("execution", {}).get("checker_model_dispatch", False),
        }
        history = Path(".ai-workflow/economics-history.jsonl")
        record["history_appended"] = economics.append_history_once(history, record)
        write_json(Path(args.output).resolve().parent / "workflow-economics.json", record)
    return 0 if result["status"] == "passed" else 2

def main():
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare"); source = prep.add_mutually_exclusive_group(required=True); source.add_argument("--facts"); source.add_argument("--hints", help="Legacy conservative input; cannot qualify for Express without complete risks"); prep.add_argument("--task-card", help="Required only when routing selects Claude"); prep.add_argument("--output-dir", required=True); prep.add_argument("--cache-dir", default=".ai-workflow/cache/context")
    rev = sub.add_parser("review"); rev.add_argument("--plan", required=True); rev.add_argument("--evidence", required=True); rev.add_argument("--previous"); rev.add_argument("--ledger", default=".ai-workflow/run-ledger.jsonl"); rev.add_argument("--milestone", choices=["implementation-complete", "validation-complete", "final-candidate"], required=True); rev.add_argument("--output", required=True)
    args = parser.parse_args(); return prepare(args) if args.command == "prepare" else review(args)

if __name__ == "__main__": raise SystemExit(main() or 0)
