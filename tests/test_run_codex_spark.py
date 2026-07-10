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
            env["CODEX_FAKE_CWD"] = bash_path(tmp_path / "cwd.txt")
            result = subprocess.run(
                [bash_exe(), bash_path(SCRIPT), bash_path(task_card), "--output", bash_path(output_dir)],
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
                                     args=["--artifact", bash_path(artifact)])
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
            result = self._run_spark(task_card, output_dir, fake_codex)
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
                                     args=["--artifact", bash_path(artifact)])
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


if __name__ == "__main__":
    unittest.main()
