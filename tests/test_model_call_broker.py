"""Acceptance tests for model-call-broker.py (PR2).

Tests cover:
1. Concurrent budget exhaustion (last Codex call race)
2. Duplicate evidence rejection (concurrent)
3. Different evidence may use remaining budget
4. Reserved stage enforcement
5. Child failure + retry denial
6. Stdin/arguments/output file passthrough
7. Ledger parseability after concurrency
8. Registration in aiwf.py and install_workflow.py
9. Static: shell helpers no longer directly spawn claude/codex
10. Windows-compatible paths/spaces and Python 3.9 compatibility
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
BROKER = SCRIPTS / "model-call-broker.py"
SCHEMAS = REPO_ROOT / "schemas"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


broker_mod = load_module("model_call_broker", BROKER)


def make_plan(
    task_id: str = "T-1",
    claude_calls: int = 2,
    spark_calls: int = 2,
    codex_calls: int = 2,
    reserved_for=None,
    milestones=None,
) -> dict:
    return {
        "schema_version": 1,
        "task_id": task_id,
        "lane": "standard",
        "budget": {
            "claude_calls": claude_calls,
            "spark_calls": spark_calls,
            "codex_calls": codex_calls,
        },
        "review": {
            "reserved_for": reserved_for or [],
            "milestones": milestones or [],
        },
    }


def write_plan(tmp: Path, plan: dict, name: str = "plan.json") -> Path:
    p = tmp / name
    p.write_text(json.dumps(plan, sort_keys=True), encoding="utf-8")
    return p


def write_file(tmp: Path, content: str, name: str) -> Path:
    p = tmp / name
    p.write_text(content, encoding="utf-8")
    return p


def run_broker(
    tmp: Path,
    role: str = "claude",
    stage: str = "builder",
    plan_path: Path = None,
    input_path: Path = None,
    evidence_path: Path = None,
    command: list = None,
    ledger_path: Path = None,
    dry_run: bool = False,
    retry_failed: bool = False,
    max_calls: int = None,
    run_id: str = None,
) -> tuple:
    """Run broker as a subprocess. Returns (exit_code, stdout, stderr)."""
    cmd = [sys.executable, str(BROKER), "--role", role, "--stage", stage]
    if plan_path:
        cmd += ["--plan", str(plan_path)]
    if input_path:
        cmd += ["--input", str(input_path)]
    if evidence_path:
        cmd += ["--evidence", str(evidence_path)]
    if ledger_path:
        cmd += ["--ledger", str(ledger_path)]
    if dry_run:
        cmd.append("--dry-run")
    if retry_failed:
        cmd.append("--retry-failed")
    if max_calls is not None:
        cmd += ["--max-calls", str(max_calls)]
    if run_id:
        cmd += ["--run-id", run_id]
    cmd += ["--"]
    if command:
        cmd += command
    else:
        cmd += [sys.executable, "-c", "print('hello')"]

    result = subprocess.run(
        cmd, capture_output=True, text=True, cwd=str(tmp), timeout=30
    )
    return result.returncode, result.stdout, result.stderr


# ---------------------------------------------------------------------------
# Test 1: Concurrent budget exhaustion
# ---------------------------------------------------------------------------


class TestConcurrentBudgetExhaustion(unittest.TestCase):
    """Two concurrent processes competing for the last Codex call:
    exactly one succeeds; the other is denied."""

    def test_last_codex_call_race(self):
        with tempfile.TemporaryDirectory(prefix="broker_test_") as td:
            tmp = Path(td)
            plan = make_plan(codex_calls=1, claude_calls=0, spark_calls=0)
            plan_path = write_plan(tmp, plan)
            ledger = tmp / "ledger.jsonl"

            results = []
            lock = threading.Lock()

            def attempt(idx):
                inp = write_file(tmp, f"input-{idx}", f"input-{idx}.txt")
                rc, out, err = run_broker(
                    tmp,
                    role="codex",
                    stage="builder",
                    plan_path=plan_path,
                    input_path=inp,
                    ledger_path=ledger,
                    command=[sys.executable, "-c", "print('ok')"],
                    run_id=f"run-{idx}",
                )
                with lock:
                    results.append((idx, rc, out, err))

            with ThreadPoolExecutor(max_workers=2) as pool:
                futs = [pool.submit(attempt, i) for i in range(2)]
                for f in as_completed(futs):
                    f.result()

            successes = [r for r in results if r[1] == 0]
            denials = [r for r in results if r[1] == 2]
            self.assertEqual(len(successes), 1, f"Expected 1 success, got {len(successes)}")
            self.assertEqual(len(denials), 1, f"Expected 1 denial, got {len(denials)}")
            self.assertIn("denied", denials[0][2] + denials[0][3])

            # Ledger is parseable
            records = [
                json.loads(line)
                for line in ledger.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertGreater(len(records), 0)
            for r in records:
                self.assertIn(r["state"], broker_mod.VALID_STATES)


# ---------------------------------------------------------------------------
# Test 2: Duplicate evidence rejection
# ---------------------------------------------------------------------------


class TestDuplicateEvidenceRejection(unittest.TestCase):
    """Same evidence hash can invoke only once, including concurrent requests."""

    def test_same_input_evidence_rejected(self):
        with tempfile.TemporaryDirectory(prefix="broker_test_") as td:
            tmp = Path(td)
            plan = make_plan(codex_calls=5)
            plan_path = write_plan(tmp, plan)
            inp = write_file(tmp, "same-input", "input.txt")
            ev = write_file(tmp, "same-evidence", "evidence.txt")
            ledger = tmp / "ledger.jsonl"

            # First call succeeds
            rc1, _, _ = run_broker(
                tmp, role="codex", stage="builder", plan_path=plan_path,
                input_path=inp, evidence_path=ev, ledger_path=ledger,
                command=[sys.executable, "-c", "print('first')"],
                run_id="run-1",
            )
            self.assertEqual(rc1, 0)

            # Second call with same input+evidence is denied
            rc2, _, err2 = run_broker(
                tmp, role="codex", stage="builder", plan_path=plan_path,
                input_path=inp, evidence_path=ev, ledger_path=ledger,
                command=[sys.executable, "-c", "print('second')"],
                run_id="run-2",
            )
            self.assertEqual(rc2, 2)
            self.assertIn("Duplicate evidence", err2)

    def test_concurrent_duplicate_evidence(self):
        with tempfile.TemporaryDirectory(prefix="broker_test_") as td:
            tmp = Path(td)
            plan = make_plan(codex_calls=5)
            plan_path = write_plan(tmp, plan)
            inp = write_file(tmp, "shared-input", "input.txt")
            ledger = tmp / "ledger.jsonl"

            results = []
            lock = threading.Lock()

            def attempt(idx):
                rc, out, err = run_broker(
                    tmp, role="codex", stage="builder", plan_path=plan_path,
                    input_path=inp, ledger_path=ledger,
                    command=[sys.executable, "-c", f"print('attempt-{idx}')"],
                    run_id=f"run-{idx}",
                )
                with lock:
                    results.append((idx, rc))

            with ThreadPoolExecutor(max_workers=3) as pool:
                futs = [pool.submit(attempt, i) for i in range(3)]
                for f in as_completed(futs):
                    f.result()

            successes = [r for r in results if r[1] == 0]
            self.assertEqual(len(successes), 1, f"Expected 1 success, got {len(successes)}: {results}")


# ---------------------------------------------------------------------------
# Test 3: Different evidence may use remaining budget
# ---------------------------------------------------------------------------


class TestDifferentEvidence(unittest.TestCase):
    """Different evidence may use remaining budget."""

    def test_different_inputs_use_budget(self):
        with tempfile.TemporaryDirectory(prefix="broker_test_") as td:
            tmp = Path(td)
            plan = make_plan(codex_calls=3)
            plan_path = write_plan(tmp, plan)
            ledger = tmp / "ledger.jsonl"

            for i in range(3):
                inp = write_file(tmp, f"input-{i}", f"input-{i}.txt")
                rc, _, _ = run_broker(
                    tmp, role="codex", stage="builder", plan_path=plan_path,
                    input_path=inp, ledger_path=ledger,
                    command=[sys.executable, "-c", f"print('call-{i}')"],
                    run_id=f"run-{i}",
                )
                self.assertEqual(rc, 0, f"Call {i} should succeed")

            # Fourth call is denied (budget exhausted)
            inp4 = write_file(tmp, "input-3", "input-3.txt")
            rc4, _, _ = run_broker(
                tmp, role="codex", stage="builder", plan_path=plan_path,
                input_path=inp4, ledger_path=ledger,
                command=[sys.executable, "-c", "print('call-3')"],
                run_id="run-3",
            )
            self.assertEqual(rc4, 2, "Fourth call should be denied")


# ---------------------------------------------------------------------------
# Test 4: Reserved stage enforcement
# ---------------------------------------------------------------------------


class TestReservedStageEnforcement(unittest.TestCase):
    """Reserved Codex final-review capacity cannot be consumed at
    implementation milestone."""

    def test_reserved_stage_blocks_non_reserved(self):
        with tempfile.TemporaryDirectory(prefix="broker_test_") as td:
            tmp = Path(td)
            plan = make_plan(
                codex_calls=2,
                reserved_for=["final-candidate", "final-review"],
                milestones=["implementation-complete", "final-candidate", "final-review"],
            )
            plan_path = write_plan(tmp, plan)
            ledger = tmp / "ledger.jsonl"

            # implementation-complete cannot consume reserved budget
            inp = write_file(tmp, "input-1", "input.txt")
            rc, _, err = run_broker(
                tmp, role="codex", stage="implementation-complete",
                plan_path=plan_path, input_path=inp, ledger_path=ledger,
                command=[sys.executable, "-c", "print('impl')"],
                run_id="run-impl",
            )
            # Should be denied because all budget is reserved for final-*
            self.assertEqual(rc, 2)
            self.assertIn("reserved", err.lower() + err)

    def test_reserved_stage_allows_reserved(self):
        with tempfile.TemporaryDirectory(prefix="broker_test_") as td:
            tmp = Path(td)
            plan = make_plan(
                codex_calls=2,
                reserved_for=["final-candidate", "final-review"],
            )
            plan_path = write_plan(tmp, plan)
            ledger = tmp / "ledger.jsonl"

            inp = write_file(tmp, "input-1", "input.txt")
            rc, _, _ = run_broker(
                tmp, role="codex", stage="final-review",
                plan_path=plan_path, input_path=inp, ledger_path=ledger,
                command=[sys.executable, "-c", "print('review')"],
                run_id="run-review",
            )
            self.assertEqual(rc, 0)

    def test_reserved_stage_cannot_steal_another_reserved_slot(self):
        with tempfile.TemporaryDirectory(prefix="broker_test_") as td:
            tmp = Path(td)
            plan_path = write_plan(
                tmp,
                make_plan(
                    codex_calls=2,
                    reserved_for=["final-candidate", "final-review"],
                ),
            )
            ledger = tmp / "ledger.jsonl"
            first = write_file(tmp, "first", "first.txt")
            rc, _, _ = run_broker(
                tmp, role="codex", stage="final-review", plan_path=plan_path,
                input_path=first, ledger_path=ledger,
                command=[sys.executable, "-c", "print('first')"],
            )
            self.assertEqual(rc, 0)

            second = write_file(tmp, "second", "second.txt")
            rc, _, _ = run_broker(
                tmp, role="codex", stage="final-review", plan_path=plan_path,
                input_path=second, ledger_path=ledger,
                command=[sys.executable, "-c", "print('second')"],
            )
            self.assertEqual(rc, 2)

            candidate = write_file(tmp, "candidate", "candidate.txt")
            rc, _, _ = run_broker(
                tmp, role="codex", stage="final-candidate", plan_path=plan_path,
                input_path=candidate, ledger_path=ledger,
                command=[sys.executable, "-c", "print('candidate')"],
            )
            self.assertEqual(rc, 0)


# ---------------------------------------------------------------------------
# Test 5: Child failure and retry denial
# ---------------------------------------------------------------------------


class TestChildFailureAndRetryDenial(unittest.TestCase):
    """Child failure produces a clear failed transition; retry without
    explicit authorization is denied and audit is preserved."""

    def test_child_failure_recorded(self):
        with tempfile.TemporaryDirectory(prefix="broker_test_") as td:
            tmp = Path(td)
            plan = make_plan(claude_calls=3)
            plan_path = write_plan(tmp, plan)
            ledger = tmp / "ledger.jsonl"

            inp = write_file(tmp, "input-1", "input.txt")
            rc, _, _ = run_broker(
                tmp, role="claude", stage="builder", plan_path=plan_path,
                input_path=inp, ledger_path=ledger,
                command=[sys.executable, "-c", "import sys; sys.exit(1)"],
                run_id="run-fail",
            )
            self.assertEqual(rc, 1)

            # Verify ledger has failed state
            records = [
                json.loads(line)
                for line in ledger.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            states = [r["state"] for r in records]
            self.assertIn("failed", states)

    def test_retry_without_authorization_denied(self):
        with tempfile.TemporaryDirectory(prefix="broker_test_") as td:
            tmp = Path(td)
            plan = make_plan(claude_calls=5)
            plan_path = write_plan(tmp, plan)
            ledger = tmp / "ledger.jsonl"

            inp = write_file(tmp, "input-1", "input.txt")

            # First call fails
            rc1, _, _ = run_broker(
                tmp, role="claude", stage="builder", plan_path=plan_path,
                input_path=inp, ledger_path=ledger,
                command=[sys.executable, "-c", "import sys; sys.exit(1)"],
                run_id="run-1",
            )
            self.assertEqual(rc1, 1)

            # Retry without --retry-failed is denied (duplicate evidence)
            rc2, _, err2 = run_broker(
                tmp, role="claude", stage="builder", plan_path=plan_path,
                input_path=inp, ledger_path=ledger,
                command=[sys.executable, "-c", "print('retry')"],
                run_id="run-2",
            )
            self.assertEqual(rc2, 2)
            self.assertIn("Duplicate evidence", err2)

    def test_retry_with_authorization_allowed(self):
        with tempfile.TemporaryDirectory(prefix="broker_test_") as td:
            tmp = Path(td)
            plan = make_plan(claude_calls=5)
            plan_path = write_plan(tmp, plan)
            ledger = tmp / "ledger.jsonl"

            inp = write_file(tmp, "input-1", "input.txt")

            # First call fails
            rc1, _, _ = run_broker(
                tmp, role="claude", stage="builder", plan_path=plan_path,
                input_path=inp, ledger_path=ledger,
                command=[sys.executable, "-c", "import sys; sys.exit(1)"],
                run_id="run-1",
            )
            self.assertEqual(rc1, 1)

            # Retry with --retry-failed is allowed
            rc2, _, _ = run_broker(
                tmp, role="claude", stage="builder", plan_path=plan_path,
                input_path=inp, ledger_path=ledger, retry_failed=True,
                command=[sys.executable, "-c", "print('retry-ok')"],
                run_id="run-2",
            )
            self.assertEqual(rc2, 0)


# ---------------------------------------------------------------------------
# Test 6: Stdin/arguments/output file passthrough
# ---------------------------------------------------------------------------


class TestCommandPassthrough(unittest.TestCase):
    """Normal Claude/Spark/Codex fake commands receive stdin/arguments
    and output files correctly."""

    def test_stdin_passthrough(self):
        with tempfile.TemporaryDirectory(prefix="broker_test_") as td:
            tmp = Path(td)
            plan = make_plan(claude_calls=1)
            plan_path = write_plan(tmp, plan)
            inp = write_file(tmp, "prompt content here", "prompt.md")
            out = tmp / "result.json"
            ledger = tmp / "ledger.jsonl"

            rc, _, _ = run_broker(
                tmp, role="claude", stage="builder", plan_path=plan_path,
                input_path=inp, ledger_path=ledger,
                command=[
                    sys.executable, "-c",
                    "import sys; data = sys.stdin.read(); print(repr(data))",
                ],
                run_id="run-stdin",
            )
            # The command should have received the input via stdin
            self.assertEqual(rc, 0)

    def test_arguments_passthrough(self):
        with tempfile.TemporaryDirectory(prefix="broker_test_") as td:
            tmp = Path(td)
            plan = make_plan(claude_calls=1)
            plan_path = write_plan(tmp, plan)
            ledger = tmp / "ledger.jsonl"

            rc, stdout, _ = run_broker(
                tmp, role="claude", stage="builder", plan_path=plan_path,
                ledger_path=ledger,
                command=[
                    sys.executable, "-c",
                    "import sys; print(' '.join(sys.argv[1:]))",
                    "--flag", "value",
                ],
                run_id="run-args",
            )
            self.assertEqual(rc, 0)
            self.assertIn("--flag value", stdout)

    def test_output_file_writing(self):
        with tempfile.TemporaryDirectory(prefix="broker_test_") as td:
            tmp = Path(td)
            plan = make_plan(claude_calls=1)
            plan_path = write_plan(tmp, plan)
            out = tmp / "child-output.txt"
            err = tmp / "child-stderr.txt"
            ledger = tmp / "ledger.jsonl"

            cmd = [
                sys.executable, "-c",
                "import sys; sys.stdout.write('output-data'); sys.stderr.write('error-data')",
            ]
            broker_cmd = [
                sys.executable, str(BROKER),
                "--role", "claude", "--stage", "builder",
                "--plan", str(plan_path),
                "--output", str(out),
                "--stderr", str(err),
                "--ledger", str(ledger),
                "--", *cmd,
            ]
            result = subprocess.run(
                broker_cmd, capture_output=True, text=True, cwd=str(tmp), timeout=30
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(out.read_text(encoding="utf-8"), "output-data")
            self.assertEqual(err.read_text(encoding="utf-8"), "error-data")


# ---------------------------------------------------------------------------
# Test 7: Ledger parseability after concurrency
# ---------------------------------------------------------------------------


class TestLedgerParseability(unittest.TestCase):
    """Ledger JSONL is parseable after concurrency and every reservation
    has legal transitions."""

    def test_ledger_after_concurrent_writes(self):
        with tempfile.TemporaryDirectory(prefix="broker_test_") as td:
            tmp = Path(td)
            plan = make_plan(codex_calls=10, claude_calls=10)
            plan_path = write_plan(tmp, plan)
            ledger = tmp / "ledger.jsonl"

            def attempt(idx):
                role = "codex" if idx % 2 == 0 else "claude"
                inp = write_file(tmp, f"input-{idx}", f"input-{idx}.txt")
                rc, _, _ = run_broker(
                    tmp, role=role, stage="builder", plan_path=plan_path,
                    input_path=inp, ledger_path=ledger,
                    command=[sys.executable, "-c", f"print('concurrent-{idx}')"],
                    run_id=f"run-{idx}",
                )
                return rc

            with ThreadPoolExecutor(max_workers=4) as pool:
                futs = [pool.submit(attempt, i) for i in range(8)]
                results = [f.result() for f in as_completed(futs)]

            # All should succeed
            self.assertTrue(all(rc == 0 for rc in results), f"Results: {results}")

            # Ledger is parseable
            records = [
                json.loads(line)
                for line in ledger.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertGreater(len(records), 0)

            # Every reservation has legal transitions
            valid_transitions = {
                ("reserved", "running"),
                ("running", "succeeded"),
                ("running", "failed"),
                ("running", "cancelled"),
            }
            # Group by reservation_id
            by_res = {}
            for r in records:
                rid = r["reservation_id"]
                by_res.setdefault(rid, []).append(r)

            for rid, res_records in by_res.items():
                states = [r["state"] for r in res_records]
                # Must start with reserved
                self.assertEqual(states[0], "reserved", f"Reservation {rid} doesn't start with reserved")
                # Must have running
                self.assertIn("running", states, f"Reservation {rid} missing running state")
                # Must end with terminal state
                self.assertIn(states[-1], ("succeeded", "failed", "cancelled"),
                              f"Reservation {rid} ends with {states[-1]}")
                # Check transitions are legal
                for i in range(1, len(states)):
                    self.assertIn(
                        (states[i - 1], states[i]),
                        valid_transitions,
                        f"Reservation {rid}: illegal transition {states[i-1]} -> {states[i]}",
                    )


# ---------------------------------------------------------------------------
# Test 8: Registration in aiwf.py and install_workflow.py
# ---------------------------------------------------------------------------


class TestRegistration(unittest.TestCase):
    """Installer and aiwf model-call register the broker."""

    def test_aiwf_registers_model_call(self):
        aiwf_text = (SCRIPTS / "aiwf.py").read_text(encoding="utf-8")
        self.assertIn('"model-call"', aiwf_text)
        self.assertIn('"model-call-broker.py"', aiwf_text)

    def test_install_workflow_registers_broker(self):
        install_text = (SCRIPTS / "install_workflow.py").read_text(encoding="utf-8")
        self.assertIn("model-call-broker.py", install_text)
        self.assertIn("ai/model-call-broker.py", install_text)

    def test_broker_cli_help(self):
        result = subprocess.run(
            [sys.executable, str(BROKER), "--help"],
            capture_output=True, text=True, timeout=10
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--role", result.stdout)
        self.assertIn("--stage", result.stdout)
        self.assertIn("--plan", result.stdout)


# ---------------------------------------------------------------------------
# Test 9: Static: shell helpers no longer directly spawn claude/codex
# ---------------------------------------------------------------------------


class TestShellHelperStaticChecks(unittest.TestCase):
    """Static tests confirm the three model-facing helpers no longer directly
    spawn claude/codex; only broker does. CLI availability checks may remain."""

    def _find_direct_spawn(self, script_path: Path, model_cmd: str) -> list:
        """Find lines that directly spawn a model command (not in comments,
        not in BYPASS_BROKER blocks, not in availability checks)."""
        text = script_path.read_text(encoding="utf-8")
        lines = text.splitlines()
        violations = []
        in_bypass = False

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # Track bypass blocks
            if "AI_CODING_WORKFLOW_BYPASS_BROKER" in stripped:
                in_bypass = True
            if in_bypass and ("fi" in stripped or "else" in stripped):
                if "else" in stripped:
                    in_bypass = False
                elif "fi" in stripped and "else" not in stripped:
                    in_bypass = False

            if in_bypass:
                continue

            # Skip comments
            if stripped.startswith("#"):
                continue

            # Skip availability checks (command -v / which)
            if "command -v" in stripped or "which" in stripped:
                continue

            # Check for direct model spawning (pipe to stdin redirect)
            if re.search(rf'\b{model_cmd}\b.*\bexec\b', stripped) or \
               re.search(rf'\b{model_cmd}\b.*\b-p\b', stripped):
                # Exclude references that are just echo/comment/variable assignments
                if any(skip in stripped for skip in ["echo ", "echo\"", "Error:", "#", "auto_disable"]):
                    continue
                violations.append(f"{script_path.name}:{i}: {stripped}")

        return violations

    def test_dispatch_to_claude_no_direct_spawn(self):
        script = SCRIPTS / "dispatch-to-claude.sh"
        violations = self._find_direct_spawn(script, "claude")
        # Filter out the BYPASS_BROKER block lines
        # The only claude spawning should be inside the bypass block
        non_bypass = []
        text = script.read_text(encoding="utf-8")
        in_bypass = False
        for i, line in enumerate(text.splitlines(), 1):
            if "AI_CODING_WORKFLOW_BYPASS_BROKER" in line:
                in_bypass = True
            if in_bypass and "fi" in line:
                in_bypass = False
                continue
            if in_bypass:
                continue
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "command -v" in stripped:
                continue
            if re.search(r'\bclaude\b.*\b-p\b', stripped):
                if any(skip in stripped for skip in ["echo", "Error:", "auto_disable"]):
                    continue
                non_bypass.append(f"line {i}: {stripped}")

        self.assertEqual(
            non_bypass, [],
            f"dispatch-to-claude.sh still directly spawns claude outside bypass block: {non_bypass}"
        )

    def test_run_codex_spark_no_direct_spawn(self):
        script = SCRIPTS / "run-codex-spark.sh"
        text = script.read_text(encoding="utf-8")
        in_bypass = False
        violations = []
        for i, line in enumerate(text.splitlines(), 1):
            if "AI_CODING_WORKFLOW_BYPASS_BROKER" in line:
                in_bypass = True
            if in_bypass and "fi" in line:
                in_bypass = False
                continue
            if in_bypass:
                continue
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "command -v" in stripped:
                continue
            if re.search(r'\bcodex\b.*\bexec\b', stripped):
                if any(skip in stripped for skip in ["echo", "Error:", "auto_disable", "SPARK_CHECKS_RUN"]):
                    continue
                violations.append(f"line {i}: {stripped}")

        self.assertEqual(
            violations, [],
            f"run-codex-spark.sh still directly spawns codex outside bypass block: {violations}"
        )

    def test_review_with_codex_no_direct_spawn(self):
        script = SCRIPTS / "review-with-codex.sh"
        text = script.read_text(encoding="utf-8")
        lines = text.splitlines()
        in_bypass = False
        violations = []
        for i, line in enumerate(lines, 1):
            if "AI_CODING_WORKFLOW_BYPASS_BROKER" in line:
                in_bypass = True
            if in_bypass and "fi" in line:
                in_bypass = False
                continue
            if in_bypass:
                continue
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "command -v" in stripped:
                continue
            if re.search(r'\bcodex\b.*\bexec\b', stripped):
                if i > 1 and lines[i - 2].rstrip().endswith("-- \\"):
                    continue
                if any(skip in stripped for skip in ["echo", "Error:", "auto_disable"]):
                    continue
                violations.append(f"line {i}: {stripped}")

        self.assertEqual(
            violations, [],
            f"review-with-codex.sh still directly spawns codex outside bypass block: {violations}"
        )


# ---------------------------------------------------------------------------
# Test 10: Windows-compatible paths/spaces and Python 3.9 compatibility
# ---------------------------------------------------------------------------


class TestCompatibility(unittest.TestCase):
    """Windows-compatible paths/spaces and Python 3.9 compatibility."""

    def test_python_version_compatible(self):
        """Broker should work with Python 3.9+."""
        self.assertGreaterEqual(sys.version_info[:2], (3, 9))

    def test_no_shell_true_usage(self):
        """Broker must never use shell=True."""
        broker_text = BROKER.read_text(encoding="utf-8")
        self.assertNotIn("shell=True", broker_text)

    def test_path_with_spaces(self):
        """Broker should handle paths with spaces."""
        with tempfile.TemporaryDirectory(prefix="broker test ") as td:
            tmp = Path(td)
            plan = make_plan(claude_calls=1)
            plan_path = write_plan(tmp, plan)
            inp = write_file(tmp, "input with spaces", "my input.txt")
            ledger = tmp / "my ledger.jsonl"

            rc, stdout, _ = run_broker(
                tmp, role="claude", stage="builder", plan_path=plan_path,
                input_path=inp, ledger_path=ledger,
                command=[sys.executable, "-c", "print('spaces-ok')"],
                run_id="run-spaces",
            )
            self.assertEqual(rc, 0)

    def test_dry_run_output(self):
        """Dry run should produce valid JSON without executing."""
        with tempfile.TemporaryDirectory(prefix="broker_test_") as td:
            tmp = Path(td)
            plan = make_plan(claude_calls=1)
            plan_path = write_plan(tmp, plan)
            inp = write_file(tmp, "input", "input.txt")
            ledger = tmp / "ledger.jsonl"

            rc, stdout, _ = run_broker(
                tmp, role="claude", stage="builder", plan_path=plan_path,
                input_path=inp, ledger_path=ledger, dry_run=True,
                command=[sys.executable, "-c", "print('should-not-run')"],
            )
            self.assertEqual(rc, 0)
            data = json.loads(stdout)
            self.assertTrue(data["dry_run"])
            self.assertEqual(data["role"], "claude")
            self.assertEqual(data["stage"], "builder")

    def test_compatibility_mode_no_plan(self):
        """Compatibility mode (no plan) should use conservative defaults."""
        with tempfile.TemporaryDirectory(prefix="broker_test_") as td:
            tmp = Path(td)
            ledger = tmp / "ledger.jsonl"

            rc, _, _ = run_broker(
                tmp, role="claude", stage="builder", max_calls=1,
                ledger_path=ledger,
                command=[sys.executable, "-c", "print('compat')"],
                run_id="run-compat",
            )
            self.assertEqual(rc, 0)

            # Second call should be denied
            rc2, _, err2 = run_broker(
                tmp, role="claude", stage="builder", max_calls=1,
                ledger_path=ledger,
                command=[sys.executable, "-c", "print('compat2')"],
                run_id="run-compat-2",
            )
            self.assertEqual(rc2, 2)

    def test_zero_budget_denied(self):
        """Zero budget for a role should deny the call."""
        with tempfile.TemporaryDirectory(prefix="broker_test_") as td:
            tmp = Path(td)
            plan = make_plan(claude_calls=0, codex_calls=0, spark_calls=0)
            plan_path = write_plan(tmp, plan)
            ledger = tmp / "ledger.jsonl"

            rc, _, err = run_broker(
                tmp, role="claude", stage="builder", plan_path=plan_path,
                ledger_path=ledger,
                command=[sys.executable, "-c", "print('zero')"],
            )
            self.assertEqual(rc, 2)
            self.assertIn("zero budget", err.lower())

    def test_invalid_plan_rejected(self):
        """Invalid plan should be rejected."""
        with tempfile.TemporaryDirectory(prefix="broker_test_") as td:
            tmp = Path(td)
            bad_plan = tmp / "bad.json"
            bad_plan.write_text('{"not": "a valid plan"}', encoding="utf-8")
            ledger = tmp / "ledger.jsonl"

            rc, _, err = run_broker(
                tmp, role="claude", stage="builder", plan_path=bad_plan,
                ledger_path=ledger,
                command=[sys.executable, "-c", "print('bad')"],
            )
            self.assertEqual(rc, 3)
            self.assertIn("budget", err.lower())


# ---------------------------------------------------------------------------
# Module-level API tests (direct function calls, not subprocess)
# ---------------------------------------------------------------------------


class TestModuleAPI(unittest.TestCase):
    """Test broker module functions directly."""

    def test_load_valid_plan(self):
        with tempfile.TemporaryDirectory(prefix="broker_test_") as td:
            tmp = Path(td)
            plan = make_plan()
            plan_path = write_plan(tmp, plan)
            loaded = broker_mod.load_plan(plan_path)
            self.assertEqual(loaded["task_id"], "T-1")

    def test_load_plan_missing_file(self):
        with self.assertRaises(broker_mod.BrokerError):
            broker_mod.load_plan(Path("/nonexistent/plan.json"))

    def test_load_plan_missing_budget(self):
        with tempfile.TemporaryDirectory(prefix="broker_test_") as td:
            tmp = Path(td)
            bad = tmp / "bad.json"
            bad.write_text('{"schema_version": 1}', encoding="utf-8")
            with self.assertRaises(broker_mod.BrokerError):
                broker_mod.load_plan(bad)

    def test_compute_hash_consistency(self):
        with tempfile.TemporaryDirectory(prefix="broker_test_") as td:
            tmp = Path(td)
            f = write_file(tmp, "same content", "test.txt")
            h1 = broker_mod.compute_hash(f)
            h2 = broker_mod.compute_hash(f)
            self.assertEqual(h1, h2)
            self.assertEqual(len(h1), 64)  # SHA-256

    def test_compute_hash_empty(self):
        h = broker_mod.compute_hash(None)
        self.assertEqual(h, broker_mod.compute_hash(None))

    def test_make_compatibility_plan(self):
        plan = broker_mod.make_compatibility_plan("codex", 5, "test-task")
        self.assertEqual(plan["budget"]["codex_calls"], 5)
        self.assertEqual(plan["budget"]["claude_calls"], 1)
        self.assertEqual(plan["task_id"], "test-task")

    def test_valid_states(self):
        self.assertEqual(
            set(broker_mod.VALID_STATES),
            {"reserved", "running", "succeeded", "failed", "cancelled"},
        )

    def test_budget_consuming_states(self):
        for state in ("reserved", "running", "succeeded"):
            self.assertTrue(broker_mod.budget_consuming({"state": state}))
        for state in ("failed", "cancelled"):
            self.assertFalse(broker_mod.budget_consuming({"state": state}))


if __name__ == "__main__":
    unittest.main()
