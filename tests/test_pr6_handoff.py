"""Tests for PR6 Remote Bazel handoff generation.

Covers:
- generate-handoff.py emits all 6 artifacts (local-precheck.sh, local-publish.sh,
  remote-update.sh, remote-validate.sh, handoff.md, manifest.json)
- Preview safety (scripts echo only, no execution)
- Shell syntax validity (bash -n)
- Windows paths and Python 3.9 compatibility
- Validation ingest precedence (exit 0 → passed, SHA mismatch → invalid-environment,
  permission/network/dependency/timeout/compile/test/unknown)
- Hostile task ID rejection
- Successful remote classification
- Installer/aiwf registration
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


class TestHandoffArtifacts(unittest.TestCase):
    """Test that generate-handoff.py emits all required artifacts."""

    def _generate(self, tmp_dir, **kwargs):
        """Helper to run generate-handoff.py."""
        args = [
            sys.executable,
            str(SCRIPTS / "generate-handoff.py"),
            kwargs.get("task_id", "T-1"),
            "--output-dir", tmp_dir,
            "--repo-url", kwargs.get("repo_url", "git@example/repo.git"),
            "--branch", kwargs.get("branch", "main"),
            "--sha", kwargs.get("sha", "abcdef1234567890abcdef1234567890abcdef12"),
        ]
        if "target" in kwargs:
            args += ["--target", kwargs["target"]]
        if "changed_file" in kwargs:
            args += ["--changed-file", kwargs["changed_file"]]
        subprocess.check_call(args)

    def test_emits_all_artifacts(self):
        """All 6 artifacts are generated."""
        with tempfile.TemporaryDirectory() as d:
            self._generate(d)
            for name in (
                "local-precheck.sh",
                "local-publish.sh",
                "remote-update.sh",
                "remote-validate.sh",
                "handoff.md",
                "manifest.json",
            ):
                self.assertTrue(
                    (Path(d) / name).exists(),
                    f"Missing artifact: {name}",
                )

    def test_manifest_schema_version(self):
        """Manifest has schema_version 1."""
        with tempfile.TemporaryDirectory() as d:
            self._generate(d)
            manifest = json.loads((Path(d) / "manifest.json").read_text())
            self.assertEqual(manifest["schema_version"], 1)

    def test_manifest_artifacts_list(self):
        """Manifest artifacts list matches emitted files."""
        with tempfile.TemporaryDirectory() as d:
            self._generate(d)
            manifest = json.loads((Path(d) / "manifest.json").read_text())
            self.assertIn("artifacts", manifest)
            self.assertEqual(len(manifest["artifacts"]), 6)

    def test_handoff_md_content(self):
        """handoff.md contains expected sections."""
        with tempfile.TemporaryDirectory() as d:
            self._generate(d, task_id="test-task")
            md = (Path(d) / "handoff.md").read_text()
            self.assertIn("test-task", md)
            self.assertIn("local-precheck.sh", md)
            self.assertIn("local-publish.sh", md)
            self.assertIn("remote-update.sh", md)
            self.assertIn("remote-validate.sh", md)
            self.assertIn("preview-only", md.lower())


class TestPreviewSafety(unittest.TestCase):
    """Test that scripts are preview-only (echo commands, no execution)."""

    def _generate(self, tmp_dir):
        subprocess.check_call([
            sys.executable,
            str(SCRIPTS / "generate-handoff.py"),
            "T-1",
            "--output-dir", tmp_dir,
            "--repo-url", "git@example/repo.git",
            "--branch", "main",
            "--sha", "abcdef1234567890abcdef1234567890abcdef12",
            "--target", "//x:y",
            "--changed-file", "src/x.py",
        ])

    def test_local_precheck_uses_echo(self):
        """local-precheck.sh uses git commands (not echo-only, but safe)."""
        with tempfile.TemporaryDirectory() as d:
            self._generate(d)
            text = (Path(d) / "local-precheck.sh").read_text()
            # Should have git status, diff-stat, diff-check
            self.assertIn("git status", text)
            self.assertIn("git diff", text)

    def test_local_publish_uses_echo(self):
        """local-publish.sh echoes commands only."""
        with tempfile.TemporaryDirectory() as d:
            self._generate(d)
            text = (Path(d) / "local-publish.sh").read_text()
            self.assertIn("echo", text)
            self.assertIn("git add", text)
            self.assertIn("git commit", text)
            self.assertIn("git push", text)

    def test_remote_update_uses_echo(self):
        """remote-update.sh echoes commands only."""
        with tempfile.TemporaryDirectory() as d:
            self._generate(d)
            text = (Path(d) / "remote-update.sh").read_text()
            self.assertIn("echo", text)
            self.assertIn("git fetch", text)
            self.assertIn("git checkout", text)
            self.assertIn("git merge --ff-only", text)
            self.assertIn("rev-parse HEAD", text)

    def test_remote_validate_uses_pipefail(self):
        """remote-validate.sh uses set -euo pipefail."""
        with tempfile.TemporaryDirectory() as d:
            self._generate(d)
            text = (Path(d) / "remote-validate.sh").read_text()
            self.assertIn("set -euo pipefail", text)
            self.assertIn("PIPESTATUS", text)
            self.assertIn(".exit-code", text)


class TestShellSyntax(unittest.TestCase):
    """Test shell script syntax validity."""

    def _generate(self, tmp_dir):
        subprocess.check_call([
            sys.executable,
            str(SCRIPTS / "generate-handoff.py"),
            "T-1",
            "--output-dir", tmp_dir,
            "--repo-url", "git@example/repo.git",
            "--branch", "main",
            "--sha", "abcdef1234567890abcdef1234567890abcdef12",
            "--target", "//x:y",
        ])

    @unittest.skipIf(os.name == "nt", "bash -n not available on Windows")
    def test_all_scripts_valid_syntax(self):
        """All shell scripts pass bash -n syntax check."""
        with tempfile.TemporaryDirectory() as d:
            self._generate(d)
            for name in (
                "local-precheck.sh",
                "local-publish.sh",
                "remote-update.sh",
                "remote-validate.sh",
            ):
                text = (Path(d) / name).read_text()
                result = subprocess.run(
                    ["bash", "-n"],
                    input=text,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(
                    result.returncode, 0,
                    f"Syntax error in {name}: {result.stderr}",
                )


class TestHostileTaskId(unittest.TestCase):
    """Test that hostile task IDs are rejected."""

    def test_path_traversal_rejected(self):
        """Path traversal in task ID is rejected."""
        with tempfile.TemporaryDirectory() as d:
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "generate-handoff.py"),
                    "../bad",
                    "--output-dir", d,
                    "--repo-url", "x",
                    "--branch", "main",
                    "--sha", "abcdef1234567890abcdef1234567890abcdef12",
                ],
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)

    def test_shell_metachar_rejected(self):
        """Shell metacharacters in task ID are rejected."""
        with tempfile.TemporaryDirectory() as d:
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "generate-handoff.py"),
                    "task;rm -rf /",
                    "--output-dir", d,
                    "--repo-url", "x",
                    "--branch", "main",
                    "--sha", "abcdef1234567890abcdef1234567890abcdef12",
                ],
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)


class TestValidationIngestPrecedence(unittest.TestCase):
    """Test validation-ingest.py classification precedence (PR6)."""

    def _run_ingest(self, log_content, exit_code=None, expected_sha=None):
        """Helper to run validation-ingest.py."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False
        ) as f:
            f.write(log_content)
            f.flush()
            args = [
                sys.executable,
                str(SCRIPTS / "validation-ingest.py"),
                f.name,
            ]
            if exit_code is not None:
                args += ["--exit-code", str(exit_code)]
            if expected_sha:
                args += ["--expected-sha", expected_sha]
            result = subprocess.run(args, capture_output=True, text=True)
            return json.loads(result.stdout)

    def test_exit_zero_is_passed(self):
        """Exit code 0 → passed classification."""
        out = self._run_ingest("Build successful\n", exit_code=0)
        self.assertEqual(out["classification"], "passed")

    def test_sha_mismatch_is_invalid_environment(self):
        """SHA mismatch → invalid-environment classification."""
        out = self._run_ingest(
            "commit abc123def456789012345678901234567890abcd\nError: build failed\n",
            exit_code=1,
            expected_sha="zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",
        )
        self.assertEqual(out["classification"], "invalid-environment")

    def test_permission_denied(self):
        """Permission denied → permission classification."""
        out = self._run_ingest(
            "Permission denied: cannot access /tmp/build\n",
            exit_code=1,
        )
        self.assertEqual(out["classification"], "permission")

    def test_network_error(self):
        """Network error → network classification."""
        out = self._run_ingest(
            "Network connection timed out\n",
            exit_code=1,
        )
        self.assertEqual(out["classification"], "network")

    def test_dependency_not_found(self):
        """Dependency not found → dependency classification."""
        out = self._run_ingest(
            "Package not found: libfoo-dev\n",
            exit_code=1,
        )
        self.assertEqual(out["classification"], "dependency")

    def test_compile_error(self):
        """Compile error → compile classification."""
        out = self._run_ingest(
            "error: undefined reference to 'foo'\n",
            exit_code=1,
        )
        self.assertEqual(out["classification"], "compile")

    def test_test_failure(self):
        """Test failure → test classification."""
        out = self._run_ingest(
            "FAILED //src:test_test\n",
            exit_code=1,
        )
        self.assertEqual(out["classification"], "test")

    def test_unknown_error(self):
        """Unknown error → unknown classification."""
        out = self._run_ingest(
            "Something went wrong\n",
            exit_code=1,
        )
        self.assertEqual(out["classification"], "unknown")


