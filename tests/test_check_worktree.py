import os
import pathlib
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-worktree.sh"


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


class CheckWorktreeTests(unittest.TestCase):
    def _init_repo(self, tmp_path: pathlib.Path) -> pathlib.Path:
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
        (repo / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
        return repo

    def test_no_discover_without_commands_skips_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._init_repo(pathlib.Path(tmp))
            report = repo / ".worktrees" / "checker-report.md"
            logs = repo / ".worktrees" / "logs"

            result = subprocess.run(
                [
                    bash_exe(),
                    bash_path(SCRIPT),
                    "--no-discover",
                    "--report",
                    bash_path(report),
                    "--logs-dir",
                    bash_path(logs),
                ],
                cwd=str(repo),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            text = report.read_text(encoding="utf-8")
            self.assertIn("SKIPPED", text)
            self.assertIn("broad discovery is disabled", text)

    def test_missing_boundary_helper_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = self._init_repo(root)
            helper_dir = root / "isolated-helper"
            helper_dir.mkdir()
            checker = helper_dir / "check-worktree.sh"
            shutil.copy2(SCRIPT, checker)
            report = repo / ".worktrees" / "checker-report.md"
            logs = repo / ".worktrees" / "logs"

            result = subprocess.run(
                [
                    bash_exe(), bash_path(checker), "--no-discover",
                    "--report", bash_path(report), "--logs-dir", bash_path(logs),
                ],
                cwd=str(repo), text=True, encoding="utf-8", errors="replace",
                capture_output=True,
            )

            self.assertEqual(result.returncode, 1)
            receipt = json.loads(report.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["status"], "failed")
            self.assertFalse(receipt["boundary_validation"]["available"])

    def test_validation_worker_count_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._init_repo(pathlib.Path(tmp))
            result = subprocess.run(
                [bash_exe(), bash_path(SCRIPT), "--no-discover", "--jobs", "9"],
                cwd=str(repo), text=True, encoding="utf-8", errors="replace",
                capture_output=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("must not exceed 8", result.stderr)

    def test_explicit_command_runs_without_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._init_repo(pathlib.Path(tmp))
            report = repo / ".worktrees" / "checker-report.md"
            logs = repo / ".worktrees" / "logs"

            result = subprocess.run(
                [
                    bash_exe(),
                    bash_path(SCRIPT),
                    "--no-discover",
                    "--command",
                    "custom=printf validation-ok",
                    "--report",
                    bash_path(report),
                    "--logs-dir",
                    bash_path(logs),
                ],
                cwd=str(repo),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            text = report.read_text(encoding="utf-8")
            self.assertIn("Artifact Collection", text)
            self.assertIn("OK", text)
            self.assertIn("- custom: `printf validation-ok`", text)
            self.assertIn("ALL GREEN", text)
            self.assertIn("validation-ok", text)
            receipt = json.loads(report.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["status"], "passed")
            self.assertTrue(receipt["read_only_fanout"])
            self.assertTrue(
                receipt["boundary_validation"]["untracked_diff_check_complete"]
            )

    def test_validation_commands_fan_out_and_aggregate_in_input_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._init_repo(pathlib.Path(tmp))
            report = repo / ".worktrees" / "checker-report.md"
            logs = repo / ".worktrees" / "logs"
            first = logs / "first.ready"
            second = logs / "second.ready"

            def barrier_command(own: pathlib.Path, peer: pathlib.Path) -> str:
                code = (
                    "import pathlib,sys,time;"
                    f"own=pathlib.Path({str(own)!r});peer=pathlib.Path({str(peer)!r});"
                    "own.parent.mkdir(parents=True,exist_ok=True);own.touch();"
                    "deadline=time.time()+3;"
                    "exec('while time.time() < deadline and not peer.exists():\\n time.sleep(0.02)');"
                    "sys.exit(0 if peer.exists() else 3)"
                )
                return shlex.join([sys.executable, "-c", code])

            result = subprocess.run(
                [
                    bash_exe(), bash_path(SCRIPT), "--no-discover", "--jobs", "2",
                    "--command", "first=" + barrier_command(first, second),
                    "--command", "second=" + barrier_command(second, first),
                    "--report", bash_path(report), "--logs-dir", bash_path(logs),
                ],
                cwd=str(repo), text=True, encoding="utf-8", errors="replace",
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            receipt = json.loads(report.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["max_concurrency"], 2)
            self.assertEqual(
                [item["label"] for item in receipt["results"]], ["first", "second"]
            )
            self.assertEqual(
                [item["exit_code"] for item in receipt["results"]], [0, 0]
            )

    def test_task_card_validation_block_runs_without_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._init_repo(pathlib.Path(tmp))
            task_card = repo / "task-card.md"
            task_card.write_text(
                "# Task\n\n"
                "## Validation Contract\n\n"
                "| Check | Command | Required? | Notes |\n"
                "|-------|---------|-----------|-------|\n"
                "| Local validation allowed? | yes | required | |\n\n"
                "```bash validation\n"
                "# comment lines are ignored\n"
                "printf task-card-validation-ok\n"
                "```\n",
                encoding="utf-8",
            )
            report = repo / ".worktrees" / "checker-report.md"
            logs = repo / ".worktrees" / "logs"

            result = subprocess.run(
                [
                    bash_exe(),
                    bash_path(SCRIPT),
                    "--no-discover",
                    "--task-card",
                    bash_path(task_card),
                    "--report",
                    bash_path(report),
                    "--logs-dir",
                    bash_path(logs),
                ],
                cwd=str(repo),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            text = report.read_text(encoding="utf-8")
            self.assertIn("- task-card-1: `printf task-card-validation-ok`", text)
            self.assertIn("task-card-validation-ok", text)
            self.assertIn("ALL GREEN", text)

    def test_task_card_local_validation_no_skips_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._init_repo(pathlib.Path(tmp))
            task_card = repo / "task-card.md"
            task_card.write_text(
                "# Task\n\n"
                "## Validation Contract\n\n"
                "| Check | Command | Required? | Notes |\n"
                "|-------|---------|-----------|-------|\n"
                "| Local validation allowed? | no | required | commands only |\n\n"
                "```bash validation\n"
                "false\n"
                "```\n",
                encoding="utf-8",
            )
            report = repo / ".worktrees" / "checker-report.md"
            logs = repo / ".worktrees" / "logs"

            result = subprocess.run(
                [
                    bash_exe(),
                    bash_path(SCRIPT),
                    "--no-discover",
                    "--task-card",
                    bash_path(task_card),
                    "--report",
                    bash_path(report),
                    "--logs-dir",
                    bash_path(logs),
                ],
                cwd=str(repo),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            text = report.read_text(encoding="utf-8")
            self.assertIn("Artifact Collection", text)
            self.assertIn("Validation", text)
            self.assertIn("SKIPPED by policy", text)
            self.assertIn("SKIPPED", text)
            self.assertIn("Local validation is disabled", text)
            self.assertNotIn("ALL GREEN", text)


if __name__ == "__main__":
    unittest.main()
