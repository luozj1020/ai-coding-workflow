import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build-takeover-receipt.py"
SPEC = importlib.util.spec_from_file_location("build_takeover_receipt", SCRIPT)
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MOD)


class TakeoverReceiptTests(unittest.TestCase):
    def test_two_counted_rounds_authorize_only_bound_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prior_path, current_path, card = root / "prior.json", root / "current.json", root / "card.md"
            counted = {"failure_class": "model-no-progress", "counts_toward_takeover": True}
            prior_path.write_text(json.dumps(counted), encoding="utf-8")
            current_path.write_text(json.dumps(counted), encoding="utf-8")
            card.write_text("## Scope\n\n- Write paths: src/a.py, tests/test_a.py\n- Forbidden paths: deploy/\n", encoding="utf-8")
            value = MOD.build(counted, current_path, counted, prior_path, card, "round-2", "round-1", "root")
            self.assertEqual(value["status"], "authorized")
            self.assertEqual(value["allowed_write_paths"], ["src/a.py", "tests/test_a.py"])
            self.assertFalse(value["merge_authorized"])

    def test_external_failure_cannot_authorize_takeover(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, card = root / "attempt.json", root / "card.md"
            external = {"failure_class": "transient-transport", "counts_toward_takeover": False}
            path.write_text(json.dumps(external), encoding="utf-8")
            card.write_text("- Write paths: src/a.py\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                MOD.build(external, path, external, path, card, "two", "one", "root")


if __name__ == "__main__":
    unittest.main()
