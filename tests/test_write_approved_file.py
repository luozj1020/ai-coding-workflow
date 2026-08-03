import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PREPARE = ROOT / "scripts" / "prepare-write-sandbox.py"
WRITER = ROOT / "scripts" / "write-approved-file.py"


class ApprovedFileWriterTests(unittest.TestCase):
    def _receipt(self, root: pathlib.Path) -> pathlib.Path:
        worktree = root / "worktree"
        staging = root / "staging"
        worktree.mkdir()
        (worktree / "src").mkdir()
        (worktree / "src" / "allowed.py").write_text("old\n", encoding="utf-8")
        card = root / "card.md"
        card.write_text("## Scope\n\n- Write paths: src/allowed.py\n", encoding="utf-8")
        receipt = root / "receipt.json"
        subprocess.run(
            [sys.executable, str(PREPARE), "--task-card", str(card),
             "--worktree", str(worktree), "--output", str(receipt),
             "--staging-root", str(staging)],
            check=True, capture_output=True, text=True,
        )
        return receipt

    def test_writes_only_staged_approved_file(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            receipt = self._receipt(root)
            replacement = root / "replacement"
            replacement.write_text("new\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(WRITER), "--receipt", str(receipt),
                 "--path", "src/allowed.py", "--source", str(replacement)],
                check=True, capture_output=True, text=True,
            )
            value = json.loads(result.stdout)
            binding = next(
                item for item in json.loads(receipt.read_text(encoding="utf-8"))["bindings"]
                if item["relative_path"] == "src/allowed.py"
            )
            self.assertEqual(pathlib.Path(binding["source"]).read_text(), "new\n")
            self.assertEqual((root / "worktree" / "src" / "allowed.py").read_text(), "old\n")
            self.assertEqual(value["relative_path"], "src/allowed.py")
            self.assertEqual(value["operation"], "complete-file")

    def test_replaces_one_unique_fragment(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            receipt = self._receipt(root)
            old = root / "old"
            new = root / "new"
            old.write_text("old", encoding="utf-8")
            new.write_text("updated", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(WRITER), "--receipt", str(receipt),
                 "--path", "src/allowed.py", "--replace-old-source", str(old),
                 "--replace-new-source", str(new)],
                check=True, capture_output=True, text=True,
            )
            value = json.loads(result.stdout)
            binding = next(
                item for item in json.loads(receipt.read_text(encoding="utf-8"))["bindings"]
                if item["relative_path"] == "src/allowed.py"
            )
            self.assertEqual(pathlib.Path(binding["source"]).read_text(), "updated\n")
            self.assertEqual(value["operation"], "unique-fragment-replacement")
            self.assertEqual(value["matches"], 1)

    def test_unique_replacement_rejects_zero_and_multiple_matches_without_writing(self):
        for initial, old_text, expected_matches in (
            ("alpha\n", "missing", 0),
            ("old old\n", "old", 2),
        ):
            with self.subTest(matches=expected_matches), tempfile.TemporaryDirectory() as raw:
                root = pathlib.Path(raw)
                receipt = self._receipt(root)
                value = json.loads(receipt.read_text(encoding="utf-8"))
                binding = next(
                    item for item in value["bindings"]
                    if item["relative_path"] == "src/allowed.py"
                )
                staged = pathlib.Path(binding["source"])
                staged.write_text(initial, encoding="utf-8")
                old = root / "old"
                new = root / "new"
                old.write_text(old_text, encoding="utf-8")
                new.write_text("replacement", encoding="utf-8")
                result = subprocess.run(
                    [sys.executable, str(WRITER), "--receipt", str(receipt),
                     "--path", "src/allowed.py", "--replace-old-source", str(old),
                     "--replace-new-source", str(new)],
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn(f"observed {expected_matches} matches", result.stderr)
                self.assertEqual(staged.read_text(encoding="utf-8"), initial)

    def test_unique_replacement_rejects_undeclared_path(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            receipt = self._receipt(root)
            old = root / "old"
            new = root / "new"
            old.write_text("old", encoding="utf-8")
            new.write_text("new", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(WRITER), "--receipt", str(receipt),
                 "--path", "src/other.py", "--replace-old-source", str(old),
                 "--replace-new-source", str(new)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("not one exact approved file", result.stderr)

    def test_rejects_undeclared_path(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            receipt = self._receipt(root)
            result = subprocess.run(
                [sys.executable, str(WRITER), "--receipt", str(receipt),
                 "--path", "src/other.py", "--stdin"], input="bad\n",
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("not one exact approved file", result.stderr)


if __name__ == "__main__":
    unittest.main()
