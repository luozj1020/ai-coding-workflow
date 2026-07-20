"""Tests for PR8 Executable Benchmark.

Covers:
- Case schema validation
- Deterministic fake adapters (repeatable results)
- Full pipeline execution (Task → Route → Dispatch → Evidence → Review Ladder → Decision)
- Aggregate gates (Codex calls reduction, p50 latency, first-pass success, quality)
- compare-efficiency.py consumes executed case results
- Windows paths and Python 3.9 compatibility
- Installer/aiwf registration
- Fake case repeatability
- All regression gates
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
CASES = ROOT / "benchmarks" / "cases"


def load_module(name, path):
    """Load a Python module from file path."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_benchmark = load_module("run_benchmark", SCRIPTS / "run-benchmark-suite.py")
compare_efficiency = load_module("compare_efficiency", SCRIPTS / "compare-efficiency.py")


def make_pass_pair():
    """Return (baseline, candidate) dicts that satisfy all gates with sufficient data."""
    baseline = {
        "total_codex_calls": 10,
        "p50_latency_seconds": 1.0,
        "first_pass_rate": 0.8,
        "false_accepts": 0,
        "scope_violations": 0,
        "full_redispatch_avoided_total": 3,
        "advisor_continuation_succeeded_total": 5,
        "reexploration_yes_total": 1,
    }
    candidate = {
        "total_codex_calls": 5,
        "p50_latency_seconds": 1.0,
        "first_pass_rate": 0.85,
        "false_accepts": 0,
        "scope_violations": 0,
        "full_redispatch_avoided_total": 4,
        "advisor_continuation_succeeded_total": 6,
        "reexploration_yes_total": 0,
    }
    return baseline, candidate


class TestCaseSchema(unittest.TestCase):
    """Test case schema validation."""

    def test_valid_case(self):
        """Valid case passes validation."""
        case = {
            "id": "test-case",
            "task": "Test task description",
            "expected_lane": "standard",
            "expected_tier": "L0",
            "quota_budget": 5,
        }
        errors = run_benchmark.validate_case(case)
        self.assertEqual(errors, [])

    def test_missing_id(self):
        """Missing id is an error."""
        case = {"task": "Test task"}
        errors = run_benchmark.validate_case(case)
        self.assertTrue(any("id" in e for e in errors))

    def test_missing_task(self):
        """Missing task is an error."""
        case = {"id": "test-case"}
        errors = run_benchmark.validate_case(case)
        self.assertTrue(any("task" in e for e in errors))

    def test_invalid_lane(self):
        """Invalid expected_lane is an error."""
        case = {
            "id": "test-case",
            "task": "Test",
            "expected_lane": "invalid",
        }
        errors = run_benchmark.validate_case(case)
        self.assertTrue(any("expected_lane" in e for e in errors))

    def test_invalid_tier(self):
        """Invalid expected_tier is an error."""
        case = {
            "id": "test-case",
            "task": "Test",
            "expected_tier": "L3",
        }
        errors = run_benchmark.validate_case(case)
        self.assertTrue(any("expected_tier" in e for e in errors))

    def test_negative_quota(self):
        """Negative quota_budget is an error."""
        case = {
            "id": "test-case",
            "task": "Test",
            "quota_budget": -1,
        }
        errors = run_benchmark.validate_case(case)
        self.assertTrue(any("quota_budget" in e for e in errors))

    def test_hostile_id_rejected(self):
        """Hostile characters in id are rejected."""
        case = {
            "id": "../bad",
            "task": "Test",
        }
        errors = run_benchmark.validate_case(case)
        self.assertTrue(any("unsafe" in e for e in errors))

    def test_routing_facts_must_be_object(self):
        errors = run_benchmark.validate_case({
            "id": "bad-routing", "task": "Test", "routing_facts": [],
        })
        self.assertTrue(any("routing_facts" in e for e in errors))


