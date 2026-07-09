import os
import pathlib
import stat
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run-codex-spark.sh"


def bash_exe() -> str:
    if os.name == "nt":
        for candidate in (
            pathlib.Path(r"C:\Program Files\Git\bin\bash.exe"),
            pathlib.Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
        ):
            if candidate.is_file():
                return str(candidate)
    return "bash"


def bash_path(path: pathlib.Path) -> str:
    value = str(path)
    if os.name == "nt":
        value = value.replace("\\", "/")
        if len(value) >= 2 and value[1] == ":":
            value = "/" + value[0].lower() + value[2:]
    return value


class RunCodexSparkTests(unittest.TestCase):
    def test_help_mentions_default_model_and_modes(self):
        result = subprocess.run(
            [bash_exe(), bash_path(SCRIPT), "--help"],
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("gpt-5.3-codex-spark", result.stderr)
        self.assertIn("review-only", result.stderr)
        self.assertIn("micro-builder", result.stderr)

    def test_review_only_invokes_codex_and_writes_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo = tmp_path / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
            task_card = repo / "task-card.md"
            task_card.write_text(
                "# Task\n\n## Codex Spark Gate\n\n"
                "| Field | Value |\n"
                "|-------|-------|\n"
                "| Spark enabled? | yes |\n"
                "| Spark purpose | review-only |\n",
                encoding="utf-8",
            )

            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            fake_codex = fake_bin / "codex"
            fake_codex.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$@\" > \"$CODEX_FAKE_ARGS\"\n"
                "cat > \"$CODEX_FAKE_STDIN\"\n"
                "echo 'spark review ok'\n",
                encoding="utf-8",
            )
            fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)

            output_dir = repo / ".worktrees" / "spark-test"
            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
            env["CODEX_SPARK_CODEX_BIN"] = bash_path(fake_codex)
            env["CODEX_FAKE_ARGS"] = bash_path(tmp_path / "args.txt")
            env["CODEX_FAKE_STDIN"] = bash_path(tmp_path / "stdin.md")

            result = subprocess.run(
                [
                    bash_exe(),
                    bash_path(SCRIPT),
                    bash_path(task_card),
                    "--mode",
                    "review-only",
                    "--output",
                    bash_path(output_dir),
                ],
                cwd=str(repo),
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = output_dir / "codex-spark.report.md"
            prompt = output_dir / "codex-spark.prompt.md"
            result_file = output_dir / "codex-spark.result.txt"
            self.assertTrue(report.exists())
            self.assertTrue(prompt.exists())
            self.assertTrue(result_file.exists())
            self.assertIn("Codex Spark Follow-up", report.read_text(encoding="utf-8"))
            self.assertIn("| Spark model used | gpt-5.3-codex-spark |", report.read_text(encoding="utf-8"))
            self.assertIn("spark review ok", result_file.read_text(encoding="utf-8"))
            self.assertIn("Codex Spark Execution Request", prompt.read_text(encoding="utf-8"))
            self.assertEqual(
                (tmp_path / "args.txt").read_text(encoding="utf-8").splitlines(),
                ["exec", "--model", "gpt-5.3-codex-spark", "--sandbox", "read-only", "-"],
            )

    def test_missing_codex_auto_disables_without_failing_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo = tmp_path / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
            task_card = repo / "task-card.md"
            task_card.write_text("# Task\n\n## Codex Spark Gate\n\n| Spark enabled? | auto |\n", encoding="utf-8")
            output_dir = repo / ".worktrees" / "spark-test"

            env = os.environ.copy()
            env["CODEX_SPARK_CODEX_BIN"] = bash_path(tmp_path / "missing-codex")
            result = subprocess.run(
                [
                    bash_exe(),
                    bash_path(SCRIPT),
                    bash_path(task_card),
                    "--output",
                    bash_path(output_dir),
                ],
                cwd=str(repo),
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            self.assertIn("| Spark invoked? | no |", report)
            self.assertIn("| Spark auto-disabled? | yes |", report)
            self.assertIn("codex CLI is not installed", report)
            self.assertIn("helper exits 0", report)

    def test_quota_failure_auto_disables_without_failing_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo = tmp_path / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
            task_card = repo / "task-card.md"
            task_card.write_text("# Task\n\n## Codex Spark Gate\n\n| Spark enabled? | auto |\n", encoding="utf-8")

            fake_codex = tmp_path / "codex.sh"
            fake_codex.write_text(
                "#!/usr/bin/env bash\n"
                "echo 'quota exceeded for requested model' >&2\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)

            output_dir = repo / ".worktrees" / "spark-test"
            env = os.environ.copy()
            env["CODEX_SPARK_CODEX_BIN"] = bash_path(fake_codex)
            result = subprocess.run(
                [
                    bash_exe(),
                    bash_path(SCRIPT),
                    bash_path(task_card),
                    "--output",
                    bash_path(output_dir),
                ],
                cwd=str(repo),
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            stderr_log = (output_dir / "codex-spark.stderr.log").read_text(encoding="utf-8")
            self.assertIn("| Spark invoked? | yes |", report)
            self.assertIn("| Spark exit code | 1 |", report)
            self.assertIn("| Spark auto-disabled? | yes |", report)
            self.assertIn("quota exceeded", stderr_log)
            self.assertIn("helper exits 0", report)

    def test_read_only_app_server_failure_auto_disables_without_failing_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo = tmp_path / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
            task_card = repo / "task-card.md"
            task_card.write_text("# Task\n\n## Codex Spark Gate\n\n| Spark enabled? | auto |\n", encoding="utf-8")

            fake_codex = tmp_path / "codex.sh"
            fake_codex.write_text(
                "#!/usr/bin/env bash\n"
                "echo 'failed to initialize in-process app-server client: Read-only file system (os error 30)' >&2\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)

            output_dir = repo / ".worktrees" / "spark-test"
            env = os.environ.copy()
            env["CODEX_SPARK_CODEX_BIN"] = bash_path(fake_codex)
            result = subprocess.run(
                [
                    bash_exe(),
                    bash_path(SCRIPT),
                    bash_path(task_card),
                    "--sandbox",
                    "read-only",
                    "--output",
                    bash_path(output_dir),
                ],
                cwd=str(repo),
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            stderr_log = (output_dir / "codex-spark.stderr.log").read_text(encoding="utf-8")
            self.assertIn("| Spark auto-disabled? | yes |", report)
            self.assertIn("read-only sandbox helper initialization", report)
            self.assertIn("Read-only file system", stderr_log)


if __name__ == "__main__":
    unittest.main()
