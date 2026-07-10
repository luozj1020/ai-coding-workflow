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
        self.assertIn("auto", result.stderr)
        self.assertIn("task-size-classifier", result.stderr)
        self.assertIn("review-only", result.stderr)
        self.assertIn("task-card-audit", result.stderr)
        self.assertIn("plan-splitter", result.stderr)
        self.assertIn("validation-planner", result.stderr)
        self.assertIn("failure-triage", result.stderr)
        self.assertIn("micro-builder", result.stderr)
        self.assertIn("--artifact", result.stderr)

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
                    "--result-mode",
                    "full",
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

    def test_auto_mode_defaults_to_preflight_bundle_balanced(self):
        """Balanced auto mode for ordinary task resolves to preflight-bundle
        (was task-size-classifier before staged pipeline)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo = tmp_path / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
            task_card = repo / "task-card.md"
            task_card.write_text("# Task\n\nSmall implementation task.\n", encoding="utf-8")

            fake_codex = tmp_path / "codex.sh"
            fake_codex.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$@\" > \"$CODEX_FAKE_ARGS\"\n"
                "pwd > \"$CODEX_FAKE_CWD\"\n"
                "cat > \"$CODEX_FAKE_STDIN\"\n"
                "echo 'audit ok'\n",
                encoding="utf-8",
            )
            fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)

            output_dir = repo / ".worktrees" / "spark-test"
            env = os.environ.copy()
            env["CODEX_SPARK_CODEX_BIN"] = bash_path(fake_codex)
            env["CODEX_FAKE_STDIN"] = bash_path(tmp_path / "stdin.md")
            env["CODEX_FAKE_ARGS"] = bash_path(tmp_path / "args.txt")
            env["CODEX_FAKE_CWD"] = bash_path(tmp_path / "cwd.txt")

            result = subprocess.run(
                [
                    bash_exe(),
                    bash_path(SCRIPT),
                    bash_path(task_card),
                    "--result-mode",
                    "full",
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
            prompt = (tmp_path / "stdin.md").read_text(encoding="utf-8")
            self.assertIn("| Spark purpose used | preflight-bundle |", report)
            self.assertIn("| Spark requested mode | auto |", report)
            self.assertIn("| Sandbox used | workspace-write |", report)
            self.assertIn("codex exec (preflight-bundle in artifact dir)", report)
            self.assertIn("Mode resolved: preflight-bundle", prompt)
            self.assertEqual(
                (tmp_path / "args.txt").read_text(encoding="utf-8").splitlines(),
                ["exec", "--model", "gpt-5.3-codex-spark", "--sandbox", "workspace-write", "-"],
            )
            cwd_text = (tmp_path / "cwd.txt").read_text(encoding="utf-8").strip()
            cwd_text = cwd_text.replace("\\", "/").rstrip("/")
            self.assertTrue(
                cwd_text.endswith("/" + output_dir.name),
                "expected Spark preflight-bundle to run in artifact dir, got {}".format(cwd_text),
            )

    def test_auto_mode_uses_validation_planner_for_checker_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo = tmp_path / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
            task_card = repo / "task-card.md"
            task_card.write_text(
                "# Task\n\n"
                "## Task Mode\n\n"
                "| Field | Value |\n"
                "|-------|-------|\n"
                "| Mode | checker-test |\n\n"
                "## Validation Contract\n\n"
                "| Local validation allowed? | yes |\n",
                encoding="utf-8",
            )

            fake_codex = tmp_path / "codex.sh"
            fake_codex.write_text("#!/usr/bin/env bash\ncat >/dev/null\necho 'planner ok'\n", encoding="utf-8")
            fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)

            output_dir = repo / ".worktrees" / "spark-test"
            env = os.environ.copy()
            env["CODEX_SPARK_CODEX_BIN"] = bash_path(fake_codex)
            result = subprocess.run(
                [bash_exe(), bash_path(SCRIPT), bash_path(task_card), "--output", bash_path(output_dir)],
                cwd=str(repo),
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            self.assertIn("| Spark purpose used | validation-planner |", report)

    def test_auto_mode_uses_failure_triage_for_failed_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo = tmp_path / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
            task_card = repo / "task-card.md"
            task_card.write_text("# Task\n", encoding="utf-8")
            artifact = repo / ".worktrees" / "claude-1.checker-report.md"
            artifact.parent.mkdir()
            artifact.write_text("FAILED\nNo valid report\n", encoding="utf-8")

            fake_codex = tmp_path / "codex.sh"
            fake_codex.write_text("#!/usr/bin/env bash\ncat >/dev/null\necho 'triage ok'\n", encoding="utf-8")
            fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)

            output_dir = repo / ".worktrees" / "spark-test"
            env = os.environ.copy()
            env["CODEX_SPARK_CODEX_BIN"] = bash_path(fake_codex)
            result = subprocess.run(
                [
                    bash_exe(),
                    bash_path(SCRIPT),
                    bash_path(task_card),
                    "--artifact",
                    bash_path(artifact),
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
            self.assertIn("| Spark purpose used | failure-triage |", report)

    def test_failure_triage_accepts_bounded_artifacts(self):
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
                "| Spark purpose | failure-triage |\n",
                encoding="utf-8",
            )
            artifact = repo / ".worktrees" / "claude-1.status.txt"
            artifact.parent.mkdir()
            artifact.write_text("line 1\nline 2\nline 3\n", encoding="utf-8")

            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            fake_codex = fake_bin / "codex"
            fake_codex.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$@\" > \"$CODEX_FAKE_ARGS\"\n"
                "cat > \"$CODEX_FAKE_STDIN\"\n"
                "echo 'triage ok'\n",
                encoding="utf-8",
            )
            fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)

            output_dir = repo / ".worktrees" / "spark-test"
            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
            env["CODEX_SPARK_CODEX_BIN"] = bash_path(fake_codex)
            env["CODEX_SPARK_ARTIFACT_LINES"] = "2"
            env["CODEX_FAKE_ARGS"] = bash_path(tmp_path / "args.txt")
            env["CODEX_FAKE_STDIN"] = bash_path(tmp_path / "stdin.md")

            result = subprocess.run(
                [
                    bash_exe(),
                    bash_path(SCRIPT),
                    bash_path(task_card),
                    "--mode",
                    "failure-triage",
                    "--artifact",
                    bash_path(artifact),
                    "--result-mode",
                    "full",
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
            prompt = (output_dir / "codex-spark.prompt.md").read_text(encoding="utf-8")
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            manifest = (output_dir / "codex-spark.artifacts.txt").read_text(encoding="utf-8")
            self.assertIn("failure-triage", prompt)
            self.assertIn("Bounded Artifact Excerpts", prompt)
            self.assertIn("line 1", prompt)
            self.assertIn("line 2", prompt)
            self.assertNotIn("line 3", prompt)
            self.assertIn("Artifact inputs", report)
            self.assertIn("claude-1.status.txt", manifest)
            self.assertEqual(
                (tmp_path / "args.txt").read_text(encoding="utf-8").splitlines(),
                ["exec", "--model", "gpt-5.3-codex-spark", "--sandbox", "read-only", "-"],
            )

    def test_micro_builder_requires_explicit_tiny_scope_contract(self):
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
                "| Spark purpose | micro-builder |\n",
                encoding="utf-8",
            )

            output_dir = repo / ".worktrees" / "spark-test"
            result = subprocess.run(
                [
                    bash_exe(),
                    bash_path(SCRIPT),
                    bash_path(task_card),
                    "--mode",
                    "micro-builder",
                    "--sandbox",
                    "workspace-write",
                    "--output",
                    bash_path(output_dir),
                ],
                cwd=str(repo),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )

            self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            self.assertIn("micro-builder contract missing", result.stderr)
            self.assertIn("Blocked: Spark micro-builder requires explicit tiny-scope authorization", report)
            self.assertIn("at most one or two files", report)

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
                    "--result-mode",
                    "full",
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
                    "--result-mode",
                    "full",
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
            self.assertIn("local app-server/helper initialization", report)
            self.assertIn("Read-only file system", stderr_log)

    def test_help_mentions_parallel_planner(self):
        """Test 9a: Spark help accepts parallel-planner mode."""
        result = subprocess.run(
            [bash_exe(), bash_path(SCRIPT), "--help"],
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("parallel-planner", result.stderr)

    def test_parallel_planner_records_requested_mode_and_schema(self):
        """Test 9b: A fake Codex invocation with --mode parallel-planner
        records explicit requested mode in the report, and the prompt contains
        the schema and advisory/no-dispatch rules."""
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
                "| Spark purpose | parallel-planner |\n",
                encoding="utf-8",
            )

            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            fake_codex = fake_bin / "codex"
            fake_codex.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$@\" > \"$CODEX_FAKE_ARGS\"\n"
                "cat > \"$CODEX_FAKE_STDIN\"\n"
                "echo 'parallel planner ok'\n",
                encoding="utf-8",
            )
            fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)

            output_dir = repo / ".worktrees" / "spark-planner-test"
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
                    "parallel-planner",
                    "--result-mode",
                    "full",
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
            prompt = (output_dir / "codex-spark.prompt.md").read_text(encoding="utf-8")

            # Report records requested mode explicitly
            self.assertIn("| Spark purpose used | parallel-planner |", report)
            self.assertIn("| Spark requested mode | parallel-planner |", report)

            # Prompt contains schema, advisory, and no-dispatch rules
            self.assertIn("parallel-planner", prompt)
            self.assertIn("schema_version", prompt)
            self.assertIn("advisory", prompt.lower())
            self.assertIn("Do not dispatch or execute any tasks", prompt)
            self.assertIn("Do not edit files", prompt)
            self.assertIn("accepted_suggestions", prompt)

    # --- Helpers for staged pipeline tests ---

    def _make_repo_with_task_card(self, tmp_path, task_card_text):
        """Create a git repo with a task card, return repo path."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
        task_card = repo / "task-card.md"
        task_card.write_text(task_card_text, encoding="utf-8")
        return repo, task_card

    def _make_fake_codex(self, tmp_path, output_text="spark ok"):
        """Create a fake codex script that captures args and stdin."""
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        fake_codex = fake_bin / "codex"
        fake_codex.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' \"$@\" > \"$CODEX_FAKE_ARGS\"\n"
            "if [ -n \"${CODEX_FAKE_CWD:-}\" ]; then pwd > \"$CODEX_FAKE_CWD\"; fi\n"
            "cat > \"$CODEX_FAKE_STDIN\"\n"
            "echo '" + output_text + "'\n",
            encoding="utf-8",
        )
        fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)
        return fake_codex

    def _run_spark(self, task_card, output_dir, fake_codex=None, env_extra=None, args=None):
        """Run the spark script and return the result."""
        env = os.environ.copy()
        if fake_codex:
            env["CODEX_SPARK_CODEX_BIN"] = bash_path(fake_codex)
            env["CODEX_FAKE_ARGS"] = bash_path(output_dir.parent / "args.txt")
            env["CODEX_FAKE_STDIN"] = bash_path(output_dir.parent / "stdin.md")
            env["CODEX_FAKE_CWD"] = bash_path(output_dir.parent / "cwd.txt")
        if env_extra:
            env.update(env_extra)
        cmd = [bash_exe(), bash_path(SCRIPT), bash_path(task_card), "--output", bash_path(output_dir)]
        if args:
            cmd.extend(args)
        result = subprocess.run(
            cmd,
            cwd=str(task_card.parent),
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )
        return result

    def _make_controlled_repo(self, tmp_path, allowed_paths, max_files=3):
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
        (repo / "base.txt").write_text("original\n", encoding="utf-8")
        task_card = repo / "task-card.md"
        task_card.write_text(
            "# Controlled Task\n\n## Codex Spark Gate\n\n"
            "| Field | Value |\n"
            "|-------|-------|\n"
            "| Spark purpose | controlled-builder |\n"
            "| Source edits allowed? | yes |\n"
            f"| Max files | {max_files} |\n"
            f"| Controlled-builder allowed paths | {', '.join(allowed_paths)} |\n"
            "| Public API risk | no |\n"
            "| Data model risk | no |\n"
            "| Security risk | no |\n"
            "| Migration risk | no |\n"
            "| Permission risk | no |\n"
            "| Concurrency risk | no |\n"
            "| Cross-module risk | no |\n"
            "| Existing pattern | base.txt |\n"
            "| Validation command | test -f base.txt |\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "base.txt", "task-card.md"], cwd=str(repo), check=True)
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
             "commit", "-m", "baseline"],
            cwd=str(repo), check=True, capture_output=True,
        )
        return repo, task_card

    def _make_editing_codex(self, tmp_path, body):
        fake_codex = tmp_path / "controlled-codex.sh"
        fake_codex.write_text(
            "#!/usr/bin/env bash\n"
            "cat >/dev/null\n" + body + "\n"
            "echo 'controlled result'\n",
            encoding="utf-8",
        )
        fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)
        return fake_codex

    def _run_controlled(self, repo, task_card, fake_codex, allowed_paths,
                        max_diff_lines=20, output_name="controlled-output"):
        output_dir = repo / ".worktrees" / output_name
        env = os.environ.copy()
        env["CODEX_SPARK_CODEX_BIN"] = bash_path(fake_codex)
        cmd = [
            bash_exe(), bash_path(SCRIPT), bash_path(task_card),
            "--mode", "controlled-builder",
            "--sandbox", "workspace-write",
            "--max-diff-lines", str(max_diff_lines),
            "--output", bash_path(output_dir),
        ]
        for path in allowed_paths:
            cmd.extend(["--allow-write", path])
        result = subprocess.run(
            cmd, cwd=str(repo), env=env, text=True, encoding="utf-8",
            errors="replace", capture_output=True,
        )
        return result, output_dir

    # --- Coverage 1: Help lists every new mode, --budget-mode, AI_SPARK_BUDGET_MODE ---

    def test_help_lists_all_new_atomic_and_bundle_modes(self):
        """Coverage 1: Help text lists all new staged pipeline modes."""
        result = subprocess.run(
            [bash_exe(), bash_path(SCRIPT), "--help"],
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        help_text = result.stderr
        # New atomic modes
        for mode in ("observe-synthesizer", "task-card-drafter", "context-packet-builder",
                      "direction-precheck", "acceptance-matrix", "revision-drafter", "lesson-extractor"):
            self.assertIn(mode, help_text, f"Help missing mode: {mode}")
        # New bundle modes
        for mode in ("preflight-bundle", "postflight-bundle"):
            self.assertIn(mode, help_text, f"Help missing bundle mode: {mode}")
        # Budget mode flag
        self.assertIn("--budget-mode", help_text, "Help missing --budget-mode flag")
        self.assertIn("AI_SPARK_BUDGET_MODE", help_text, "Help missing AI_SPARK_BUDGET_MODE env var")

    # --- Coverage 2: Invalid budget mode fails before invoking fake Codex ---

    def test_invalid_budget_mode_fails_before_codex(self):
        """Coverage 2: Invalid --budget-mode exits non-zero without invoking Codex."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path, "# Task\n")
            output_dir = repo / ".worktrees" / "spark-test"
            fake_codex = self._make_fake_codex(tmp_path)
            env = os.environ.copy()
            env["CODEX_SPARK_CODEX_BIN"] = bash_path(fake_codex)
            result = subprocess.run(
                [bash_exe(), bash_path(SCRIPT), bash_path(task_card),
                 "--budget-mode", "invalid-mode", "--output", bash_path(output_dir)],
                cwd=str(repo), env=env,
                text=True, encoding="utf-8", errors="replace", capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid", result.stderr.lower())

    def test_invalid_budget_mode_env_fails_before_codex(self):
        """Coverage 2: Invalid AI_SPARK_BUDGET_MODE env var exits non-zero."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path, "# Task\n")
            output_dir = repo / ".worktrees" / "spark-test"
            fake_codex = self._make_fake_codex(tmp_path)
            env = os.environ.copy()
            env["CODEX_SPARK_CODEX_BIN"] = bash_path(fake_codex)
            env["AI_SPARK_BUDGET_MODE"] = "invalid-mode"
            result = subprocess.run(
                [bash_exe(), bash_path(SCRIPT), bash_path(task_card),
                 "--output", bash_path(output_dir)],
                cwd=str(repo), env=env,
                text=True, encoding="utf-8", errors="replace", capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid", result.stderr.lower())

    # --- Coverage 3: Balanced ordinary → preflight-bundle, stage preflight, roles, cwd, workspace-write ---

    def test_balanced_ordinary_task_resolves_to_preflight_bundle(self):
        """Coverage 3: Default balanced ordinary task → preflight-bundle, stage preflight,
        expected roles, artifact-dir cwd, effective workspace-write."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path, "# Task\nSmall implementation task.\n")
            output_dir = repo / ".worktrees" / "spark-test"
            fake_codex = self._make_fake_codex(tmp_path)
            env = os.environ.copy()
            env["CODEX_SPARK_CODEX_BIN"] = bash_path(fake_codex)
            env["CODEX_FAKE_ARGS"] = bash_path(tmp_path / "args.txt")
            env["CODEX_FAKE_STDIN"] = bash_path(tmp_path / "stdin.md")
            env["CODEX_FAKE_CWD"] = bash_path(tmp_path / "cwd.txt")
            result = subprocess.run(
                [bash_exe(), bash_path(SCRIPT), bash_path(task_card), "--result-mode", "full", "--output", bash_path(output_dir)],
                cwd=str(repo), env=env,
                text=True, encoding="utf-8", errors="replace", capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            prompt = (tmp_path / "stdin.md").read_text(encoding="utf-8")
            self.assertIn("| Spark purpose used | preflight-bundle |", report)
            self.assertIn("| Spark pipeline stage | preflight |", report)
            # Roles list should include risk-classifier, evidence-synthesizer, etc.
            self.assertIn("risk-classifier", report)
            self.assertIn("evidence-synthesizer", report)
            self.assertIn("task-card-drafter", report)
            self.assertIn("context-packet-builder", report)
            self.assertIn("unknown-extractor", report)
            self.assertIn("split-advisor", report)
            # Effective sandbox is workspace-write for read-only synthesis modes
            self.assertIn("| Sandbox used | workspace-write |", report)
            # Cwd is artifact dir
            cwd_text = (tmp_path / "cwd.txt").read_text(encoding="utf-8").strip()
            cwd_text = cwd_text.replace("\\", "/").rstrip("/")
            self.assertTrue(
                cwd_text.endswith("/" + output_dir.name),
                "expected preflight-bundle to run in artifact dir, got {}".format(cwd_text),
            )
            self.assertIn("preflight-bundle", prompt)

    # --- Coverage 4: Balanced checker without artifacts retains validation-planner ---

    def test_balanced_checker_task_retains_validation_planner(self):
        """Coverage 4: Balanced checker task without artifacts → validation-planner."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path,
                "# Task\n\n## Task Mode\n\n| Field | Value |\n|-------|-------|\n| Mode | checker-test |\n\n"
                "## Validation Contract\n\n| Local validation allowed? | yes |\n")
            output_dir = repo / ".worktrees" / "spark-test"
            fake_codex = self._make_fake_codex(tmp_path)
            result = self._run_spark(task_card, output_dir, fake_codex)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            self.assertIn("| Spark purpose used | validation-planner |", report)
            self.assertIn("| Spark pipeline stage | validation |", report)

    # --- Coverage 5: Balanced diff/report/checker evidence → postflight-bundle ---

    def test_balanced_diff_artifact_resolves_to_postflight_bundle(self):
        """Coverage 5a: Balanced with diff artifact → postflight-bundle."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path, "# Task\n")
            artifact = repo / "changes.diff"
            artifact.write_text("diff --git a/foo.py b/foo.py\n", encoding="utf-8")
            output_dir = repo / ".worktrees" / "spark-test"
            fake_codex = self._make_fake_codex(tmp_path)
            result = self._run_spark(task_card, output_dir, fake_codex,
                                     args=["--artifact", bash_path(artifact)])
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            self.assertIn("| Spark purpose used | postflight-bundle |", report)
            self.assertIn("| Spark pipeline stage | postflight |", report)

    def test_balanced_report_artifact_resolves_to_postflight_bundle(self):
        """Coverage 5b: Balanced with checker report artifact → postflight-bundle."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path, "# Task\n")
            artifact = repo / ".worktrees" / "claude-1.checker-report.md"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("# Report\n\nAll checks passed.\n", encoding="utf-8")
            output_dir = repo / ".worktrees" / "spark-test"
            fake_codex = self._make_fake_codex(tmp_path)
            result = self._run_spark(task_card, output_dir, fake_codex,
                                     args=["--artifact", bash_path(artifact)])
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            self.assertIn("| Spark purpose used | postflight-bundle |", report)

    def test_balanced_checker_evidence_artifact_resolves_to_postflight_bundle(self):
        """Coverage 5c: Balanced with checker evidence (non-failing) → postflight-bundle."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path, "# Task\n")
            artifact = repo / ".worktrees" / "claude-1.checker-status.txt"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("PASS\n", encoding="utf-8")
            output_dir = repo / ".worktrees" / "spark-test"
            fake_codex = self._make_fake_codex(tmp_path)
            result = self._run_spark(task_card, output_dir, fake_codex,
                                     args=["--artifact", bash_path(artifact)])
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            self.assertIn("| Spark purpose used | postflight-bundle |", report)

    # --- Coverage 6: Aggressive routing (no-artifact, evidence, failure) ---

    def test_aggressive_no_artifact_resolves_to_preflight_bundle(self):
        """Coverage 6a: Aggressive no-artifact → preflight-bundle."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path, "# Task\nSmall task.\n")
            output_dir = repo / ".worktrees" / "spark-test"
            fake_codex = self._make_fake_codex(tmp_path)
            result = self._run_spark(task_card, output_dir, fake_codex,
                                     env_extra={"AI_SPARK_BUDGET_MODE": "aggressive"})
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            self.assertIn("| Spark purpose used | preflight-bundle |", report)
            self.assertIn("| Spark budget mode effective | aggressive |", report)

    def test_aggressive_evidence_resolves_to_postflight_bundle(self):
        """Coverage 6b: Aggressive with non-failing evidence → postflight-bundle."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path, "# Task\n")
            artifact = repo / ".worktrees" / "claude-1.checker-report.md"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("# Report\n\nAll checks passed.\n", encoding="utf-8")
            output_dir = repo / ".worktrees" / "spark-test"
            fake_codex = self._make_fake_codex(tmp_path)
            result = self._run_spark(task_card, output_dir, fake_codex,
                                     env_extra={"AI_SPARK_BUDGET_MODE": "aggressive"},
                                     args=["--artifact", bash_path(artifact)])
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            self.assertIn("| Spark purpose used | postflight-bundle |", report)
            self.assertIn("| Spark budget mode effective | aggressive |", report)

    def test_aggressive_failure_resolves_to_failure_triage_with_revision_drafter(self):
        """Coverage 6c: Aggressive with failing artifact → failure-triage,
        roles include both failure-triage and revision-drafter."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path, "# Task\n")
            artifact = repo / ".worktrees" / "claude-1.checker-report.md"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("FAILED\nNo valid report\n", encoding="utf-8")
            output_dir = repo / ".worktrees" / "spark-test"
            fake_codex = self._make_fake_codex(tmp_path)
            result = self._run_spark(task_card, output_dir, fake_codex,
                                     env_extra={"AI_SPARK_BUDGET_MODE": "aggressive"},
                                     args=["--artifact", bash_path(artifact), "--result-mode", "full"])
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            prompt = (output_dir / "codex-spark.prompt.md").read_text(encoding="utf-8")
            self.assertIn("| Spark purpose used | failure-triage |", report)
            self.assertIn("| Spark budget mode effective | aggressive |", report)
            # Roles should include both failure-triage and revision-drafter
            self.assertIn("failure-triage", report)
            self.assertIn("revision-drafter", report)
            # Prompt should include revision drafting responsibilities
            self.assertIn("revision", prompt.lower())

    # --- Coverage 7: Conservative preserves old routing ---

    def test_conservative_ordinary_task_resolves_to_task_size_classifier(self):
        """Coverage 7a: Conservative ordinary task → task-size-classifier."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path, "# Task\nSmall implementation task.\n")
            output_dir = repo / ".worktrees" / "spark-test"
            fake_codex = self._make_fake_codex(tmp_path)
            result = self._run_spark(task_card, output_dir, fake_codex,
                                     env_extra={"AI_SPARK_BUDGET_MODE": "conservative"})
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            self.assertIn("| Spark purpose used | task-size-classifier |", report)
            self.assertIn("| Spark budget mode effective | conservative |", report)

    def test_conservative_diff_artifact_resolves_to_review_only(self):
        """Coverage 7b: Conservative diff artifact → review-only."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path, "# Task\n")
            artifact = repo / "changes.diff"
            artifact.write_text("diff --git a/foo.py b/foo.py\n", encoding="utf-8")
            output_dir = repo / ".worktrees" / "spark-test"
            fake_codex = self._make_fake_codex(tmp_path)
            result = self._run_spark(task_card, output_dir, fake_codex,
                                     env_extra={"AI_SPARK_BUDGET_MODE": "conservative"},
                                     args=["--artifact", bash_path(artifact)])
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            self.assertIn("| Spark purpose used | review-only |", report)

    def test_conservative_other_evidence_resolves_to_evidence_checker(self):
        """Coverage 7c: Conservative non-diff/non-failure evidence → evidence-checker."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path, "# Task\n")
            artifact = repo / ".worktrees" / "claude-1.checker-status.txt"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("PASS\n", encoding="utf-8")
            output_dir = repo / ".worktrees" / "spark-test"
            fake_codex = self._make_fake_codex(tmp_path)
            result = self._run_spark(task_card, output_dir, fake_codex,
                                     env_extra={"AI_SPARK_BUDGET_MODE": "conservative"},
                                     args=["--artifact", bash_path(artifact)])
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            self.assertIn("| Spark purpose used | evidence-checker |", report)

    def test_conservative_failure_artifact_resolves_to_failure_triage(self):
        """Coverage 7d: Conservative failing artifact → failure-triage."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path, "# Task\n")
            artifact = repo / ".worktrees" / "claude-1.checker-report.md"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("FAILED\nNo valid report\n", encoding="utf-8")
            output_dir = repo / ".worktrees" / "spark-test"
            fake_codex = self._make_fake_codex(tmp_path)
            result = self._run_spark(task_card, output_dir, fake_codex,
                                     env_extra={"AI_SPARK_BUDGET_MODE": "conservative"},
                                     args=["--artifact", bash_path(artifact)])
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            self.assertIn("| Spark purpose used | failure-triage |", report)

    # --- Coverage 8: Explicit --mode overrides budget/auto inference ---

    def test_explicit_mode_overrides_budget_inference(self):
        """Coverage 8: --mode always wins over budget mode inference."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path, "# Task\nSmall task.\n")
            output_dir = repo / ".worktrees" / "spark-test"
            fake_codex = self._make_fake_codex(tmp_path)
            result = self._run_spark(task_card, output_dir, fake_codex,
                                     env_extra={"AI_SPARK_BUDGET_MODE": "aggressive"},
                                     args=["--mode", "review-only"])
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            self.assertIn("| Spark purpose used | review-only |", report)
            self.assertIn("| Spark requested mode | review-only |", report)

    # --- Coverage 9: direction-precheck/acceptance-matrix → postflight; lesson-extractor → learning ---

    def test_direction_precheck_maps_to_postflight_stage(self):
        """Coverage 9a: direction-precheck should map to postflight stage (not preflight)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path,
                "# Task\n\n## Codex Spark Gate\n\n| Spark enabled? | yes |\n| Spark purpose | direction-precheck |\n")
            output_dir = repo / ".worktrees" / "spark-test"
            fake_codex = self._make_fake_codex(tmp_path)
            result = self._run_spark(task_card, output_dir, fake_codex,
                                     args=["--mode", "direction-precheck"])
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            self.assertIn("| Spark purpose used | direction-precheck |", report)
            self.assertIn("| Spark pipeline stage | postflight |", report)

    def test_acceptance_matrix_maps_to_postflight_stage(self):
        """Coverage 9b: acceptance-matrix maps to postflight stage."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path,
                "# Task\n\n## Codex Spark Gate\n\n| Spark enabled? | yes |\n| Spark purpose | acceptance-matrix |\n")
            output_dir = repo / ".worktrees" / "spark-test"
            fake_codex = self._make_fake_codex(tmp_path)
            result = self._run_spark(task_card, output_dir, fake_codex,
                                     args=["--mode", "acceptance-matrix"])
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            self.assertIn("| Spark purpose used | acceptance-matrix |", report)
            self.assertIn("| Spark pipeline stage | postflight |", report)

    def test_lesson_extractor_maps_to_learning_stage(self):
        """Coverage 9c: lesson-extractor maps to learning stage."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path,
                "# Task\n\n## Codex Spark Gate\n\n| Spark enabled? | yes |\n| Spark purpose | lesson-extractor |\n")
            output_dir = repo / ".worktrees" / "spark-test"
            fake_codex = self._make_fake_codex(tmp_path)
            result = self._run_spark(task_card, output_dir, fake_codex,
                                     args=["--mode", "lesson-extractor"])
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            self.assertIn("| Spark purpose used | lesson-extractor |", report)
            self.assertIn("| Spark pipeline stage | learning |", report)

    # --- Coverage 10: Preflight/postflight prompts contain 7 handoff headings + advisory/no-merge ---

    PREFLIGHT_POSTFLIGHT_HEADINGS = [
        "Decision Summary", "Risk Flags", "Scope and Boundaries",
        "Acceptance Matrix", "Evidence Conflicts", "Required Codex Decisions",
        "Recommended Next Action",
    ]

    def test_preflight_prompt_contains_all_handoff_headings(self):
        """Coverage 10a: preflight-bundle prompt contains all 7 compressed handoff headings."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path, "# Task\nSmall task.\n")
            output_dir = repo / ".worktrees" / "spark-test"
            fake_codex = self._make_fake_codex(tmp_path)
            result = self._run_spark(task_card, output_dir, fake_codex,
                                     args=["--result-mode", "full"])
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            prompt = (output_dir / "codex-spark.prompt.md").read_text(encoding="utf-8")
            for heading in self.PREFLIGHT_POSTFLIGHT_HEADINGS:
                self.assertIn(heading, prompt, f"Preflight prompt missing heading: {heading}")
            # Advisory/no-merge ownership
            self.assertIn("advisory", prompt.lower())
            self.assertIn("do not edit", prompt.lower())

    def test_postflight_prompt_contains_all_handoff_headings(self):
        """Coverage 10b: postflight-bundle prompt contains all 7 compressed handoff headings."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path, "# Task\n")
            artifact = repo / ".worktrees" / "claude-1.checker-report.md"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("# Report\nAll passed.\n", encoding="utf-8")
            output_dir = repo / ".worktrees" / "spark-test"
            fake_codex = self._make_fake_codex(tmp_path)
            result = self._run_spark(task_card, output_dir, fake_codex,
                                     args=["--artifact", bash_path(artifact), "--result-mode", "full"])
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            prompt = (output_dir / "codex-spark.prompt.md").read_text(encoding="utf-8")
            for heading in self.PREFLIGHT_POSTFLIGHT_HEADINGS:
                self.assertIn(heading, prompt, f"Postflight prompt missing heading: {heading}")
            self.assertIn("advisory", prompt.lower())
            self.assertIn("do not edit", prompt.lower())

    # --- Coverage 11: Reports contain budget, stage, roles, call count, provisional acceptance, strong-review, merge ---

    def test_report_contains_all_staged_pipeline_fields(self):
        """Coverage 11: Reports contain all new staged pipeline report fields."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path, "# Task\nSmall task.\n")
            output_dir = repo / ".worktrees" / "spark-test"
            fake_codex = self._make_fake_codex(tmp_path)
            result = self._run_spark(task_card, output_dir, fake_codex)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            self.assertIn("| Spark budget mode requested |", report)
            self.assertIn("| Spark budget mode effective |", report)
            self.assertIn("| Spark pipeline stage |", report)
            self.assertIn("| Spark roles executed |", report)
            self.assertIn("| Spark calls used |", report)
            self.assertIn("| Spark provisional acceptance |", report)
            self.assertIn("| Strong review required |", report)
            self.assertIn("| Merge authorized |", report)

    # --- Coverage 12: Call count: 0 for missing CLI / pre-call auto-disable; 1 for actual invocation ---

    def test_missing_cli_reports_call_count_zero(self):
        """Coverage 12a: Missing CLI auto-disable reports Spark calls used = 0."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path, "# Task\n")
            output_dir = repo / ".worktrees" / "spark-test"
            env = os.environ.copy()
            env["CODEX_SPARK_CODEX_BIN"] = bash_path(tmp_path / "nonexistent-codex")
            result = subprocess.run(
                [bash_exe(), bash_path(SCRIPT), bash_path(task_card), "--output", bash_path(output_dir)],
                cwd=str(repo), env=env,
                text=True, encoding="utf-8", errors="replace", capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            self.assertIn("| Spark auto-disabled? | yes |", report)
            self.assertIn("| Spark calls used | 0 |", report)

    def test_completed_invocation_reports_call_count_one(self):
        """Coverage 12b: Successful Codex invocation reports Spark calls used = 1."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path, "# Task\nSmall task.\n")
            output_dir = repo / ".worktrees" / "spark-test"
            fake_codex = self._make_fake_codex(tmp_path)
            result = self._run_spark(task_card, output_dir, fake_codex)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            self.assertIn("| Spark calls used | 1 |", report)

    def test_failing_invocation_reports_call_count_one(self):
        """Coverage 12c: Failing Codex invocation (quota) reports Spark calls used = 1."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path, "# Task\n")
            output_dir = repo / ".worktrees" / "spark-test"
            fake_codex = tmp_path / "codex.sh"
            fake_codex.write_text(
                "#!/usr/bin/env bash\n"
                "echo 'quota exceeded for requested model' >&2\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)
            result = self._run_spark(task_card, output_dir, fake_codex)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            self.assertIn("| Spark invoked? | yes |", report)
            self.assertIn("| Spark calls used | 1 |", report)

    # --- Direct result mode tests ---

    def test_direct_advisory_stdout_is_exactly_fake_result(self):
        """Direct mode: stdout is exactly the fake result with no extra output."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path, "# Task\nSmall task.\n")
            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            fake_codex = fake_bin / "codex"
            fake_codex.write_text(
                "#!/usr/bin/env bash\necho 'direct result line'\n",
                encoding="utf-8",
            )
            fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
            env["CODEX_SPARK_CODEX_BIN"] = bash_path(fake_codex)
            result = subprocess.run(
                [bash_exe(), bash_path(SCRIPT), bash_path(task_card)],
                cwd=str(repo), env=env,
                text=True, encoding="utf-8", errors="replace", capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            # stdout should be exactly the fake result, no report path or extra lines
            self.assertEqual(result.stdout.strip(), "direct result line")

    def test_direct_no_permanent_spark_directory(self):
        """Direct mode: no permanent codex-spark directory is created."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path, "# Task\n")
            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            fake_codex = fake_bin / "codex"
            fake_codex.write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")
            fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
            env["CODEX_SPARK_CODEX_BIN"] = bash_path(fake_codex)
            result = subprocess.run(
                [bash_exe(), bash_path(SCRIPT), bash_path(task_card)],
                cwd=str(repo), env=env,
                text=True, encoding="utf-8", errors="replace", capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            # No codex-spark-* directory should exist under .worktrees
            worktrees_dir = repo / ".worktrees"
            if worktrees_dir.exists():
                spark_entries = list(worktrees_dir.glob("codex-spark-*"))
                self.assertEqual(
                    spark_entries, [],
                    "direct mode should not create permanent directories, found: {}".format(spark_entries),
                )

    def test_direct_temp_cwd_is_writable_and_removed_after_exit(self):
        """Direct mode: temp cwd is outside source, writable, and removed after exit."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path, "# Task\n")

            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            fake_codex = fake_bin / "codex"
            fake_codex.write_text(
                "#!/usr/bin/env bash\n"
                "pwd > \"$CODEX_FAKE_CWD\"\n"
                "touch \"$(pwd)/writable-test\"\n"
                "echo 'temp cwd ok'\n",
                encoding="utf-8",
            )
            fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)

            cwd_file = tmp_path / "cwd.txt"
            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
            env["CODEX_SPARK_CODEX_BIN"] = bash_path(fake_codex)
            env["CODEX_FAKE_CWD"] = bash_path(cwd_file)
            result = subprocess.run(
                [bash_exe(), bash_path(SCRIPT), bash_path(task_card)],
                cwd=str(repo), env=env,
                text=True, encoding="utf-8", errors="replace", capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            # Cwd should be captured and outside source tree
            cwd_text = cwd_file.read_text(encoding="utf-8").strip()
            cwd_text = cwd_text.replace("\\", "/")
            repo_str = str(repo).replace("\\", "/")
            self.assertFalse(
                cwd_text.startswith(repo_str),
                "temp cwd should be outside source tree, got: {}".format(cwd_text),
            )
            # Temp dir should be removed after exit
            self.assertFalse(
                pathlib.Path(cwd_text).exists(),
                "temp cwd should be cleaned up after exit, still exists: {}".format(cwd_text),
            )

    def test_direct_availability_failure_exits_0_stderr_no_report(self):
        """Direct mode availability failure: exits 0, writes reason to stderr, no permanent report."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path, "# Task\n")

            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            fake_codex = fake_bin / "codex"
            fake_codex.write_text(
                "#!/usr/bin/env bash\n"
                "echo 'quota exceeded for requested model' >&2\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
            env["CODEX_SPARK_CODEX_BIN"] = bash_path(fake_codex)
            result = subprocess.run(
                [bash_exe(), bash_path(SCRIPT), bash_path(task_card)],
                cwd=str(repo), env=env,
                text=True, encoding="utf-8", errors="replace", capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn("auto-disabled", result.stderr)
            # No permanent report directory
            worktrees_dir = repo / ".worktrees"
            if worktrees_dir.exists():
                spark_entries = list(worktrees_dir.glob("codex-spark-*"))
                self.assertEqual(spark_entries, [])

    # --- Minimal result mode tests ---

    def test_implicit_output_selects_minimal(self):
        """Implicit --output (no explicit --result-mode) selects minimal mode."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path, "# Task\n")
            output_dir = repo / ".worktrees" / "spark-minimal"
            fake_codex = self._make_fake_codex(tmp_path)
            result = self._run_spark(task_card, output_dir, fake_codex)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            self.assertIn("| Result mode | minimal |", report)

    def test_minimal_stdout_is_exactly_fake_result_report_path_on_stderr(self):
        """Minimal mode: stdout is exactly fake result; report path is on stderr only."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path, "# Task\n")
            output_dir = repo / ".worktrees" / "spark-minimal"
            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            fake_codex = fake_bin / "codex"
            fake_codex.write_text(
                "#!/usr/bin/env bash\necho 'minimal result line'\n",
                encoding="utf-8",
            )
            fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
            env["CODEX_SPARK_CODEX_BIN"] = bash_path(fake_codex)
            result = subprocess.run(
                [bash_exe(), bash_path(SCRIPT), bash_path(task_card),
                 "--output", bash_path(output_dir)],
                cwd=str(repo), env=env,
                text=True, encoding="utf-8", errors="replace", capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            # stdout is exactly the fake result
            self.assertEqual(result.stdout.strip(), "minimal result line")
            # report path is on stderr, not stdout
            self.assertIn("Codex Spark report:", result.stderr)
            self.assertNotIn("Codex Spark report:", result.stdout)

    def test_minimal_directory_contains_exactly_report(self):
        """Minimal mode: output directory contains exactly codex-spark.report.md."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path, "# Task\n")
            output_dir = repo / ".worktrees" / "spark-minimal"
            fake_codex = self._make_fake_codex(tmp_path)
            result = self._run_spark(task_card, output_dir, fake_codex)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            # Directory should contain exactly one file
            entries = sorted(p.name for p in output_dir.iterdir())
            self.assertEqual(entries, ["codex-spark.report.md"],
                             "minimal output dir should contain only the report, found: {}".format(entries))

    def test_minimal_report_contains_result_mode_no_transient_paths(self):
        """Minimal mode: report contains result mode and no retained transient paths."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path, "# Task\n")
            output_dir = repo / ".worktrees" / "spark-minimal"
            fake_codex = self._make_fake_codex(tmp_path)
            result = self._run_spark(task_card, output_dir, fake_codex)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            self.assertIn("| Result mode | minimal |", report)
            # Transient paths should be replaced with placeholders
            self.assertNotIn("codex-spark.prompt.md", report)
            self.assertNotIn("codex-spark.result.txt", report)
            self.assertNotIn("codex-spark.stderr.log", report)
            self.assertIn("(transient, cleaned up)", report)

    def test_minimal_availability_failure_compact_classification_no_stderr_log(self):
        """Minimal mode availability failure: persists compact classification, no stderr log."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path, "# Task\n")
            output_dir = repo / ".worktrees" / "spark-minimal"

            fake_codex = tmp_path / "codex.sh"
            fake_codex.write_text(
                "#!/usr/bin/env bash\n"
                "echo 'quota exceeded for requested model' >&2\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)

            env = os.environ.copy()
            env["CODEX_SPARK_CODEX_BIN"] = bash_path(fake_codex)
            result = subprocess.run(
                [bash_exe(), bash_path(SCRIPT), bash_path(task_card),
                 "--output", bash_path(output_dir)],
                cwd=str(repo), env=env,
                text=True, encoding="utf-8", errors="replace", capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            self.assertIn("| Spark auto-disabled? | yes |", report)
            self.assertIn("helper exits 0", report)
            # No stderr log in output directory (transient, cleaned up)
            self.assertFalse(
                (output_dir / "codex-spark.stderr.log").exists(),
                "stderr log should not persist in minimal mode output directory",
            )

    # --- Usage error: --output + --result-mode direct ---

    def test_output_with_result_mode_direct_errors(self):
        """--output with --result-mode direct returns a clear usage error."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_repo_with_task_card(tmp_path, "# Task\n")
            output_dir = repo / ".worktrees" / "spark-test"
            fake_codex = self._make_fake_codex(tmp_path)

            env = os.environ.copy()
            env["CODEX_SPARK_CODEX_BIN"] = bash_path(fake_codex)
            result = subprocess.run(
                [bash_exe(), bash_path(SCRIPT), bash_path(task_card),
                 "--output", bash_path(output_dir), "--result-mode", "direct"],
                cwd=str(repo), env=env,
                text=True, encoding="utf-8", errors="replace", capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("incompatible", result.stderr.lower())
            # Output directory should not be created
            self.assertFalse(output_dir.exists(),
                             "output directory should not be created on usage error")

    def test_controlled_builder_rejects_task_card_allowlist_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_controlled_repo(tmp_path, ["base.txt"])
            fake_codex = self._make_editing_codex(tmp_path, "printf 'changed\\n' > base.txt")
            result, _ = self._run_controlled(
                repo, task_card, fake_codex, ["different.txt"]
            )
            self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
            self.assertIn("do not match", result.stderr)
            self.assertEqual((repo / "base.txt").read_text(encoding="utf-8"), "original\n")

    def test_controlled_builder_accepts_tracked_allowlisted_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_controlled_repo(tmp_path, ["base.txt"])
            fake_codex = self._make_editing_codex(tmp_path, "printf 'changed\\n' > base.txt")
            result, output_dir = self._run_controlled(
                repo, task_card, fake_codex, ["base.txt"], max_diff_lines=2
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            diff = (output_dir / "codex-spark.diff").read_text(encoding="utf-8")
            self.assertIn("| Result mode | full |", report)
            self.assertIn("| Boundary outcome | pass |", report)
            self.assertIn("+changed", diff)
            self.assertEqual((repo / "base.txt").read_text(encoding="utf-8"), "original\n")
            self.assertTrue((output_dir / "worktree" / "base.txt").exists())

    def test_controlled_builder_accepts_untracked_one_line_without_newline(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_controlled_repo(tmp_path, ["new.txt"])
            fake_codex = self._make_editing_codex(tmp_path, "printf 'one line' > new.txt")
            result, output_dir = self._run_controlled(
                repo, task_card, fake_codex, ["new.txt"], max_diff_lines=1
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            diff = (output_dir / "codex-spark.diff").read_text(encoding="utf-8")
            self.assertIn("new.txt", diff)
            self.assertIn("+one line", diff)
            self.assertFalse((repo / "new.txt").exists())

    def test_controlled_builder_rejects_outside_allowlist_and_preserves_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_controlled_repo(tmp_path, ["base.txt"])
            fake_codex = self._make_editing_codex(
                tmp_path, "printf 'changed\\n' > base.txt\nprintf 'escape\\n' > outside.txt"
            )
            result, output_dir = self._run_controlled(
                repo, task_card, fake_codex, ["base.txt"], max_diff_lines=10
            )
            self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
            self.assertIn("not in allowlist", result.stderr)
            self.assertTrue((output_dir / "codex-spark.report.md").exists())
            self.assertTrue((output_dir / "codex-spark.diff").exists())
            self.assertTrue((output_dir / "worktree" / "outside.txt").exists())
            self.assertFalse((repo / "outside.txt").exists())
            self.assertEqual((repo / "base.txt").read_text(encoding="utf-8"), "original\n")

    def test_controlled_builder_rejects_combined_diff_over_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_controlled_repo(
                tmp_path, ["base.txt", "new.txt"]
            )
            fake_codex = self._make_editing_codex(
                tmp_path, "printf 'changed\\n' > base.txt\nprintf 'one line' > new.txt"
            )
            result, output_dir = self._run_controlled(
                repo, task_card, fake_codex, ["base.txt", "new.txt"], max_diff_lines=2
            )
            self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            self.assertIn("diff too large", report)
            self.assertEqual((repo / "base.txt").read_text(encoding="utf-8"), "original\n")

    def test_controlled_builder_rejects_binary_untracked_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_controlled_repo(tmp_path, ["binary.dat"])
            fake_codex = self._make_editing_codex(
                tmp_path, "printf '\\000\\001binary' > binary.dat"
            )
            result, output_dir = self._run_controlled(
                repo, task_card, fake_codex, ["binary.dat"], max_diff_lines=20
            )
            self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            self.assertIn("binary content", report)
            self.assertFalse((repo / "binary.dat").exists())

    def test_controlled_builder_rejects_invalid_allow_write_paths(self):
        cases = [
            (["/absolute.txt"], "absolute"),
            (["../escape.txt"], "must not contain"),
            (["*.txt"], "specific file"),
            (["directory/"], "specific file"),
            (["base.txt", "base.txt"], "unique"),
            (["a.txt", "b.txt", "c.txt", "d.txt"], "1-3"),
        ]
        for allowed_paths, expected in cases:
            with self.subTest(allowed_paths=allowed_paths):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    repo, task_card = self._make_controlled_repo(
                        tmp_path, allowed_paths[:3]
                    )
                    fake_codex = self._make_editing_codex(tmp_path, ":")
                    result, _ = self._run_controlled(
                        repo, task_card, fake_codex, allowed_paths
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(expected, result.stderr)

    @unittest.skipIf(os.name == "nt", "symlink creation requires extra privileges on Windows")
    def test_controlled_builder_rejects_symlink_path_component(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_controlled_repo(tmp_path, ["linked/new.txt"])
            outside = tmp_path / "outside"
            outside.mkdir()
            (repo / "linked").symlink_to(outside, target_is_directory=True)
            subprocess.run(["git", "add", "linked"], cwd=str(repo), check=True)
            subprocess.run(
                ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "commit", "-m", "add symlink"],
                cwd=str(repo), check=True, capture_output=True,
            )
            fake_codex = self._make_editing_codex(tmp_path, "printf x > linked/new.txt")
            result, _ = self._run_controlled(
                repo, task_card, fake_codex, ["linked/new.txt"]
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("symlink", result.stderr)
            self.assertFalse((outside / "new.txt").exists())

    def test_controlled_builder_rejects_more_than_three_changed_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            allowed = ["one.txt", "two.txt", "three.txt"]
            repo, task_card = self._make_controlled_repo(tmp_path, allowed)
            fake_codex = self._make_editing_codex(
                tmp_path,
                "printf 1 > one.txt\nprintf 2 > two.txt\nprintf 3 > three.txt\nprintf 4 > four.txt",
            )
            result, output_dir = self._run_controlled(
                repo, task_card, fake_codex, allowed, max_diff_lines=20
            )
            self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            self.assertIn("too many changed files", report)
            self.assertFalse((repo / "four.txt").exists())

    def test_controlled_builder_rejects_binary_tracked_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo, task_card = self._make_controlled_repo(tmp_path, ["base.txt"])
            fake_codex = self._make_editing_codex(
                tmp_path, "printf '\\000\\001binary' > base.txt"
            )
            result, output_dir = self._run_controlled(
                repo, task_card, fake_codex, ["base.txt"], max_diff_lines=20
            )
            self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
            report = (output_dir / "codex-spark.report.md").read_text(encoding="utf-8")
            self.assertIn("binary content", report)
            self.assertEqual((repo / "base.txt").read_text(encoding="utf-8"), "original\n")


if __name__ == "__main__":
    unittest.main()