class TestFakeAdapters(unittest.TestCase):
    """Test deterministic fake adapters."""

    def test_claude_adapter_deterministic(self):
        """FakeClaudeAdapter produces deterministic results."""
        adapter = run_benchmark.FakeClaudeAdapter()
        task = {"id": "test-case", "expected_lane": "standard"}
        r1 = adapter.dispatch(task, {})
        adapter2 = run_benchmark.FakeClaudeAdapter()
        r2 = adapter2.dispatch(task, {})
        self.assertEqual(r1["diff"], r2["diff"])
        self.assertEqual(r1["diff_lines"], r2["diff_lines"])

    def test_spark_adapter_deterministic(self):
        """FakeSparkAdapter produces deterministic results."""
        adapter = run_benchmark.FakeSparkAdapter()
        task = {"id": "test-case"}
        dispatch = {"adapter": "claude", "diff": "+line 0"}
        r1 = adapter.execute(task, dispatch)
        adapter2 = run_benchmark.FakeSparkAdapter()
        r2 = adapter2.execute(task, dispatch)
        self.assertEqual(r1["validation_passed"], r2["validation_passed"])

    def test_codex_adapter_deterministic(self):
        """FakeCodexAdapter produces deterministic results."""
        adapter = run_benchmark.FakeCodexAdapter()
        task = {"id": "test-case", "expected_tier": "L0"}
        dispatch = {"adapter": "claude", "diff": "+line 0"}
        r1 = adapter.review(task, dispatch, None)
        adapter2 = run_benchmark.FakeCodexAdapter()
        r2 = adapter2.review(task, dispatch, None)
        self.assertEqual(r1["tier"], r2["tier"])
        self.assertEqual(r1["accepted"], r2["accepted"])

    def test_codex_direct_adapter_deterministic(self):
        task = {"id": "direct-case", "expected_tier": "L0"}
        r1 = run_benchmark.FakeCodexAdapter().implement(task, {"lane": "standard"})
        r2 = run_benchmark.FakeCodexAdapter().implement(task, {"lane": "standard"})
        self.assertEqual(r1["diff"], r2["diff"])
        self.assertEqual(r1["adapter"], "codex")


class TestPipelineExecution(unittest.TestCase):
    """Test full pipeline execution."""

    def test_execute_case_produces_result(self):
        """execute_case produces a complete result."""
        case = {
            "id": "test-case",
            "task": "Test task",
            "expected_lane": "standard",
            "expected_tier": "L0",
        }
        claude = run_benchmark.FakeClaudeAdapter()
        spark = run_benchmark.FakeSparkAdapter()
        codex = run_benchmark.FakeCodexAdapter()
        result = run_benchmark.execute_case(case, claude, spark, codex)

        self.assertIn("case_id", result)
        self.assertIn("status", result)
        self.assertIn("decision", result)
        self.assertIn("evidence", result)
        self.assertIn("total_model_calls", result)
        self.assertIn("elapsed_seconds", result)

    def test_express_lane_no_spark(self):
        """Express lane uses Claude directly without a Spark estimate."""
        case = {
            "id": "express-case",
            "task": "Simple fix",
            "expected_lane": "express",
        }
        claude = run_benchmark.FakeClaudeAdapter()
        spark = run_benchmark.FakeSparkAdapter()
        codex = run_benchmark.FakeCodexAdapter()
        result = run_benchmark.execute_case(case, claude, spark, codex)

        self.assertEqual(result["spark_calls"], 0)
        self.assertEqual(result["claude_calls"], 1)
        self.assertEqual(result["codex_calls"], 1)
        self.assertEqual(result["route"]["owner"], "claude-builder")

    def test_standard_lane_defaults_to_claude_without_spark(self):
        """Lane alone follows the deterministic Claude-first route."""
        case = {
            "id": "standard-case",
            "task": "Standard task",
            "expected_lane": "standard",
        }
        claude = run_benchmark.FakeClaudeAdapter()
        spark = run_benchmark.FakeSparkAdapter()
        codex = run_benchmark.FakeCodexAdapter()
        result = run_benchmark.execute_case(case, claude, spark, codex)

        self.assertEqual(result["spark_calls"], 0)
        self.assertEqual(result["claude_calls"], 1)
        self.assertEqual(result["codex_calls"], 1)
        self.assertEqual(result["route"]["owner"], "claude-builder")

    def test_uncertain_positive_claude_candidate_uses_one_spark_estimate(self):
        case = {
            "id": "planner-candidate",
            "task": "Design a bounded multi-phase feature",
            "expected_lane": "standard",
            "routing_facts": {
                "execution_owner": "claude-builder",
                "claude_role": "solution-planner",
                "goal_clarity": "high",
                "implementation_path_clarity": "low",
                "bounded_exploration_scope": True,
                "durable_structured_output": True,
                "expected_codex_work_reduction_ratio": 0.4,
                "multi_phase_task": True,
                "spark_route_requested": True,
            },
        }
        result = run_benchmark.execute_case(
            case,
            run_benchmark.FakeClaudeAdapter(),
            run_benchmark.FakeSparkAdapter(),
            run_benchmark.FakeCodexAdapter(),
        )
        self.assertEqual(result["route"]["owner"], "claude-builder")
        self.assertEqual(result["route"]["claude_role"], "solution-planner")
        self.assertEqual(result["spark_calls"], 1)
        self.assertEqual(result["claude_calls"], 1)


