import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "clean_runtime.py"
INSTALLER = ROOT / "scripts" / "install_workflow.py"


def load_module():
    spec = importlib.util.spec_from_file_location("clean_runtime", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _init_repo(path):
    """Initialize a git repo with an initial commit."""
    subprocess.run(["git", "init", str(path)], capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(path),
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=str(path),
        capture_output=True,
        check=True,
    )
    # Write a .gitignore matching the real repo
    (path / ".gitignore").write_text(
        ".worktrees/\ntmp-*/\ntask-cards/\n",
        encoding="utf-8",
    )
    (path / "README.md").write_text("# fixture\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "-A"],
        cwd=str(path),
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(path),
        capture_output=True,
        check=True,
    )


class CleanRuntimeTests(unittest.TestCase):
    def run_clean(self, repo, extra_args=None):
        args = [sys.executable, str(SCRIPT), str(repo)]
        if extra_args:
            args.extend(extra_args)
        return subprocess.run(
            args,
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )

    # --- Dry-run behavior ---

    def test_dry_run_reports_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)
            # Create runtime artifacts
            (repo / ".worktrees").mkdir(exist_ok=True)
            (repo / ".worktrees" / ".gitkeep").write_text("", encoding="utf-8")
            (repo / ".worktrees" / "claude-1234.result.json").write_text("{}", encoding="utf-8")
            (repo / "tmp-something").mkdir()

            result = self.run_clean(repo)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Dry-run", result.stdout)
            self.assertIn("2 runtime artifact(s)", result.stdout)
            self.assertIn(".worktrees/claude-1234.result.json", result.stdout)
            self.assertIn("tmp-something", result.stdout)
            # Should not have deleted anything
            self.assertTrue((repo / ".worktrees" / "claude-1234.result.json").exists())
            self.assertTrue((repo / "tmp-something").exists())

    def test_dry_run_skips_gitkeep(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)
            (repo / ".worktrees").mkdir(exist_ok=True)
            (repo / ".worktrees" / ".gitkeep").write_text("", encoding="utf-8")

            result = self.run_clean(repo)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("No runtime artifacts", result.stdout)

    def test_dry_run_no_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)

            result = self.run_clean(repo)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("No runtime artifacts", result.stdout)

    # --- Apply behavior ---

    def test_apply_deletes_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)
            (repo / ".worktrees").mkdir(exist_ok=True)
            (repo / ".worktrees" / ".gitkeep").write_text("", encoding="utf-8")
            (repo / ".worktrees" / "claude-1234.result.json").write_text("{}", encoding="utf-8")
            (repo / "tmp-something").mkdir()

            result = self.run_clean(repo, ["--apply"])

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Removing", result.stdout)
            self.assertIn("removed: .worktrees/claude-1234.result.json", result.stdout)
            self.assertIn("removed: tmp-something", result.stdout)
            # Should have deleted artifacts
            self.assertFalse((repo / ".worktrees" / "claude-1234.result.json").exists())
            self.assertFalse((repo / "tmp-something").exists())
            # .gitkeep should survive
            self.assertTrue((repo / ".worktrees" / ".gitkeep").exists())

    def test_apply_preserves_gitkeep(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)
            (repo / ".worktrees").mkdir(exist_ok=True)
            (repo / ".worktrees" / ".gitkeep").write_text("", encoding="utf-8")
            (repo / ".worktrees" / "stale-entry").write_text("data", encoding="utf-8")

            result = self.run_clean(repo, ["--apply"])

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((repo / ".worktrees" / ".gitkeep").exists())
            self.assertFalse((repo / ".worktrees" / "stale-entry").exists())

    # --- Tracked-file protection ---

    def test_never_deletes_tracked_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)
            # Create a tracked tmp- file (commit it)
            (repo / "tmp-tracked.txt").write_text("tracked\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "tmp-tracked.txt"],
                cwd=str(repo),
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "add tracked tmp"],
                cwd=str(repo),
                capture_output=True,
                check=True,
            )

            result = self.run_clean(repo, ["--apply"])

            self.assertEqual(result.returncode, 0, result.stderr)
            # Tracked file should survive even though it matches tmp-*
            self.assertTrue((repo / "tmp-tracked.txt").exists())

    def test_never_deletes_directory_containing_tracked_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)
            subprocess.run(
                ["git", "rm", "--cached", ".gitignore"],
                cwd=str(repo),
                capture_output=True,
                check=True,
            )
            (repo / ".gitignore").write_text("task-cards/\n", encoding="utf-8")
            (repo / "task-cards").mkdir()
            (repo / "task-cards" / "tracked.md").write_text("# tracked\n", encoding="utf-8")
            subprocess.run(["git", "add", "-f", "task-cards/tracked.md"], cwd=str(repo), capture_output=True, check=True)
            subprocess.run(["git", "commit", "-m", "track task card"], cwd=str(repo), capture_output=True, check=True)

            result = self.run_clean(repo, ["--apply"])

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((repo / "task-cards" / "tracked.md").exists())

    def test_skips_active_claude_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)
            (repo / ".worktrees").mkdir(exist_ok=True)
            (repo / ".worktrees" / "claude-active").mkdir()
            (repo / ".worktrees" / "claude-active.pid").write_text(str(os.getpid()), encoding="utf-8")
            (repo / ".worktrees" / "claude-active.result.json").write_text("{}", encoding="utf-8")

            result = self.run_clean(repo, ["--apply"])

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((repo / ".worktrees" / "claude-active").exists())
            self.assertTrue((repo / ".worktrees" / "claude-active.pid").exists())
            self.assertTrue((repo / ".worktrees" / "claude-active.result.json").exists())

    # --- Installer inclusion ---

    def test_installer_copies_clean_runtime(self):
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
            self.assertTrue((repo / "ai" / "clean_runtime.py").exists())

    def test_installed_clean_runtime_runs(self):
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
            # Init git so clean_runtime can find repo root
            _init_repo(repo)
            result = subprocess.run(
                [sys.executable, str(repo / "ai" / "clean_runtime.py"), str(repo)],
                cwd=str(repo),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    # --- Doctor suggestion ---

    def test_doctor_suggests_clean_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)
            (repo / ".worktrees").mkdir(exist_ok=True)
            (repo / ".worktrees" / "claude-1234.result.json").write_text("{}", encoding="utf-8")

            doctor = ROOT / "scripts" / "doctor_workflow.py"
            result = subprocess.run(
                [sys.executable, str(doctor), str(repo)],
                cwd=str(ROOT),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("clean_runtime.py", result.stdout)

    # --- Task-cards directory ---

    def test_dry_run_reports_stale_task_cards(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)
            (repo / "task-cards").mkdir()
            (repo / "task-cards" / "old-task.md").write_text("# old\n", encoding="utf-8")

            result = self.run_clean(repo)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("task-cards/", result.stdout)

    # --- Registered worktree handling ---

    def _add_worktree(self, repo, name):
        """Create a registered git worktree under .worktrees/<name>."""
        wt_dir = repo / ".worktrees" / name
        wt_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "worktree", "add", str(wt_dir)],
            cwd=str(repo),
            capture_output=True,
            check=True,
        )
        return wt_dir

    def test_apply_removes_clean_registered_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)
            (repo / ".worktrees").mkdir(exist_ok=True)
            wt_dir = self._add_worktree(repo, "claude-clean")
            self.assertTrue(wt_dir.exists())

            result = self.run_clean(repo, ["--apply"])

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("removed: .worktrees/claude-clean (worktree)", result.stdout)
            # Worktree directory should be gone
            self.assertFalse(wt_dir.exists())

    def test_apply_skips_dirty_registered_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)
            (repo / ".worktrees").mkdir(exist_ok=True)
            wt_dir = self._add_worktree(repo, "claude-dirty")
            # Create an uncommitted file inside the worktree
            (wt_dir / "uncommitted.txt").write_text("pending changes\n", encoding="utf-8")
            self.assertTrue(wt_dir.exists())

            result = self.run_clean(repo, ["--apply"])

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("skipped: .worktrees/claude-dirty", result.stdout)
            self.assertIn("dirty", result.stdout.lower())
            # Worktree directory must NOT be removed
            self.assertTrue(wt_dir.exists())

    def test_dry_run_annotates_registered_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)
            (repo / ".worktrees").mkdir(exist_ok=True)
            wt_dir = self._add_worktree(repo, "claude-dry")
            self.assertTrue(wt_dir.exists())

            result = self.run_clean(repo)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Dry-run", result.stdout)
            self.assertIn(".worktrees/claude-dry (worktree)", result.stdout)
            # Should not have deleted anything
            self.assertTrue(wt_dir.exists())

    def test_adjacent_artifacts_cleaned_when_worktree_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)
            (repo / ".worktrees").mkdir(exist_ok=True)
            wt_dir = self._add_worktree(repo, "claude-mixed")
            # Make the worktree dirty so it gets skipped
            (wt_dir / "wip.txt").write_text("work in progress\n", encoding="utf-8")
            # Add adjacent artifact files (not a worktree, just files)
            (repo / ".worktrees" / "claude-mixed.result.json").write_text("{}", encoding="utf-8")
            (repo / ".worktrees" / "claude-mixed.pid").write_text("not-a-pid", encoding="utf-8")

            result = self.run_clean(repo, ["--apply"])

            self.assertEqual(result.returncode, 0, result.stderr)
            # Dirty worktree should be skipped
            self.assertIn("skipped: .worktrees/claude-mixed", result.stdout)
            self.assertTrue(wt_dir.exists())
            # Adjacent artifacts should still be cleaned
            self.assertFalse((repo / ".worktrees" / "claude-mixed.result.json").exists())
            self.assertFalse((repo / ".worktrees" / "claude-mixed.pid").exists())


if __name__ == "__main__":
    unittest.main()
