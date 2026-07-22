import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "compare-transfer-pilot.py"


def load_module():
    spec = importlib.util.spec_from_file_location("compare_transfer_pilot", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(pair, arm, **overrides):
    values = {
        "pair_id": pair,
        "arm": arm,
        "run_kind": "real-model",
        "usage_complete": True,
        "accepted": True,
        "task_hash": f"sha256:{pair}",
        "baseline_commit": "a" * 40,
        "codex_input_tokens": 100 if arm == "markdown-baseline" else 80,
        "active_elapsed_seconds": 100 if arm == "markdown-baseline" else 120,
        "seconds_to_first_meaningful_action": 30 if arm == "markdown-baseline" else 20,
        "receiver_reads_before_first_action": 8 if arm == "markdown-baseline" else 4,
        "handoff_revision_count": 2 if arm == "markdown-baseline" else 1,
        "payload_bytes": 2000 if arm == "markdown-baseline" else 1000,
        "final_diff_reuse_ratio": 0.7 if arm == "markdown-baseline" else 0.8,
    }
    values.update(overrides)
    return values


class CompareTransferPilotTests(unittest.TestCase):
    def test_real_complete_pairs_can_prove_effective_and_economic(self):
        module = load_module()
        records = [run(str(index), arm) for index in range(3) for arm in module.ARMS]
        result = module.compare(records)
        self.assertEqual(result["verdict"], "effective-and-economic")
        self.assertAlmostEqual(result["codex_input_token_saving_ratio"], 0.2)

    def test_simulated_or_incomplete_usage_is_insufficient(self):
        module = load_module()
        records = [run(str(index), arm) for index in range(3) for arm in module.ARMS]
        records[0]["run_kind"] = "simulated"
        records[1]["usage_complete"] = False
        result = module.compare(records)
        self.assertEqual(result["verdict"], "insufficient-evidence")
        self.assertFalse(result["comparable"])

    def test_quality_regression_cannot_pass(self):
        module = load_module()
        records = [run(str(index), arm) for index in range(3) for arm in module.ARMS]
        for record in records:
            if record["arm"] == "stateful":
                record["accepted"] = False
        result = module.compare(records)
        self.assertEqual(result["verdict"], "not-yet-proven")
        self.assertFalse(result["gates"]["acceptance_no_regression"])


if __name__ == "__main__":
    unittest.main()