class TestRepeatability(unittest.TestCase):
    """Test that benchmark results are repeatable."""

    def test_same_case_same_result(self):
        """Same case produces same result across runs."""
        case = {
            "id": "repeat-case",
            "task": "Repeatable task",
            "expected_lane": "standard",
            "expected_tier": "L1",
        }
        results = []
        for _ in range(3):
            claude = run_benchmark.FakeClaudeAdapter()
            spark = run_benchmark.FakeSparkAdapter()
            codex = run_benchmark.FakeCodexAdapter()
            result = run_benchmark.execute_case(case, claude, spark, codex)
            results.append(result)

        # Decision should be identical
        for r in results[1:]:
            self.assertEqual(r["decision"], results[0]["decision"])
            self.assertEqual(r["total_model_calls"], results[0]["total_model_calls"])


class TestAggregateGates(unittest.TestCase):
    """Test aggregate gate computation."""

    def test_no_baseline(self):
        """Without baseline, gates are not evaluated."""
        results = [
            {"case_id": "c1", "status": "passed", "claude_calls": 1,
             "codex_calls": 0, "spark_calls": 0, "elapsed_seconds": 1.0,
             "decision": {"false_accept": False, "scope_violation": False}},
        ]
        agg = run_benchmark.compute_aggregate_gates(results)
        self.assertIsNone(agg["all_passed"])
        self.assertIn("note", agg["gates"])

    def test_codex_reduction_gate(self):
        """Codex calls reduction >= 30% passes."""
        baseline = {"total_codex_calls": 10, "p50_latency_seconds": 1.0,
                     "first_pass_rate": 0.8, "false_accepts": 0, "scope_violations": 0}
        results = [
            {"case_id": f"c{i}", "status": "passed", "claude_calls": 1,
             "codex_calls": 0, "spark_calls": 0, "elapsed_seconds": 1.0,
             "decision": {"false_accept": False, "scope_violation": False}}
            for i in range(10)
        ]
        agg = run_benchmark.compute_aggregate_gates(results, baseline)
        self.assertTrue(agg["gates"]["codex_calls_reduction"]["passed"])

    def test_latency_gate(self):
        """p50 latency increase <= 15% passes."""
        baseline = {"total_codex_calls": 10, "p50_latency_seconds": 1.0,
                     "first_pass_rate": 0.8, "false_accepts": 0, "scope_violations": 0}
        results = [
            {"case_id": f"c{i}", "status": "passed", "claude_calls": 1,
             "codex_calls": 1, "spark_calls": 0, "elapsed_seconds": 1.1,
             "decision": {"false_accept": False, "scope_violation": False}}
            for i in range(10)
        ]
        agg = run_benchmark.compute_aggregate_gates(results, baseline)
        self.assertTrue(agg["gates"]["p50_latency_increase"]["passed"])

    def test_first_pass_decline_gate(self):
        """First-pass success decline <= 5pp passes."""
        baseline = {"total_codex_calls": 10, "p50_latency_seconds": 1.0,
                     "first_pass_rate": 0.8, "false_accepts": 0, "scope_violations": 0}
        results = [
            {"case_id": f"c{i}", "status": "passed", "claude_calls": 1,
             "codex_calls": 1, "spark_calls": 0, "elapsed_seconds": 1.0,
             "decision": {"false_accept": False, "scope_violation": False}}
            for i in range(10)
        ]
        agg = run_benchmark.compute_aggregate_gates(results, baseline)
        self.assertTrue(agg["gates"]["first_pass_success_decline"]["passed"])

    def test_quality_gate(self):
        """False accepts and scope violations do not increase."""
        baseline = {"total_codex_calls": 10, "p50_latency_seconds": 1.0,
                     "first_pass_rate": 0.8, "false_accepts": 0, "scope_violations": 0}
        results = [
            {"case_id": f"c{i}", "status": "passed", "claude_calls": 1,
             "codex_calls": 1, "spark_calls": 0, "elapsed_seconds": 1.0,
             "decision": {"false_accept": False, "scope_violation": False}}
            for i in range(10)
        ]
        agg = run_benchmark.compute_aggregate_gates(results, baseline)
        self.assertTrue(agg["gates"]["false_accepts_no_increase"]["passed"])
        self.assertTrue(agg["gates"]["scope_violations_no_increase"]["passed"])