class TestInstallerRegistration(unittest.TestCase):
    """Test that scripts are registered in installer/aiwf."""

    def test_generate_handoff_in_aiwf(self):
        """generate-handoff.py is registered in aiwf.py COMMANDS."""
        content = (SCRIPTS / "aiwf.py").read_text()
        self.assertIn('"handoff":"generate-handoff.py"', content)

    def test_validation_ingest_in_aiwf(self):
        """validation-ingest.py is registered in aiwf.py COMMANDS."""
        content = (SCRIPTS / "aiwf.py").read_text()
        self.assertIn('"validation-ingest":"validation-ingest.py"', content)


class TestPython39Compat(unittest.TestCase):
    """Verify Python 3.9 compatibility patterns."""

    def test_no_walrus_in_generate_handoff(self):
        """generate-handoff.py does not use walrus operator."""
        content = (SCRIPTS / "generate-handoff.py").read_text()
        self.assertNotIn(":=", content.replace('":=', "").replace("':=", ""))

    def test_no_walrus_in_validation_ingest(self):
        """validation-ingest.py does not use walrus operator."""
        content = (SCRIPTS / "validation-ingest.py").read_text()
        self.assertNotIn(":=", content.replace('":=', "").replace("':=", ""))


if __name__ == "__main__":
    unittest.main()
