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
                "[2099-01-01 00:00:00] Starting Claude Code\n"
                "[2099-01-01 00:00:05] Claude completed successfully\n",
                encoding="utf-8",
            )
            (dispatch / "claude.checker-report.md").write_text(
                "# Checker Report\n\nALL GREEN\n",
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

            summary = module.summarize(run)

            self.assertEqual(summary["decision"], "ACCEPT")
            self.assertEqual(summary["quality_score"], 1.0)
            self.assertEqual(summary["speed"]["elapsed_seconds_from_progress"], 5)
            self.assertEqual(summary["cost"]["input_tokens"], 100)
            self.assertEqual(summary["cost"]["output_tokens"], 50)
            self.assertEqual(summary["cost"]["total_cost_usd"], 0.25)
            self.assertEqual(summary["stability"]["finding_count"], 0)
            self.assertEqual(summary["artifacts"]["events"], 1)

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