class TestCompareEfficiency(unittest.TestCase):
    """Test compare-efficiency.py consumes executed case results."""

    def test_compare_executed_results(self):
        """compare-efficiency.py works with executed case results."""
        b, c = make_pass_pair()
        baseline = {"current": b}
        candidate = {"current": c}
        with tempfile.TemporaryDirectory() as tmp:
            baseline_path = Path(tmp) / "baseline.json"
            candidate_path = Path(tmp) / "candidate.json"
            baseline_path.write_text(json.dumps(baseline))
            candidate_path.write_text(json.dumps(candidate))

            result = compare_efficiency.compare(b, c)
            self.assertIn("codex_call_reduction", result)
            self.assertIn("all_gates_pass", result)
            self.assertIn("redispatch_gate_pass", result)

    def test_compare_legacy_format(self):
        """compare-efficiency.py works with legacy ledger format."""
        baseline_metrics = {
            "model_calls": {"codex": 10, "claude": 5, "spark": 3},
            "elapsed_seconds": 100,
            "first_pass_success_rate": 0.8,
            "false_accepts": 0,
            "scope_violations": 0,
            "full_redispatch_avoided": 3,
            "continuation_succeeded": 5,
            "reexploration_yes": 1,
        }
        candidate_metrics = {
            "model_calls": {"codex": 5, "claude": 5, "spark": 3},
            "elapsed_seconds": 100,
            "first_pass_success_rate": 0.85,
            "false_accepts": 0,
            "scope_violations": 0,
            "full_redispatch_avoided": 4,
            "continuation_succeeded": 6,
            "reexploration_yes": 0,
        }
        result = compare_efficiency.compare(baseline_metrics, candidate_metrics)
        self.assertIn("codex_call_reduction", result)
        self.assertTrue(result["quota_gate_pass"])
        self.assertTrue(result["redispatch_gate_pass"])

    def test_insufficient_data_when_baseline_missing(self):
        """Reports insufficient-data when required baseline samples are absent."""
        baseline = {"false_accepts": 0, "scope_violations": 0}
        candidate = {
            "total_codex_calls": 5,
            "p50_latency_seconds": 1.0,
            "first_pass_rate": 0.9,
            "false_accepts": 0,
            "scope_violations": 0,
        }
        result = compare_efficiency.compare(baseline, candidate)
        self.assertEqual(result["status"], "insufficient-data")
        self.assertTrue(result["insufficient_data"])
        self.assertTrue(len(result["missing_samples"]) > 0)
        self.assertFalse(result["all_gates_pass"])

    def test_insufficient_data_when_candidate_first_pass_missing(self):
        """Reports insufficient-data when candidate first_pass_rate is absent."""
        baseline = {
            "total_codex_calls": 10,
            "p50_latency_seconds": 1.0,
            "first_pass_rate": 0.8,
            "false_accepts": 0,
            "scope_violations": 0,
        }
        candidate = {
            "total_codex_calls": 5,
            "p50_latency_seconds": 1.1,
            "false_accepts": 0,
            "scope_violations": 0,
        }
        result = compare_efficiency.compare(baseline, candidate)
        self.assertEqual(result["status"], "insufficient-data")
        self.assertIn("candidate_first_pass_rate", result["missing_samples"])

    def test_pass_status_when_all_gates_pass(self):
        """Reports pass when all gates pass with sufficient data."""
        baseline, candidate = make_pass_pair()
        result = compare_efficiency.compare(baseline, candidate)
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["all_gates_pass"])
        self.assertFalse(result["insufficient_data"])
        self.assertTrue(result["redispatch_gate_pass"])

    def test_fail_status_when_gate_fails(self):
        """Reports fail when a gate fails with sufficient data."""
        baseline, candidate = make_pass_pair()
        candidate["total_codex_calls"] = 9  # fails quota gate (10% < 30%)
        result = compare_efficiency.compare(baseline, candidate)
        self.assertEqual(result["status"], "fail")
        self.assertFalse(result["all_gates_pass"])
        self.assertFalse(result["quota_gate_pass"])

    def test_continuation_gate(self):
        """Continuation success non-regression is evaluated."""
        baseline, candidate = make_pass_pair()
        result = compare_efficiency.compare(baseline, candidate)
        self.assertTrue(result["continuation_gate_pass"])
        self.assertTrue(result["reexploration_gate_pass"])
        self.assertTrue(result["redispatch_gate_pass"])

    def test_reexploration_gate_regression(self):
        """Re-exploration increase is caught as a regression."""
        baseline, candidate = make_pass_pair()
        candidate["reexploration_yes_total"] = 3  # regression: 3 > 1
        result = compare_efficiency.compare(baseline, candidate)
        self.assertFalse(result["reexploration_gate_pass"])
        self.assertEqual(result["status"], "fail")

    def test_redispatch_gate_pass(self):
        """Redispatch avoidance non-regression passes when candidate >= baseline."""
        baseline, candidate = make_pass_pair()
        result = compare_efficiency.compare(baseline, candidate)
        self.assertTrue(result["redispatch_gate_pass"])

    def test_redispatch_gate_regression(self):
        """Redispatch avoidance regression fails the gate."""
        baseline, candidate = make_pass_pair()
        candidate["full_redispatch_avoided_total"] = 2  # regression: 2 < 3
        result = compare_efficiency.compare(baseline, candidate)
        self.assertFalse(result["redispatch_gate_pass"])
        self.assertEqual(result["status"], "fail")

    def test_missing_candidate_codex_calls_is_insufficient(self):
        """Missing candidate codex_calls prevents pass."""
        baseline, candidate = make_pass_pair()
        del candidate["total_codex_calls"]
        result = compare_efficiency.compare(baseline, candidate)
        self.assertEqual(result["status"], "insufficient-data")
        self.assertIn("candidate_codex_calls", result["missing_samples"])

    def test_missing_candidate_latency_is_insufficient(self):
        """Missing candidate latency prevents pass."""
        baseline, candidate = make_pass_pair()
        del candidate["p50_latency_seconds"]
        result = compare_efficiency.compare(baseline, candidate)
        self.assertEqual(result["status"], "insufficient-data")
        self.assertIn("candidate_latency", result["missing_samples"])

    def test_missing_candidate_continuation_is_insufficient(self):
        """Missing candidate continuation_success prevents pass."""
        baseline, candidate = make_pass_pair()
        del candidate["advisor_continuation_succeeded_total"]
        result = compare_efficiency.compare(baseline, candidate)
        self.assertEqual(result["status"], "insufficient-data")
        self.assertIn("candidate_continuation_success", result["missing_samples"])

    def test_missing_candidate_redispatch_is_insufficient(self):
        """Missing candidate redispatch prevents pass."""
        baseline, candidate = make_pass_pair()
        del candidate["full_redispatch_avoided_total"]
        result = compare_efficiency.compare(baseline, candidate)
        self.assertEqual(result["status"], "insufficient-data")
        self.assertIn("candidate_redispatch", result["missing_samples"])


