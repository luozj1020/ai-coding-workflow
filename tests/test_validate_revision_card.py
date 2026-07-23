import importlib.util
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate-revision-card.py"


def load_module():
    spec = importlib.util.spec_from_file_location("validate_revision_card", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class ValidateRevisionCardTests(unittest.TestCase):
    def check(self, text):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "card.md"
            path.write_text(text, encoding="utf-8")
            return module.validate(path)

    def test_non_revision_is_not_applicable(self):
        self.assertEqual(self.check("# Builder\n")["status"], "not-applicable")

    def test_external_path_or_placeholder_only_is_invalid(self):
        result = self.check(
            "## Revision Delta\n\nReview: /outside/findings.md\n"
            "- `finding_id=F-01 | evidence=<file:symbol> | "
            "required_change=<change> | acceptance=<check>`\n"
        )
        self.assertEqual(result["status"], "invalid")
        self.assertTrue(result["errors"])

    def test_exact_inline_finding_is_valid(self):
        result = self.check(
            "## Revision Delta\n\n"
            "- finding_id=F-03 | evidence=src/a.py:route skips belts | "
            "required_change=include placed belts in BFS frontier | "
            "acceptance=pytest tests/test_route.py::test_belt_frontier\n"
        )
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["findings"][0]["finding_id"], "F-03")


if __name__ == "__main__":
    unittest.main()
