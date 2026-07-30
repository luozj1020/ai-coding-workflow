import contextlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, call


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "update_skill.py"


def load_module():
    spec = importlib.util.spec_from_file_location("update_skill", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ParseArgsGuidedSetupTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_setup_current_accepted(self):
        args = self.module.parse_args(["--setup-current"])
        self.assertTrue(args.setup_current)
        self.assertFalse(args.apply)

    def test_setup_current_with_apply(self):
        args = self.module.parse_args(["--setup-current", "--apply"])
        self.assertTrue(args.setup_current)
        self.assertTrue(args.apply)

    def test_setup_repo_accepted(self):
        args = self.module.parse_args(["--setup-repo", "/tmp/repo"])
        self.assertEqual(args.setup_repo, "/tmp/repo")
        self.assertFalse(args.apply)

    def test_setup_repo_with_apply(self):
        args = self.module.parse_args(["--setup-repo", "/tmp/repo", "--apply"])
        self.assertEqual(args.setup_repo, "/tmp/repo")
        self.assertTrue(args.apply)

    def test_setup_current_and_setup_repo_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            self.module.parse_args(["--setup-current", "--setup-repo", "/tmp/repo"])

    def test_apply_without_setup_errors(self):
        with self.assertRaises(SystemExit):
            self.module.parse_args(["--apply"])

    def test_apply_with_bootstrap_errors(self):
        with self.assertRaises(SystemExit):
            self.module.parse_args(["--bootstrap-current", "--apply"])

    def test_setup_current_and_bootstrap_current_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            self.module.parse_args(["--setup-current", "--bootstrap-current"])

    def test_setup_repo_and_bootstrap_repo_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            self.module.parse_args(["--setup-repo", "/tmp/repo", "--bootstrap-repo", "/tmp/repo"])

    def test_cross_setup_and_bootstrap_modes_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            self.module.parse_args(["--setup-current", "--bootstrap-repo", "/tmp/repo"])
        with self.assertRaises(SystemExit):
            self.module.parse_args(["--setup-repo", "/tmp/repo", "--bootstrap-current"])

    def test_guided_preview_rejects_pull_because_it_writes(self):
        with self.assertRaises(SystemExit):
            self.module.parse_args(["--setup-current", "--pull"])

    def test_guided_apply_allows_pull(self):
        args = self.module.parse_args(["--setup-current", "--pull", "--apply"])
        self.assertTrue(args.pull)

    def test_source_defaults_to_deferred_resolution(self):
        args = self.module.parse_args([])
        self.assertIsNone(args.source)


class ResolveSourceTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_explicit_source_is_used_without_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, provenance = self.module.resolve_source(tmp, "/missing/install")
        self.assertEqual(source, os.path.abspath(tmp))
        self.assertIsNone(provenance)

    def test_installed_updater_resolves_recorded_git_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            installed = root / "installed"
            source = root / "source"
            installed.mkdir()
            source.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=str(source), check=True)
            (source / "tracked.txt").write_text("ok\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=str(source), check=True)
            subprocess.run(
                [
                    "git", "-c", "user.name=Test",
                    "-c", "user.email=test@example.com",
                    "commit", "-qm", "initial",
                ],
                cwd=str(source),
                check=True,
            )
            commit = self.module.git_commit(str(source))
            (installed / self.module.INSTALL_PROVENANCE_FILE).write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "source_path": str(source),
                        "source_commit": commit,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            resolved, provenance = self.module.resolve_source(
                running_root=str(installed)
            )

            self.assertEqual(resolved, str(source))
            self.assertEqual(provenance["source_commit"], commit)

    def test_installed_updater_without_provenance_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "no valid update provenance"):
                self.module.resolve_source(running_root=tmp)

    def test_self_referential_provenance_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            installed = pathlib.Path(tmp)
            (installed / self.module.INSTALL_PROVENANCE_FILE).write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "source_path": str(installed),
                        "source_commit": None,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "points back"):
                self.module.resolve_source(running_root=str(installed))


class BuildGuidedPhasesTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_returns_four_phases(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = pathlib.Path(tmp) / "source"
            (source / "scripts").mkdir(parents=True)
            (source / "assets").mkdir()
            (source / "scripts" / "install_for_codex.py").write_text("ok\n", encoding="utf-8")
            _, _, phases = self.module.build_guided_phases(str(source), "/tmp/repo")
        self.assertEqual(len(phases), 4)

    def test_phase_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = pathlib.Path(tmp) / "source"
            (source / "scripts").mkdir(parents=True)
            (source / "assets").mkdir()
            (source / "scripts" / "install_for_codex.py").write_text("ok\n", encoding="utf-8")
            _, _, phases = self.module.build_guided_phases(str(source), "/tmp/repo")
        labels = [p["label"] for p in phases]
        self.assertEqual(labels, ["skill-update", "workflow-bootstrap", "auto-setup", "doctor"])

    def test_phase_commands_reference_source_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = pathlib.Path(tmp) / "source"
            (source / "scripts").mkdir(parents=True)
            (source / "assets").mkdir()
            (source / "scripts" / "install_for_codex.py").write_text("ok\n", encoding="utf-8")
            _, _, phases = self.module.build_guided_phases(str(source), "/tmp/repo")
        # skill-update uses install_for_codex.py
        self.assertIn("install_for_codex.py", phases[0]["argv"][1])
        # workflow-bootstrap uses install_workflow.py
        self.assertIn("install_workflow.py", phases[1]["argv"][1])
        # auto-setup uses install_for_codex.py --auto-setup
        self.assertIn("--auto-setup", phases[2]["argv"])
        self.assertIn("--apply", phases[2]["argv"])
        # doctor uses doctor_workflow.py
        self.assertIn("doctor_workflow.py", phases[3]["argv"][1])


class PrintGuidedPreviewTests(unittest.TestCase):
    """Preview must not create any files or call subprocess."""

    def setUp(self):
        self.module = load_module()

    def test_preview_prints_phase_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = pathlib.Path(tmp) / "source"
            (source / "scripts").mkdir(parents=True)
            (source / "assets").mkdir()
            (source / "scripts" / "install_for_codex.py").write_text("ok\n", encoding="utf-8")
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            _, _, phases = self.module.build_guided_phases(str(source), str(repo))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.module.print_guided_preview(str(source), str(repo), phases)
            output = stdout.getvalue()

        self.assertIn("Guided setup preview (no changes):", output)
        self.assertIn("skill-update", output)
        self.assertIn("workflow-bootstrap", output)
        self.assertIn("auto-setup", output)
        self.assertIn("doctor", output)
        self.assertIn("--apply", output)

    def test_preview_creates_no_files_in_repo(self):
        """Preview must not create any files in the target repository."""
        with tempfile.TemporaryDirectory() as tmp:
            source = pathlib.Path(tmp) / "source"
            (source / "scripts").mkdir(parents=True)
            (source / "assets").mkdir()
            (source / "scripts" / "install_for_codex.py").write_text("ok\n", encoding="utf-8")
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            _, _, phases = self.module.build_guided_phases(str(source), str(repo))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.module.print_guided_preview(str(source), str(repo), phases)

            # Only the directory we created should exist
            self.assertEqual(os.listdir(str(repo)), [])

    def test_preview_creates_no_skill_install(self):
        """Preview must not install the skill."""
        with tempfile.TemporaryDirectory() as tmp:
            source = pathlib.Path(tmp) / "source"
            (source / "scripts").mkdir(parents=True)
            (source / "assets").mkdir()
            (source / "scripts" / "install_for_codex.py").write_text("ok\n", encoding="utf-8")
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            home = pathlib.Path(tmp) / "home"
            home.mkdir()
            _, _, phases = self.module.build_guided_phases(str(source), str(repo))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.module.print_guided_preview(str(source), str(repo), phases)

            self.assertFalse((home / ".codex").exists())


class RunGuidedSetupTests(unittest.TestCase):
    """Apply orchestration tests mock subprocess phases and verify order/argv/failure propagation."""

    def setUp(self):
        self.module = load_module()

    def test_apply_runs_all_phases_in_order(self):
        """Verify phases execute in order with correct argv."""
        call_log = []

        def mock_run(argv, **kwargs):
            call_log.append(list(argv))

            class R:
                returncode = 0
            return R()

        with tempfile.TemporaryDirectory() as tmp:
            source = pathlib.Path(tmp) / "source"
            (source / "scripts").mkdir(parents=True)
            (source / "assets").mkdir()
            (source / "scripts" / "install_for_codex.py").write_text("ok\n", encoding="utf-8")
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            _, _, phases = self.module.build_guided_phases(str(source), str(repo))

            with patch("subprocess.run", side_effect=mock_run):
                result = self.module.run_guided_setup(str(source), str(repo), phases)

        self.assertEqual(result, 0)
        self.assertEqual(len(call_log), 4)
        # Phase 1: skill update
        self.assertIn("install_for_codex.py", call_log[0][1])
        self.assertNotIn("--auto-setup", call_log[0])
        # Phase 2: workflow bootstrap
        self.assertIn("install_workflow.py", call_log[1][1])
        self.assertIn("--update-workflow-files", call_log[1])
        # Phase 3: auto-setup with --apply
        self.assertIn("install_for_codex.py", call_log[2][1])
        self.assertIn("--auto-setup", call_log[2])
        self.assertIn("--apply", call_log[2])
        # Phase 4: doctor
        self.assertIn("doctor_workflow.py", call_log[3][1])

    def test_apply_stops_on_failure(self):
        """Verify that a failed phase stops execution and returns non-zero."""
        call_count = [0]

        def mock_run(argv, **kwargs):
            call_count[0] += 1

            class R:
                returncode = 1 if call_count[0] == 2 else 0
            return R()

        with tempfile.TemporaryDirectory() as tmp:
            source = pathlib.Path(tmp) / "source"
            (source / "scripts").mkdir(parents=True)
            (source / "assets").mkdir()
            (source / "scripts" / "install_for_codex.py").write_text("ok\n", encoding="utf-8")
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            _, _, phases = self.module.build_guided_phases(str(source), str(repo))

            with patch("subprocess.run", side_effect=mock_run):
                result = self.module.run_guided_setup(str(source), str(repo), phases)

        self.assertEqual(result, 1)
        # Only 2 phases ran (first succeeded, second failed)
        self.assertEqual(call_count[0], 2)

    def test_apply_returns_nonzero_on_command_not_found(self):
        """Verify that FileNotFoundError is caught and returns non-zero."""
        def mock_run(argv, **kwargs):
            raise FileNotFoundError("no such command")

        with tempfile.TemporaryDirectory() as tmp:
            source = pathlib.Path(tmp) / "source"
            (source / "scripts").mkdir(parents=True)
            (source / "assets").mkdir()
            (source / "scripts" / "install_for_codex.py").write_text("ok\n", encoding="utf-8")
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            _, _, phases = self.module.build_guided_phases(str(source), str(repo))

            with patch("subprocess.run", side_effect=mock_run):
                result = self.module.run_guided_setup(str(source), str(repo), phases)

        self.assertEqual(result, 1)

    def test_apply_returns_nonzero_on_os_error(self):
        """Verify that OSError is caught and returns non-zero."""
        def mock_run(argv, **kwargs):
            raise OSError("permission denied")

        with tempfile.TemporaryDirectory() as tmp:
            source = pathlib.Path(tmp) / "source"
            (source / "scripts").mkdir(parents=True)
            (source / "assets").mkdir()
            (source / "scripts" / "install_for_codex.py").write_text("ok\n", encoding="utf-8")
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            _, _, phases = self.module.build_guided_phases(str(source), str(repo))

            with patch("subprocess.run", side_effect=mock_run):
                result = self.module.run_guided_setup(str(source), str(repo), phases)

        self.assertEqual(result, 1)


class MainGuidedSetupTests(unittest.TestCase):
    """Test main() routing for guided setup flags."""

    def setUp(self):
        self.module = load_module()

    def test_main_setup_current_calls_preview(self):
        """--setup-current without --apply should call print_guided_preview."""
        with tempfile.TemporaryDirectory() as tmp:
            source = pathlib.Path(tmp) / "source"
            (source / "scripts").mkdir(parents=True)
            (source / "assets").mkdir()
            (source / "scripts" / "install_for_codex.py").write_text("ok\n", encoding="utf-8")
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = self.module.main(["--source", str(source), "--setup-repo", str(repo)])

            output = stdout.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("Guided setup preview", output)

    def test_main_setup_repo_with_apply_calls_run(self):
        """--setup-repo --apply should call run_guided_setup."""
        phases_called = []

        def mock_run_guided(source, repo, phases):
            phases_called.extend(phases)
            return 0

        with tempfile.TemporaryDirectory() as tmp:
            source = pathlib.Path(tmp) / "source"
            (source / "scripts").mkdir(parents=True)
            (source / "assets").mkdir()
            (source / "scripts" / "install_for_codex.py").write_text("ok\n", encoding="utf-8")
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()

            with patch.object(self.module, "run_guided_setup", side_effect=mock_run_guided):
                result = self.module.main(["--source", str(source), "--setup-repo", str(repo), "--apply"])

        self.assertEqual(result, 0)
        self.assertEqual(len(phases_called), 4)

    def test_main_setup_repo_with_apply_propagates_failure(self):
        """--setup-repo --apply should propagate non-zero exit from run_guided_setup."""
        with tempfile.TemporaryDirectory() as tmp:
            source = pathlib.Path(tmp) / "source"
            (source / "scripts").mkdir(parents=True)
            (source / "assets").mkdir()
            (source / "scripts" / "install_for_codex.py").write_text("ok\n", encoding="utf-8")
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()

            with patch.object(self.module, "run_guided_setup", return_value=1):
                result = self.module.main(["--source", str(source), "--setup-repo", str(repo), "--apply"])

        self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