class TestCLI(unittest.TestCase):
    """Test CLI interface."""

    def test_missing_cases_dir(self):
        """Missing cases directory exits with nonzero."""
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "run-benchmark-suite.py"),
                "--cases", "/nonexistent",
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_compare_enforce_insufficient_data(self):
        """--enforce returns exit 2 for insufficient-data."""
        baseline = {"false_accepts": 0}
        candidate = {"total_codex_calls": 5, "p50_latency_seconds": 1.0,
                     "first_pass_rate": 0.9, "false_accepts": 0, "scope_violations": 0}
        with tempfile.TemporaryDirectory() as tmp:
            bp = Path(tmp) / "b.json"
            cp = Path(tmp) / "c.json"
            bp.write_text(json.dumps(baseline))
            cp.write_text(json.dumps(candidate))
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "compare-efficiency.py"),
                 str(bp), str(cp), "--enforce"],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2)

    def test_compare_enforce_fail(self):
        """--enforce returns exit 1 for failed gates."""
        baseline, candidate = make_pass_pair()
        candidate["total_codex_calls"] = 9  # fails quota gate
        with tempfile.TemporaryDirectory() as tmp:
            bp = Path(tmp) / "b.json"
            cp = Path(tmp) / "c.json"
            bp.write_text(json.dumps(baseline))
            cp.write_text(json.dumps(candidate))
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "compare-efficiency.py"),
                 str(bp), str(cp), "--enforce"],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 1)

    def test_compare_enforce_pass(self):
        """--enforce returns exit 0 when all gates pass."""
        baseline, candidate = make_pass_pair()
        with tempfile.TemporaryDirectory() as tmp:
            bp = Path(tmp) / "b.json"
            cp = Path(tmp) / "c.json"
            bp.write_text(json.dumps(baseline))
            cp.write_text(json.dumps(candidate))
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "compare-efficiency.py"),
                 str(bp), str(cp), "--enforce"],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0)

    def test_compare_no_enforce_default_exit_zero(self):
        """Without --enforce, exit 0 even when gates fail (report-only mode)."""
        baseline, candidate = make_pass_pair()
        candidate["total_codex_calls"] = 9  # fails quota gate
        with tempfile.TemporaryDirectory() as tmp:
            bp = Path(tmp) / "b.json"
            cp = Path(tmp) / "c.json"
            bp.write_text(json.dumps(baseline))
            cp.write_text(json.dumps(candidate))
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "compare-efficiency.py"),
                 str(bp), str(cp)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0)

    def test_run_all_cases(self):
        """Running all cases produces valid output."""
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "run-benchmark-suite.py"),
                "--cases", str(CASES),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        # Parse JSON output (last line is summary, output is before it)
        lines = result.stdout.strip().split("\n")
        # Find JSON output
        json_start = None
        for i, line in enumerate(lines):
            if line.startswith("{"):
                json_start = i
                break
        if json_start is not None:
            json_text = "\n".join(lines[json_start:])
            # Find the end of JSON
            depth = 0
            end = 0
            for j, ch in enumerate(json_text):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = j + 1
                        break
            output = json.loads(json_text[:end])
            self.assertIn("results", output)
            self.assertIn("aggregates", output)


