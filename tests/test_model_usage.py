import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


usage = load_script("model_usage", "model-usage.py")


class ModelUsageTests(unittest.TestCase):
    def test_claude_and_codex_normalize_to_same_token_fields(self):
        claude = usage.parse_claude({
            "duration_ms": 1200,
            "duration_api_ms": 800,
            "total_cost_usd": 0.12,
            "is_error": False,
            "usage": {"input_tokens": 40, "output_tokens": 9, "cache_read_input_tokens": 7},
        }, call_id="c1", stage="builder")
        codex = usage.parse_codex_events([
            json.dumps({"type": "turn.completed", "usage": {
                "input_tokens": 40, "output_tokens": 9, "cached_input_tokens": 7,
            }})
        ], call_id="c2", role="spark", stage="preflight")
        for record in (claude, codex):
            self.assertEqual(record["input_tokens"], 40)
            self.assertEqual(record["output_tokens"], 9)
            self.assertEqual(record["cached_input_tokens"], 7)
            self.assertTrue(record["usage_complete"])
        self.assertEqual(codex["role"], "spark")

    def test_codex_last_cumulative_usage_event_wins(self):
        record = usage.parse_codex_events([
            json.dumps({"usage": {"input_tokens": 10, "output_tokens": 2}}),
            json.dumps({"usage": {"input_tokens": 20, "output_tokens": 4}}),
        ], call_id="c1")
        self.assertEqual(record["input_tokens"], 20)
        self.assertEqual(record["output_tokens"], 4)

    def test_missing_usage_is_null_and_incomplete(self):
        record = usage.parse_claude({"duration_ms": 10}, call_id="c1")
        self.assertIsNone(record["input_tokens"])
        self.assertIsNone(record["output_tokens"])
        self.assertFalse(record["usage_complete"])

    def test_append_is_idempotent_and_refuses_malformed_ledger(self):
        with tempfile.TemporaryDirectory() as raw:
            ledger = Path(raw) / "usage.jsonl"
            record = usage.parse_claude({"usage": {"input_tokens": 1, "output_tokens": 2}}, call_id="same")
            self.assertTrue(usage.append_once(ledger, record))
            self.assertFalse(usage.append_once(ledger, record))
            self.assertEqual(len(ledger.read_text(encoding="utf-8").splitlines()), 1)
            ledger.write_text("{broken\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                usage.append_once(ledger, {**record, "call_id": "new"})
            self.assertEqual(ledger.read_text(encoding="utf-8"), "{broken\n")

    def test_aggregate_reports_role_completeness(self):
        records = [
            usage.parse_claude({"usage": {"input_tokens": 5, "output_tokens": 2}}, call_id="a"),
            usage.parse_codex_events([], call_id="b", role="spark"),
        ]
        result = usage.aggregate(records)
        self.assertEqual(result["totals"]["calls"], 2)
        self.assertEqual(result["totals"]["input_tokens"], 5)
        self.assertFalse(result["totals"]["usage_complete"])
        self.assertTrue(result["by_role"]["claude"]["usage_complete"])
        self.assertFalse(result["by_role"]["spark"]["usage_complete"])

    def test_concurrent_cli_appends_preserve_every_record(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            ledger = root / "usage.jsonl"
            processes = []
            for index in range(6):
                record = root / "record-{}.json".format(index)
                record.write_text(json.dumps({
                    "schema_version": 1,
                    "call_id": "call-{}".format(index),
                    "role": "claude",
                    "usage_complete": False,
                }), encoding="utf-8")
                processes.append(subprocess.Popen([
                    sys.executable, str(ROOT / "scripts" / "model-usage.py"),
                    "append", str(ledger), str(record),
                ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
            # The compatibility append subcommand intentionally remains useful
            # for deterministic fixture and migration tests.
            for process in processes:
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, stderr + stdout)
            self.assertEqual(len(usage.load_records(ledger, strict=True)), 6)

    def test_workflow_economics_embeds_canonical_role_totals(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            metrics = root / "metrics.json"
            ledger = root / "model-usage.jsonl"
            output = root / "economics.json"
            metrics.write_text(json.dumps({"run_id": "r1", "task_id": "t1"}), encoding="utf-8")
            ledger.write_text(json.dumps({
                "schema_version": 1, "call_id": "c1", "role": "claude",
                "stage": "builder", "input_tokens": 8, "output_tokens": 2,
                "usage_complete": True,
            }) + "\n", encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(ROOT / "scripts" / "workflow_economics.py"),
                "record", "--metrics", str(metrics), "--usage-ledger", str(ledger),
                "--owner", "codex-fast-path", "--accepted", "yes", "--output", str(output),
            ], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(value["model_usage"]["by_role"]["claude"]["input_tokens"], 8)
            self.assertTrue(value["model_usage_complete"])


if __name__ == "__main__":
    unittest.main()
