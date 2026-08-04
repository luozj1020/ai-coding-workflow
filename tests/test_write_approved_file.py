import base64
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PREPARE = ROOT / "scripts" / "prepare-write-sandbox.py"
WRITER = ROOT / "scripts" / "write-approved-file.py"


class ApprovedFileWriterTests(unittest.TestCase):
    def _receipt(self, root: pathlib.Path, *, allow_full: bool = True) -> pathlib.Path:
        worktree = root / "worktree"
        staging = root / "staging"
        worktree.mkdir()
        (worktree / "src").mkdir()
        (worktree / "src" / "allowed.py").write_text("old\n", encoding="utf-8")
        card = root / "card.md"
        card.write_text(
            "## Scope\n\n- Write paths: src/allowed.py\n"
            + ("- Full file replacement paths: src/allowed.py\n" if allow_full else ""),
            encoding="utf-8",
        )
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

    def test_complete_write_preserves_source_bytes(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            receipt = self._receipt(root)
            replacement = root / "replacement"
            expected = b"first\r\nsecond\n"
            replacement.write_bytes(expected)
            subprocess.run(
                [sys.executable, str(WRITER), "--receipt", str(receipt),
                 "--path", "src/allowed.py", "--source", str(replacement)],
                check=True, capture_output=True, text=True,
            )
            binding = next(
                item for item in json.loads(receipt.read_text(encoding="utf-8"))["bindings"]
                if item["relative_path"] == "src/allowed.py"
            )
            self.assertEqual(pathlib.Path(binding["source"]).read_bytes(), expected)

    def test_environment_receipt_and_base64_content_need_no_shell_expansion(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            receipt = self._receipt(root)
            expected = b"new without temp file\n"
            env = dict(os.environ, AI_WORKFLOW_WRITE_SCOPE_RECEIPT=str(receipt))
            result = subprocess.run(
                [sys.executable, str(WRITER), "--path", "src/allowed.py",
                 "--content-base64", base64.b64encode(expected).decode("ascii")],
                env=env, check=True, capture_output=True, text=True,
            )
            value = json.loads(receipt.read_text(encoding="utf-8"))
            binding = next(
                item for item in value["bindings"]
                if item["relative_path"] == "src/allowed.py"
            )
            self.assertEqual(pathlib.Path(binding["source"]).read_bytes(), expected)
            self.assertEqual(json.loads(result.stdout)["operation"], "complete-file")

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

    def test_base64_unique_replacement_needs_no_temp_files(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            receipt = self._receipt(root, allow_full=False)
            env = dict(os.environ, AI_WORKFLOW_WRITE_SCOPE_RECEIPT=str(receipt))
            result = subprocess.run(
                [
                    sys.executable, str(WRITER), "--path", "src/allowed.py",
                    "--replace-old-base64", base64.b64encode(b"old").decode("ascii"),
                    "--replace-new-base64", base64.b64encode(b"updated").decode("ascii"),
                ],
                env=env, check=True, capture_output=True, text=True,
            )
            value = json.loads(receipt.read_text(encoding="utf-8"))
            binding = next(
                item for item in value["bindings"]
                if item["relative_path"] == "src/allowed.py"
            )
            self.assertEqual(pathlib.Path(binding["source"]).read_text(), "updated\n")
            self.assertEqual(json.loads(result.stdout)["operation"], "unique-fragment-replacement")

    def test_probe_preserves_approved_file_bytes(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            receipt = self._receipt(root, allow_full=False)
            env = dict(os.environ, AI_WORKFLOW_WRITE_SCOPE_RECEIPT=str(receipt))
            value = json.loads(receipt.read_text(encoding="utf-8"))
            binding = next(
                item for item in value["bindings"]
                if item["relative_path"] == "src/allowed.py"
            )
            staged = pathlib.Path(binding["source"])
            before = staged.read_bytes()
            result = subprocess.run(
                [sys.executable, str(WRITER), "--path", "src/allowed.py", "--probe"],
                env=env, check=True, capture_output=True, text=True,
            )
            self.assertEqual(staged.read_bytes(), before)
            self.assertEqual(json.loads(result.stdout)["operation"], "write-probe")

    def test_noncanonical_base64_is_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            receipt = self._receipt(root)
            env = dict(os.environ, AI_WORKFLOW_WRITE_SCOPE_RECEIPT=str(receipt))
            value = json.loads(receipt.read_text(encoding="utf-8"))
            binding = next(
                item for item in value["bindings"]
                if item["relative_path"] == "src/allowed.py"
            )
            staged = pathlib.Path(binding["source"])
            before = staged.read_bytes()
            result = subprocess.run(
                [sys.executable, str(WRITER), "--path", "src/allowed.py",
                 "--content-base64", "Zh=="],
                env=env, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("canonical base64", result.stderr)
            self.assertEqual(staged.read_bytes(), before)

    def test_existing_file_complete_write_requires_explicit_declaration(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            receipt = self._receipt(root, allow_full=False)
            replacement = root / "replacement"
            replacement.write_text("unrelated rewrite\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(WRITER), "--receipt", str(receipt),
                 "--path", "src/allowed.py", "--source", str(replacement)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("unique-fragment-replacement", result.stderr)

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