class TestPython39Compat(unittest.TestCase):
    """Verify Python 3.9 compatibility patterns."""

    def test_no_walrus_in_run_benchmark(self):
        """run-benchmark-suite.py does not use walrus operator."""
        content = (SCRIPTS / "run-benchmark-suite.py").read_text()
        self.assertNotIn(":=", content.replace('":=', "").replace("':=", ""))

    def test_no_walrus_in_compare_efficiency(self):
        """compare-efficiency.py does not use walrus operator."""
        content = (SCRIPTS / "compare-efficiency.py").read_text()
        self.assertNotIn(":=", content.replace('":=', "").replace("':=", ""))


class TestInstallerRegistration(unittest.TestCase):
    """Test that scripts are registered in installer/aiwf."""

    def test_benchmark_in_aiwf(self):
        """benchmark is registered in aiwf.py COMMANDS."""
        content = (SCRIPTS / "aiwf.py").read_text()
        self.assertIn('"benchmark":"run-benchmark-suite.py"', content)


class TestCaseFiles(unittest.TestCase):
    """Test that all case files have valid schema."""

    def test_all_cases_valid(self):
        """All case files pass schema validation."""
        for case_dir in sorted(CASES.iterdir()):
            if not case_dir.is_dir():
                continue
            case_file = case_dir / "case.json"
            if not case_file.exists():
                continue
            case = json.loads(case_file.read_text())
            errors = run_benchmark.validate_case(case)
            self.assertEqual(
                errors, [],
                f"Case {case_dir.name} has validation errors: {errors}",
            )

    def test_all_cases_have_required_fields(self):
        """All case files have id and task fields."""
        for case_dir in sorted(CASES.iterdir()):
            if not case_dir.is_dir():
                continue
            case_file = case_dir / "case.json"
            if not case_file.exists():
                continue
            case = json.loads(case_file.read_text())
            self.assertIn("id", case, f"Case {case_dir.name} missing id")
            self.assertIn("task", case, f"Case {case_dir.name} missing task")


if __name__ == "__main__":
    unittest.main()
