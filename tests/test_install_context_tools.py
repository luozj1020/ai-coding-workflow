import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install_context_tools.py"
INSTALLER = ROOT / "scripts" / "install_workflow.py"


def load_module():
    spec = importlib.util.spec_from_file_location("install_context_tools", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_helper(*extra_args, **kwargs):
    """Run install_context_tools.py as a subprocess."""
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + list(extra_args),
        cwd=str(ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        **kwargs,
    )


def missing_tools_env():
    env = os.environ.copy()
    env["PATH"] = ""
    return env


def run_installer(repo):
    return subprocess.run(
        [sys.executable, str(INSTALLER), str(repo)],
        cwd=str(ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=True,
    )


class TestDefaultCheck(unittest.TestCase):
    """Default invocation: read-only check, no network, no installs."""

    def test_check_runs_without_error(self):
        result = run_helper()
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Context tools status", result.stdout)

    def test_check_reports_profiles(self):
        result = run_helper()
        for profile in ["python", "node", "go", "rust"]:
            self.assertIn("[{}]".format(profile), result.stdout)

    def test_check_reports_tool_status(self):
        result = run_helper()
        # Each tool should appear with either OK or MISSING
        self.assertRegex(result.stdout, r"pyright (OK|MISSING)")
        self.assertRegex(result.stdout, r"ruff (OK|MISSING)")
        self.assertRegex(result.stdout, r"mypy (OK|MISSING)")
        self.assertRegex(result.stdout, r"typescript-language-server (OK|MISSING)")
        self.assertRegex(result.stdout, r"gopls (OK|MISSING)")
        self.assertRegex(result.stdout, r"rust-analyzer (OK|MISSING)")


class TestApplyDryRun(unittest.TestCase):
    """--apply PROFILE --manager MANAGER prints commands but does not run them."""

    def test_apply_unknown_profile_errors(self):
        result = run_helper("--apply", "nonexistent")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown profile", result.stdout.lower())

    def test_apply_unknown_manager_errors(self):
        result = run_helper("--apply", "python", "--manager", "nonexistent")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown manager", result.stdout.lower())

    def test_apply_python_npm_shows_commands(self):
        result = run_helper("--apply", "python", "--manager", "npm", env=missing_tools_env())
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Planned install commands", result.stdout)
        self.assertIn("Dry-run", result.stdout)
        # Should not contain "Installing"
        self.assertNotIn("Installing...", result.stdout)

    def test_apply_python_pip_shows_commands(self):
        result = run_helper("--apply", "python", "--manager", "pip", env=missing_tools_env())
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Planned install commands", result.stdout)
        self.assertIn("Dry-run", result.stdout)

    def test_apply_node_npm_shows_commands(self):
        result = run_helper("--apply", "node", "--manager", "npm", env=missing_tools_env())
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Planned install commands", result.stdout)

    def test_apply_go_go_shows_commands(self):
        result = run_helper("--apply", "go", "--manager", "go", env=missing_tools_env())
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Planned install commands", result.stdout)

    def test_apply_rust_rustup_shows_commands(self):
        result = run_helper("--apply", "rust", "--manager", "rustup", env=missing_tools_env())
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Planned install commands", result.stdout)

    def test_apply_without_manager_shows_all_suggestions(self):
        result = run_helper("--apply", "python", env=missing_tools_env())
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Planned install commands", result.stdout)
        self.assertIn("Dry-run", result.stdout)

    def test_yes_without_apply_errors(self):
        result = run_helper("--yes")
        self.assertNotEqual(result.returncode, 0)

    def test_yes_without_manager_errors(self):
        result = run_helper("--apply", "python", "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--yes requires --manager", result.stderr)

    def test_manager_without_apply_errors(self):
        result = run_helper("--manager", "npm")
        self.assertNotEqual(result.returncode, 0)


class TestApplyWithYes(unittest.TestCase):
    """--apply PROFILE --manager MANAGER --yes can be tested with a fake binary."""

    def test_yes_with_fake_manager_runs_command(self):
        """Create a fake 'npm' script that records invocation, then test --yes."""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "npm.log")
            # Create a fake npm that records invocation and exits 0.
            if sys.platform == "win32":
                fake_bin = os.path.join(tmp, "npm.cmd")
                with open(fake_bin, "w", encoding="utf-8") as f:
                    f.write('@echo off\necho fake-npm %*>>"%FAKE_NPM_LOG%"\n')
            else:
                fake_bin = os.path.join(tmp, "npm")
                with open(fake_bin, "w", encoding="utf-8") as f:
                    f.write("#!/bin/sh\necho fake-npm \"$@\" >> \"$FAKE_NPM_LOG\"\n")
                os.chmod(fake_bin, 0o755)

            env = os.environ.copy()
            env["PATH"] = tmp
            env["FAKE_NPM_LOG"] = log_path

            # Run with --yes using our fake npm
            result = subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--apply", "node", "--manager", "npm", "--yes"],
                cwd=str(ROOT),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                env=env,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            with open(log_path, "r", encoding="utf-8") as f:
                log = f.read()
            self.assertIn("fake-npm install -g typescript-language-server typescript", log)
            self.assertIn("fake-npm install -g eslint", log)


class TestInstallerCopiesHelper(unittest.TestCase):
    """install_workflow.py must copy install_context_tools.py to ai/."""

    def test_installer_copies_context_tools_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            run_installer(repo)
            self.assertTrue((repo / "ai" / "install_context_tools.py").exists())

    def test_installed_helper_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            run_installer(repo)
            result = subprocess.run(
                [sys.executable, str(repo / "ai" / "install_context_tools.py")],
                cwd=str(repo),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("Context tools status", result.stdout)

    def test_installed_helper_apply_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            run_installer(repo)
            result = subprocess.run(
                [sys.executable, str(repo / "ai" / "install_context_tools.py"),
                 "--apply", "python", "--manager", "pip"],
                cwd=str(repo),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                env=missing_tools_env(),
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("Dry-run", result.stdout)


class TestSuggestionTable(unittest.TestCase):
    """Validate the static suggestion table structure."""

    def test_all_profiles_have_tools(self):
        module = load_module()
        for profile, tools in module.SUGGESTIONS.items():
            self.assertIsInstance(tools, list)
            self.assertGreater(len(tools), 0, msg="Profile '{}' has no tools".format(profile))

    def test_all_tools_have_check_and_commands(self):
        module = load_module()
        for profile, tools in module.SUGGESTIONS.items():
            for tool in tools:
                self.assertIn("name", tool)
                self.assertIn("check", tool)
                self.assertIn("commands", tool)
                self.assertIsInstance(tool["check"], list)
                self.assertIsInstance(tool["commands"], dict)
                self.assertGreater(len(tool["commands"]), 0,
                                   msg="Tool '{}' in profile '{}' has no commands".format(
                                       tool["name"], profile))

    def test_all_managers_listed(self):
        module = load_module()
        expected = {"apt", "brew", "cargo", "choco", "go", "npm", "pip", "rustup", "scoop"}
        self.assertEqual(set(module.ALL_MANAGERS), expected)


if __name__ == "__main__":
    unittest.main()
