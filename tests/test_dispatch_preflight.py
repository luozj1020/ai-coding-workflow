import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "dispatch-preflight.py"
SPEC = importlib.util.spec_from_file_location("dispatch_preflight", SCRIPT)
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MOD)


class DispatchPreflightTests(unittest.TestCase):
    def test_untracked_task_directory_missing_from_fresh_worktree_blocks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root / "source", root / "target"
            (source / "src/headless").mkdir(parents=True)
            target.mkdir()
            (source / "src/headless/main.ts").write_text("new module", encoding="utf-8")
            card = root / "task.md"
            card.write_text("Implement `src/headless/`.", encoding="utf-8")
            result = MOD.assess(source, target, card, ["src/headless/main.ts"])
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["blocked_paths"], ["src/headless/main.ts"])

    def test_unrelated_dirty_file_does_not_block_isolated_task(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root / "source", root / "target"
            source.mkdir()
            target.mkdir()
            card = root / "task.md"
            card.write_text("Edit `src/server.py`.", encoding="utf-8")
            result = MOD.assess(source, target, card, ["notes/local.txt"])
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["task_relevant_dirty_paths"], [])

    def test_cli_writes_hash_bound_mismatch_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root / "source", root / "target"
            (source / "src").mkdir(parents=True)
            (target / "src").mkdir(parents=True)
            (source / "src/app.py").write_text("dirty", encoding="utf-8")
            (target / "src/app.py").write_text("head", encoding="utf-8")
            card, dirty, output = root / "task.md", root / "dirty.txt", root / "preflight.json"
            card.write_text("Target: src/app.py", encoding="utf-8")
            dirty.write_text("src/app.py\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--source", str(source), "--worktree", str(target),
                 "--task-card", str(card), "--dirty-paths", str(dirty), "--output", str(output)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 2)
            evidence = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(evidence["path_evidence"][0]["state"], "stale-in-execution-worktree")
            self.assertTrue(evidence["path_evidence"][0]["source_object"].startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()
