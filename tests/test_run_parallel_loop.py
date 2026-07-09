import os
import pathlib
import stat
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run-parallel-loop.sh"


def write_task(path: pathlib.Path, scope: str, parallel: str = "yes"):
    path.write_text(
        "# Task\n\n"
        "## Parallel Execution Gate\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        f"| Parallel allowed? | {parallel} |\n"
        "| Parallel group id | fixture |\n"
        f"| Allowed files/modules | {scope} |\n",
        encoding="utf-8",
    )


class RunParallelLoopTests(unittest.TestCase):
    def test_help_mentions_experimental_parallel_dispatch(self):
        result = subprocess.run(
            ["bash", str(SCRIPT), "--help"],
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Experimental helper", result.stderr)
        self.assertIn("--max-concurrency", result.stderr)
        self.assertIn("--allow-overlap", result.stderr)

    def test_parallel_dispatch_writes_summary_with_fake_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo = tmp_path / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
            task_a = repo / "task-a.md"
            task_b = repo / "task-b.md"
            write_task(task_a, "src/a.py")
            write_task(task_b, "src/b.py")

            fake_dispatch = tmp_path / "fake-dispatch.sh"
            fake_dispatch.write_text(
                "#!/usr/bin/env bash\n"
                "name=$(basename \"$1\" .md)\n"
                "echo \"Result: /tmp/${name}.result.json\"\n"
                "echo \"Diff: /tmp/${name}.diff\"\n"
                "echo \"Report: /tmp/${name}.report.md\"\n",
                encoding="utf-8",
            )
            fake_dispatch.chmod(fake_dispatch.stat().st_mode | stat.S_IXUSR)

            output_dir = repo / ".worktrees" / "parallel-test"
            env = os.environ.copy()
            env["AI_CODING_WORKFLOW_DISPATCH_BIN"] = str(fake_dispatch)
            result = subprocess.run(
                [
                    "bash",
                    str(SCRIPT),
                    "--max-concurrency",
                    "2",
                    "--output",
                    str(output_dir),
                    str(task_a),
                    str(task_b),
                ],
                cwd=str(repo),
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            summary = output_dir / "parallel-summary.md"
            events = output_dir / "parallel-events.jsonl"
            manifest = output_dir / "parallel-manifest.tsv"
            self.assertTrue(summary.exists())
            self.assertTrue(events.exists())
            self.assertTrue(manifest.exists())
            summary_text = summary.read_text(encoding="utf-8")
            self.assertIn("Experimental Parallel Dispatch Summary", summary_text)
            self.assertIn("Parallel Execution Follow-up", summary_text)
            self.assertIn("| Dispatches succeeded | 2 |", summary_text)
            self.assertIn("| Automatic merge performed? | no |", summary_text)
            self.assertIn("task-a.md", manifest.read_text(encoding="utf-8"))
            self.assertIn("dispatch_start", events.read_text(encoding="utf-8"))

    def test_requires_parallel_gate_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
            task_a = repo / "task-a.md"
            task_b = repo / "task-b.md"
            write_task(task_a, "src/a.py", parallel="no")
            write_task(task_b, "src/b.py")

            result = subprocess.run(
                ["bash", str(SCRIPT), str(task_a), str(task_b)],
                cwd=str(repo),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("not parallel-enabled", result.stderr)

    def test_rejects_scope_overlap_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
            task_a = repo / "task-a.md"
            task_b = repo / "task-b.md"
            write_task(task_a, "src/shared.py")
            write_task(task_b, "src/shared.py")

            result = subprocess.run(
                ["bash", str(SCRIPT), str(task_a), str(task_b)],
                cwd=str(repo),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )

            self.assertEqual(result.returncode, 3)
            self.assertIn("scopes overlap", result.stderr)


if __name__ == "__main__":
    unittest.main()
