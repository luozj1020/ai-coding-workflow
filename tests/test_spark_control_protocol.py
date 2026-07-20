from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "spark_control_protocol.py"
SPEC = importlib.util.spec_from_file_location("spark_control_protocol", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SparkControlProtocolTests(unittest.TestCase):
    def test_route_normalizes_legacy_fields_for_direct_downstream_use(self):
        value = MODULE.parse_and_normalize(
            "route",
            "recommended_owner=claude-builder\nconfidence=high\n"
            "risk_flags=none\npredicted_diff_lines_high=300\npredicted_files=4\n",
        )
        self.assertEqual(value["protocol"], "spark-route-decision-v1")
        self.assertEqual(value["decision"], "claude-builder")
        self.assertTrue(value["task_card_required"])
        self.assertTrue(value["advisory_only"])
        self.assertEqual(len(value["evidence_hash"]), 64)

    def test_route_preserves_solution_planner_advice(self):
        value = MODULE.parse_and_normalize(
            "route",
            "recommended_owner=claude-builder\nconfidence=high\n"
            "claude_role=solution-planner\ndurable_output_required=yes\n"
            "readonly_delegation_value=no\n",
        )
        self.assertEqual(value["claude_role"], "solution-planner")
        self.assertTrue(value["durable_output_required"])
        self.assertFalse(value["readonly_delegation_value"])

    def test_monitor_can_never_authorize_interrupt(self):
        value = MODULE.parse_and_normalize(
            "monitor",
            "decision=interrupt-candidate\nconfidence=high\n"
            "reason_code=confirmed-deviation\ninterrupt_authorized=yes\n",
        )
        self.assertFalse(value["interrupt_authorized"])

    def test_monitor_preserves_structured_completion_advice(self):
        value = MODULE.parse_and_normalize(
            "monitor",
            "decision=continue\nconfidence=high\nexecution_phase=tail\n"
            "implementation_complete=yes\ncompletion_ready=yes\n"
            "finish_recommended=yes\ninterrupt_authorized=yes\n",
        )
        self.assertEqual(value["execution_phase"], "tail")
        self.assertTrue(value["implementation_complete"])
        self.assertTrue(value["completion_ready"])
        self.assertTrue(value["finish_recommended"])
        self.assertFalse(value["interrupt_authorized"])

    def test_parallel_is_capped_and_cannot_dispatch(self):
        value = MODULE.parse_and_normalize(
            "parallel",
            "parallel_decision=parallel-candidate\nconfidence=high\nmax_concurrency=9\n",
        )
        self.assertEqual(value["max_concurrency"], 2)
        self.assertFalse(value["dispatch_authorized"])
        self.assertTrue(value["serial_reconciliation_required"])

    def test_evidence_hash_is_order_independent(self):
        first = MODULE.evidence_hash("monitor", {"a": 1, "b": 2})
        second = MODULE.evidence_hash("monitor", {"b": 2, "a": 1})
        self.assertEqual(first, second)

    def test_input_bound_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "input-too-large"):
            MODULE.parse_and_normalize("route", "x" * (MODULE.MAX_INPUT_BYTES + 1))

    def test_cli_accepts_trusted_deterministic_override(self):
        result = subprocess.run(
            [sys.executable, str(MODULE_PATH), "route", "--compact", "--set",
             "deterministic_owner=codex-fast-path"],
            input="recommended_owner=claude-builder\nconfidence=high\n",
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["decision"], "codex-fast-path")


if __name__ == "__main__":
    unittest.main()
