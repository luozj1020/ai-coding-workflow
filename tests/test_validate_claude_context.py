import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate-claude-context.py"
SPEC = importlib.util.spec_from_file_location("validate_claude_context", SCRIPT)
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MOD)


class Tests(unittest.TestCase):
    def test_complete_packet_enables_execution_only(self):
        text = """## Claude Context Packet
| Field | Value |
|---|---|
| Target files/modules | a.py, b.py |
| Relevant symbols/functions | f, g |
| Reference examples / source of truth | ref.py |
| Do not read / do not modify | vendor/ |
| Known constraints | no API changes |
| Narrow validation commands | pytest -q tests/x.py |
| Context is sufficient for execution? | yes |
| Execution-only eligible? | yes |
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "task.md"
            path.write_text(text, encoding="utf-8")
            result = MOD.validate(path)
            self.assertTrue(result["complete"])
            self.assertTrue(result["execution_only_eligible"])
            self.assertEqual(result["target_file_count"], 2)

    def test_missing_fields_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "task.md"
            path.write_text(
                "## Claude Context Packet\n| Target files/modules | a.py |",
                encoding="utf-8",
            )
            self.assertFalse(MOD.validate(path)["complete"])

    def test_legacy_context_sufficient_alias_is_normalized(self):
        text = """## Claude Context Packet
| Field | Value |
|---|---|
| Target files/modules | a.py |
| Relevant symbols/functions | f |
| Reference examples / source of truth | ref.py |
| Do not read / do not modify | vendor/ |
| Known constraints | no API changes |
| Narrow validation commands | pytest -q tests/x.py |
| Context sufficient for execution? | yes |
| Execution-only eligible? | yes |
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "task.md"
            path.write_text(text, encoding="utf-8")
            self.assertTrue(MOD.validate(path)["execution_only_eligible"])


if __name__ == "__main__":
    unittest.main()
