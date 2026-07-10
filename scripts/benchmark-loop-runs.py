#!/usr/bin/env python3
"""Aggregate loop quality summaries into a lightweight workflow benchmark."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


def load_summarizer():
    script = Path(__file__).resolve().with_name("summarize-loop-run.py")
    spec = importlib.util.spec_from_file_location("summarize_loop_run", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load summarizer: {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def discover_runs(paths: list[Path], repo_root: Path) -> list[Path]:
    if paths:
        return [path.resolve() for path in paths]
    worktrees = repo_root / ".worktrees"
    return sorted(path.resolve() for path in worktrees.glob("loop-*") if path.is_dir())


def cost_value(cost: dict, key: str) -> float:
    value = cost.get(key)
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def field_number(fields: dict, key: str) -> float:
    value = fields.get(key, "")
    if not value:
        return 0.0
    token = str(value).strip().split()[0]
    try:
        return float(token)
    except ValueError:
        return 0.0


def benchmark(paths: list[Path], repo_root: Path) -> dict:
    summarizer = load_summarizer()
    runs = []
    for path in discover_runs(paths, repo_root):
        summary = summarizer.summarize(path)
        advisor_gate = summary.get("advisor_gate", {})
        advisor_followup = summary.get("advisor_followup", {})
        codex_spark_gate = summary.get("codex_spark_gate", {})
        codex_spark_followup = summary.get("codex_spark_followup", {})
        spark_status = summary.get("spark_status", {})
        parallel_gate = summary.get("parallel_execution_gate", {})
        parallel_followup = summary.get("parallel_execution_followup", {})
        spec_gate = summary.get("spec_gate", {})
        spec_followup = summary.get("spec_followup", {})
        root_cause_followup = summary.get("root_cause_followup", {})
        tdd_followup = summary.get("tdd_followup", {})
        runs.append(
            {
                "run_path": summary["run_path"],
                "decision": summary["decision"],
                "quality_score": summary["quality_score"],
                "elapsed_seconds": summary["speed"]["elapsed_seconds_from_progress"],
                "claude_startup_seconds": summary["speed"].get("claude_startup_seconds"),
                "claude_execution_seconds": summary["speed"].get("claude_execution_seconds"),
                "checker_seconds": summary["speed"].get("checker_seconds"),
                "artifact_finalization_seconds": summary["speed"].get("artifact_finalization_seconds"),
                "input_tokens": cost_value(summary["cost"], "input_tokens"),
                "output_tokens": cost_value(summary["cost"], "output_tokens"),
                "total_cost_usd": cost_value(summary["cost"], "total_cost_usd"),
                "stability_findings": summary["stability"]["finding_count"],
                "loop_type": summary["goal_loop_contract"].get("loop_type", ""),
                "benchmark_tags": summary["goal_loop_contract"].get("benchmark_tags", ""),
                "advisor_required": advisor_gate.get("advisor_required", ""),
                "advisor_model": advisor_gate.get("advisor_model_or_person", ""),
                "advisor_calls": field_number(advisor_followup, "advisor_calls_used"),
                "advisor_input_tokens": field_number(advisor_followup, "advisor_input_tokens"),
                "advisor_output_tokens": field_number(advisor_followup, "advisor_output_tokens"),
                "advisor_cost_usd": field_number(advisor_followup, "advisor_cost_usd"),
                "spark_enabled": spark_status.get("enabled", codex_spark_gate.get("spark_enabled", "")),
                "spark_purpose": spark_status.get("mode", codex_spark_gate.get("spark_purpose", "")),
                "spark_requested_mode": spark_status.get("requested_mode", ""),
                "spark_invoked": spark_status.get("invoked", codex_spark_followup.get("spark_invoked", "")),
                "spark_model": spark_status.get("model", codex_spark_followup.get("spark_model_used", "")),
                "spark_exit_code": field_number(spark_status, "exit_code"),
                "spark_auto_disabled": spark_status.get("auto_disabled", ""),
                "spark_artifact": spark_status.get("artifact", ""),
                "spark_task_size": spark_status.get("task_size_classification", ""),
                "spark_route": spark_status.get("routing_recommendation", ""),
                "spark_confidence": spark_status.get("classification_confidence", ""),
                "spark_accepted_suggestions": spark_status.get("accepted_suggestions", ""),
                "spark_ignored_suggestions": spark_status.get("ignored_suggestions", ""),
                "spark_strong_fallback_used": spark_status.get(
                    "strong_model_fallback",
                    codex_spark_followup.get("strong_model_fallback_used", ""),
                ),
                "parallel_allowed": parallel_gate.get("parallel_allowed", ""),
                "parallel_group_id": parallel_gate.get("parallel_group_id", ""),
                "parallel_helper_invoked": parallel_followup.get("parallel_helper_invoked", ""),
                "parallel_max_concurrency": field_number(parallel_followup, "max_concurrency_used"),
                "spec_required": spec_gate.get("spec_required", ""),
                "spec_matched": spec_followup.get("implementation_matched_spec", ""),
                "root_cause_identified": root_cause_followup.get("root_cause_identified", ""),
                "tdd_mode": tdd_followup.get("tdd_mode", ""),
                "tdd_red_captured": tdd_followup.get("failing_test_or_failing_evidence_captured_before_production_edit", ""),
            }
        )

    total = len(runs)
    accepted = sum(1 for run in runs if run["decision"] == "ACCEPT")
    quality_average = round(sum(run["quality_score"] for run in runs) / total, 3) if total else 0.0
    elapsed_total = sum((run["elapsed_seconds"] or 0) for run in runs)
    return {
        "run_count": total,
        "accepted_count": accepted,
        "accept_rate": round(accepted / total, 3) if total else 0.0,
        "quality_average": quality_average,
        "elapsed_seconds_total": elapsed_total,
        "input_tokens_total": sum(run["input_tokens"] for run in runs),
        "output_tokens_total": sum(run["output_tokens"] for run in runs),
        "total_cost_usd": round(sum(run["total_cost_usd"] for run in runs), 6),
        "advisor_calls_total": sum(run["advisor_calls"] for run in runs),
        "advisor_input_tokens_total": sum(run["advisor_input_tokens"] for run in runs),
        "advisor_output_tokens_total": sum(run["advisor_output_tokens"] for run in runs),
        "advisor_cost_usd_total": round(sum(run["advisor_cost_usd"] for run in runs), 6),
        "spark_enabled_count": sum(1 for run in runs if str(run["spark_enabled"]).startswith("yes")),
        "spark_auto_disabled_count": sum(1 for run in runs if str(run["spark_auto_disabled"]).startswith("yes")),
        "spark_invoked_count": sum(1 for run in runs if str(run["spark_invoked"]).startswith("yes")),
        "spark_strong_fallback_count": sum(1 for run in runs if str(run["spark_strong_fallback_used"]).startswith("yes")),
        "parallel_allowed_count": sum(1 for run in runs if str(run["parallel_allowed"]).startswith("yes")),
        "parallel_invoked_count": sum(1 for run in runs if str(run["parallel_helper_invoked"]).startswith("yes")),
        "spec_required_count": sum(1 for run in runs if str(run["spec_required"]).startswith("yes")),
        "tdd_required_count": sum(1 for run in runs if str(run["tdd_mode"]).startswith("required")),
        "runs": runs,
    }


def format_value(value) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def render_markdown(report: dict) -> str:
    lines = [
        "# Workflow Benchmark Summary",
        "",
        "## Headline",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Runs | {report['run_count']} |",
        f"| Accepted | {report['accepted_count']} |",
        f"| Accept rate | {report['accept_rate']} |",
        f"| Average quality | {report['quality_average']} |",
        f"| Total elapsed seconds | {format_value(report['elapsed_seconds_total'])} |",
        f"| Input tokens | {format_value(report['input_tokens_total'])} |",
        f"| Output tokens | {format_value(report['output_tokens_total'])} |",
        f"| Total cost USD | {format_value(report['total_cost_usd'])} |",
        f"| Advisor calls | {format_value(report['advisor_calls_total'])} |",
        f"| Advisor input tokens | {format_value(report['advisor_input_tokens_total'])} |",
        f"| Advisor output tokens | {format_value(report['advisor_output_tokens_total'])} |",
        f"| Advisor cost USD | {format_value(report['advisor_cost_usd_total'])} |",
        f"| Spark-enabled runs | {format_value(report['spark_enabled_count'])} |",
        f"| Spark-invoked runs | {format_value(report['spark_invoked_count'])} |",
        f"| Spark auto-disabled runs | {format_value(report['spark_auto_disabled_count'])} |",
        f"| Spark strong-fallback runs | {format_value(report['spark_strong_fallback_count'])} |",
        f"| Parallel-allowed runs | {format_value(report['parallel_allowed_count'])} |",
        f"| Parallel-invoked runs | {format_value(report['parallel_invoked_count'])} |",
        f"| Spec-required runs | {format_value(report['spec_required_count'])} |",
        f"| TDD-required runs | {format_value(report['tdd_required_count'])} |",
        "",
        "## Runs",
        "",
        "| Run | Decision | Quality | Seconds | Claude Startup | Claude Exec | Checker | Finalize | Input | Output | Cost | Loop | Tags | Advisor | Advisor Calls | Spark | Spark Mode | Spark Size | Spark Route | Spark Confidence | Spark Model | Spark Accepted | Spark Ignored | Parallel | Spec | TDD | Stability |",
        "|-----|----------|---------|---------|----------------|-------------|---------|----------|-------|--------|------|------|------|---------|---------------|-------|------------|------------|-------------|------------------|-------------|----------------|---------------|----------|------|-----|-----------|",
    ]
    if report["runs"]:
        for run in report["runs"]:
            lines.append(
                "| {run_path} | {decision} | {quality_score} | {elapsed_seconds} | {claude_startup_seconds} | "
                "{claude_execution_seconds} | {checker_seconds} | {artifact_finalization_seconds} | {input_tokens} | "
                "{output_tokens} | {total_cost_usd} | {loop_type} | {benchmark_tags} | {advisor_model} | "
                "{advisor_calls} | {spark_invoked} | {spark_purpose} | {spark_task_size} | {spark_route} | "
                "{spark_confidence} | {spark_model} | {spark_accepted_suggestions} | "
                "{spark_ignored_suggestions} | {parallel_helper_invoked} | {spec_matched} | {tdd_mode} | {stability_findings} |".format(
                    **{key: format_value(value) for key, value in run.items()}
                )
            )
    else:
        lines.append("| no runs | UNKNOWN | 0 | unavailable | unavailable | unavailable | unavailable | unavailable | 0 | 0 | 0 | | | | 0 | | | | | | | | | | | | 0 |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="*", type=Path, help="Loop run directories. Defaults to .worktrees/loop-*.")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository root for default discovery.")
    parser.add_argument("--output", type=Path, help="Write Markdown benchmark report.")
    parser.add_argument("--json-output", type=Path, help="Write machine-readable JSON benchmark report.")
    args = parser.parse_args(argv)

    report = benchmark(args.runs, args.repo.resolve())
    markdown = render_markdown(report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(markdown, encoding="utf-8")
    else:
        print(markdown, end="")
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
