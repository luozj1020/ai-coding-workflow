import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "codegraph-worktree-guard.py"


class CodeGraphWorktreeGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        self.source = self.root / "source"
        self.worktree = self.root / "execution"
        self.bin = self.root / "bin"
        self.source.mkdir()
        self.worktree.mkdir()
        self.bin.mkdir()
        (self.source / ".codegraph").mkdir()
        for path in (self.source, self.worktree):
            subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
        fake = self.bin / "codegraph"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, pathlib, sys\n"
            "command, root = sys.argv[1], pathlib.Path(sys.argv[2]).resolve()\n"
            "marker = root / '.fake-codegraph-ready'\n"
            "pending_marker = root / '.fake-codegraph-pending'\n"
            "if command in {'index', 'sync'}:\n"
            "    marker.write_text(command, encoding='utf-8')\n"
            "    pending_marker.unlink(missing_ok=True)\n"
            "    raise SystemExit(0)\n"
            "if command != 'status': raise SystemExit(2)\n"
            "ready = marker.exists()\n"
            "project = str(root if ready else pathlib.Path(os.environ['FAKE_SOURCE']).resolve())\n"
            "pending = {'added': 0, 'modified': 1 if pending_marker.exists() else 0, 'removed': 0}\n"
            "print(json.dumps({'initialized': True, 'projectPath': project, "
            "'worktreeMismatch': None if ready else {'indexed': project, 'current': str(root)}, "
            "'pendingChanges': pending}))\n",
            encoding="utf-8",
        )
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.env = os.environ.copy()
        self.env["PATH"] = str(self.bin) + os.pathsep + self.env.get("PATH", "")
        self.env["FAKE_SOURCE"] = str(self.source)

    def tearDown(self):
        self.temporary.cleanup()

    def invoke(self, policy):
        receipt = self.root / f"{policy}.json"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--source", str(self.source),
             "--worktree", str(self.worktree), "--output", str(receipt),
             "--policy", policy, "--timeout", "10"],
            env=self.env, text=True, encoding="utf-8", errors="replace",
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(receipt.read_text(encoding="utf-8"))

    def test_mismatch_falls_back_without_accepting_stale_results(self):
        value = self.invoke("fallback")
        self.assertEqual(value["status"], "fallback-local")
        self.assertEqual(value["reason"], "different-worktree")
        self.assertFalse(value["safe_to_use"])
        self.assertFalse(value["stale_results_allowed"])
        self.assertEqual(value["observed_project_path"], str(self.source.resolve()))

    def test_explicit_repair_reindexes_execution_worktree(self):
        value = self.invoke("repair")
        self.assertEqual(value["status"], "ready")
        self.assertEqual(value["action"], "index-then-use")
        self.assertTrue(value["safe_to_use"])
        self.assertEqual(value["observed_project_path"], str(self.worktree.resolve()))

    def test_existing_execution_index_is_reused(self):
        (self.worktree / ".fake-codegraph-ready").write_text("existing", encoding="utf-8")
        value = self.invoke("fallback")
        self.assertEqual(value["status"], "ready")
        self.assertEqual(value["action"], "use-current-index")
        self.assertTrue(value["safe_to_use"])

    def test_explicit_repair_syncs_pending_execution_index(self):
        (self.worktree / ".fake-codegraph-ready").write_text("existing", encoding="utf-8")
        (self.worktree / ".fake-codegraph-pending").write_text("pending", encoding="utf-8")
        value = self.invoke("repair")
        self.assertEqual(value["status"], "ready")
        self.assertEqual(value["action"], "sync-then-use")
        self.assertTrue(value["safe_to_use"])


if __name__ == "__main__":
    unittest.main()
