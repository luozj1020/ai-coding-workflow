import base64
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
PREPARE = ROOT / "scripts" / "prepare-write-sandbox.py"
WRITER = ROOT / "scripts" / "write-approved-file.py"
SPEC = importlib.util.spec_from_file_location("write_approved_file", WRITER)
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MOD)


class ApprovedFileWriterTests(unittest.TestCase):
    def test_reports_exact_write_runtime_protocol(self):
        result = subprocess.run(
            [sys.executable, str(WRITER), "--runtime-protocol"],
            check=True, capture_output=True, text=True,
        )
        self.assertEqual(result.stdout.strip(), "aiwf-exact-write-v3")

    def test_io_failure_restores_previous_checkpoint(self):
        with tempfile.TemporaryDirectory() as raw:
            target = pathlib.Path(raw) / "staged.py"
            previous = b"value = 1\n"
            target.write_bytes(previous)
            descriptor = MOD._open_private_file(target)
            original_replace = MOD._replace_descriptor_content
            calls = 0

            def fail_after_partial_write(fd, content):
                nonlocal calls
                calls += 1
                if calls == 1:
                    os.lseek(fd, 0, os.SEEK_SET)
                    os.ftruncate(fd, 0)
                    os.write(fd, b"partial")
                    raise OSError("injected write failure")
                return original_replace(fd, content)

            try:
                with mock.patch.object(
                    MOD, "_replace_descriptor_content", side_effect=fail_after_partial_write
                ):
                    with self.assertRaisesRegex(
                        MOD.ApprovedWriteError, "previous checkpoint was restored"
                    ):
                        MOD._transactional_descriptor_write(
                            descriptor, previous, b"value = 2\n"
                        )
            finally:
                os.close(descriptor)
            self.assertEqual(target.read_bytes(), previous)

    def _receipt(
        self, root: pathlib.Path, *, allow_full: bool = True,
        relative_path: str = "src/allowed.py", initial: bytes = b"old\n",
    ) -> pathlib.Path:
        worktree = root / "worktree"
        staging = root / "staging"
        worktree.mkdir()
        target = worktree / relative_path
        target.parent.mkdir(parents=True)
        target.write_bytes(initial)
        card = root / "card.md"
        card.write_text(
            f"## Scope\n\n- Write paths: {relative_path}\n"
            + (f"- Full file replacement paths: {relative_path}\n" if allow_full else ""),
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
            expected = b"value = 'new without temp file'\n"
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
            self.assertEqual(value["candidate_validation"]["status"], "passed")
            self.assertIn("python-ast", value["candidate_validation"]["checks"])

    def test_python_syntax_failure_rejects_candidate_without_writing(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            receipt = self._receipt(root, allow_full=False)
            value = json.loads(receipt.read_text(encoding="utf-8"))
            staged = pathlib.Path(next(
                item for item in value["bindings"]
                if item["relative_path"] == "src/allowed.py"
            )["source"])
            before = staged.read_bytes()
            result = subprocess.run(
                [sys.executable, str(WRITER), "--receipt", str(receipt),
                 "--path", "src/allowed.py",
                 "--replace-old-base64", base64.b64encode(b"old").decode("ascii"),
                 "--replace-new-base64", base64.b64encode(b"def broken(:").decode("ascii")],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("Python syntax error", result.stderr)
            self.assertEqual(staged.read_bytes(), before)

    def test_invalid_json_candidate_is_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            receipt = self._receipt(
                root, relative_path="config/data.json", initial=b'{"ok": true}\n'
            )
            value = json.loads(receipt.read_text(encoding="utf-8"))
            binding = next(
                item for item in value["bindings"]
                if item["relative_path"] == "config/data.json"
            )
            staged = pathlib.Path(binding["source"])
            before = staged.read_bytes()
            replacement = root / "invalid.json"
            replacement.write_bytes(b'{"broken": }\n')
            result = subprocess.run(
                [sys.executable, str(WRITER), "--receipt", str(receipt),
                 "--path", "config/data.json", "--source", str(replacement)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("invalid JSON", result.stderr)
            self.assertEqual(staged.read_bytes(), before)

    def test_new_duplicate_definition_is_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            receipt = self._receipt(root, allow_full=False)
            value = json.loads(receipt.read_text(encoding="utf-8"))
            staged = pathlib.Path(next(
                item for item in value["bindings"]
                if item["relative_path"] == "src/allowed.py"
            )["source"])
            before = b"def keep():\n    return 1\n"
            staged.write_bytes(before)
            candidate = before + b"\ndef keep():\n    return 2\n"
            result = subprocess.run(
                [sys.executable, str(WRITER), "--receipt", str(receipt),
                 "--path", "src/allowed.py",
                 "--replace-old-base64", base64.b64encode(before).decode("ascii"),
                 "--replace-new-base64", base64.b64encode(candidate).decode("ascii")],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("newly duplicated top-level definitions", result.stderr)
            self.assertEqual(staged.read_bytes(), before)

    def test_new_duplicate_import_is_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            receipt = self._receipt(root, allow_full=False)
            value = json.loads(receipt.read_text(encoding="utf-8"))
            staged = pathlib.Path(next(
                item for item in value["bindings"]
                if item["relative_path"] == "src/allowed.py"
            )["source"])
            before = b"import json\n"
            staged.write_bytes(before)
            candidate = b"import json\nimport json\n"
            result = subprocess.run(
                [sys.executable, str(WRITER), "--receipt", str(receipt),
                 "--path", "src/allowed.py",
                 "--replace-old-base64", base64.b64encode(before).decode("ascii"),
                 "--replace-new-base64", base64.b64encode(candidate).decode("ascii")],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("newly duplicated top-level imports", result.stderr)
            self.assertEqual(staged.read_bytes(), before)

    def test_concatenated_python_module_boundary_is_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            receipt = self._receipt(root)
            value = json.loads(receipt.read_text(encoding="utf-8"))
            binding = next(
                item for item in value["bindings"]
                if item["relative_path"] == "src/allowed.py"
            )
            staged = pathlib.Path(binding["source"])
            before = b'"""First module."""\nvalue = 1\n'
            staged.write_bytes(before)
            candidate = before + b'\n"""Second module."""\nother = 2\n'
            result = subprocess.run(
                [sys.executable, str(WRITER), "--receipt", str(receipt),
                 "--path", "src/allowed.py",
                 "--replace-old-base64", base64.b64encode(before).decode("ascii"),
                 "--replace-new-base64", base64.b64encode(candidate).decode("ascii")],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("concatenated file boundary", result.stderr)
            self.assertEqual(staged.read_bytes(), before)

    def test_duplicate_python_module_entry_point_is_rejected(self):
        previous = b'if __name__ == "__main__":\n    print("one")\n'
        candidate = previous + b'\nif __name__ == "__main__":\n    print("two")\n'
        with self.assertRaisesRegex(
            MOD.ApprovedWriteError, "duplicated module entry point"
        ):
            MOD._validate_candidate("tool.py", previous, candidate)

    def test_abnormal_python_line_growth_is_rejected(self):
        previous = "".join(f"value_{index} = {index}\n" for index in range(20)).encode()
        candidate = "".join(f"value_{index} = {index}\n" for index in range(421)).encode()
        with self.assertRaisesRegex(MOD.ApprovedWriteError, "abnormal line-count growth"):
            MOD._validate_candidate("tool.py", previous, candidate)

    def test_removed_still_used_import_is_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            receipt = self._receipt(root, allow_full=False)
            value = json.loads(receipt.read_text(encoding="utf-8"))
            staged = pathlib.Path(next(
                item for item in value["bindings"]
                if item["relative_path"] == "src/allowed.py"
            )["source"])
            before = b"import json\n\ndef decode(value):\n    return json.loads(value)\n"
            staged.write_bytes(before)
            candidate = b"def decode(value):\n    return json.loads(value)\n"
            result = subprocess.run(
                [sys.executable, str(WRITER), "--receipt", str(receipt),
                 "--path", "src/allowed.py",
                 "--replace-old-base64", base64.b64encode(before).decode("ascii"),
                 "--replace-new-base64", base64.b64encode(candidate).decode("ascii")],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("removed imports remain globally referenced: json", result.stderr)
            self.assertEqual(staged.read_bytes(), before)

    def test_dataclass_default_order_failure_is_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            receipt = self._receipt(root, allow_full=False)
            value = json.loads(receipt.read_text(encoding="utf-8"))
            staged = pathlib.Path(next(
                item for item in value["bindings"]
                if item["relative_path"] == "src/allowed.py"
            )["source"])
            before = (
                b"from dataclasses import dataclass\n\n@dataclass\n"
                b"class Item:\n    required: int\n"
            )
            staged.write_bytes(before)
            candidate = (
                b"from dataclasses import dataclass\n\n@dataclass\n"
                b"class Item:\n    optional: int = 1\n    required: int\n"
            )
            result = subprocess.run(
                [sys.executable, str(WRITER), "--receipt", str(receipt),
                 "--path", "src/allowed.py",
                 "--replace-old-base64", base64.b64encode(before).decode("ascii"),
                 "--replace-new-base64", base64.b64encode(candidate).decode("ascii")],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("non-default field 'required' after a default field", result.stderr)
            self.assertEqual(staged.read_bytes(), before)

    def test_large_fragment_rewrite_requires_explicit_full_replacement(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            receipt = self._receipt(root, allow_full=False)
            value = json.loads(receipt.read_text(encoding="utf-8"))
            staged = pathlib.Path(next(
                item for item in value["bindings"]
                if item["relative_path"] == "src/allowed.py"
            )["source"])
            before = (b"# retained\n" * 500) + b"value = 1\n"
            staged.write_bytes(before)
            candidate = b"value = 2\n"
            result = subprocess.run(
                [sys.executable, str(WRITER), "--receipt", str(receipt),
                 "--path", "src/allowed.py",
                 "--replace-old-base64", base64.b64encode(before).decode("ascii"),
                 "--replace-new-base64", base64.b64encode(candidate).decode("ascii")],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("covers more than 75%", result.stderr)
            self.assertEqual(staged.read_bytes(), before)

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
