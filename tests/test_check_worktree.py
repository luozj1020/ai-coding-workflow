import os
import pathlib
import subprocess
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
