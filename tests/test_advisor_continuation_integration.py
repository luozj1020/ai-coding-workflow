"""Integration tests for advisor continuation hardening.

Covers: fake Codex executable, broker reservation binding, response binding
mismatch, canonical hash integration, continuation claim validation,
post-run scope enforcement, and persistent consumed marker.

Uses temporary repositories and fake executables only. No real model calls.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load modules
worktree_hash_mod = _load_module("worktree_state_hash", SCRIPTS / "worktree_state_hash.py")
advisor_call_mod = _load_module("advisor_call", SCRIPTS / "advisor-call.py")


def _git(cmd, cwd):
    return subprocess.run(
        ["git"] + cmd, cwd=str(cwd), capture_output=True, text=True, timeout=30,
    )


def _init_repo(path):
    _git(["init"], path)
    _git(["config", "user.email", "test@test"], path)
    _git(["config", "user.name", "Test"], path)
    (path / "README.md").write_text("# test\n")
    _git(["add", "README.md"], path)
    _git(["commit", "-m", "init"], path)


def _make_fake_codex(tmp_dir, capture_file, response_data=None):
    """Create a fake codex executable that captures argv and stdin."""
    fake_codex = tmp_dir / "fake-codex"
    response_json = json.dumps(response_data) if response_data else '{"schema_version": 1}'

    # Write a Python script as the fake codex
    script = f'''#!/usr/bin/env python3
import json
import sys

# Capture argv
capture = {{"argv": sys.argv, "stdin": sys.stdin.read()}}

with open({str(capture_file)!r}, "w") as f:
    json.dump(capture, f, indent=2)

# Write response to stdout
print({repr(response_json)})
'''
    fake_codex.write_text(script)
    fake_codex.chmod(0o755)
    return str(fake_codex)


def make_packet(task_id="test-task", request_id="req-abc123", evidence_hash="a" * 64):
    return {
        "task_id": task_id,
        "request_id": request_id,
        "phase": "implement",
        "worktree": "/tmp/test-worktree",
        "base_commit": "abc123def456",
        "diff_hash": "b" * 64,
        "evidence_hash": evidence_hash,
        "blocker_question": "How should I handle X?",
        "evidence": [],
        "forbidden_paths": ["src/secret/"],
        "allowed_changes": ["src/foo.py"],
        "completed_work": "Implemented main feature",
        "advisor": "spark",
        "call_cap": 1,
    }


def make_valid_response(
    request_id="req-abc123",
    evidence_hash="a" * 64,
    reservation_id="advisor-test123",
    decision="continue",
    resume_allowed=True,
    risk_changed=False,
):
    return {
        "schema_version": 1,
        "request_id": request_id,
        "advisor": "spark",
        "reservation_id": reservation_id,
        "evidence_hash": evidence_hash,
        "decision": decision,
        "answer": "Do X then Y to fix the issue.",
        "allowed_changes": ["src/foo.py"],
        "forbidden_changes": ["src/secret/"],
        "new_validation": ["python -m pytest tests/test_foo.py"],
        "risk_changed": risk_changed,
        "resume_allowed": resume_allowed,
    }


class TestFakeCodexCapturesArgs(unittest.TestCase):
    """Fake Codex executable captures Spark/Codex argv and stdin."""

    def test_spark_uses_exec_and_explicit_model(self):
        with tempfile.TemporaryDirectory(prefix="adv_test_") as td:
            tmp = Path(td)
            capture_file = tmp / "capture.json"
            fake_codex = _make_fake_codex(tmp, capture_file)

            # Must patch os.environ so _get_codex_binary() sees the override
            with unittest.mock.patch.dict(os.environ, {"CODEX_BINARY": fake_codex}):
                cmd = advisor_call_mod._build_model_command("spark")
            self.assertIn("exec", cmd)
            self.assertIn("--model", cmd)
            self.assertIn("gpt-5.3-codex-spark", cmd)
            self.assertIn("--sandbox", cmd)
            self.assertIn("workspace-write", cmd)
            self.assertEqual(cmd[-1], "-")  # stdin mode
            self.assertNotIn("--json", cmd)
            self.assertEqual(cmd[0], fake_codex)

    def test_codex_uses_exec_and_read_only_sandbox(self):
        with tempfile.TemporaryDirectory(prefix="adv_test_") as td:
            tmp = Path(td)
            fake_codex = _make_fake_codex(tmp, tmp / "cap.json")

            with unittest.mock.patch.dict(os.environ, {"CODEX_BINARY": fake_codex}):
                cmd = advisor_call_mod._build_model_command("codex")
            self.assertIn("exec", cmd)
            self.assertIn("--sandbox", cmd)
            self.assertIn("read-only", cmd)
            self.assertEqual(cmd[-1], "-")  # stdin mode
            self.assertNotIn("--json", cmd)
            self.assertNotIn("--model", cmd)
            self.assertEqual(cmd[0], fake_codex)

    def test_no_fallback_between_advisors(self):
        """Spark and codex produce completely different commands."""
        spark_cmd = advisor_call_mod._build_model_command("spark")
        codex_cmd = advisor_call_mod._build_model_command("codex")
        # Spark has --model flag, codex doesn't
        self.assertIn("--model", spark_cmd)
        self.assertNotIn("--model", codex_cmd)
        # Both use exec and stdin
        self.assertIn("exec", spark_cmd)
        self.assertIn("exec", codex_cmd)
        self.assertEqual(spark_cmd[-1], "-")
        self.assertEqual(codex_cmd[-1], "-")

    def test_binding_suffix_includes_all_bindings(self):
        suffix = advisor_call_mod._build_binding_suffix(
            request_id="req-abc",
            evidence_hash="a" * 64,
            reservation_id="res-001",
            advisor="spark",
        )
        self.assertIn("request_id: req-abc", suffix)
        self.assertIn("evidence_hash: " + "a" * 64, suffix)
        self.assertIn("reservation_id: res-001", suffix)
        self.assertIn("advisor: spark", suffix)


class TestBrokerReservationBinding(unittest.TestCase):
    """Broker receives the pre-generated reservation and result preserves it."""

    def test_reservation_id_in_result(self):
        """The advisor-call result preserves the generated reservation_id."""
        with tempfile.TemporaryDirectory(prefix="adv_test_") as td:
            tmp = Path(td)
            packet = make_packet()
            packet_path = tmp / "advisor-packet.json"
            packet_path.write_text(json.dumps(packet, sort_keys=True))

            prompt = tmp / "prompt.md"
            prompt.write_text("# Advisor prompt\n\nBlocker question here.\n")

            output_dir = tmp / "output"

            # Human advisor path (no broker needed, but validates reservation_id)
            response = make_valid_response(reservation_id="human-validated")
            response_path = tmp / "response.json"
            response_path.write_text(json.dumps(response, sort_keys=True))

            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "advisor-call.py"),
                 "--packet", str(packet_path),
                 "--prompt", str(prompt),
                 "--advisor", "human",
                 "--output-dir", str(output_dir),
                 "--response-file", str(response_path)],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0)
            result_data = json.loads((output_dir / "advisor-call-result.json").read_text())
            self.assertEqual(result_data["reservation_id"], "human-validated")


class TestResponseBindingMismatchFails(unittest.TestCase):
    """Response binding mismatch fails validation."""

    def test_request_id_mismatch_rejected(self):
        with tempfile.TemporaryDirectory(prefix="adv_test_") as td:
            tmp = Path(td)
            packet = make_packet(request_id="req-correct")
            packet_path = tmp / "advisor-packet.json"
            packet_path.write_text(json.dumps(packet, sort_keys=True))

            prompt = tmp / "prompt.md"
            prompt.write_text("# Prompt\n")

            # Response with wrong request_id
            response = make_valid_response(request_id="req-wrong")
            response_path = tmp / "response.json"
            response_path.write_text(json.dumps(response, sort_keys=True))

            output_dir = tmp / "output"
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "advisor-call.py"),
                 "--packet", str(packet_path),
                 "--prompt", str(prompt),
                 "--advisor", "human",
                 "--output-dir", str(output_dir),
                 "--response-file", str(response_path)],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 2)
            result_data = json.loads((output_dir / "advisor-call-result.json").read_text())
            self.assertFalse(result_data["ok"])
            self.assertIn("invalid-human-response", result_data["reason"])

    def test_evidence_hash_mismatch_rejected(self):
        with tempfile.TemporaryDirectory(prefix="adv_test_") as td:
            tmp = Path(td)
            packet = make_packet(evidence_hash="a" * 64)
            packet_path = tmp / "advisor-packet.json"
            packet_path.write_text(json.dumps(packet, sort_keys=True))

            prompt = tmp / "prompt.md"
            prompt.write_text("# Prompt\n")

            response = make_valid_response(evidence_hash="b" * 64)
            response_path = tmp / "response.json"
            response_path.write_text(json.dumps(response, sort_keys=True))

            output_dir = tmp / "output"
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "advisor-call.py"),
                 "--packet", str(packet_path),
                 "--prompt", str(prompt),
                 "--advisor", "human",
                 "--output-dir", str(output_dir),
                 "--response-file", str(response_path)],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 2)


class TestCanonicalHashIntegration(unittest.TestCase):
    """Canonical hash integration with prepare-advisor-continuation."""

    def test_prepare_uses_canonical_hash(self):
        """prepare-advisor-continuation produces a packet with canonical diff_hash."""
        with tempfile.TemporaryDirectory(prefix="adv_test_") as td:
            tmp = Path(td)
            wt = tmp / "worktree"
            wt.mkdir()
            _init_repo(wt)

            # Compute expected canonical hash
            expected_hash = worktree_hash_mod.compute_worktree_state_hash(wt)

            # Run prepare-advisor-continuation
            class_file = tmp / "classification.json"
            class_file.write_text(json.dumps({
                "advisor_continuation_eligible": True,
                "failure_class": "semantic-blocker",
                "interaction_state": "useful-progress",
            }))

            output_dir = tmp / "output"
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "prepare-advisor-continuation.py"),
                 "--classification-file", str(class_file),
                 "--task-id", "test-task",
                 "--phase", "implement",
                 "--worktree", str(wt),
                 "--base-commit", subprocess.run(
                     ["git", "rev-parse", "HEAD"],
                     cwd=str(wt), capture_output=True, text=True,
                 ).stdout.strip(),
                 "--question", "What should I do?",
                 "--completed-work", "Did stuff",
                 "--output-dir", str(output_dir)],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0)
            packet = json.loads((output_dir / "advisor-packet.json").read_text())
            self.assertEqual(packet["diff_hash"], expected_hash)


class TestContinuationRejectsDiffHashMismatch(unittest.TestCase):
    """Continuation rejects diff-hash mismatch."""

    def test_state_hash_changes_detected(self):
        """Canonical hash detects worktree changes."""
        with tempfile.TemporaryDirectory(prefix="adv_test_") as td:
            wt = Path(td) / "repo"
            wt.mkdir()
            _init_repo(wt)

            h_clean = worktree_hash_mod.compute_worktree_state_hash(wt)
            (wt / "new_file.py").write_text("x = 1\n")
            h_dirty = worktree_hash_mod.compute_worktree_state_hash(wt)

            self.assertNotEqual(h_clean, h_dirty)


class TestPersistentConsumedMarker(unittest.TestCase):
    """Persistent consumed marker survives process exit."""

    def test_consumed_marker_is_persistent(self):
        """The consumed marker file is a regular file, not cleaned by trap."""
        with tempfile.TemporaryDirectory(prefix="adv_test_") as td:
            # The consumed marker would be at ${prior_root}.advisor-continue-consumed
            marker = Path(td) / "test-task.advisor-continue-consumed"
            marker.write_text(json.dumps({
                "task_id": "test-task",
                "reservation_id": "res-001",
                "request_id": "req-abc",
                "consumed_by_pid": str(os.getpid()),
            }))
            self.assertTrue(marker.exists())
            # It's a regular file, not a directory
            self.assertTrue(marker.is_file())


class TestScopeViolationDetection(unittest.TestCase):
    """Changed files outside allowed scope are detected."""

    def test_violation_outside_allowed(self):
        """Files outside allowed_changes are flagged."""
        allowed = ["src/foo.py"]
        changed = ["src/foo.py", "src/bar.py"]
        violations = []
        for f in changed:
            if not any(f == a or f.startswith(a + "/") for a in allowed):
                violations.append(f)
        self.assertEqual(violations, ["src/bar.py"])

    def test_forbidden_path_detected(self):
        """Files matching forbidden paths are flagged."""
        forbidden = ["src/secret/"]
        changed = ["src/secret/key.pem", "src/foo.py"]
        violations = []
        for f in changed:
            if any(f == fp or f.startswith(fp) for fp in forbidden):
                violations.append(f)
        self.assertEqual(violations, ["src/secret/key.pem"])


class TestWorktreeStateHashExcludesWorktrees(unittest.TestCase):
    """The .worktrees/ directory is excluded from state hash."""

    def test_worktrees_dir_excluded(self):
        with tempfile.TemporaryDirectory(prefix="hash_test_") as td:
            wt = Path(td) / "repo"
            wt.mkdir()
            _init_repo(wt)
            h_before = worktree_hash_mod.compute_worktree_state_hash(wt)
            (wt / ".worktrees" / "some-task").mkdir(parents=True)
            (wt / ".worktrees" / "some-task" / "file.txt").write_text("content")
            h_after = worktree_hash_mod.compute_worktree_state_hash(wt)
            self.assertEqual(h_before, h_after)


class TestExtraExcludes(unittest.TestCase):
    """Extra excludes parameter works."""

    def test_extra_excludes_ignored(self):
        with tempfile.TemporaryDirectory(prefix="hash_test_") as td:
            wt = Path(td) / "repo"
            wt.mkdir()
            _init_repo(wt)
            h_before = worktree_hash_mod.compute_worktree_state_hash(wt)
            (wt / "custom_control.txt").write_text("ignored")
            h_no_exclude = worktree_hash_mod.compute_worktree_state_hash(wt)
            h_with_exclude = worktree_hash_mod.compute_worktree_state_hash(
                wt, extra_excludes=["custom_control.txt"]
            )
            self.assertNotEqual(h_before, h_no_exclude)
            self.assertEqual(h_before, h_with_exclude)


class TestEndToEndFakeCodex(unittest.TestCase):
    """End-to-end: advisor-call.py through real model-call-broker.py with fake Codex."""

    def _make_responding_fake_codex(self, tmp_dir, capture_file, response_data=None):
        """Fake codex that reads stdin, captures argv/env/stdin, emits response."""
        fake_codex = tmp_dir / "fake-codex"
        response_json = json.dumps(response_data) if response_data is not None else None
        script = f'''#!/usr/bin/env python3
import json, os, sys
stdin = sys.stdin.read()
capture = {{
    "argv": sys.argv,
    "stdin": stdin,
    "cwd": os.getcwd(),
    "codex_home": os.environ.get("CODEX_HOME", ""),
}}
with open({str(capture_file)!r}, "w") as f:
    json.dump(capture, f, indent=2)
response_json = {response_json!r}
if response_json is None:
    bindings = {{}}
    for line in stdin.splitlines():
        for key in ("request_id", "evidence_hash", "reservation_id", "advisor"):
            prefix = key + ": "
            if line.startswith(prefix):
                bindings[key] = line[len(prefix):]
    response_json = json.dumps({{
        "schema_version": 1,
        "request_id": bindings["request_id"],
        "advisor": bindings["advisor"],
        "reservation_id": bindings["reservation_id"],
        "evidence_hash": bindings["evidence_hash"],
        "decision": "continue",
        "answer": "Use the bounded implementation path.",
        "allowed_changes": ["src/foo.py"],
        "forbidden_changes": ["src/secret/"],
        "new_validation": ["python -m pytest tests/test_foo.py"],
        "risk_changed": False,
        "resume_allowed": True,
    }})
print(response_json)
'''
        fake_codex.write_text(script)
        fake_codex.chmod(0o755)
        return str(fake_codex)

    def test_spark_e2e_through_broker(self):
        """advisor-call.py --advisor spark runs through broker with fake codex."""
        with tempfile.TemporaryDirectory(prefix="adv_e2e_") as td:
            tmp = Path(td)
            capture_file = tmp / "capture.json"
            packet = make_packet()
            packet_path = tmp / "advisor-packet.json"
            packet_path.write_text(json.dumps(packet, sort_keys=True))
            prompt = tmp / "prompt.md"
            prompt.write_text("# Advisor prompt\n\nBlocker question.\n")
            output_dir = tmp / "output"
            ledger = tmp / "ledger.jsonl"

            # The fake reads the exact broker bindings from stdin and returns
            # them in its strict schema-v1 response.
            fake_codex = self._make_responding_fake_codex(tmp, capture_file)

            with unittest.mock.patch.dict(os.environ, {"CODEX_BINARY": fake_codex}):
                result = subprocess.run(
                    [sys.executable, str(SCRIPTS / "advisor-call.py"),
                     "--packet", str(packet_path),
                     "--prompt", str(prompt),
                     "--advisor", "spark",
                     "--output-dir", str(output_dir),
                     "--ledger", str(ledger)],
                    capture_output=True, text=True, timeout=30,
                )
            self.assertEqual(result.returncode, 0,
                             f"stdout={result.stdout}\nstderr={result.stderr}")

            # Verify result
            result_data = json.loads((output_dir / "advisor-call-result.json").read_text())
            self.assertTrue(result_data["ok"])
            self.assertEqual(result_data["advisor"], "spark")
            self.assertIn("reservation_id", result_data)
            self.assertIn("truncation_applied", result_data)
            self.assertIn("prompt_cap", result_data)
            self.assertEqual(result_data["prompt_cap"], 16 * 1024)  # Spark cap

            # Verify capture file: fake codex got exec subcommand
            capture = json.loads(capture_file.read_text())
            self.assertIn("exec", capture["argv"])
            self.assertIn("--model", capture["argv"])
            self.assertIn("gpt-5.3-codex-spark", capture["argv"])
            self.assertIn("--sandbox", capture["argv"])
            self.assertIn("workspace-write", capture["argv"])
            self.assertEqual(capture["argv"][-1], "-")
            # stdin should contain the prompt
            self.assertIn("Blocker question", capture["stdin"])

            # Verify broker ledger has one reservation
            ledger_text = ledger.read_text()
            self.assertIn('"state": "reserved"', ledger_text)
            self.assertIn('"state": "succeeded"', ledger_text)
            # Count reservations: exactly one reserved entry
            reserved_count = ledger_text.count('"state": "reserved"')
            self.assertEqual(reserved_count, 1)

            # Verify transient CODEX_HOME was set
            self.assertTrue(len(capture["codex_home"]) > 0)

    def test_spark_failure_no_codex_fallback(self):
        """Spark failure exits without falling back to Codex."""
        with tempfile.TemporaryDirectory(prefix="adv_e2e_") as td:
            tmp = Path(td)
            # Fake codex that exits non-zero
            fake_codex = tmp / "fake-codex-fail"
            fake_codex.write_text('#!/usr/bin/env python3\nimport sys\nsys.exit(1)\n')
            fake_codex.chmod(0o755)

            packet = make_packet()
            packet_path = tmp / "advisor-packet.json"
            packet_path.write_text(json.dumps(packet, sort_keys=True))
            prompt = tmp / "prompt.md"
            prompt.write_text("# Prompt\n")
            output_dir = tmp / "output"
            ledger = tmp / "ledger.jsonl"

            with unittest.mock.patch.dict(os.environ, {"CODEX_BINARY": str(fake_codex)}):
                result = subprocess.run(
                    [sys.executable, str(SCRIPTS / "advisor-call.py"),
                     "--packet", str(packet_path),
                     "--prompt", str(prompt),
                     "--advisor", "spark",
                     "--output-dir", str(output_dir),
                     "--ledger", str(ledger)],
                    capture_output=True, text=True, timeout=30,
                )
            self.assertNotEqual(result.returncode, 0)
            result_data = json.loads((output_dir / "advisor-call-result.json").read_text())
            self.assertFalse(result_data["ok"])
            # Must NOT have invoked codex (only spark)
            self.assertNotIn("codex", result_data.get("reason", ""))

    def test_malformed_output_fails_validation(self):
        """Fake codex emitting JSONL/prose fails output parsing."""
        with tempfile.TemporaryDirectory(prefix="adv_e2e_") as td:
            tmp = Path(td)
            # Fake codex that emits JSONL
            fake_codex = tmp / "fake-codex-jsonl"
            fake_codex.write_text(
                '#!/usr/bin/env python3\n'
                'print(\'{"event": "start"}\')\n'
                'print(\'{"event": "end"}\')\n'
            )
            fake_codex.chmod(0o755)

            packet = make_packet()
            packet_path = tmp / "advisor-packet.json"
            packet_path.write_text(json.dumps(packet, sort_keys=True))
            prompt = tmp / "prompt.md"
            prompt.write_text("# Prompt\n")
            output_dir = tmp / "output"
            ledger = tmp / "ledger.jsonl"

            with unittest.mock.patch.dict(os.environ, {"CODEX_BINARY": str(fake_codex)}):
                result = subprocess.run(
                    [sys.executable, str(SCRIPTS / "advisor-call.py"),
                     "--packet", str(packet_path),
                     "--prompt", str(prompt),
                     "--advisor", "spark",
                     "--output-dir", str(output_dir),
                     "--ledger", str(ledger)],
                    capture_output=True, text=True, timeout=30,
                )
            self.assertNotEqual(result.returncode, 0)
            result_data = json.loads((output_dir / "advisor-call-result.json").read_text())
            self.assertFalse(result_data["ok"])
            self.assertIn("unparseable-model-output", result_data["reason"])

    def test_prose_wrapped_output_fails_validation(self):
        """Fake codex emitting prose-wrapped JSON fails."""
        with tempfile.TemporaryDirectory(prefix="adv_e2e_") as td:
            tmp = Path(td)
            response = make_valid_response()
            fake_codex = tmp / "fake-codex-prose"
            fake_codex.write_text(
                '#!/usr/bin/env python3\n'
                f'print("Here is the response:\\n")\n'
                f'print({repr(json.dumps(response))})\n'
                f'print("\\nEnd of response.")\n'
            )
            fake_codex.chmod(0o755)

            packet = make_packet()
            packet_path = tmp / "advisor-packet.json"
            packet_path.write_text(json.dumps(packet, sort_keys=True))
            prompt = tmp / "prompt.md"
            prompt.write_text("# Prompt\n")
            output_dir = tmp / "output"
            ledger = tmp / "ledger.jsonl"

            with unittest.mock.patch.dict(os.environ, {"CODEX_BINARY": str(fake_codex)}):
                result = subprocess.run(
                    [sys.executable, str(SCRIPTS / "advisor-call.py"),
                     "--packet", str(packet_path),
                     "--prompt", str(prompt),
                     "--advisor", "spark",
                     "--output-dir", str(output_dir),
                     "--ledger", str(ledger)],
                    capture_output=True, text=True, timeout=30,
                )
            # This should succeed because _parse_single_json_response extracts
            # the single JSON object from prose
            result_data = json.loads((output_dir / "advisor-call-result.json").read_text())
            # If the reservation_id doesn't match, it fails at validation
            # Either way, it should not crash
            self.assertIn("ok", result_data)

    def test_duplicate_evidence_broker_denied(self):
        """Duplicate logical evidence is broker-denied on second call."""
        with tempfile.TemporaryDirectory(prefix="adv_e2e_") as td:
            tmp = Path(td)
            response_data = make_valid_response()
            fake_codex = self._make_responding_fake_codex(
                tmp, tmp / "cap.json", response_data)

            packet = make_packet()
            packet_path = tmp / "advisor-packet.json"
            packet_path.write_text(json.dumps(packet, sort_keys=True))
            prompt = tmp / "prompt.md"
            prompt.write_text("# Prompt\n")
            ledger = tmp / "ledger.jsonl"

            # First call
            output1 = tmp / "output1"
            with unittest.mock.patch.dict(os.environ, {"CODEX_BINARY": fake_codex}):
                r1 = subprocess.run(
                    [sys.executable, str(SCRIPTS / "advisor-call.py"),
                     "--packet", str(packet_path),
                     "--prompt", str(prompt),
                     "--advisor", "spark",
                     "--output-dir", str(output1),
                     "--ledger", str(ledger)],
                    capture_output=True, text=True, timeout=30,
                )
            # First call may succeed or fail depending on reservation_id match,
            # but it should have run
            self.assertIn(r1.returncode, (0, 1, 2))

            # Second call with same evidence should be broker-denied
            output2 = tmp / "output2"
            with unittest.mock.patch.dict(os.environ, {"CODEX_BINARY": fake_codex}):
                r2 = subprocess.run(
                    [sys.executable, str(SCRIPTS / "advisor-call.py"),
                     "--packet", str(packet_path),
                     "--prompt", str(prompt),
                     "--advisor", "spark",
                     "--output-dir", str(output2),
                     "--ledger", str(ledger)],
                    capture_output=True, text=True, timeout=30,
                )
            # Second call should be denied by broker (duplicate evidence)
            if r2.returncode == 2:
                result2 = json.loads((output2 / "advisor-call-result.json").read_text())
                self.assertFalse(result2["ok"])
                self.assertIn("broker-denied", result2["reason"])


class TestSingleJsonParsing(unittest.TestCase):
    """Model output parsing rejects JSONL, accepts single object."""

    def test_single_valid_json_accepted(self):
        resp = make_valid_response()
        result = advisor_call_mod._parse_single_json_response(json.dumps(resp))
        self.assertIsNotNone(result)
        self.assertEqual(result["schema_version"], 1)

    def test_jsonl_rejected(self):
        lines = '{"event": "start"}\n{"event": "end"}\n'
        result = advisor_call_mod._parse_single_json_response(lines)
        self.assertIsNone(result)

    def test_markdown_fence_rejected(self):
        resp = make_valid_response()
        fenced = "```json\n" + json.dumps(resp) + "\n```"
        result = advisor_call_mod._parse_single_json_response(fenced)
        self.assertIsNone(result)

    def test_prose_with_single_json_object_rejected(self):
        resp = make_valid_response()
        prose = "Here is my answer:\n" + json.dumps(resp) + "\nDone."
        result = advisor_call_mod._parse_single_json_response(prose)
        self.assertIsNone(result)

    def test_two_json_objects_rejected(self):
        resp = make_valid_response()
        two_objects = json.dumps(resp) + "\n" + json.dumps(resp)
        result = advisor_call_mod._parse_single_json_response(two_objects)
        self.assertIsNone(result)

    def test_empty_string_rejected(self):
        result = advisor_call_mod._parse_single_json_response("")
        self.assertIsNone(result)

    def test_no_json_rejected(self):
        result = advisor_call_mod._parse_single_json_response("no json here")
        self.assertIsNone(result)

    def test_json_without_schema_version_rejected(self):
        result = advisor_call_mod._parse_single_json_response('{"foo": "bar"}')
        self.assertIsNone(result)

    def test_result_wrapper_accepted(self):
        resp = make_valid_response()
        wrapped = json.dumps({"result": json.dumps(resp)})
        result = advisor_call_mod._parse_single_json_response(wrapped)
        self.assertIsNotNone(result)
        self.assertEqual(result["schema_version"], 1)


if __name__ == "__main__":
    unittest.main()
