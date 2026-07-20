from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
PATH = ROOT / "scripts" / "parse-spark-output.py"
SPEC = importlib.util.spec_from_file_location("parse_spark_output", PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ParseSparkOutputTests(unittest.TestCase):
    def test_complete_route_envelope_exposes_structured_decision(self):
        result = MODULE.parse("\n".join((
            "spark_protocol=aiwf-spark-stdout-v1",
            "spark_status=started",
            "spark_mode=execution-cost-estimator",
            "recommended_owner=codex-fast-path",
            "confidence=high",
            "predicted_diff_lines_high=80",
            "predicted_files=2",
            "spark_status=success",
            "spark_protocol_end=aiwf-spark-stdout-v1",
        )))
        self.assertTrue(result["complete"])
        self.assertTrue(result["decision_valid"])
        self.assertEqual(result["structured_decision"]["decision"], "codex-fast-path")


if __name__ == "__main__":
    unittest.main()
