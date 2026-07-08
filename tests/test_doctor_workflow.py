import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile
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

    def run_installer(self, repo):
        return subprocess.run(
            [sys.executable, str(INSTALLER), str(repo)],
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
            self.assertIn("[artifacts]", result.stdout)
            self.assertIn("1 runtime", result.stdout)
            self.assertIn("1 tmp-*", result.stdout)

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


if __name__ == "__main__":
    unittest.main()
