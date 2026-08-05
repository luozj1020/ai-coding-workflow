import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "route_task_cli", ROOT / "scripts" / "route-task.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RouteTaskCliTests(unittest.TestCase):
    def test_output_is_materialized_by_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            facts = root / "facts.json"
            output = root / "route.json"
            facts.write_text(json.dumps({
                "risks": {key: False for key in MODULE.HIGH},
                "files": 1,
                "diff_lines": 20,
                "exact_validation": True,
                "delegation_value": False,
            }), encoding="utf-8")

            with contextlib.redirect_stdout(io.StringIO()):
                MODULE.main([str(facts), "--output", str(output)])

            route = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(route["schema_version"], 1)
            self.assertEqual(
                route["planning"]["adversarial_review_artifact_owner"],
                "deterministic-tools",
            )


if __name__ == "__main__":
    unittest.main()
