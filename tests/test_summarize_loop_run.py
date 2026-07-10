import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "summarize-loop-run.py"
INSTALLER = ROOT / "scripts" / "install_workflow.py"


def load_module():
    spec = importlib.util.spec_from_file_location("summarize_loop_run", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SummarizeLoopRunTests(unittest.TestCase):
    def test_summarizes_quality_speed_cost_and_stability(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            run = pathlib.Path(tmp) / "loop-20990101-000000"
            dispatch = run / "dispatch-1"
            dispatch.mkdir(parents=True)

            (dispatch / "claude.result.json").write_text("{}", encoding="utf-8")
            (dispatch / "claude.usage.txt").write_text(
                "# Token / Cost Usage Summary\n\n"
                "total_cost_usd: 0.25\n"
                "input_tokens: 100\n"
                "output_tokens: 50\n",
                encoding="utf-8",
            )
            (dispatch / "claude.progress.log").write_text(
                "[2099-01-01 00:00:00] Starting Claude Code: execution_profile=balanced\n"
                "[2099-01-01 00:00:01] Claude process started: pid=123\n"
                "[2099-01-01 00:00:04] Claude subprocess ended; dispatcher finalizing artifacts: pid=123, wait_status=0, elapsed_seconds=3\n"
                "[2099-01-01 00:00:05] Starting checker helper: ai/check-worktree.sh\n"
                "[2099-01-01 00:00:07] Checker helper completed: artifact collection OK; validation ALL GREEN\n"
                "[2099-01-01 00:00:09] Dispatch evidence classification: state=diff + valid report\n",
                encoding="utf-8",
            )
            (dispatch / "claude.checker-report.md").write_text(
                "# Checker Report\n\nALL GREEN\n",
                encoding="utf-8",
            )
            (dispatch / "claude.report.md").write_text(
                "# Claude Report\n\n"
                "## Advisor Follow-up\n\n"
                "| Field | Value |\n"
                "|-------|-------|\n"
                "| Advisor consulted? | yes |\n"
                "| Advisor calls used | 1 |\n"
                "| Advisor output tokens | 80 |\n"
                "\n"
                "## Codex Spark Follow-up\n\n"
                "| Field | Value |\n"
                "|-------|-------|\n"
                "| Spark invoked? | yes |\n"
                "| Spark purpose used | review-only |\n"
                "| Spark requested mode | auto |\n"
                "| Spark model used | gpt-5.3-codex-spark |\n"
                "| Task size classification | tiny |\n"
                "| Spark routing recommendation | codex-fast-path |\n"
                "| Spark classification confidence | high |\n"
                "| Artifact directory | .worktrees/codex-spark-fixture |\n"
                "| Spark exit code | 0 |\n"
                "| Spark auto-disabled? | no |\n"
                "| Strong-model fallback used? | no |\n"
                "| accepted_suggestions | validation file placement |\n"
                "| ignored_suggestions | none |\n"
                "| conflicts_with_claude | none |\n"
                "| conflicts_with_local_evidence | none |\n"
                "| acceptance_satisfied_by_spark | no |\n"
                "\n"
                "## Parallel Execution Follow-up\n\n"
                "| Field | Value |\n"
                "|-------|-------|\n"
                "| Parallel helper invoked? | yes |\n"
                "| Parallel group id | fixture-group |\n"
                "| Max concurrency used | 2 |\n"
                "| Dispatches succeeded | 2 |\n"
                "| Dispatches failed | 0 |\n"
                "\n"
                "## Spec Follow-up\n\n"
                "| Field | Value |\n"
                "|-------|-------|\n"
                "| Implementation matched spec? | yes |\n"
                "| Non-goals respected? | yes |\n"
                "\n"
                "## Root Cause Follow-up\n\n"
                "| Field | Value |\n"
                "|-------|-------|\n"
                "| Root cause identified? | yes |\n"
                "| Fix targets cause rather than symptom? | yes |\n"
                "\n"
                "## Test-First / TDD Follow-up\n\n"
                "| Field | Value |\n"
                "|-------|-------|\n"
                "| TDD mode | required |\n"
                "| Failing test or failing evidence captured before production edit? | yes |\n"
                "\n"
                "## Finish Branch Follow-up\n\n"
                "| Field | Value |\n"
                "|-------|-------|\n"
                "| Fresh verification rerun? | yes: pytest |\n",
                encoding="utf-8",
            )
            (run / "review-1.txt").write_text(
                "### Decision\n\n**ACCEPT**\n",
                encoding="utf-8",
            )
            (run / "loop-events.jsonl").write_text(
                '{"event":"run_start"}\n{"event":"decision","decision":"ACCEPT"}\n',
                encoding="utf-8",
            )
            (run / "task-card-001.md").write_text(
                "# Task Card\n\n"
                "## Goal Loop Contract\n\n"
                "| Field | Value |\n"
                "|-------|-------|\n"
                "| Loop type | goal-based |\n"
                "| Success signal | pytest passes |\n"
                "| Benchmark tags | bugfix, harness |\n"
                "\n"
                "## Advisor Gate\n\n"
                "| Field | Value |\n"
                "|-------|-------|\n"
                "| Advisor required? | yes |\n"
                "| Advisor model or person | claude-fable-5 |\n"
                "\n"
                "## Codex Spark Gate\n\n"
                "| Field | Value |\n"
                "|-------|-------|\n"
                "| Spark enabled? | yes |\n"
                "| Spark purpose | review-only |\n"
                "| Spark model | gpt-5.3-codex-spark |\n"
                "\n"
                "## Parallel Execution Gate\n\n"
                "| Field | Value |\n"
                "|-------|-------|\n"
                "| Parallel allowed? | yes |\n"
                "| Parallel group id | fixture-group |\n"
                "| Max concurrency | 2 |\n"
                "\n"
                "## Spec Gate\n\n"
                "| Field | Value |\n"
                "|-------|-------|\n"
                "| Spec required? | yes |\n"
                "| Spec artifact | ai/specs/2099-01-01--fixture.md |\n"
                "\n"
                "## Root Cause Gate\n\n"
                "| Field | Value |\n"
                "|-------|-------|\n"
                "| Root cause required before fix? | yes |\n"
                "\n"
                "## Test-First / TDD Contract\n\n"
                "| Field | Value |\n"
                "|-------|-------|\n"
                "| TDD mode | required |\n"
                "| Failing test required before production change? | yes |\n",
                encoding="utf-8",
            )

            summary = module.summarize(run)

            self.assertEqual(summary["decision"], "ACCEPT")
            self.assertEqual(summary["quality_score"], 1.0)
            self.assertEqual(summary["speed"]["elapsed_seconds_from_progress"], 9)
            self.assertEqual(summary["speed"]["claude_startup_seconds"], 1)
            self.assertEqual(summary["speed"]["claude_execution_seconds"], 3)
            self.assertEqual(summary["speed"]["checker_seconds"], 2)
            self.assertEqual(summary["speed"]["artifact_finalization_seconds"], 5)
            self.assertEqual(summary["cost"]["input_tokens"], 100)
            self.assertEqual(summary["cost"]["output_tokens"], 50)
            self.assertEqual(summary["cost"]["total_cost_usd"], 0.25)
            self.assertEqual(summary["goal_loop_contract"]["loop_type"], "goal-based")
            self.assertEqual(summary["goal_loop_contract"]["benchmark_tags"], "bugfix, harness")
            self.assertEqual(summary["advisor_gate"]["advisor_required"], "yes")
            self.assertEqual(summary["advisor_gate"]["advisor_model_or_person"], "claude-fable-5")
            self.assertEqual(summary["advisor_followup"]["advisor_consulted"], "yes")
            self.assertEqual(summary["advisor_followup"]["advisor_calls_used"], "1")
            self.assertEqual(summary["codex_spark_gate"]["spark_enabled"], "yes")
            self.assertEqual(summary["codex_spark_followup"]["spark_invoked"], "yes")
            self.assertEqual(summary["codex_spark_followup"]["spark_model_used"], "gpt-5.3-codex-spark")
            self.assertEqual(summary["spark_status"]["enabled"], "yes")
            self.assertEqual(summary["spark_status"]["invoked"], "yes")
            self.assertEqual(summary["spark_status"]["mode"], "review-only")
            self.assertEqual(summary["spark_status"]["requested_mode"], "auto")
            self.assertEqual(summary["spark_status"]["task_size_classification"], "tiny")
            self.assertEqual(summary["spark_status"]["routing_recommendation"], "codex-fast-path")
            self.assertEqual(summary["spark_status"]["classification_confidence"], "high")
            self.assertEqual(summary["spark_status"]["artifact"], ".worktrees/codex-spark-fixture")
            self.assertEqual(summary["spark_status"]["exit_code"], "0")
            self.assertEqual(summary["spark_status"]["auto_disabled"], "no")
            self.assertEqual(summary["spark_status"]["accepted_suggestions"], "validation file placement")
            self.assertEqual(summary["spark_status"]["ignored_suggestions"], "none")
            self.assertEqual(summary["spark_status"]["conflicts_with_claude"], "none")
            self.assertEqual(summary["spark_status"]["conflicts_with_local_evidence"], "none")
            self.assertEqual(summary["spark_status"]["acceptance_satisfied_by_spark"], "no")
            self.assertEqual(summary["claude_evidence"]["evidence_state"], "valid report without diff")
            self.assertEqual(summary["claude_evidence"]["valid_report"], "yes")
            self.assertEqual(summary["parallel_execution_gate"]["parallel_allowed"], "yes")
            self.assertEqual(summary["parallel_execution_followup"]["parallel_helper_invoked"], "yes")
            self.assertEqual(summary["parallel_execution_followup"]["max_concurrency_used"], "2")
            self.assertEqual(summary["spec_gate"]["spec_required"], "yes")
            self.assertEqual(summary["spec_followup"]["implementation_matched_spec"], "yes")
            self.assertEqual(summary["root_cause_gate"]["root_cause_required_before_fix"], "yes")
            self.assertEqual(summary["root_cause_followup"]["root_cause_identified"], "yes")
            self.assertEqual(summary["tdd_contract"]["tdd_mode"], "required")
            self.assertEqual(summary["tdd_followup"]["tdd_mode"], "required")
            self.assertEqual(summary["finish_branch_followup"]["fresh_verification_rerun"], "yes: pytest")
            self.assertEqual(summary["stability"]["finding_count"], 0)
            self.assertEqual(summary["artifacts"]["events"], 1)
            self.assertEqual(summary["artifacts"]["task_card"], 1)
            self.assertEqual(summary["artifacts"]["spark_report"], 0)

            markdown = module.render_markdown(summary)
            self.assertIn("## Speed", markdown)
            self.assertIn("| claude_execution_seconds | 3 |", markdown)
            self.assertIn("## Spark Status", markdown)
            self.assertIn("| routing_recommendation | codex-fast-path |", markdown)
            self.assertIn("| invoked | yes |", markdown)
            self.assertIn("| accepted_suggestions | validation file placement |", markdown)
            self.assertIn("| conflicts_with_claude | none |", markdown)
            self.assertIn("| acceptance_satisfied_by_spark | no |", markdown)
            self.assertIn("## Claude Evidence Classification", markdown)
            self.assertIn("| evidence_state | valid report without diff |", markdown)

    def test_classifies_accepted_diff_without_valid_report(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            run = pathlib.Path(tmp) / "loop-20990101-000000"
            dispatch = run / "dispatch-1"
            dispatch.mkdir(parents=True)
            (dispatch / "claude.diff").write_text(
                "diff --git a/app.py b/app.py\n"
                "--- a/app.py\n"
                "+++ b/app.py\n"
                "@@ -1 +1 @@\n"
                "-old\n"
                "+new\n",
                encoding="utf-8",
            )
            (dispatch / "claude.report.md").write_text(
                "<!-- AI-CODING-WORKFLOW:DISPATCH-FALLBACK-REPORT -->\n"
                "# Claude Modification Report\n\n"
                "This fallback report was generated from workflow artifacts.\n",
                encoding="utf-8",
            )
            (run / "review-1.txt").write_text("### Decision\n\n**ACCEPT**\n", encoding="utf-8")

            summary = module.summarize(run)

            self.assertEqual(summary["claude_evidence"]["evidence_state"], "no report but diff accepted")
            self.assertEqual(summary["claude_evidence"]["report_state"], "fallback report")
            self.assertEqual(summary["claude_evidence"]["diff_present"], "yes")
            self.assertEqual(summary["claude_evidence"]["accepted_without_valid_report"], "yes")

    def test_classifies_seeded_report_as_invalid(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            run = pathlib.Path(tmp) / "loop-20990101-000000"
            dispatch = run / "dispatch-1"
            dispatch.mkdir(parents=True)
            (dispatch / "claude.report.md").write_text(
                "<!-- AI-CODING-WORKFLOW:DISPATCH-SEEDED-REPORT -->\n"
                "# Claude Modification Report\n\n"
                "This dispatcher-created draft must be replaced by Claude.\n",
                encoding="utf-8",
            )

            summary = module.summarize(run)

            self.assertEqual(summary["claude_evidence"]["evidence_state"], "seeded report only")
            self.assertEqual(summary["claude_evidence"]["report_state"], "seeded report only")
            self.assertEqual(summary["claude_evidence"]["valid_report"], "no")

    def test_cli_writes_markdown_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = pathlib.Path(tmp) / "loop-20990101-000000"
            run.mkdir()
            (run / "review-1.txt").write_text("Decision: REVISE\n", encoding="utf-8")
            md = run / "summary.md"
            js = run / "summary.json"

            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(run), "--output", str(md), "--json-output", str(js)],
                cwd=str(ROOT),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Workflow Quality Summary", md.read_text(encoding="utf-8"))
            data = json.loads(js.read_text(encoding="utf-8"))
            self.assertEqual(data["decision"], "REVISE")

    def test_installer_copies_summary_helper(self):
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

            self.assertTrue((repo / "ai" / "summarize-loop-run.py").exists())


if __name__ == "__main__":
    unittest.main()
