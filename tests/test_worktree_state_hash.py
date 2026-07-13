"""Tests for worktree_state_hash.py.

Covers: canonical hash changes for unstaged content, staged content,
untracked bytes and binary changes, but ignores known control artifacts;
deterministic ordering.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

import importlib.util
import sys

spec = importlib.util.spec_from_file_location("worktree_state_hash", SCRIPTS / "worktree_state_hash.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["worktree_state_hash"] = mod
spec.loader.exec_module(mod)


def _git(cmd, cwd):
    """Run a git command."""
    return subprocess.run(
        ["git"] + cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
    )


def _init_repo(path):
    """Initialize a git repo with an initial commit."""
    _git(["init"], path)
    _git(["config", "user.email", "test@test"], path)
    _git(["config", "user.name", "Test"], path)
    (path / "README.md").write_text("# test\n")
    _git(["add", "README.md"], path)
    _git(["commit", "-m", "init"], path)


class TestCanonicalHashDeterministic(unittest.TestCase):
    """Hash is deterministic for the same state."""

    def test_same_state_same_hash(self):
        with tempfile.TemporaryDirectory(prefix="hash_test_") as td:
            wt = Path(td) / "repo"
            wt.mkdir()
            _init_repo(wt)
            h1 = mod.compute_worktree_state_hash(wt)
            h2 = mod.compute_worktree_state_hash(wt)
            self.assertEqual(h1, h2)
            self.assertEqual(len(h1), 64)  # SHA-256 hex

    def test_empty_worktree_hash_stable(self):
        with tempfile.TemporaryDirectory(prefix="hash_test_") as td:
            wt = Path(td) / "repo"
            wt.mkdir()
            _init_repo(wt)
            h = mod.compute_worktree_state_hash(wt)
            # Should be a non-empty hex string
            self.assertTrue(h)
            self.assertEqual(len(h), 64)


class TestUnstagedChangeDetected(unittest.TestCase):
    """Unstaged tracked file changes affect the hash."""

    def test_unstaged_change_changes_hash(self):
        with tempfile.TemporaryDirectory(prefix="hash_test_") as td:
            wt = Path(td) / "repo"
            wt.mkdir()
            _init_repo(wt)
            h_before = mod.compute_worktree_state_hash(wt)
            (wt / "README.md").write_text("# modified\n")
            h_after = mod.compute_worktree_state_hash(wt)
            self.assertNotEqual(h_before, h_after)

    def test_different_unstaged_content_different_hash(self):
        with tempfile.TemporaryDirectory(prefix="hash_test_") as td:
            wt = Path(td) / "repo"
            wt.mkdir()
            _init_repo(wt)
            (wt / "README.md").write_text("# version A\n")
            h_a = mod.compute_worktree_state_hash(wt)
            (wt / "README.md").write_text("# version B\n")
            h_b = mod.compute_worktree_state_hash(wt)
            self.assertNotEqual(h_a, h_b)


class TestStagedChangeDetected(unittest.TestCase):
    """Staged changes affect the hash."""

    def test_staged_change_changes_hash(self):
        with tempfile.TemporaryDirectory(prefix="hash_test_") as td:
            wt = Path(td) / "repo"
            wt.mkdir()
            _init_repo(wt)
            h_before = mod.compute_worktree_state_hash(wt)
            (wt / "new_file.py").write_text("x = 1\n")
            _git(["add", "new_file.py"], wt)
            h_after = mod.compute_worktree_state_hash(wt)
            self.assertNotEqual(h_before, h_after)


class TestUntrackedFileDetected(unittest.TestCase):
    """Untracked files (path + bytes) affect the hash."""

    def test_untracked_file_changes_hash(self):
        with tempfile.TemporaryDirectory(prefix="hash_test_") as td:
            wt = Path(td) / "repo"
            wt.mkdir()
            _init_repo(wt)
            h_before = mod.compute_worktree_state_hash(wt)
            (wt / "untracked.txt").write_text("some content")
            h_after = mod.compute_worktree_state_hash(wt)
            self.assertNotEqual(h_before, h_after)

    def test_different_untracked_content_different_hash(self):
        with tempfile.TemporaryDirectory(prefix="hash_test_") as td:
            wt = Path(td) / "repo"
            wt.mkdir()
            _init_repo(wt)
            (wt / "data.bin").write_bytes(b"\x00\x01\x02")
            h_a = mod.compute_worktree_state_hash(wt)
            (wt / "data.bin").write_bytes(b"\x03\x04\x05")
            h_b = mod.compute_worktree_state_hash(wt)
            self.assertNotEqual(h_a, h_b)


class TestBinaryChangeDetected(unittest.TestCase):
    """Binary file changes affect the hash without lossy text decoding."""

    def test_binary_change_detected(self):
        with tempfile.TemporaryDirectory(prefix="hash_test_") as td:
            wt = Path(td) / "repo"
            wt.mkdir()
            _init_repo(wt)
            (wt / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00")
            _git(["add", "image.png"], wt)
            _git(["commit", "-m", "add binary"], wt)
            h_before = mod.compute_worktree_state_hash(wt)
            # Modify binary content
            (wt / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe\xfd")
            h_after = mod.compute_worktree_state_hash(wt)
            self.assertNotEqual(h_before, h_after)


class TestControlArtifactIgnored(unittest.TestCase):
    """Known workflow control artifacts are excluded from the hash."""

    def test_claude_progress_md_ignored(self):
        with tempfile.TemporaryDirectory(prefix="hash_test_") as td:
            wt = Path(td) / "repo"
            wt.mkdir()
            _init_repo(wt)
            h_before = mod.compute_worktree_state_hash(wt)
            (wt / "CLAUDE_PROGRESS.md").write_text("# Progress\nSome content")
            h_after = mod.compute_worktree_state_hash(wt)
            self.assertEqual(h_before, h_after)

    def test_claude_report_md_ignored(self):
        with tempfile.TemporaryDirectory(prefix="hash_test_") as td:
            wt = Path(td) / "repo"
            wt.mkdir()
            _init_repo(wt)
            h_before = mod.compute_worktree_state_hash(wt)
            (wt / "CLAUDE_REPORT.md").write_text("# Report\nContent")
            h_after = mod.compute_worktree_state_hash(wt)
            self.assertEqual(h_before, h_after)

    def test_advisor_packet_ignored(self):
        with tempfile.TemporaryDirectory(prefix="hash_test_") as td:
            wt = Path(td) / "repo"
            wt.mkdir()
            _init_repo(wt)
            h_before = mod.compute_worktree_state_hash(wt)
            (wt / "advisor-packet.json").write_text('{"task_id": "test"}')
            (wt / "advisor-prompt.md").write_text("# Prompt\n")
            (wt / "advisor-call-result.json").write_text('{"ok": true}')
            h_after = mod.compute_worktree_state_hash(wt)
            self.assertEqual(h_before, h_after)

    def test_advisor_request_json_ignored(self):
        with tempfile.TemporaryDirectory(prefix="hash_test_") as td:
            wt = Path(td) / "repo"
            wt.mkdir()
            _init_repo(wt)
            h_before = mod.compute_worktree_state_hash(wt)
            (wt / "ADVISOR_REQUEST.json").write_text('{"schema_version": 1}')
            h_after = mod.compute_worktree_state_hash(wt)
            self.assertEqual(h_before, h_after)


class TestOrderingDeterministic(unittest.TestCase):
    """Hash is stable regardless of filesystem ordering."""

    def test_multiple_untracked_deterministic(self):
        with tempfile.TemporaryDirectory(prefix="hash_test_") as td:
            wt = Path(td) / "repo"
            wt.mkdir()
            _init_repo(wt)
            # Create multiple untracked files
            for name in ["z_file.txt", "a_file.txt", "m_file.txt"]:
                (wt / name).write_text(f"content of {name}")
            hashes = [mod.compute_worktree_state_hash(wt) for _ in range(5)]
            # All should be identical
            self.assertEqual(len(set(hashes)), 1)


class TestCLI(unittest.TestCase):
    """CLI interface works."""

    def test_cli_output(self):
        with tempfile.TemporaryDirectory(prefix="hash_test_") as td:
            wt = Path(td) / "repo"
            wt.mkdir()
            _init_repo(wt)
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "worktree_state_hash.py"),
                 "--worktree", str(wt)],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0)
            h = result.stdout.strip()
            self.assertEqual(len(h), 64)


if __name__ == "__main__":
    unittest.main()
