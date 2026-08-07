import json
import pathlib
import subprocess
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "direct-change.py"


class DirectChangeTests(unittest.TestCase):
    def run_direct(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )

    def test_workflow_maintenance_is_a_no_delegation_record(self):
        result = self.run_direct(
            "--kind", "workflow-maintenance",
            "--reason", "narrow the Skill entry gate",
            "--path", "SKILL.md",
            "--check", "git diff --check",
            "--json",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["workflow_bypassed"], "workflow-maintenance")
        self.assertEqual(value["write_paths"], ["SKILL.md"])
        self.assertFalse(value["task_card_required"])
        self.assertFalse(value["spark_required"])
        self.assertFalse(value["claude_required"])
        self.assertFalse(value["merge_authorized"])

    def test_rejects_unsafe_or_duplicate_paths(self):
        unsafe = self.run_direct("--reason", "x", "--path", "../outside")
        broad = self.run_direct("--reason", "x", "--path", ".")
        duplicate = self.run_direct(
            "--reason", "x", "--path", "README.md", "--path", "README.md"
        )

        self.assertNotEqual(unsafe.returncode, 0)
        self.assertIn("safe repository-relative", unsafe.stderr)
        self.assertNotEqual(broad.returncode, 0)
        self.assertIn("safe repository-relative", broad.stderr)
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertIn("must be unique", duplicate.stderr)
