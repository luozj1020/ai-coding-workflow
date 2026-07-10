import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "benchmark-loop-runs.py"
INSTALLER = ROOT / "scripts" / "install_workflow.py"


def load_module():
    spec = importlib.util.spec_from_file_location("benchmark_loop_runs", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_loop_run(run: pathlib.Path, decision: str, cost: str = "0.10"):
    dispatch = run / "dispatch-1"
    dispatch.mkdir(parents=True)
    (dispatch / "claude.progress.log").write_text(
        "[2099-01-01 00:00:00] Starting Claude Code: execution_profile=balanced\n"
        "[2099-01-01 00:00:01] Claude process started: pid=123\n"
        "[2099-01-01 00:00:08] Claude subprocess ended; dispatcher finalizing artifacts: pid=123, wait_status=0, elapsed_seconds=7\n"
        "[2099-01-01 00:00:09] Starting checker helper: ai/check-worktree.sh\n"
        "[2099-01-01 00:00:10] Checker helper completed: artifact collection OK; validation ALL GREEN\n"
        "[2099-01-01 00:00:10] Dispatch evidence classification: state=diff + valid report\n",
        encoding="utf-8",
    )
    (dispatch / "claude.checker-report.md").write_text("ALL GREEN\n", encoding="utf-8")
    (dispatch / "claude.usage.txt").write_text(
        f"input_tokens: 10\noutput_tokens: 5\ntotal_cost_usd: {cost}\n",
        encoding="utf-8",
    )
    (dispatch / "claude.report.md").write_text(
        "# Claude Report\n\n"
        "## Advisor Follow-up\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        "| Advisor consulted? | yes |\n"
        "| Advisor calls used | 1 |\n"
        "| Advisor input tokens | 20 |\n"
        "| Advisor output tokens | 8 |\n"
        "| Advisor cost USD | 0.03 |\n"
        "\n"
        "## Spec Follow-up\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        "| Implementation matched spec? | yes |\n"
        "\n"
        "## Root Cause Follow-up\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        "| Root cause identified? | yes |\n"
        "\n"
        "## Parallel Execution Follow-up\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        "| Parallel helper invoked? | yes |\n"
        "| Max concurrency used | 2 |\n"
        "\n"
        "## Codex Spark Follow-up\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        "| Spark invoked? | yes |\n"
        "| Spark purpose used | failure-triage |\n"
        "| Spark requested mode | auto |\n"
        "| Spark model used | gpt-5.3-codex-spark |\n"
        "| Task size classification | small |\n"
        "| Spark routing recommendation | claude-builder |\n"
        "| Spark classification confidence | medium |\n"
        "| Spark exit code | 0 |\n"
        "| Spark auto-disabled? | no |\n"
        "| Strong-model fallback used? | no |\n"
        "| accepted_suggestions | failure attribution |\n"
        "| ignored_suggestions | none |\n"
        "| conflicts_with_claude | none |\n"
        "| acceptance_satisfied_by_spark | no |\n"
        "\n"
        "## Test-First / TDD Follow-up\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        "| TDD mode | required |\n"
        "| Failing test or failing evidence captured before production edit? | yes |\n",
        encoding="utf-8",
    )
    (run / "review-1.txt").write_text(f"Decision: {decision}\n", encoding="utf-8")
    (run / "task-card-001.md").write_text(
        "# Task Card\n\n"
        "## Goal Loop Contract\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        "| Loop type | goal-based |\n"
        "| Benchmark tags | fixture |\n"
        "\n"
        "## Advisor Gate\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        "| Advisor required? | yes |\n"
        "| Advisor model or person | claude-fable-5 |\n"
        "\n"
        "## Spec Gate\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        "| Spec required? | yes |\n"
        "\n"
        "## Parallel Execution Gate\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        "| Parallel allowed? | yes |\n"
        "| Parallel group id | fixture-group |\n"
        "\n"
        "## Codex Spark Gate\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        "| Spark enabled? | yes |\n"
        "| Spark purpose | failure-triage |\n",
        encoding="utf-8",
    )


class BenchmarkLoopRunsTests(unittest.TestCase):
    def test_aggregates_quality_speed_cost_and_tags(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            run1 = root / "loop-20990101-000001"
            run2 = root / "loop-20990101-000002"
            write_loop_run(run1, "ACCEPT", "0.10")
            write_loop_run(run2, "REVISE", "0.20")

            report = module.benchmark([run1, run2], root)

            self.assertEqual(report["run_count"], 2)
            self.assertEqual(report["accepted_count"], 1)
            self.assertEqual(report["accept_rate"], 0.5)
            self.assertEqual(report["elapsed_seconds_total"], 20)
            self.assertEqual(report["input_tokens_total"], 20)
            self.assertEqual(report["output_tokens_total"], 10)
            self.assertEqual(report["total_cost_usd"], 0.3)
            self.assertEqual(report["advisor_calls_total"], 2)
            self.assertEqual(report["advisor_input_tokens_total"], 40)
            self.assertEqual(report["advisor_output_tokens_total"], 16)
            self.assertEqual(report["advisor_cost_usd_total"], 0.06)
            self.assertEqual(report["parallel_allowed_count"], 2)
            self.assertEqual(report["parallel_invoked_count"], 2)
            self.assertEqual(report["spark_enabled_count"], 2)
            self.assertEqual(report["spark_invoked_count"], 2)
            self.assertEqual(report["spark_auto_disabled_count"], 0)
            self.assertEqual(report["spec_required_count"], 2)
            self.assertEqual(report["tdd_required_count"], 2)
            self.assertEqual(report["runs"][0]["loop_type"], "goal-based")
            self.assertEqual(report["runs"][0]["benchmark_tags"], "fixture")
            self.assertEqual(report["runs"][0]["advisor_required"], "yes")
            self.assertEqual(report["runs"][0]["advisor_model"], "claude-fable-5")
            self.assertEqual(report["runs"][0]["spec_matched"], "yes")
            self.assertEqual(report["runs"][0]["root_cause_identified"], "yes")
            self.assertEqual(report["runs"][0]["parallel_allowed"], "yes")
            self.assertEqual(report["runs"][0]["parallel_helper_invoked"], "yes")
            self.assertEqual(report["runs"][0]["spark_invoked"], "yes")
            self.assertEqual(report["runs"][0]["spark_purpose"], "failure-triage")
            self.assertEqual(report["runs"][0]["spark_requested_mode"], "auto")
            self.assertEqual(report["runs"][0]["spark_auto_disabled"], "no")
            self.assertEqual(report["runs"][0]["spark_task_size"], "small")
            self.assertEqual(report["runs"][0]["spark_route"], "claude-builder")
            self.assertEqual(report["runs"][0]["spark_confidence"], "medium")
            self.assertEqual(report["runs"][0]["spark_accepted_suggestions"], "failure attribution")
            self.assertEqual(report["runs"][0]["spark_ignored_suggestions"], "none")
            self.assertEqual(report["runs"][0]["spark_conflicts_with_claude"], "none")
            self.assertEqual(report["runs"][0]["spark_acceptance_satisfied"], "no")
            self.assertEqual(report["runs"][0]["claude_startup_seconds"], 1)
            self.assertEqual(report["runs"][0]["claude_execution_seconds"], 7)
            self.assertEqual(report["runs"][0]["checker_seconds"], 1)
            self.assertEqual(report["runs"][0]["tdd_mode"], "required")
            self.assertEqual(report["runs"][0]["tdd_red_captured"], "yes")

    def test_cli_writes_markdown_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            run = root / "loop-20990101-000001"
            write_loop_run(run, "ACCEPT")
            md = root / "benchmark.md"
            js = root / "benchmark.json"

            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(run), "--output", str(md), "--json-output", str(js)],
                cwd=str(ROOT),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Workflow Benchmark Summary", md.read_text(encoding="utf-8"))
            data = json.loads(js.read_text(encoding="utf-8"))
            self.assertEqual(data["run_count"], 1)
            self.assertEqual(data["accepted_count"], 1)

    def test_installer_copies_benchmark_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"

            subprocess.run(
                [sys.executable, str(INSTALLER), str(repo)],
                cwd=str(ROOT),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=True,
            )

            self.assertTrue((repo / "ai" / "benchmark-loop-runs.py").exists())


    def test_benchmark_exposes_staged_spark_fields_and_totals(self):
        """Benchmark exposes per-run staged fields plus helper-invocation and call totals."""
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)

            # Run 1: staged (two dispatches, each with a Spark followup)
            run1 = root / "loop-20990101-000001"
            d1a = run1 / "dispatch-1"
            d1b = run1 / "dispatch-2"
            d1a.mkdir(parents=True)
            d1b.mkdir(parents=True)

            (d1a / "claude.report.md").write_text(
                "# Claude Report\n\n"
                "## Codex Spark Follow-up\n\n"
                "| Field | Value |\n"
                "|-------|-------|\n"
                "| Spark invoked? | yes |\n"
                "| Spark purpose used | review-only |\n"
                "| Spark pipeline stage | preflight |\n"
                "| Spark calls used | 1 |\n"
                "| Spark roles executed | reviewer |\n"
                "| Spark budget mode requested | standard |\n"
                "| Spark budget mode effective | standard |\n"
                "| Spark provisional acceptance | yes |\n"
                "| Strong review required | no |\n"
                "| Merge authorized | no |\n"
                "| Spark auto-disabled? | no |\n",
                encoding="utf-8",
            )

            (d1b / "claude.report.md").write_text(
                "# Claude Report\n\n"
                "## Codex Spark Follow-up\n\n"
                "| Field | Value |\n"
                "|-------|-------|\n"
                "| Spark invoked? | yes |\n"
                "| Spark purpose used | failure-triage |\n"
                "| Spark pipeline stage | postflight |\n"
                "| Spark calls used | 1 |\n"
                "| Spark roles executed | triage |\n"
                "| Spark budget mode requested | extended |\n"
                "| Spark budget mode effective | extended |\n"
                "| Spark provisional acceptance | no |\n"
                "| Strong review required | yes |\n"
                "| Merge authorized | yes |\n"
                "| Spark auto-disabled? | no |\n",
                encoding="utf-8",
            )

            (d1a / "claude.progress.log").write_text(
                "[2099-01-01 00:00:00] Starting Claude Code: execution_profile=balanced\n"
                "[2099-01-01 00:00:01] Claude process started: pid=123\n"
                "[2099-01-01 00:00:08] Claude subprocess ended; dispatcher finalizing artifacts: pid=123, wait_status=0, elapsed_seconds=7\n"
                "[2099-01-01 00:00:09] Starting checker helper: ai/check-worktree.sh\n"
                "[2099-01-01 00:00:10] Checker helper completed: artifact collection OK; validation ALL GREEN\n",
                encoding="utf-8",
            )
            (d1a / "claude.usage.txt").write_text(
                "input_tokens: 10\noutput_tokens: 5\ntotal_cost_usd: 0.10\n",
                encoding="utf-8",
            )
            (run1 / "review-1.txt").write_text(
                "### Decision\n\n**ACCEPT**\n", encoding="utf-8"
            )
            (run1 / "task-card-001.md").write_text(
                "# Task Card\n\n"
                "## Goal Loop Contract\n\n"
                "| Field | Value |\n"
                "|-------|-------|\n"
                "| Loop type | goal-based |\n"
                "| Benchmark tags | staged |\n\n"
                "## Codex Spark Gate\n\n"
                "| Field | Value |\n"
                "|-------|-------|\n"
                "| Spark enabled? | yes |\n"
                "| Spark purpose | review-only |\n",
                encoding="utf-8",
            )

            # Run 2: single followup (legacy)
            run2 = root / "loop-20990101-000002"
            write_loop_run(run2, "ACCEPT", "0.20")

            report = module.benchmark([run1, run2], root)

            # Aggregate totals
            self.assertEqual(report["spark_helper_invocations_total"], 3)
            self.assertEqual(report["spark_calls_total"], 2)

            # Per-run staged fields for run1
            r1 = report["runs"][0]
            self.assertEqual(r1["spark_helper_invocations"], 2)
            self.assertEqual(r1["spark_total_calls"], 2)
            self.assertEqual(r1["spark_unique_modes"], ["review-only", "failure-triage"])
            self.assertEqual(r1["spark_unique_pipeline_stages"], ["preflight", "postflight"])
            self.assertEqual(r1["spark_unique_roles"], ["reviewer", "triage"])
            self.assertEqual(r1["spark_budget_requested"], ["standard", "extended"])
            self.assertEqual(r1["spark_budget_effective"], ["standard", "extended"])
            self.assertEqual(r1["spark_provisional_acceptance"], ["yes", "no"])
            self.assertEqual(r1["spark_strong_review_required"], ["no", "yes"])
            self.assertEqual(r1["spark_merge_authorized"], ["no", "yes"])

            # Per-run staged fields for run2 (single followup, no spark_calls_used)
            r2 = report["runs"][1]
            self.assertEqual(r2["spark_helper_invocations"], 1)
            self.assertEqual(r2["spark_total_calls"], 0)

            # Markdown exposes staged field names
            md = module.render_markdown(report)
            self.assertIn("Spark helper invocations", md)
            self.assertIn("Spark calls total", md)
            self.assertIn("Spark Invocations", md)
            self.assertIn("Spark Calls", md)


if __name__ == "__main__":
    unittest.main()
