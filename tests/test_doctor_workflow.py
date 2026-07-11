import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "doctor_workflow.py"
INSTALLER = ROOT / "scripts" / "install_workflow.py"


def load_module():
    spec = importlib.util.spec_from_file_location("doctor_workflow", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DoctorWorkflowTests(unittest.TestCase):
    def run_doctor(self, repo):
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(repo)],
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )

    def run_installer(self, repo, *extra_args):
        return subprocess.run(
            [sys.executable, str(INSTALLER), str(repo)] + list(extra_args),
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=True,
        )

    # --- Exit code behavior ---

    def test_doctor_exits_nonzero_when_workflow_missing(self):
        """Doctor reports a git repo that has not been bootstrapped yet."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(
                ["git", "init", str(repo)],
                capture_output=True,
                check=True,
            )
            result = self.run_doctor(repo)
            self.assertEqual(result.returncode, 1)
            self.assertIn("Workflow Doctor", result.stdout)
            self.assertIn("Project workflow is not bootstrapped", result.stdout)
            self.assertIn("install_workflow.py", result.stdout)

    def test_doctor_exits_zero_on_bootstrapped_repo(self):
        """Doctor exits 0 when workflow files are installed in a git repo."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)
            subprocess.run(
                ["git", "init", str(repo)],
                capture_output=True,
                check=True,
            )
            result = self.run_doctor(repo)
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertIn("Project workflow files are installed", result.stdout)

    def test_doctor_warns_when_local_workflow_files_are_outdated(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)
            subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
            (repo / "ai" / "dispatch-to-claude.sh").write_text(
                "# old local dispatch\n", encoding="utf-8"
            )

            old_roots = module._candidate_skill_roots
            try:
                module._candidate_skill_roots = lambda: [str(ROOT)]
                findings, has_error = module.run_doctor(str(repo))
            finally:
                module._candidate_skill_roots = old_roots

            self.assertFalse(has_error)
            text = "\n".join("{} [{}] {}".format(*f) for f in findings)
            self.assertIn("workflow-version", text)
            self.assertIn("ai/dispatch-to-claude.sh", text)
            self.assertIn("--update-workflow-files", text)

    def test_doctor_exits_nonzero_without_git(self):
        """Doctor exits 1 when no .git is found."""
        with tempfile.TemporaryDirectory() as tmp:
            # tmp itself is not a git repo
            result = self.run_doctor(pathlib.Path(tmp))
            self.assertEqual(result.returncode, 1)
            self.assertIn("ERROR", result.stdout)

    # --- Check categories reported ---

    def test_doctor_reports_repo_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
            result = self.run_doctor(repo)
            self.assertIn("Repository root:", result.stdout)

    def test_doctor_reports_git_availability(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
            result = self.run_doctor(repo)
            self.assertIn("[git]", result.stdout)

    def test_doctor_reports_dirty_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
            # Create an untracked file
            (repo / "dirty.txt").write_text("dirty", encoding="utf-8")
            result = self.run_doctor(repo)
            self.assertIn("dirty", result.stdout.lower())

    def test_doctor_reports_clean_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
            (repo / "README.md").write_text("# fixture\n", encoding="utf-8")
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo), capture_output=True, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(repo), capture_output=True, check=True)
            subprocess.run(
                ["git", "add", "-A"],
                cwd=str(repo),
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "init"],
                cwd=str(repo),
                capture_output=True,
                check=True,
            )
            result = self.run_doctor(repo)
            self.assertIn("clean", result.stdout.lower())

    def test_doctor_reports_bash_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
            result = self.run_doctor(repo)
            self.assertIn("[bash]", result.stdout)

    def test_doctor_reports_claude_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
            result = self.run_doctor(repo)
            self.assertIn("[claude]", result.stdout)

    def test_doctor_reports_proxy_vars(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
            env = os.environ.copy()
            env["HTTP_PROXY"] = "http://user:secret@proxy:8080"
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(repo)],
                cwd=str(ROOT),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                env=env,
            )
            self.assertIn("[proxy]", result.stdout)
            self.assertIn("HTTP_PROXY", result.stdout)
            # Credentials must be masked
            self.assertNotIn("secret", result.stdout)
            self.assertIn("***", result.stdout)

    def test_doctor_reports_codex_skill_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
            result = self.run_doctor(repo)
            self.assertIn("[codex-skill]", result.stdout)

    def test_doctor_reports_context_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
            result = self.run_doctor(repo)
            self.assertIn("[context-tools]", result.stdout)
            # Should mention either Available or Missing
            self.assertTrue(
                "Available:" in result.stdout or "Missing:" in result.stdout,
                msg="Expected 'Available:' or 'Missing:' in context-tools output"
            )


    def test_context_tools_resolve_cmd_on_path(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            if sys.platform == "win32":
                fake_tool = pathlib.Path(tmp) / "pyright.cmd"
                fake_tool.write_text("@echo off\nexit /b 0\n", encoding="utf-8")
            else:
                fake_tool = pathlib.Path(tmp) / "pyright"
                fake_tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                fake_tool.chmod(0o755)

            old_path = os.environ.get("PATH", "")
            old_tools = module.CONTEXT_TOOLS
            try:
                os.environ["PATH"] = tmp
                module.CONTEXT_TOOLS = [("pyright", ["pyright", "--version"])]
                self.assertEqual(module._check_context_tools(), [("pyright", True)])
            finally:
                os.environ["PATH"] = old_path
                module.CONTEXT_TOOLS = old_tools

    def test_doctor_reports_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
            # Create some runtime artifacts
            (repo / ".worktrees").mkdir()
            (repo / ".worktrees" / "claude-1234.result.json").write_text("{}", encoding="utf-8")
            (repo / "tmp-something").mkdir()
            result = self.run_doctor(repo)
            self.assertIn("[worktrees-inventory]", result.stdout)
            self.assertIn(".worktrees/", result.stdout)
            self.assertIn("1 entries", result.stdout)
            self.assertIn("1 tmp-*", result.stdout)

    def test_doctor_warns_when_worktrees_ignore_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)

            result = self.run_doctor(repo)

            self.assertIn("[worktrees-ignore]", result.stdout)
            self.assertIn("/.worktrees/*", result.stdout)

    def test_doctor_reports_worktrees_ignore_when_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)
            subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)

            result = self.run_doctor(repo)

            self.assertIn("[worktrees-ignore]", result.stdout)
            self.assertIn("runtime artifacts are ignored", result.stdout)

    def test_doctor_accepts_local_only_info_exclude(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
            self.run_installer(repo, "--local-only")

            result = self.run_doctor(repo)

            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertIn("[worktrees-ignore]", result.stdout)
            self.assertIn("local-only control-plane ignore active", result.stdout)
            self.assertFalse((repo / ".gitignore").exists())

    def test_doctor_warns_for_large_repositories(self):
        module = load_module()
        old_count = module._tracked_file_count
        try:
            module._tracked_file_count = lambda repo_root: 20000
            with tempfile.TemporaryDirectory() as tmp:
                repo = pathlib.Path(tmp) / "repo"
                repo.mkdir()
                subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)

                findings, has_error = module.run_doctor(str(repo))
        finally:
            module._tracked_file_count = old_count

        self.assertTrue(has_error)
        text = "\n".join("{} [{}] {}".format(*f) for f in findings)
        self.assertIn("large-repo", text)
        self.assertIn("Worktree / Large Repo Strategy Gate", text)
        self.assertIn("CLAUDE_CODE_EXECUTION_PROFILE=fast-large-repo", text)
        self.assertIn("Claude Context Packet", text)
        self.assertIn("task-size-classifier", text)
        self.assertIn("CLAUDE_CODE_WORKTREE_STRATEGY=reuse-managed", text)
        self.assertIn("prefer ai/locate-code.py", text)

    def test_doctor_recommends_reuse_and_local_only_for_very_large_repositories(self):
        module = load_module()
        old_count = module._tracked_file_count
        try:
            module._tracked_file_count = lambda repo_root: 50000
            with tempfile.TemporaryDirectory() as tmp:
                repo = pathlib.Path(tmp) / "repo"
                repo.mkdir()
                subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)

                findings, has_error = module.run_doctor(str(repo))
        finally:
            module._tracked_file_count = old_count

        self.assertTrue(has_error)
        text = "\n".join("{} [{}] {}".format(*f) for f in findings)
        self.assertIn("very large", text)
        self.assertIn("--local-only", text)
        self.assertIn("CLAUDE_CODE_EVIDENCE_MODE=summary", text)

    # --- Proxy masking ---

    def test_mask_proxy_value_with_credentials(self):
        module = load_module()
        masked = module._mask_proxy_value("http://user:pass@proxy.example.com:8080")
        self.assertNotIn("pass", masked)
        self.assertIn("***", masked)
        self.assertIn("proxy.example.com", masked)

    def test_mask_proxy_value_without_credentials(self):
        module = load_module()
        masked = module._mask_proxy_value("http://proxy.example.com:8080")
        self.assertEqual(masked, "http://proxy.example.com:8080")

    def test_mask_proxy_value_ip_only(self):
        module = load_module()
        masked = module._mask_proxy_value("10.0.0.1:3128")
        self.assertEqual(masked, "10.0.0.1:3128")

    # --- Installer includes doctor ---

    def test_installer_copies_doctor_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)
            self.assertTrue((repo / "ai" / "doctor_workflow.py").exists())

    def test_installed_doctor_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)
            # Init git so doctor can find repo root
            subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
            result = subprocess.run(
                [sys.executable, str(repo / "ai" / "doctor_workflow.py"), str(repo)],
                cwd=str(repo),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("Workflow Doctor", result.stdout)

    def test_doctor_is_idempotent_after_reinstall(self):
        """Re-installing doesn't break the doctor script."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)
            self.run_installer(repo)  # second install
            subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
            result = subprocess.run(
                [sys.executable, str(repo / "ai" / "doctor_workflow.py"), str(repo)],
                cwd=str(repo),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)


class InventoryTests(unittest.TestCase):
    """Tests for _inventory_worktrees and related runtime inventory behavior."""

    def _make_repo(self, tmp):
        repo = pathlib.Path(tmp) / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
        return repo

    # --- Missing / empty .worktrees ---

    def test_inventory_missing_worktrees(self):
        """No .worktrees directory returns zero counts and no error."""
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._make_repo(tmp)
            result = module._inventory_worktrees(str(repo))
        self.assertEqual(result["entry_count"], 0)
        self.assertEqual(result["approximate_bytes"], 0)
        self.assertIsNone(result["oldest_path"])
        self.assertEqual(result["oldest_age_days"], 0)
        self.assertEqual(result["buckets"], {"<7": 0, "7-30": 0, ">30": 0})
        self.assertFalse(result["partial"])
        self.assertIsNone(result["error"])

    def test_inventory_empty_worktrees_only_gitkeep(self):
        """.worktrees with only .gitkeep counts as zero entries."""
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._make_repo(tmp)
            wt = repo / ".worktrees"
            wt.mkdir()
            (wt / ".gitkeep").touch()
            result = module._inventory_worktrees(str(repo))
        self.assertEqual(result["entry_count"], 0)
        self.assertEqual(result["approximate_bytes"], 0)

    # --- Entry count excludes .gitkeep ---

    def test_inventory_entry_count_excludes_gitkeep(self):
        """Only non-.gitkeep entries are counted."""
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._make_repo(tmp)
            wt = repo / ".worktrees"
            wt.mkdir()
            (wt / ".gitkeep").touch()
            (wt / "entry-a").mkdir()
            (wt / "entry-b").mkdir()
            result = module._inventory_worktrees(str(repo))
        self.assertEqual(result["entry_count"], 2)

    # --- Age buckets and oldest entry ---

    def test_inventory_age_buckets(self):
        """Entries are bucketed by age: <7d, 7-30d, >30d."""
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._make_repo(tmp)
            wt = repo / ".worktrees"
            wt.mkdir()
            now = time.time()
            # Recent entry (< 7 days)
            recent = wt / "recent"
            recent.mkdir()
            (recent / "f.txt").write_text("x", encoding="utf-8")
            os.utime(str(recent), (now - 2 * 86400, now - 2 * 86400))
            # Medium entry (7-30 days)
            medium = wt / "medium"
            medium.mkdir()
            (medium / "f.txt").write_text("x", encoding="utf-8")
            os.utime(str(medium), (now - 15 * 86400, now - 15 * 86400))
            # Old entry (> 30 days)
            old = wt / "old"
            old.mkdir()
            (old / "f.txt").write_text("x", encoding="utf-8")
            os.utime(str(old), (now - 45 * 86400, now - 45 * 86400))
            result = module._inventory_worktrees(str(repo))
        self.assertEqual(result["entry_count"], 3)
        self.assertEqual(result["buckets"]["<7"], 1)
        self.assertEqual(result["buckets"]["7-30"], 1)
        self.assertEqual(result["buckets"][">30"], 1)

    def test_inventory_oldest_entry(self):
        """Oldest entry path and age are tracked correctly."""
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._make_repo(tmp)
            wt = repo / ".worktrees"
            wt.mkdir()
            now = time.time()
            young = wt / "young"
            young.mkdir()
            os.utime(str(young), (now - 5 * 86400, now - 5 * 86400))
            old = wt / "old"
            old.mkdir()
            os.utime(str(old), (now - 60 * 86400, now - 60 * 86400))
            result = module._inventory_worktrees(str(repo))
        self.assertIsNotNone(result["oldest_path"])
        self.assertIn("old", result["oldest_path"])
        self.assertGreater(result["oldest_age_days"], 59)
        self.assertLess(result["oldest_age_days"], 62)

    # --- Approximate size and traversal cap/partial marker ---

    def test_inventory_approximate_size(self):
        """Size is computed from files inside worktree directories."""
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._make_repo(tmp)
            wt = repo / ".worktrees"
            wt.mkdir()
            entry = wt / "entry"
            entry.mkdir()
            (entry / "a.txt").write_text("hello", encoding="utf-8")  # 5 bytes
            (entry / "b.txt").write_text("world!!", encoding="utf-8")  # 7 bytes
            result = module._inventory_worktrees(str(repo))
        self.assertEqual(result["approximate_bytes"], 12)
        self.assertFalse(result["partial"])

    def test_inventory_approximate_size_single_file_entry(self):
        """Non-directory entries use stat size."""
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._make_repo(tmp)
            wt = repo / ".worktrees"
            wt.mkdir()
            f = wt / "result.json"
            f.write_text("abcde", encoding="utf-8")  # 5 bytes
            result = module._inventory_worktrees(str(repo))
        self.assertEqual(result["entry_count"], 1)
        self.assertEqual(result["approximate_bytes"], 5)
        self.assertFalse(result["partial"])

    def test_inventory_traversal_cap_sets_partial(self):
        """When file count exceeds _WORKTREES_MAX_SIZE_NODES, partial is True."""
        module = load_module()
        old_cap = module._WORKTREES_MAX_SIZE_NODES
        module._WORKTREES_MAX_SIZE_NODES = 5
        try:
            with tempfile.TemporaryDirectory() as tmp:
                repo = self._make_repo(tmp)
                wt = repo / ".worktrees"
                wt.mkdir()
                entry = wt / "big"
                entry.mkdir()
                # Create more files than the cap
                for i in range(10):
                    (entry / "f{}.txt".format(i)).write_text("x", encoding="utf-8")
                result = module._inventory_worktrees(str(repo))
            self.assertTrue(result["partial"])
        finally:
            module._WORKTREES_MAX_SIZE_NODES = old_cap

    # --- Threshold guidance ---

    def test_threshold_guidance_count(self):
        """High entry count triggers guidance with preview command, no auto-delete."""
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._make_repo(tmp)
            wt = repo / ".worktrees"
            wt.mkdir()
            for i in range(101):
                (wt / "entry-{}".format(i)).mkdir()
            findings, has_error = module.run_doctor(str(repo))
        text = "\n".join("{} [{}] {}".format(*f) for f in findings)
        self.assertIn("clean_runtime.py", text)
        self.assertNotIn("automatically", text.lower().replace("never deletes automatically", ""))
        self.assertIn("never deletes automatically", text)
        self.assertIn("High entry count", text)

    def test_threshold_guidance_size(self):
        """Large disk usage triggers guidance with preview command."""
        module = load_module()
        old_inventory = module._inventory_worktrees
        def fake_inventory(repo_root):
            r = old_inventory(repo_root)
            r["approximate_bytes"] = 2 * 1024 * 1024 * 1024  # 2 GiB
            return r
        module._inventory_worktrees = fake_inventory
        try:
            with tempfile.TemporaryDirectory() as tmp:
                repo = self._make_repo(tmp)
                wt = repo / ".worktrees"
                wt.mkdir()
                (wt / "entry").mkdir()
                findings, has_error = module.run_doctor(str(repo))
        finally:
            module._inventory_worktrees = old_inventory
        text = "\n".join("{} [{}] {}".format(*f) for f in findings)
        self.assertIn("clean_runtime.py", text)
        self.assertIn("never deletes automatically", text)
        self.assertIn("Disk usage is large", text)

    def test_threshold_guidance_age(self):
        """Old entries trigger guidance with preview command."""
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._make_repo(tmp)
            wt = repo / ".worktrees"
            wt.mkdir()
            old = wt / "stale"
            old.mkdir()
            now = time.time()
            os.utime(str(old), (now - 35 * 86400, now - 35 * 86400))
            findings, has_error = module.run_doctor(str(repo))
        text = "\n".join("{} [{}] {}".format(*f) for f in findings)
        self.assertIn("clean_runtime.py", text)
        self.assertIn("never deletes automatically", text)
        self.assertIn("30 days", text)

    # --- Stat/list errors warn without crash ---

    def test_inventory_listdir_error_warns_without_crash(self):
        """os.listdir failure produces an error string without raising."""
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._make_repo(tmp)
            wt = repo / ".worktrees"
            wt.mkdir()
            real_listdir = os.listdir
            def fake_listdir(path):
                if str(wt) == str(path):
                    raise PermissionError("denied")
                return real_listdir(path)
            os.listdir = fake_listdir
            try:
                result = module._inventory_worktrees(str(repo))
            finally:
                os.listdir = real_listdir
        self.assertIsNotNone(result["error"])
        self.assertIn("cannot list .worktrees", result["error"])
        self.assertEqual(result["entry_count"], 0)

    def test_inventory_stat_error_warns_without_crash(self):
        """os.stat failure on an entry produces a warning without raising."""
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._make_repo(tmp)
            wt = repo / ".worktrees"
            wt.mkdir()
            (wt / "ok-entry").mkdir()
            real_stat = os.stat
            call_count = [0]
            def fake_stat(path, *args, **kwargs):
                # Let the worktrees dir stat pass, fail on ok-entry
                if "ok-entry" in str(path) and "ok-entry" == pathlib.Path(path).name:
                    raise OSError("stat failed")
                return real_stat(path, *args, **kwargs)
            os.stat = fake_stat
            try:
                result = module._inventory_worktrees(str(repo))
            finally:
                os.stat = real_stat
        self.assertIsNotNone(result["error"])
        self.assertIn("stat error", result["error"])
        self.assertEqual(result["entry_count"], 1)

    def test_doctor_survives_inventory_errors(self):
        """Full doctor run does not crash when inventory encounters errors."""
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._make_repo(tmp)
            wt = repo / ".worktrees"
            wt.mkdir()
            (wt / "entry").mkdir()
            real_listdir = os.listdir
            def fake_listdir(path):
                if str(wt) == str(path):
                    raise PermissionError("denied")
                return real_listdir(path)
            os.listdir = fake_listdir
            try:
                findings, has_error = module.run_doctor(str(repo))
            finally:
                os.listdir = real_listdir
        # Should not crash; should report the error
        text = "\n".join("{} [{}] {}".format(*f) for f in findings)
        self.assertIn("cannot list .worktrees", text)

    # --- Symlink entries ---

    def test_inventory_symlink_entry_is_counted(self):
        """Symlink entries are counted in entry_count."""
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._make_repo(tmp)
            wt = repo / ".worktrees"
            wt.mkdir()
            # Create a target outside .worktrees
            outside = pathlib.Path(tmp) / "outside"
            outside.mkdir()
            (outside / "data.txt").write_text("x" * 1000, encoding="utf-8")
            # Create symlink inside .worktrees pointing outside
            link = wt / "link-to-outside"
            os.symlink(str(outside), str(link))
            result = module._inventory_worktrees(str(repo))
        self.assertEqual(result["entry_count"], 1)

    def test_inventory_symlink_size_does_not_escape(self):
        """Size traversal does not follow symlinks outside .worktrees."""
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._make_repo(tmp)
            wt = repo / ".worktrees"
            wt.mkdir()
            # Create a large target outside .worktrees
            outside = pathlib.Path(tmp) / "outside"
            outside.mkdir()
            (outside / "big.txt").write_text("x" * 10000, encoding="utf-8")
            # Create symlink inside .worktrees
            link = wt / "link-to-outside"
            os.symlink(str(outside), str(link))
            result = module._inventory_worktrees(str(repo))
        # The symlink's own stat size should be used, not the target's contents.
        # A symlink's size is typically the length of the target path, far less than 10000.
        self.assertLess(result["approximate_bytes"], 10000)

    def test_inventory_symlink_with_real_entries(self):
        """Symlink and real entries coexist correctly in inventory."""
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._make_repo(tmp)
            wt = repo / ".worktrees"
            wt.mkdir()
            # Real entry
            real = wt / "real-entry"
            real.mkdir()
            (real / "f.txt").write_text("hello", encoding="utf-8")  # 5 bytes
            # Symlink entry
            outside = pathlib.Path(tmp) / "outside"
            outside.mkdir()
            (outside / "big.txt").write_text("x" * 5000, encoding="utf-8")
            link = wt / "symlink-entry"
            os.symlink(str(outside), str(link))
            result = module._inventory_worktrees(str(repo))
        self.assertEqual(result["entry_count"], 2)
        # Only the real entry's content should contribute to size
        self.assertGreaterEqual(result["approximate_bytes"], 5)
        self.assertLess(result["approximate_bytes"], 5000)


if __name__ == "__main__":
    unittest.main()
