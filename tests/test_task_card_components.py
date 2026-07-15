import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "compose_task_card.py"


def load_module():
    spec = importlib.util.spec_from_file_location("compose_task_card", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TaskCardComponentTests(unittest.TestCase):
    def test_always_loaded_policy_has_bounded_context_budget(self):
        agents = (ROOT / "assets" / "AGENTS.md").read_text(encoding="utf-8")
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        routing = (ROOT / "references" / "routing-and-spark.md").read_text(encoding="utf-8")
        self.assertLess(len(agents.encode("utf-8")), 12_000)
        self.assertLess(len(skill.encode("utf-8")), 6_000)
        self.assertNotIn("controlled-builder", agents)
        self.assertIn("controlled-builder", routing)
        self.assertIn("do not load multiple", skill.lower())

    def test_catalog_is_small_and_exposes_choices_not_component_bodies(self):
        text = (ROOT / "assets" / "task-card-components" / "catalog.md").read_text(encoding="utf-8")
        self.assertLess(len(text.splitlines()), 70)
        self.assertIn("`builder`", text)
        self.assertIn("`revision`", text)
        self.assertNotIn("## Acceptance Criteria", text)

    def test_builder_card_is_concise_and_only_selected_gate_is_added(self):
        module = load_module()
        root = module.component_root(SCRIPT)
        text, selected = module.compose(root, module.load_catalog(root), "builder", ["root-cause"])
        self.assertEqual(selected, ["core", "builder", "root-cause"])
        self.assertIn("## Builder Contract", text)
        self.assertIn("## Root Cause Gate", text)
        self.assertIn("| Mode | builder |", text)
        self.assertIn("Checker model dispatch", text)
        self.assertNotIn("## Parallel Execution Gate", text)
        self.assertLess(len(text.splitlines()), 120)

    def test_revision_is_delta_only(self):
        module = load_module()
        root = module.component_root(SCRIPT)
        text, selected = module.compose(root, module.load_catalog(root), "revision", [])
        self.assertEqual(selected, ["core", "revision"])
        self.assertIn("## Revision Delta", text)
        self.assertNotIn("## Builder Contract", text)

    def test_unknown_gate_fails_without_writing(self):
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "card.md"
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--preset", "builder", "--gate", "missing", "--output", str(output)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("unknown gate", result.stderr)
            self.assertFalse(output.exists())

    def test_list_is_machine_readable(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--list"],
            text=True,
            capture_output=True,
            check=True,
        )
        value = json.loads(result.stdout)
        self.assertIn("builder", value["presets"])
        self.assertIn("large-repo", value["gates"])


if __name__ == "__main__":
    unittest.main()
