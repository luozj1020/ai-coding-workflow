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
            self.assertIn("- custom: `printf validation-ok`", text)
            self.assertIn("ALL GREEN", text)
            self.assertIn("validation-ok", text)


if __name__ == "__main__":
    unittest.main()
