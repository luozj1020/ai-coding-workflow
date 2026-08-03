import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


usage = load_script("model_usage", "model-usage.py")


class ModelUsageTests(unittest.TestCase):
    def test_portable_lock_retries_windows_style_permission_contention(self):
        with tempfile.TemporaryDirectory() as raw:
            ledger = Path(raw) / "usage.jsonl"
            real_open = usage.os.open
            attempts = 0

            def permission_once(*args, **kwargs):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise PermissionError("simulated Windows lock contention")
                return real_open(*args, **kwargs)

            with mock.patch.object(usage.os, "open", side_effect=permission_once):
                with usage._portable_lock(ledger, timeout=1):
                    self.assertTrue(ledger.with_name("usage.jsonl.lock").exists())
            self.assertEqual(attempts, 2)

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

    def test_claude_cache_rate_is_token_weighted_and_grouped_by_lane(self):
        records = [
            usage.parse_claude({"is_error": False, "usage": {
                "input_tokens": 10, "cache_read_input_tokens": 90,
                "cache_creation_input_tokens": 0, "output_tokens": 1,
            }}, call_id="a", cache_lane="lane-a"),
            usage.parse_claude({"is_error": False, "usage": {
                "input_tokens": 90, "cache_read_input_tokens": 10,
                "cache_creation_input_tokens": 100, "output_tokens": 1,
            }}, call_id="b", cache_lane="lane-b"),
        ]
        result = usage.aggregate(records)
        self.assertAlmostEqual(result["totals"]["cache_hit_rate"], 100 / 300)
        self.assertEqual(result["totals"]["cache_eligible_input_tokens"], 300)
        self.assertAlmostEqual(result["by_cache_lane"]["lane-a"]["cache_hit_rate"], 0.9)
        self.assertAlmostEqual(result["by_cache_lane"]["lane-b"]["cache_hit_rate"], 0.05)
        self.assertIn("unknown::unknown::lane-a", result["by_model_cache_lane"])

    def test_cache_classification_uses_observable_component_drift(self):
        base = {
            "role": "claude", "model": "mimo", "input_tokens": 10,
            "cached_input_tokens": 90, "cache_creation_input_tokens": 0,
            "cache_lane": "lane", "stable_prefix_sha256": "sha256:prefix-a",
            "tool_schema_sha256": "sha256:tools-a",
            "provider_route_sha256": "sha256:route-a", "session_mode": "resume",
        }
        self.assertEqual(usage._classify_cache(dict(base), []), "cold-start")
        previous = [dict(base)]
        self.assertEqual(usage._classify_cache(dict(base), previous), "warm-hit")
        prefix_changed = {**base, "stable_prefix_sha256": "sha256:prefix-b"}
        self.assertEqual(usage._classify_cache(prefix_changed, previous), "prefix-drift")
        tools_changed = {**base, "tool_schema_sha256": "sha256:tools-b"}
        self.assertEqual(usage._classify_cache(tools_changed, previous), "tool-profile-change")
        route_changed = {**base, "provider_route_sha256": "sha256:route-b"}
        self.assertEqual(usage._classify_cache(route_changed, previous), "provider-route-change")
        new_lane = {**base, "cache_lane": "another-lane"}
        self.assertEqual(usage._classify_cache(new_lane, previous), "cold-start")
        resumed_fresh = {**base, "session_resume_status": "resume-failed-session-not-found-fresh-fallback"}
        self.assertEqual(usage._classify_cache(resumed_fresh, previous), "resume-failed")
        unknown = {**base, "session_mode": "new", "cached_input_tokens": 0}
        self.assertEqual(usage._classify_cache(unknown, previous), "provider-unknown")

    def test_append_persists_hashes_not_component_bodies(self):
        with tempfile.TemporaryDirectory() as raw:
            ledger = Path(raw) / "usage.jsonl"
            record = usage.parse_claude({"is_error": False, "usage": {
                "input_tokens": 5, "cache_read_input_tokens": 5, "output_tokens": 1,
            }}, call_id="cache-call", cache_lane="cache-lane:abc",
                stable_prefix_sha256="sha256:prefix", tool_schema_sha256="sha256:tools",
                task_suffix_sha256="sha256:suffix", session_mode="new")
            self.assertTrue(usage.append_once(ledger, record))
            stored = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(stored["cache_miss_classification"], "cold-start")
            self.assertEqual(stored["stable_prefix_sha256"], "sha256:prefix")
            self.assertNotIn("prompt", stored)
            self.assertNotIn("tool_schema", stored)

    def test_cache_evaluation_separates_comparable_warm_continuations(self):
        def row(call_id, *, cached, session_mode, prefix="sha256:p", tools="sha256:t"):
            return usage.parse_claude({"is_error": False, "usage": {
                "input_tokens": 10, "cache_read_input_tokens": cached, "output_tokens": 1,
            }}, call_id=call_id, model="mimo", cache_lane="lane",
                stable_prefix_sha256=prefix, tool_schema_sha256=tools,
                provider_route_sha256="sha256:route",
                session_mode=session_mode)

        records = [
            row("cold", cached=90, session_mode="new"),
            row("warm-1", cached=90, session_mode="resume"),
            row("warm-2", cached=0, session_mode="resume"),
            row("changed", cached=90, session_mode="resume", tools="sha256:new-tools"),
        ]
        result = usage.cache_evaluation(
            records, minimum_warm_calls=2, minimum_warm_hit_rate=0.4,
        )
        self.assertEqual(result["warm"]["calls"], 2)
        self.assertEqual(result["cold_or_changed"]["calls"], 2)
        self.assertAlmostEqual(result["warm"]["cache_hit_rate"], 90 / 110)
        self.assertEqual(result["status"], "pass")

        regression = usage.cache_evaluation(
            records, minimum_warm_calls=2, minimum_warm_hit_rate=0.9,
        )
        self.assertEqual(regression["status"], "regression-candidate")
        insufficient = usage.cache_evaluation(
            records, minimum_warm_calls=3, minimum_warm_hit_rate=0.4,
        )
        self.assertEqual(insufficient["status"], "insufficient-evidence")

    def test_cache_gate_cli_requires_explicit_threshold_and_fails_regression(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            ledger = root / "usage.jsonl"
            base = {
                "schema_version": 1, "role": "claude", "model": "mimo",
                "cache_lane": "lane", "stable_prefix_sha256": "sha256:p",
                "tool_schema_sha256": "sha256:t", "provider_route_sha256": "sha256:r",
                "input_tokens": 100,
                "cached_input_tokens": 0, "cache_creation_input_tokens": 0,
                "output_tokens": 1, "usage_complete": True,
            }
            rows = [
                {**base, "call_id": "cold", "session_mode": "new"},
                {**base, "call_id": "warm", "session_mode": "resume"},
            ]
            ledger.write_text("\n".join(json.dumps(item) for item in rows) + "\n", encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(ROOT / "scripts" / "model-usage.py"),
                "aggregate", str(ledger), "--minimum-warm-cache-calls", "1",
                "--minimum-warm-cache-hit-rate", "0.9", "--require-cache-gate",
            ], text=True, capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["cache_evaluation"]["status"],
                             "regression-candidate")

    def test_external_pricing_separates_calculated_and_provider_cost(self):
        pricing = {
            "schema_version": 1,
            "models": [{
                "pattern": "gpt-test*",
                "input_per_million": 5.0,
                "cached_input_per_million": 0.5,
                "output_per_million": 30.0,
                "input_includes_cached": True,
            }],
        }
        records = [usage.parse_codex_events([
            json.dumps({"model": "gpt-test-1", "usage": {
                "input_tokens": 1000, "cached_input_tokens": 800, "output_tokens": 100,
            }})
        ], call_id="priced")]
        result = usage.aggregate(records, pricing)
        self.assertAlmostEqual(result["totals"]["calculated_cost_usd"], 0.0044)
        self.assertTrue(result["totals"]["calculated_cost_complete"])
        self.assertFalse(result["totals"]["provider_cost_complete"])

    def test_claude_style_pricing_does_not_subtract_cache_hits_from_input(self):
        pricing = {
            "schema_version": 1,
            "models": [{
                "pattern": "mimo-*",
                "input_per_million": 1.0,
                "cached_input_per_million": 0.1,
                "output_per_million": 2.0,
                "input_includes_cached": False,
            }],
        }
        record = usage.parse_claude({
            "is_error": False,
            "usage": {"input_tokens": 100, "cache_read_input_tokens": 1000, "output_tokens": 10},
        }, call_id="mimo", model="mimo-test")
        self.assertAlmostEqual(usage.calculate_cost(record, pricing), 0.00022)

    def test_non_billable_catalog_entry_keeps_usage_but_zeroes_cost(self):
        pricing = {"schema_version": 1, "models": [{
            "pattern": "spark-*", "input_per_million": 9.0,
            "cached_input_per_million": 1.0, "output_per_million": 20.0,
            "input_includes_cached": True, "billable": False,
        }]}
        record = usage.parse_codex_events([json.dumps({
            "model": "spark-test", "usage": {"input_tokens": 1000, "output_tokens": 100}
        })], call_id="free-spark", role="spark")
        result = usage.aggregate([record], pricing)
        self.assertEqual(result["totals"]["calculated_cost_usd"], 0.0)
        self.assertEqual(result["by_role"]["spark"]["input_tokens"], 1000)

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

    def test_workflow_economics_attributes_codex_token_hotspots(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            ledger = root / "model-usage.jsonl"
            output = root / "economics.json"
            records = [
                {"schema_version": 1, "call_id": "c1", "role": "codex",
                 "stage": "repository-discovery", "input_tokens": 60,
                 "output_tokens": 10, "usage_complete": True},
                {"schema_version": 1, "call_id": "c2", "role": "codex",
                 "stage": "final-review", "input_tokens": 40,
                 "output_tokens": 6, "usage_complete": True},
            ]
            ledger.write_text("\n".join(json.dumps(row) for row in records) + "\n", encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(ROOT / "scripts" / "workflow_economics.py"),
                "record", "--usage-ledger", str(ledger),
                "--owner", "codex-fast-path", "--accepted", "yes",
                "--accepted-acceptance-count", "2", "--output", str(output),
            ], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            hotspots = json.loads(output.read_text(encoding="utf-8"))["codex_token_hotspots"]
            self.assertEqual(hotspots["responsibilities"]["repository-discovery"]["input_tokens"], 60)
            self.assertEqual(hotspots["responsibilities"]["final-review"]["input_tokens"], 40)
            self.assertEqual(hotspots["input_tokens_per_accepted_acceptance"], 50.0)


if __name__ == "__main__":
    unittest.main()
